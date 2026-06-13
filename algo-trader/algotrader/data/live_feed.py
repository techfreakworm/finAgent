"""Live 1-min bar feed for paper trading (ARCHITECTURE §2, §6).

Produces core.Bar objects (1-min, complete=True) from two sources:
  1. LiveBarFeed  — dhanhq marketfeed WebSocket (Quote packets) → bar builder
  2. ReplayDriver — replays stored parquet bars at configurable speed (tests/dry-runs)

Design invariants:
  - All datetimes tz-aware Asia/Kolkata; naive datetimes rejected at boundaries.
  - Session filter: only bars with ts_open in [09:15, 15:29] IST are emitted.
  - Consumers receive a BarEvent(bar, is_catchup) — cannot add fields to frozen Bar.
  - Token-expiry reconnect (code 807): fresh token obtained via token_manager BEFORE
    the SDK's reconnect path re-uses the dead token (documented SDK bug).
  - Watchdog: no bar emitted for >90s during market hours → REST catch-up fetch.
  - Gap recovery: at most MAX_GAP_BARS catch-up bars; beyond that a warning is logged
    and data-quality is flagged.

Paper-only: this module never imports or calls any order-placement method.
"""
from __future__ import annotations

import logging
import struct
import threading
import time as _time_mod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from algotrader.core import Bar, Instrument, IST, Segment, require_ist

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------- session
SESSION_START = time(9, 15, 0)    # first bar ts_open
SESSION_END   = time(15, 29, 0)   # last bar ts_open
GRACE_SECS    = 5                 # seconds after last tick to close bar
WATCHDOG_SECS = 90                # seconds without a bar before gap-fetch
MAX_GAP_BARS  = 15                # max catch-up bars; beyond → warn + flag
API_BASE      = "https://api.dhan.co/v2"
CACHE         = Path(__file__).resolve().parent.parent.parent / "data" / "cache"

# Exchange segment string → dhanhq integer code for subscription tuples
_SEG_TO_INT: dict[str, int] = {
    "IDX_I":    0,
    "NSE_EQ":   1,
    "NSE_FNO":  2,
}


def _in_market_hours(ts: datetime) -> bool:
    t = ts.astimezone(IST).time()
    return SESSION_START <= t <= time(15, 30)


def _minute_floor(ts: datetime) -> datetime:
    """Return the minute-floored tz-aware IST datetime."""
    ist = ts.astimezone(IST)
    return ist.replace(second=0, microsecond=0)


def _seg_str(segment: Segment) -> str:
    return segment.value  # already matches "NSE_EQ" | "IDX_I" | "NSE_FNO"


def _instr_type(instr: Instrument) -> str:
    if instr.segment is Segment.IDX:
        return "INDEX"
    if instr.is_derivative:
        return "FUTIDX"
    return "EQUITY"


# -------------------------------------------------------------------- BarEvent

@dataclass(frozen=True)
class BarEvent:
    """Wrapper around a completed 1-min Bar.

    ``is_catchup=True`` for bars fetched via REST gap-fill rather than built
    from live ticks.  Consumers that track running P&L should process these in
    order but may note that fills on catch-up bars are not available.
    """
    bar: Bar
    is_catchup: bool = False


# ----------------------------------------------------------------- _BarBuilder

