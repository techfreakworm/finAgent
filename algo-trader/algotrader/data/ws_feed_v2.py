"""DhanHQ v2 Live Market Feed — raw WebSocket implementation.

Replaces the SDK's broken ``MarketFeed`` whose ``get_instrument_data()``
awaits ``ws.recv()`` with NO timeout and blocked for 6.5 h on a silent socket
on 2026-06-15.

Core fix: every ``ws.recv()`` is wrapped in ``asyncio.wait_for(..., timeout=
recv_timeout_sec)``; on ``TimeoutError`` the session is torn down and a new
WebSocket connection is established immediately.

Design invariants
-----------------
- PAPER/DATA ONLY.  No order-placement imports or calls.
- Same public interface as ``LiveBarFeed``.
- ``_BarBuilder`` is reused directly from ``live_feed`` (aggregates ticks into
  1-min bars; handles grace timers and session filtering).
- All datetimes tz-aware Asia/Kolkata.
- Access-token refresh on disconnect code 807 via ``token_manager``.
- Reconnect with exponential back-off (1 → 2 → 4 → … → 60 s max).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import threading
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

try:
    import websockets
    import websockets.exceptions as _ws_exc
    _WEBSOCKETS_AVAILABLE = True
except ImportError:
    _WEBSOCKETS_AVAILABLE = False

from algotrader.core import Instrument, IST
from algotrader.data.live_feed import BarEvent, _BarBuilder, GRACE_SECS

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ constants

_WSS_URL = "wss://api-feed.dhan.co"

# v2 RequestCode values (from SDK source / docs)
_RC_TICKER = 15
_RC_QUOTE  = 17
_RC_FULL   = 21
_RC_DISCONNECT = 12

# Default mode: Quote (code 17) — carries LTP + LTT + volume, sufficient for
# building 1-min OHLCV bars.  Full (21) adds market depth which we don't need.
_DEFAULT_REQUEST_CODE = _RC_QUOTE

# Binary response-packet first-byte codes
_RESP_TICKER    = 2
_RESP_MARKET_D  = 3
_RESP_QUOTE     = 4
_RESP_OI        = 5
_RESP_PREV_CL   = 6
_RESP_STATUS    = 7
_RESP_FULL      = 8
_RESP_DISCONNECT = 50

# Server disconnect codes
_DISC_TOO_MANY_CONNS = 805
_DISC_NO_SUBSCRIPTION = 806
_DISC_TOKEN_EXPIRED   = 807
_DISC_INVALID_CLIENT  = 808
_DISC_AUTH_FAILED     = 809

# Exchange segment int (binary header) → string (subscription JSON)
_SEG_INT_TO_STR: dict[int, str] = {
    0: "IDX_I",
    1: "NSE_EQ",
    2: "NSE_FNO",
    3: "NSE_CURRENCY",
    4: "BSE_EQ",
    5: "MCX_COMM",
    7: "BSE_CURRENCY",
    8: "BSE_FNO",
}

# Exchange segment string → int (for subscription msg assembly)
_SEG_STR_TO_INT: dict[str, int] = {v: k for k, v in _SEG_INT_TO_STR.items()}


# ---------------------------------------------------------------- struct fmts
# All formats verified against SDK marketfeed.py process_* methods.
# Byte order: Little Endian (<).

_FMT_TICKER  = "<BHBIfI"   # 16 bytes: code, msg_len, seg, sec_id, LTP, LTT
_FMT_QUOTE   = "<BHBIfHIfIIIffff"    # 50 bytes
_FMT_FULL    = "<BHBIfHIfIIIIIIffff100s"  # 162 bytes
_FMT_OI      = "<BHBII"    # 12 bytes
_FMT_PREV_CL = "<BHBIfI"   # 16 bytes (same layout as ticker)
_FMT_DISC    = "<BHBIH"    # 10 bytes: code, msg_len, seg, sec_id, disc_code

# Response header (every packet): code(B,1) msg_len(H,2) seg(B,1) sec_id(I,4)
_HEADER_LEN = 8

# Known fixed packet sizes per response code (bytes). Verified against live
# captures: the header msg_len field equals these exactly (e.g. QUOTE msg_len=50).
# Codes NOT in this map (market-depth=3, status=7) advance by the header's
# msg_len field instead — we never subscribe to those in Quote mode.
_PKT_SIZE: dict[int, int] = {
    _RESP_TICKER: 16,
    _RESP_QUOTE:  50,
    _RESP_FULL:   162,
    _RESP_OI:     12,
    _RESP_PREV_CL: 16,
    _RESP_DISCONNECT: 10,
}


# =================================================================== main class

class DhanLiveFeedV2:
    """DhanHQ v2 live market feed via raw WebSocket.

    Public interface identical to ``LiveBarFeed``; can be used as a drop-in
    replacement wherever ``LiveBarFeed`` is constructed.

    Parameters
    ----------
    subscriptions:
        List of ``(security_id_str, exchange_segment_str, Instrument)`` triples.
    on_bar:
        Callback ``(BarEvent) -> None`` called from a background thread.
    client_id:
        Dhan client ID.  Read from env via ``token_manager`` if empty.
    access_token:
        Initial access token.  Read from env/token_manager if empty.
    grace_sec:
        Seconds after last tick before an open bar is force-closed.
    watchdog_sec:
        Unused (kept for interface parity; failover is handled by FeedManager).
    recv_timeout_sec:
        Hard timeout per ``ws.recv()`` call.  On timeout the session is
        torn down and reconnected.  **This is the core fix for the zombie-socket
        bug.**  Default 15 s (well under the server's 40 s silence limit).
    request_code:
        Subscription mode: 15=Ticker, 17=Quote (default), 21=Full.
    """

    def __init__(
        self,
        subscriptions: list[tuple[str, str, Instrument]],
        on_bar: Callable[[BarEvent], None],
        client_id: str = "",
        access_token: str = "",
        grace_sec: float = GRACE_SECS,
        watchdog_sec: float = 90.0,
        recv_timeout_sec: float = 15.0,
        request_code: int = _DEFAULT_REQUEST_CODE,
    ) -> None:
        if not _WEBSOCKETS_AVAILABLE:
            raise RuntimeError("websockets package not installed; pip install websockets")

        self._subs: list[tuple[str, str, Instrument]] = list(subscriptions)
        self._on_bar   = on_bar
        self._client_id    = client_id
        self._access_token = access_token
        self._grace    = grace_sec
        self._recv_timeout = recv_timeout_sec
        self._request_code = request_code

        # Per-instrument bar builders keyed by str(security_id)
        self._builders: dict[str, _BarBuilder] = {}
        for sec_id, seg, instr in subscriptions:
            self._builders[str(sec_id)] = _BarBuilder(
                instrument=instr,
                on_bar=self._on_bar,
                grace_sec=grace_sec,
            )

        # Background async infrastructure
        self._stop_evt   = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop:   asyncio.AbstractEventLoop | None = None
        self._ws = None   # live websockets.WebSocketClientProtocol

        # Pending dynamic subscriptions (thread-safe queue → sent in recv_loop)
        self._pending_subs_lock = threading.Lock()
        self._pending_subs: list[tuple[str, str, Instrument]] = []

        # Stats
        self._reconnect_count = 0
        self._tick_count      = 0

        # Optional raw-frame capture (diagnostics only; gated by env)
        self._capture_enabled = os.environ.get("WS_FRAME_CAPTURE", "") == "1"
        self._capture_fh = None
        self._capture_n = 0
        try:
            self._capture_max = int(os.environ.get("WS_FRAME_CAPTURE_MAX", "50000"))
        except ValueError:
            self._capture_max = 50000

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Spawn background thread and connect to the feed."""
        self._stop_evt.clear()
        self._ensure_token()
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="DhanFeedV2"
        )
        self._thread.start()
        log.info(
            "DhanLiveFeedV2 started: %d instruments, recv_timeout=%ss",
            len(self._subs), self._recv_timeout,
        )

    def stop(self) -> None:
        """Signal stop; flush open bars; wait for background thread."""
        self._stop_evt.set()
        # Flush all open bars before exiting
        for b in self._builders.values():
            try:
                b.flush()
                b.stop()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._capture_fh is not None:
            try:
                self._capture_fh.close()
                log.info("DhanLiveFeedV2: raw frame capture closed (%d frames)",
                         self._capture_n)
            except Exception:
                pass
            self._capture_fh = None
        log.info(
            "DhanLiveFeedV2 stopped (ticks=%d reconnects=%d)",
            self._tick_count, self._reconnect_count,
        )

    def subscribe_dynamic(
        self, subscriptions: list[tuple[str, str, Instrument]]
    ) -> None:
        """Add instruments to the running feed (thread-safe).

        New builders are created immediately; subscription messages are queued
        and dispatched on the next recv-loop iteration.
        """
        with self._pending_subs_lock:
            for sec_id, seg, instr in subscriptions:
                key = str(sec_id)
                if key not in self._builders:
                    self._builders[key] = _BarBuilder(
                        instrument=instr,
                        on_bar=self._on_bar,
                        grace_sec=self._grace,
                    )
                    self._pending_subs.append((sec_id, seg, instr))
                    self._subs.append((sec_id, seg, instr))
                    log.info("subscribe_dynamic queued: %s (sec=%s)", instr.symbol, key)

    # ----------------------------------------------------------------- private

    def _ensure_token(self) -> None:
        """Populate client_id / access_token from token_manager if not provided."""
        if self._access_token and self._client_id:
            return
        try:
            from algotrader.data.token_manager import get_valid_token
            import os
            tok = get_valid_token()
            if tok and not self._access_token:
                self._access_token = tok
            if not self._client_id:
                self._client_id = os.environ.get("DHAN_CLIENT_ID", "")
        except Exception:
            log.debug("token_manager not available", exc_info=True)

    def _refresh_token(self) -> bool:
        """Refresh access token on code-807 disconnect.  Returns True on success."""
        try:
            from algotrader.data.token_manager import get_valid_token
            tok = get_valid_token()
            if tok:
                self._access_token = tok
                log.info("DhanLiveFeedV2: token refreshed after 807")
                return True
            log.error("DhanLiveFeedV2: get_valid_token returned None on 807")
            return False
        except Exception:
            log.exception("DhanLiveFeedV2: token refresh raised")
            return False

    # ---------------------------------------------------------------- thread

    def _thread_main(self) -> None:
        """Background thread entry point — creates and runs the event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._connect_loop())
        finally:
            loop.close()
            self._loop = None

    async def _connect_loop(self) -> None:
        """Outer reconnect loop with exponential back-off."""
        backoff = 1.0
        while not self._stop_evt.is_set():
            try:
                await self._run_session()
                backoff = 1.0  # clean exit → reset
            except Exception as exc:
                if not self._stop_evt.is_set():
                    log.warning("DhanLiveFeedV2 session error: %s", exc)

            if self._stop_evt.is_set():
                break

            self._reconnect_count += 1
            log.info(
                "DhanLiveFeedV2: reconnecting in %.0fs (attempt %d)",
                backoff, self._reconnect_count,
            )
            # Use wait + check stop so we exit cleanly on stop()
            try:
                await asyncio.wait_for(
                    asyncio.sleep(backoff), timeout=backoff + 1
                )
            except asyncio.TimeoutError:
                pass
            # Except if stop was set during sleep
            if self._stop_evt.is_set():
                break
            backoff = min(backoff * 2, 60.0)

    async def _run_session(self) -> None:
        """Single WebSocket session: connect → subscribe → recv loop."""
        url = (
            f"{_WSS_URL}?version=2"
            f"&token={self._access_token}"
            f"&clientId={self._client_id}"
            f"&authType=2"
        )
        # Never log token or clientId
        log.info("DhanLiveFeedV2: connecting to wss://api-feed.dhan.co (v2)")

        async with websockets.connect(
            url,
            ping_interval=None,   # we let the server manage ping/pong
            ping_timeout=None,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            log.info("DhanLiveFeedV2: connected; subscribing %d instruments", len(self._subs))

            # Subscribe all current instruments
            await self._subscribe_all(ws, self._subs)

            # Enter recv loop
            await self._recv_loop(ws)

        self._ws = None

    async def _subscribe_all(
        self, ws, subs: list[tuple[str, str, Instrument]]
    ) -> None:
        """Send subscription messages for *subs* in batches of ≤100."""
        if not subs:
            return
        # Batch by 100 (API limit per message)
        for i in range(0, len(subs), 100):
            batch = subs[i : i + 100]
            msg = {
                "RequestCode": self._request_code,
                "InstrumentCount": len(batch),
                "InstrumentList": [
                    {
                        "ExchangeSegment": seg,
                        "SecurityId": str(sec_id),
                    }
                    for sec_id, seg, _ in batch
                ],
            }
            await ws.send(json.dumps(msg))
            log.debug(
                "DhanLiveFeedV2: subscribed batch %d-%d (RC=%d)",
                i, i + len(batch) - 1, self._request_code,
            )

    async def _recv_loop(self, ws) -> None:
        """Core recv loop with hard timeout — THE zombie-socket fix."""
        while not self._stop_evt.is_set():
            # Send any pending dynamic subscriptions
            await self._flush_pending_subs(ws)

            try:
                data = await asyncio.wait_for(
                    ws.recv(), timeout=self._recv_timeout
                )
            except asyncio.TimeoutError:
                # THE FIX: hard timeout means we never block like the SDK did.
                # Force reconnect so a new connection (and re-auth) is established.
                log.warning(
                    "DhanLiveFeedV2: recv timeout after %.0fs — forcing reconnect",
                    self._recv_timeout,
                )
                return  # triggers reconnect in _connect_loop

            except (_ws_exc.ConnectionClosed, _ws_exc.ConnectionClosedOK,
                    _ws_exc.ConnectionClosedError) as exc:
                log.info("DhanLiveFeedV2: connection closed (%s)", exc)
                return

            if isinstance(data, bytes):
                self._dispatch(data)
            # str frames (text) are unexpected but harmless — ignore

    async def _flush_pending_subs(self, ws) -> None:
        """Send any queued dynamic subscriptions."""
        with self._pending_subs_lock:
            pending = list(self._pending_subs)
            self._pending_subs.clear()
        if pending:
            await self._subscribe_all(ws, pending)

    # -------------------------------------------------------------- dispatch

    def _dispatch(self, data: bytes) -> None:
        """Route a raw binary frame to parsers, walking ALL packets it carries.

        Dhan v2 may concatenate multiple response packets into one WebSocket
        frame.  The previous implementation parsed only the first packet
        (``data[:16/50/162]``) and dropped the rest — a latent data-loss bug if
        the server ever batches.  We now walk the frame packet-by-packet:
        read the 8-byte header (code, msg_len), parse that one packet, then
        advance by its length (the known fixed size for the code, which equals
        the header ``msg_len`` — verified against live captures; falling back to
        ``msg_len`` for codes we don't size explicitly).
        """
        n = len(data)
        if n < _HEADER_LEN:
            return
        if self._capture_enabled:
            self._capture_frame(data)

        off = 0
        guard = 0
        while off + _HEADER_LEN <= n:
            guard += 1
            if guard > 4096:  # runaway protection — no real frame has this many
                log.warning("DhanLiveFeedV2: frame walk exceeded 4096 packets; aborting")
                break
            code, msg_len = struct.unpack_from("<BH", data, off)
            size = _PKT_SIZE.get(code, msg_len)
            if size <= 0 or off + size > n:
                # Unknown/short boundary — can't safely continue this frame.
                if off == 0:
                    log.debug("DhanLiveFeedV2: undecodable frame head code=%d "
                              "size=%d frame_len=%d", code, size, n)
                break
            self._dispatch_one(code, data[off:off + size])
            off += size

    def _dispatch_one(self, code: int, pkt: bytes) -> None:
        """Parse a single response packet and feed any resulting tick."""
        if code == _RESP_TICKER:
            result = self._parse_ticker(pkt)
        elif code == _RESP_QUOTE:
            result = self._parse_quote(pkt)
        elif code == _RESP_FULL:
            result = self._parse_full(pkt)
        elif code == _RESP_DISCONNECT:
            self._handle_disconnect(pkt)
            return
        elif code in (_RESP_OI, _RESP_PREV_CL, _RESP_STATUS, _RESP_MARKET_D):
            # No ticks from these; ignore
            return
        else:
            log.debug("DhanLiveFeedV2: unknown response code %d", code)
            return

        if result is not None:
            sec_id_int, ltp, ltt_epoch = result
            self._feed_tick(sec_id_int, ltp, ltt_epoch)

    # ----------------------------------------------------------- raw capture

    def _capture_frame(self, data: bytes) -> None:
        """Append a raw frame (hex) to the capture file, if enabled and uncapped.

        Gated by env ``WS_FRAME_CAPTURE=1``.  Best-effort, bounded by
        ``self._capture_max`` frames so the file can't grow without limit.
        Pure diagnostics — never affects bar building or the trade path.
        """
        if self._capture_n >= self._capture_max:
            return
        try:
            if self._capture_fh is None:
                ts = datetime.now(IST).strftime("%Y%m%dT%H%M%S")
                path = os.path.join(
                    os.environ.get("WS_FRAME_CAPTURE_DIR", "reports/ws_shadow"),
                    f"raw_frames_{ts}.jsonl",
                )
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self._capture_fh = open(path, "w")
                log.info("DhanLiveFeedV2: raw frame capture -> %s (max=%d)",
                         path, self._capture_max)
            # JSONL: arrival epoch (the bar clock) + raw hex (carries LTT).
            # Lets a borderline shadow verdict be decomposed post-hoc into
            # arrival-vs-LTT skew, boundary latency, or clock issues.
            self._capture_fh.write(
                '{"a": %.3f, "h": "%s"}\n'
                % (self._arrival_now().timestamp(), data.hex())
            )
            self._capture_n += 1
        except Exception:
            log.debug("frame capture write failed", exc_info=True)

    # --------------------------------------------------------------- parsers

    def _parse_ticker(self, data: bytes) -> tuple[int, float, int] | None:
        """Parse Ticker packet (response code 2).

        Returns (security_id_int, ltp_float, ltt_epoch_int) or None.
        """
        if len(data) < 16:
            log.debug("Ticker packet too short: %d bytes", len(data))
            return None
        try:
            unpacked = struct.unpack(_FMT_TICKER, data[:16])
            # (<BHBIfI>): code, msg_len, seg, sec_id, LTP, LTT
            return int(unpacked[3]), float(unpacked[4]), int(unpacked[5])
        except struct.error as exc:
            log.debug("Ticker parse error: %s", exc)
            return None

    def _parse_quote(self, data: bytes) -> tuple[int, float, int] | None:
        """Parse Quote packet (response code 4).

        Returns (security_id_int, ltp_float, ltt_epoch_int) or None.
        """
        if len(data) < 50:
            log.debug("Quote packet too short: %d bytes", len(data))
            return None
        try:
            unpacked = struct.unpack(_FMT_QUOTE, data[:50])
            # (<BHBIfHIfIIIffff>): [3]=sec_id, [4]=LTP, [5]=LTQ, [6]=LTT
            return int(unpacked[3]), float(unpacked[4]), int(unpacked[6])
        except struct.error as exc:
            log.debug("Quote parse error: %s", exc)
            return None

    def _parse_full(self, data: bytes) -> tuple[int, float, int] | None:
        """Parse Full packet (response code 8).

        Returns (security_id_int, ltp_float, ltt_epoch_int) or None.
        """
        if len(data) < 162:
            log.debug("Full packet too short: %d bytes", len(data))
            return None
        try:
            unpacked = struct.unpack(_FMT_FULL, data[:162])
            # same header layout: [3]=sec_id, [4]=LTP, [5]=LTQ, [6]=LTT
            return int(unpacked[3]), float(unpacked[4]), int(unpacked[6])
        except struct.error as exc:
            log.debug("Full parse error: %s", exc)
            return None

    def _handle_disconnect(self, data: bytes) -> None:
        """Parse server-sent disconnect packet and react to code 807."""
        if len(data) < 10:
            log.warning("Disconnect packet too short: %d bytes", len(data))
            return
        try:
            unpacked = struct.unpack(_FMT_DISC, data[:10])
            code = int(unpacked[4])
        except struct.error:
            log.warning("Could not parse disconnect packet")
            return

        msgs = {
            _DISC_TOO_MANY_CONNS:   "too many WebSocket connections (>5)",
            _DISC_NO_SUBSCRIPTION:  "no data-API subscription",
            _DISC_TOKEN_EXPIRED:    "access token expired (807) — refreshing",
            _DISC_INVALID_CLIENT:   "invalid client ID",
            _DISC_AUTH_FAILED:      "authentication failed",
        }
        log.warning(
            "DhanLiveFeedV2: server disconnect code %d — %s",
            code, msgs.get(code, "unknown reason"),
        )

        if code == _DISC_TOKEN_EXPIRED:
            # Refresh BEFORE the reconnect loop re-embeds the token in the URL.
            # This is the fix for the SDK bug that reused a dead token.
            self._refresh_token()

    # ---------------------------------------------------------- tick dispatch

    def _arrival_now(self) -> datetime:
        """Wall-clock arrival time (tz-aware IST).  Seam for deterministic tests."""
        return datetime.now(IST)

    def _feed_tick(self, security_id_int: int, ltp: float, ltt_epoch: int) -> None:
        """Convert a parsed tick into a BarBuilder feed call.

        Bars are bucketed by tick ARRIVAL (wall-clock) time, NOT the packet's
        last-trade-time (``ltt_epoch``).

        ROOT CAUSE of the 2026-06-19 shadow failure (819,582 ticks collapsed to
        ~2,333 bars vs REST's 19,448; 99.6% close-mismatch; OPEN phase empty):
        Dhan's Quote ``LTT`` is the last-TRADE timestamp and updates coarsely —
        many quote packets repeat a stale LTT (and off-hours it is nonsensical,
        e.g. 21:2x for equities).  Bucketing 1-min bars on it folded a whole
        day of ticks into ~45 distinct LTT-minutes per instrument and stamped
        wrong closes.  Arrival time is the correct clock for building bars from
        a live tick stream and matches the exchange minute to sub-second
        latency.  ``ltt_epoch`` is retained only for optional diagnostics.
        """
        key = str(security_id_int)
        builder = self._builders.get(key)
        if builder is None:
            log.debug("DhanLiveFeedV2: no builder for security_id=%d", security_id_int)
            return

        if ltp is None or ltp <= 0:
            # Zero/negative price = off-hours snapshot or bad print; never a bar.
            return

        ts = self._arrival_now()
        tick = {"ltp": ltp, "ltq": 1, "ts": ts}
        self._tick_count += 1
        try:
            builder.process(tick)
        except Exception:
            log.exception("_BarBuilder.process raised for sec=%s", key)

    # ---------------------------------------------------- property (for tests)

    @property
    def reconnect_count(self) -> int:
        """Total reconnect attempts since start()."""
        return self._reconnect_count

    @property
    def tick_count(self) -> int:
        """Total ticks processed since start()."""
        return self._tick_count