class _BarBuilder:
    """Thread-safe 1-min bar accumulator for a single instrument.

    Accepts normalised tick dicts: ``{"ltp": float, "ltq": int, "ts": datetime}``.
    Emits a ``BarEvent`` via ``on_bar`` when the minute ends (next tick in a
    later minute) or when the grace timer fires (no new tick for *grace_sec*).

    Out-of-order ticks (ts < current bar start) are silently dropped.
    Bars outside the session window are not emitted.
    """

    def __init__(
        self,
        instrument: Instrument,
        on_bar: Callable[[BarEvent], None],
        grace_sec: float = GRACE_SECS,
    ) -> None:
        self._instr   = instrument
        self._on_bar  = on_bar
        self._grace   = grace_sec
        self._lock    = threading.Lock()

        # Current bar state
        self._bucket: datetime | None = None   # minute-floor IST
        self._open:   float = 0.0
        self._high:   float = 0.0
        self._low:    float = 0.0
        self._close:  float = 0.0
        self._volume: int   = 0

        # Last bar emitted (for gap detection)
        self._last_bar_ts: datetime | None = None

        # Grace timer
        self._grace_timer: threading.Timer | None = None

    # ---------------------------------------------------------------- public

    @property
    def last_bar_ts(self) -> datetime | None:
        with self._lock:
            return self._last_bar_ts

    def process(self, tick: dict) -> None:
        """Accept a normalised tick dict and update bar state.

        tick = {"ltp": float, "ltq": int, "ts": datetime (tz-aware)}
        """
        ts: datetime = require_ist(tick["ts"])
        ltp: float   = float(tick["ltp"])
        ltq: int     = int(tick.get("ltq", 0))
        bucket       = _minute_floor(ts)
        to_emit: tuple | None = None

        with self._lock:
            self._cancel_grace_timer_locked()

            if self._bucket is None:
                # Very first tick
                if self._session_ok(bucket):
                    self._start_bar_locked(bucket, ltp, ltq)
            elif bucket < self._bucket:
                # Out-of-order tick → drop
                log.debug("dropping out-of-order tick ts=%s bucket=%s < current=%s",
                          ts, bucket, self._bucket)
                return
            elif bucket == self._bucket:
                # Same minute — update bar
                self._update_bar_locked(ltp, ltq)
            else:
                # New minute → close current bar, start new
                to_emit = self._snapshot_locked()
                if self._session_ok(bucket):
                    self._start_bar_locked(bucket, ltp, ltq)
                else:
                    self._reset_state_locked()

            # Restart grace timer if we have an open bar
            if self._bucket is not None:
                self._start_grace_timer_locked()

        if to_emit is not None:
            self._emit(*to_emit)

    def flush(self) -> None:
        """Force-emit any open bar (e.g. session close, stop signal)."""
        to_emit: tuple | None = None
        with self._lock:
            self._cancel_grace_timer_locked()
            if self._bucket is not None:
                to_emit = self._snapshot_locked()
                self._reset_state_locked()
        if to_emit is not None:
            self._emit(*to_emit)

    def stop(self) -> None:
        """Cancel grace timer cleanly (called on feed shutdown)."""
        with self._lock:
            self._cancel_grace_timer_locked()

    # --------------------------------------------------------------- private

    def _session_ok(self, bucket: datetime) -> bool:
        t = bucket.time()
        return SESSION_START <= t <= SESSION_END

    def _start_bar_locked(self, bucket: datetime, ltp: float, ltq: int) -> None:
        self._bucket = bucket
        self._open   = ltp
        self._high   = ltp
        self._low    = ltp
        self._close  = ltp
        self._volume = ltq

    def _update_bar_locked(self, ltp: float, ltq: int) -> None:
        if ltp > self._high:
            self._high = ltp
        if ltp < self._low:
            self._low = ltp
        self._close  = ltp
        self._volume += ltq

    def _snapshot_locked(self) -> tuple:
        """Return (bucket, open, high, low, close, volume) and reset state."""
        snap = (self._bucket, self._open, self._high,
                self._low,   self._close, self._volume)
        self._reset_state_locked()
        return snap

    def _reset_state_locked(self) -> None:
        self._bucket = None
        self._open = self._high = self._low = self._close = self._volume = 0  # type: ignore[assignment]

    def _cancel_grace_timer_locked(self) -> None:
        if self._grace_timer is not None:
            self._grace_timer.cancel()
            self._grace_timer = None

    def _start_grace_timer_locked(self) -> None:
        t = threading.Timer(self._grace, self._on_grace_fire)
        t.daemon = True
        t.start()
        self._grace_timer = t

    def _on_grace_fire(self) -> None:
        """Called by grace timer thread — emit open bar."""
        to_emit: tuple | None = None
        with self._lock:
            self._grace_timer = None   # timer has fired, clear reference
            if self._bucket is not None:
                to_emit = self._snapshot_locked()
                self._reset_state_locked()
        if to_emit is not None:
            self._emit(*to_emit)

    def _emit(self, bucket: datetime, o: float, h: float,
              l: float, c: float, vol: int, is_catchup: bool = False) -> None:
        bar = Bar(
            instrument=self._instr,
            ts_open=bucket,
            interval_min=1,
            open=o, high=h, low=l, close=c,
            volume=vol,
            complete=True,
        )
        with self._lock:
            self._last_bar_ts = bucket
        try:
            self._on_bar(BarEvent(bar=bar, is_catchup=is_catchup))
        except Exception:
            log.exception("on_bar callback raised")


# ------------------------------------------------------------------ gap fetch

def _fetch_gap_bars(
    security_id: str,
    exchange_segment: str,
    instrument: Instrument,
    from_dt: datetime,
    to_dt: datetime,
    token: str,
) -> list[Bar]:
    """REST fetch of 1-min bars between from_dt and to_dt (both inclusive).

    Returns at most MAX_GAP_BARS bars; beyond that logs a warning.
    Secrets are never logged.
    """
    if from_dt > to_dt:
        return []

    # Format: "YYYY-MM-DD HH:MM:SS"
    from_s = from_dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
    to_s   = to_dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "securityId":       security_id,
        "exchangeSegment":  exchange_segment,
        "instrument":       _instr_type(instrument),
        "interval":         "1",
        "fromDate":         from_s,
        "toDate":           to_s,
    }
    try:
        r = requests.post(
            f"{API_BASE}/charts/intraday",
            headers={"access-token": token, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        log.warning("gap fetch request error: %s", type(e).__name__)
        return []

    if r.status_code != 200:
        log.warning("gap fetch HTTP %s (secrets not logged)", r.status_code)
        return []

    try:
        data = r.json()
    except ValueError:
        log.warning("gap fetch: non-JSON response")
        return []

    if not data.get("timestamp"):
        return []

    rows = list(zip(
        data["timestamp"], data["open"], data["high"],
        data["low"], data["close"], data["volume"],
    ))
    oi_list = data.get("open_interest") or []

    if len(rows) > MAX_GAP_BARS:
        log.warning(
            "gap fetch: %d bars for %s (> MAX_GAP_BARS=%d); "
            "emitting all but flagging data-quality",
            len(rows), instrument.symbol, MAX_GAP_BARS,
        )

    bars: list[Bar] = []
    for i, (ts_epoch, o, h, l, c, vol) in enumerate(rows):
        ts_ist = datetime.fromtimestamp(ts_epoch, tz=IST)
        t = ts_ist.time()
        if not (SESSION_START <= t <= SESSION_END):
            continue
        oi_val = int(oi_list[i]) if i < len(oi_list) else None
        bars.append(Bar(
            instrument=instrument,
            ts_open=ts_ist,
            interval_min=1,
            open=float(o), high=float(h), low=float(l), close=float(c),
            volume=int(vol),
            oi=oi_val,
            complete=True,
        ))

    bars.sort(key=lambda b: b.ts_open)
    return bars


# ------------------------------------------------- TokenRefreshFeed (SDK wrap)

try:
    from dhanhq.marketfeed import MarketFeed as _MarketFeed

    class _TokenRefreshFeed(_MarketFeed):
        """MarketFeed subclass that refreshes the access token on code-807 disconnect.

        The stock SDK embeds `self.access_token` in the WSS URL at connect() time
        and never refreshes it on reconnect — verified in marketfeed.py source.
        We override `server_disconnection` to update `self.access_token` with a
        fresh token BEFORE the reconnect loop calls `connect()` again.
        """

        def __init__(self, *args, token_refresh_fn: Callable[[], str | None], **kwargs):
            super().__init__(*args, **kwargs)
            self._token_refresh_fn = token_refresh_fn
            self.last_disconnect_code: int | None = None

        def server_disconnection(self, data: bytes) -> dict:  # type: ignore[override]
            try:
                pkt = struct.unpack('<BHBIH', data[0:10])
                code = pkt[4]
            except struct.error:
                code = 0
            self.last_disconnect_code = code
            if code == 807:
                log.warning(
                    "WS disconnect 807 (token expired) — refreshing token before reconnect"
                )
                new_token = self._token_refresh_fn()
                if new_token:
                    # Update the token that connect() will embed in the URL
                    self.access_token = new_token
                else:
                    log.error("token refresh returned None on 807; reconnect will likely fail")
            # Delegate to parent (which calls self.on_close)
            return super().server_disconnection(data)  # type: ignore[return-value]

    _DHANHQ_AVAILABLE = True

except ImportError:
    _TokenRefreshFeed = None  # type: ignore[assignment, misc]
    _DHANHQ_AVAILABLE = False


# -----------------------------------------------------------------  LiveBarFeed

class LiveBarFeed:
    """Connects to the dhanhq marketfeed WebSocket and produces 1-min BarEvents.

    Usage::

        feed = LiveBarFeed(
            subscriptions=[("1234", "NSE_EQ", reliance_instr)],
            on_bar=lambda ev: print(ev),
        )
        feed.start()
        # … run paper session …
        feed.stop()

    The *on_bar* callback is called from a background thread; it must be
    thread-safe and should return quickly (do not block).

    Parameters
    ----------
    subscriptions:
        List of (security_id_str, exchange_segment_str, Instrument) triples.
    on_bar:
        Callback(BarEvent) invoked for each completed bar.
    client_id:
        Dhan clientId (read from env by default).
    access_token:
        Initial access token (read from env/token_manager by default).
    grace_sec:
        Seconds after last tick before an open bar is force-closed (default 5).
    watchdog_sec:
        Seconds with no bar emitted before gap-fetch triggered (default 90).
    """

    def __init__(
        self,
        subscriptions: list[tuple[str, str, Instrument]],
        on_bar: Callable[[BarEvent], None],
        client_id: str = "",
        access_token: str = "",
        grace_sec: float = GRACE_SECS,
        watchdog_sec: float = WATCHDOG_SECS,
    ) -> None:
        if not _DHANHQ_AVAILABLE:
            raise RuntimeError("dhanhq package not installed")

        self._subs    = list(subscriptions)  # copy so subscribe_dynamic mutations stay local
        self._on_bar  = on_bar
        self._grace   = grace_sec
        self._wdog_sec = watchdog_sec

        # Credentials (resolved lazily if empty)
        self._client_id    = client_id
        self._access_token = access_token

        # Per-instrument bar builders keyed by security_id string
        self._builders: dict[str, _BarBuilder] = {}
        for sec_id, seg, instr in subscriptions:
            self._builders[str(sec_id)] = _BarBuilder(
                instrument=instr,
                on_bar=self._handle_bar,
                grace_sec=grace_sec,
            )

        # REST gap-fill context (per instrument)
        self._instr_by_sec: dict[str, tuple[str, str, Instrument]] = {
            str(s): (s, seg, instr) for s, seg, instr in subscriptions
        }

        self._feed: "_TokenRefreshFeed | None" = None
        self._watchdog_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._running = False

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Connect to marketfeed and start emitting bars."""
        self._running = True
        self._ensure_token()
        self._build_feed()
        self._reset_watchdog()
        self._feed.start()  # type: ignore[union-attr]

    def stop(self) -> None:
        """Disconnect and clean up all timers."""
        self._running = False
        self._cancel_watchdog()
        for b in self._builders.values():
            b.stop()
        if self._feed is not None:
            try:
                self._feed.close_connection()
            except Exception:
                pass

    def subscribe_dynamic(
        self, subscriptions: list[tuple[str, str, Instrument]]
    ) -> None:
        """Add instruments to a running (or not-yet-started) feed.

        The SDK's ``MarketFeed.subscribe_symbols()`` (marketfeed.py lines 544–579)
        supports mid-session additions: if the WebSocket is open it sends a
        JSON subscription message immediately via ``_run_coroutine``; if the
        socket is closed it only updates ``self.instruments`` so the new entries
        are included when the connection is (re-)established.  We wrap that
        here and also keep our own bar-builder state in sync.

        Thread-safety: ``self._lock`` guards mutations to ``_builders``,
        ``_subs``, and ``_instr_by_sec``.  The SDK's ``subscribe_symbols``
        call happens *outside* the lock to avoid holding it across a
        potentially blocking coroutine dispatch.

        Parameters
        ----------
        subscriptions:
            List of ``(security_id_str, exchange_segment_str, Instrument)``
            triples — the same format as the ``__init__`` parameter.
        """
        # Identify subscriptions that are genuinely new
        new_subs: list[tuple[str, str, Instrument]] = []
        with self._lock:
            for sec_id, seg, instr in subscriptions:
                key = str(sec_id)
                if key not in self._builders:
                    builder = _BarBuilder(
                        instrument=instr,
                        on_bar=self._handle_bar,
                        grace_sec=self._grace,
                    )
                    self._builders[key] = builder
                    self._instr_by_sec[key] = (sec_id, seg, instr)
                    self._subs.append((sec_id, seg, instr))
                    new_subs.append((sec_id, seg, instr))

        if not new_subs:
            log.debug("subscribe_dynamic: all instruments already subscribed")
            return

        log.info(
            "subscribe_dynamic: adding %d new instrument(s): %s",
            len(new_subs),
            [instr.symbol for _, _, instr in new_subs],
        )

        if not self._running or self._feed is None:
            # Feed not started yet — instruments are queued in _subs and will
            # be picked up by _build_feed() when start() is called.
            log.debug(
                "subscribe_dynamic: feed not running; %d instrument(s) queued",
                len(new_subs),
            )
            return

        # Feed is running — send subscription to the open WebSocket.
        # SDK subscribe_symbols() takes the same (seg_int, sec_id, mode) tuples
        # that __init__ uses.  mode 17 = Quote (same as the initial subs).
        sdk_tuples = [
            (_SEG_TO_INT.get(seg, 1), sec_id, 17)
            for sec_id, seg, _ in new_subs
        ]
        try:
            self._feed.subscribe_symbols(sdk_tuples)  # type: ignore[union-attr]
        except Exception:
            log.exception(
                "subscribe_dynamic: SDK subscribe_symbols raised; "
                "instruments are in _builders but may not receive ticks until reconnect"
            )

    def trigger_gap_recovery(self, security_id: str | None = None) -> None:
        """Manually trigger a REST gap-fill for one or all instruments.

        Called internally by the watchdog; can also be called externally after
        a reconnect or startup reconciliation.
        """
        now_ist = datetime.now(IST)
        if not _in_market_hours(now_ist):
            return
        targets = ([security_id] if security_id else list(self._instr_by_sec.keys()))
        for sec in targets:
            self._fill_gap_for(sec, now_ist)

    # ----------------------------------------------------------------- private

    def _ensure_token(self) -> None:
        if not self._client_id or not self._access_token:
            from algotrader.data.token_manager import get_valid_token, _load_env
            env = _load_env()
            self._client_id    = self._client_id    or env.get("DHAN_CLIENT_ID", "")
            self._access_token = self._access_token or (get_valid_token() or "")

    def _refresh_token(self) -> str | None:
        from algotrader.data.token_manager import get_valid_token
        tok = get_valid_token()
        if tok:
            self._access_token = tok
        return tok

    def _build_feed(self) -> None:
        from dhanhq.dhan_context import DhanContext

        ctx = DhanContext(self._client_id, self._access_token)
        instruments = [
            (_SEG_TO_INT.get(seg, 1), sec_id, 17)   # 17 = Quote
            for sec_id, seg, _ in self._subs
        ]
        self._feed = _TokenRefreshFeed(  # type: ignore[operator]
            dhan_context=ctx,
            instruments=instruments,
            version="v2",
            on_message=self._on_message,
            on_close=self._on_close,
            on_error=self._on_error,
            token_refresh_fn=self._refresh_token,
        )

    def _on_message(self, feed: object, data: dict | None) -> None:
        if data is None:
            return
        if data.get("type") != "Quote Data":
            return
        sec_id = str(data.get("security_id", ""))
        builder = self._builders.get(sec_id)
        if builder is None:
            return

        # Parse LTT (UTC "HH:MM:SS") → IST datetime using today's session date
        ltt: str = data.get("LTT", "")
        ts = self._parse_ltt(ltt)
        if ts is None:
            return

        try:
            ltp = float(data["LTP"])
            ltq = int(data.get("LTQ", 0))
        except (ValueError, KeyError):
            return

        builder.process({"ltp": ltp, "ltq": ltq, "ts": ts})
        self._reset_watchdog()

    def _on_close(self, feed: object) -> None:
        log.info("WS closed (reconnect loop will handle)")
        # Gap-fill on reconnect
        if self._running:
            self.trigger_gap_recovery()

    def _on_error(self, feed: object, err: Exception) -> None:
        log.warning("WS error: %s", type(err).__name__)

    def _parse_ltt(self, ltt: str) -> datetime | None:
        """Parse UTC HH:MM:SS string → tz-aware IST datetime on session date."""
        try:
            h, m, s = map(int, ltt.split(":"))
        except (ValueError, AttributeError):
            return None
        today = datetime.now(IST).date()
        from datetime import timezone
        utc_dt = datetime(today.year, today.month, today.day, h, m, s,
                          tzinfo=timezone.utc)
        return utc_dt.astimezone(IST)

    def _handle_bar(self, event: BarEvent) -> None:
        self._reset_watchdog()
        try:
            self._on_bar(event)
        except Exception:
            log.exception("consumer on_bar callback raised")

    # ---------------------------------------------------------------- watchdog

    def _reset_watchdog(self) -> None:
        with self._lock:
            if self._watchdog_timer is not None:
                self._watchdog_timer.cancel()
            if not self._running:
                return
            t = threading.Timer(self._wdog_sec, self._on_watchdog_fire)
            t.daemon = True
            t.start()
            self._watchdog_timer = t

    def _cancel_watchdog(self) -> None:
        with self._lock:
            if self._watchdog_timer is not None:
                self._watchdog_timer.cancel()
                self._watchdog_timer = None

    def _on_watchdog_fire(self) -> None:
        now = datetime.now(IST)
        if not _in_market_hours(now):
            return
        log.warning("watchdog: no bar for >%ds during market hours; triggering gap fill",
                    self._wdog_sec)
        self.trigger_gap_recovery()
        self._reset_watchdog()

    # ----------------------------------------------------------- gap fill impl

    def _fill_gap_for(self, security_id: str, now_ist: datetime) -> None:
        sec_id, seg, instr = self._instr_by_sec.get(security_id, (None, None, None))
        if instr is None:
            return
        builder = self._builders.get(security_id)
        if builder is None:
            return

        last_ts = builder.last_bar_ts
        if last_ts is None:
            # No bar yet today — fetch from session start
            session_date = now_ist.date()
            from_dt = datetime(session_date.year, session_date.month,
                               session_date.day, 9, 15, 0, tzinfo=IST)
        else:
            from_dt = last_ts + timedelta(minutes=1)

        # Last complete bar is the minute that ended before "now"
        to_dt = _minute_floor(now_ist) - timedelta(minutes=1)
        if to_dt < from_dt:
            return

        bars = _fetch_gap_bars(
            security_id=str(sec_id),
            exchange_segment=str(seg),
            instrument=instr,
            from_dt=from_dt,
            to_dt=to_dt,
            token=self._access_token,
        )
        log.info("gap fill: emitting %d catch-up bars for %s", len(bars), instr.symbol)
        for bar in bars:
            event = BarEvent(bar=bar, is_catchup=True)
            with self._lock:
                # Update builder's last_bar_ts so watchdog sees progress
                builder._last_bar_ts = bar.ts_open  # type: ignore[assignment]
            try:
                self._on_bar(event)
            except Exception:
                log.exception("consumer on_bar callback raised (catchup)")


# ---------------------------------------------------------------- ReplayDriver

class ReplayDriver:
    """Replays stored 1-min parquet bars for a given session date.

    Implements the same on_bar callback interface as LiveBarFeed so strategies
    and tests can be driven identically without a live connection.

    Parameters
    ----------
    subscriptions:
        List of (security_id_str, exchange_segment_str, Instrument) triples.
    on_bar:
        Callback(BarEvent) called for each bar.
    speed:
        Replay speed multiplier.  0 = instant (no sleep), 1.0 = real-time,
        2.0 = twice real-time (sleep ½ minute between bars), etc.
    cache_dir:
        Root of the parquet store.  Defaults to data/cache/ in project root.
    data_symbol_map:
        Optional override dict {instrument.symbol → cache_dir_name} for
        instruments stored under a different folder (e.g. futures stored as
        "NIFTY" for index bars).
    """

    def __init__(
        self,
        subscriptions: list[tuple[str, str, Instrument]],
        on_bar: Callable[[BarEvent], None],
        speed: float = 0.0,
        cache_dir: Path | None = None,
        data_symbol_map: dict[str, str] | None = None,
    ) -> None:
        self._subs     = subscriptions
        self._on_bar   = on_bar
        self._speed    = speed
        self._cache    = cache_dir or CACHE
        self._sym_map  = data_symbol_map or {}

    def run(self, session_date: date | None = None) -> None:
        """Replay bars.

        If *session_date* is given, only bars from that date are emitted.
        Otherwise all available dates are replayed in order.
        """
        rows = self._load(session_date)
        prev_ts: datetime | None = None
        for row in rows:
            ts: datetime = row["ts"].to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            else:
                ts = ts.astimezone(IST)

            t = ts.time()
            if not (SESSION_START <= t <= SESSION_END):
                continue

            if self._speed > 0 and prev_ts is not None:
                gap_s = (ts - prev_ts).total_seconds()
                _time_mod.sleep(gap_s / self._speed)

            bar = Bar(
                instrument=row["instrument"],
                ts_open=ts,
                interval_min=1,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                oi=int(row["oi"]) if "oi" in row and row["oi"] is not None else None,
                complete=True,
            )
            try:
                self._on_bar(BarEvent(bar=bar, is_catchup=False))
            except Exception:
                log.exception("ReplayDriver on_bar callback raised")
            prev_ts = ts

    def _load(self, session_date: date | None) -> list[dict]:
        """Load and merge bars from all subscribed instruments, sorted by ts."""
        frames: list[pd.DataFrame] = []
        for sec_id, seg, instr in self._subs:
            dir_name = self._sym_map.get(instr.symbol, instr.symbol)
            sym_dir  = self._cache / dir_name / "1m"
            files    = sorted(sym_dir.glob("*.parquet"))
            if not files:
                log.warning("ReplayDriver: no parquet files for %s at %s", instr.symbol, sym_dir)
                continue
            df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
            df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
            if session_date is not None:
                df = df[df["ts"].dt.date == session_date]
            df = df.sort_values("ts").reset_index(drop=True)
            df["instrument"] = instr
            frames.append(df)

        if not frames:
            return []

        combined = pd.concat(frames, ignore_index=True)
        combined = combined.sort_values("ts").reset_index(drop=True)
        return combined.to_dict("records")
