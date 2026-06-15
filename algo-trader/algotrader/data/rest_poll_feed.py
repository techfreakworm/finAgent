"""REST-polling live bar source — reliable fallback for WebSocket feed failures.

Polls Dhan /charts/intraday at configurable intervals (default every 60 s,
aligned so each poll fires ~5 s after the minute boundary and the just-closed
1-min bar is available).  Uses HistoryFetcher for all network I/O (proven path:
25 M bars fetched flawlessly; token auto-refresh on 401 already handled there).

Deduplicates via per-instrument high-water marks; emits bars in global
chronological order across instruments.

Public interface MATCHES LiveBarFeed exactly so _BarRouter and the runner need
no changes:
  RestPollingBarSource(subscriptions, on_bar, access_token=None, ...)
  .start()
  .stop()
  .subscribe_dynamic(subs)

on_bar callback receives a BarEvent(bar, is_catchup=False) — same as
LiveBarFeed.

Design: PAPER/DATA ONLY.  No order-placement imports or calls.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from algotrader.core import Bar, Instrument, IST, Segment
from algotrader.data.live_feed import BarEvent
from algotrader.data.history import FetchSpec, HistoryFetcher

log = logging.getLogger(__name__)

# ----------------------------------------------------------------- constants

SESSION_START   = time(9, 15, 0)   # first valid bar ts_open
SESSION_END     = time(15, 29, 0)  # last valid bar ts_open
_SESSION_CLOSE  = time(15, 30, 0)  # loop exits after this
_NO_BAR_ALERT_T = time(9, 35, 0)  # trigger alert if zero bars by here

# Probe-align seconds after each minute boundary for the default production
# poll schedule: fire at HH:MM:05 so the just-closed HH:MM-1 bar is ready.
_POLL_ALIGN_SECS = 5

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


# ----------------------------------------------------------------- helpers

def _ist_now() -> datetime:
    return datetime.now(IST)


def _in_session(t: time) -> bool:
    return SESSION_START <= t <= _SESSION_CLOSE


def _instr_type_for(seg: str, instr: Instrument) -> str:
    """Dhan instrument-type for the fetch payload, derived from the SUBSCRIPTION
    SEGMENT (authoritative for REST), not the Instrument object. The strategy-
    facing Instrument can differ from the fetch target — e.g. the NIFTY-FUT
    signal is sourced from the NIFTY index on IDX_I, and option legs are OPTIDX
    on NSE_FNO. Mismatching these silently returns no data."""
    if seg == "IDX_I":
        return "INDEX"
    if seg == "NSE_EQ":
        return "EQUITY"
    if seg == "NSE_FNO":
        s = instr.symbol.upper()
        return "OPTIDX" if s.endswith("CE") or s.endswith("PE") else "FUTIDX"
    # Fallback: infer from the instrument
    if instr.segment is Segment.IDX:
        return "INDEX"
    return "FUTIDX" if instr.is_derivative else "EQUITY"


# ================================================================= main class

class RestPollingBarSource:
    """Polls Dhan REST API for 1-min bars; same interface as LiveBarFeed.

    Parameters
    ----------
    subscriptions:
        List of ``(security_id_str, exchange_segment_str, Instrument)`` triples.
        Same format as LiveBarFeed.__init__.
    on_bar:
        Callback ``(BarEvent) -> None`` invoked for each completed bar.
        Called from the background poll thread; must be thread-safe and fast.
    access_token:
        Optional initial access token.  HistoryFetcher will call
        ``get_valid_token()`` on init and auto-refresh on 401.
    interval:
        Bar interval to request (default "1" for 1-min bars).
    poll_seconds:
        How many seconds to wait between successive poll iterations
        (default 60 — one poll per minute).  Pass 0 for test mode
        (effectively no sleep between polls; now_fn advances the clock).
    now_fn:
        Callable ``() -> datetime`` returning tz-aware IST datetime.
        Defaults to ``datetime.now(IST)``.  Injectable for deterministic tests.
    """

    def __init__(
        self,
        subscriptions: list[tuple[str, str, Instrument]],
        on_bar: Callable[[BarEvent], None],
        access_token: str | None = None,
        interval: str = "1",
        poll_seconds: int = 60,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._subs: list[tuple[str, str, Instrument]] = list(subscriptions)
        self._on_bar = on_bar
        self._access_token = access_token
        self._interval = interval
        self._poll_seconds = poll_seconds
        self._now_fn: Callable[[], datetime] = now_fn or _ist_now

        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

        # Per-instrument high-water mark: str(security_id) → last emitted ts_open
        self._hwm: dict[str, datetime | None] = {
            str(sec_id): None for sec_id, _, _ in subscriptions
        }

        self._total_emitted: int = 0        # guarded by _lock
        self._alert_checked: bool = False   # set once at/after 09:35

        # Lazy-initialised in the background thread
        self._fetcher: HistoryFetcher | None = None
        self._stamp_offset: int = 0

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Spawn background poll thread."""
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="RestPollFeed"
        )
        self._thread.start()
        log.info("RestPollingBarSource started (poll_seconds=%d)", self._poll_seconds)

    def stop(self) -> None:
        """Signal stop and wait for the poll thread to exit (timeout 15 s)."""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
        with self._lock:
            total = self._total_emitted
        log.info("RestPollingBarSource stopped (total_emitted=%d)", total)

    def subscribe_dynamic(
        self, subscriptions: list[tuple[str, str, Instrument]]
    ) -> None:
        """Thread-safe addition of new instruments.

        New entries are picked up on the next poll iteration.  Idempotent:
        already-subscribed security_ids are silently ignored.

        Parameters
        ----------
        subscriptions:
            Same ``(security_id_str, exchange_segment_str, Instrument)`` format
            as ``__init__``.
        """
        with self._lock:
            for sec_id, seg, instr in subscriptions:
                key = str(sec_id)
                if key not in self._hwm:
                    self._subs.append((sec_id, seg, instr))
                    self._hwm[key] = None
                    log.info(
                        "subscribe_dynamic: queued %s (security_id=%s)",
                        instr.symbol, key,
                    )

    # ----------------------------------------------------------------- private

    def _run(self) -> None:
        """Background thread: initialise, then poll until session end or stop."""
        # Lazy init (network call — don't block __init__)
        self._fetcher = HistoryFetcher()
        self._detect_stamp_offset()

        while not self._stop_evt.is_set():
            now = self._now_fn()
            ist = now.astimezone(IST)
            t = ist.time()
            session_date = ist.date()

            if t < SESSION_START:
                # Pre-market wait
                self._stop_evt.wait(timeout=5)
                continue

            if t > _SESSION_CLOSE:
                log.info("RestPollingBarSource: session ended (%s > 15:30)", t)
                break

            # ---- poll
            self._poll_once(session_date)

            # ---- no-bars alert (at 09:35, once per session)
            if not self._alert_checked and t >= _NO_BAR_ALERT_T:
                self._check_no_bars_alert(session_date)

            # ---- sleep until next aligned fire
            if self._poll_seconds > 0:
                next_fire = self._next_aligned_fire(ist)
                sleep_secs = max(
                    0.0, (next_fire - self._now_fn()).total_seconds()
                )
                if sleep_secs > 0:
                    self._stop_evt.wait(timeout=sleep_secs)
            else:
                # test mode: minimal yield
                self._stop_evt.wait(timeout=0.005)

    def _next_aligned_fire(self, now_ist: datetime) -> datetime:
        """Return next poll fire time: minute-boundary + poll_seconds + align offset.

        Aligns so that the poll fires _POLL_ALIGN_SECS after each
        ``poll_seconds``-sized boundary from the start of the minute, ensuring
        the just-closed 1-min bar (available ~2-3 s after the minute) is ready.

        For poll_seconds=60:  fire at HH:MM:05 (5 s after each minute tick).
        """
        minute_floor = now_ist.replace(second=0, microsecond=0)
        # How many full poll_seconds intervals have elapsed since minute floor?
        elapsed = (now_ist - minute_floor).total_seconds()
        n = int(elapsed // self._poll_seconds)
        next_boundary = minute_floor + timedelta(seconds=(n + 1) * self._poll_seconds)
        fire = next_boundary + timedelta(seconds=_POLL_ALIGN_SECS)
        if fire <= now_ist:
            fire += timedelta(seconds=self._poll_seconds)
        return fire

    # --------------------------------------------------------------- poll logic

    def _poll_once(self, session_date: date) -> None:
        """Fetch bars for all subscribed instruments; emit new ones in ts order."""
        with self._lock:
            subs_snapshot = list(self._subs)

        # Collect (ts_open, security_id_key, Bar) for all new bars
        new_bars: list[tuple[datetime, str, Bar]] = []

        for sec_id, seg, instr in subs_snapshot:
            key = str(sec_id)
            try:
                bars = self._fetch_bars(sec_id, seg, instr, session_date)
            except Exception:
                log.exception("poll: fetch error for %s (%s)", instr.symbol, key)
                continue

            with self._lock:
                hwm = self._hwm.get(key)

            for bar in bars:
                if hwm is None or bar.ts_open > hwm:
                    new_bars.append((bar.ts_open, key, bar))

        if not new_bars:
            log.debug("poll: no new bars for %s", session_date)
            return

        # Global chronological sort across instruments
        new_bars.sort(key=lambda x: x[0])

        for ts, key, bar in new_bars:
            # Re-check under lock (subscribe_dynamic race guard)
            with self._lock:
                hwm = self._hwm.get(key)
                already_emitted = hwm is not None and ts <= hwm
            if already_emitted:
                continue
            try:
                self._on_bar(BarEvent(bar=bar, is_catchup=False))
                with self._lock:
                    self._hwm[key] = ts
                    self._total_emitted += 1
            except Exception:
                log.exception(
                    "poll: on_bar raised for %s @ %s", bar.instrument.symbol, ts
                )

        log.debug("poll: emitted %d new bars (session=%s)", len(new_bars), session_date)

    # --------------------------------------------------------------- fetch

    def _fetch_bars(
        self,
        sec_id: str,
        seg: str,
        instr: Instrument,
        session_date: date,
    ) -> list[Bar]:
        """Fetch and normalize 1-min bars for one instrument for session_date."""
        assert self._fetcher is not None
        spec = FetchSpec(
            symbol=instr.symbol,
            security_id=str(sec_id),
            exchange_segment=seg,
            instrument=_instr_type_for(seg, instr),
        )
        df = self._fetcher.fetch_window(spec, session_date, session_date)
        df = self._fetcher.normalize(df, self._stamp_offset)

        if df.empty:
            return []

        bars: list[Bar] = []
        oi_col = "oi" in df.columns

        for row in df.itertuples(index=False):
            ts = row.ts
            # Ensure tz-aware IST datetime
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            else:
                ts = ts.astimezone(IST)

            oi: int | None = None
            if oi_col:
                raw_oi = row.oi
                if raw_oi is not None and raw_oi == raw_oi:  # NaN check
                    try:
                        oi = int(raw_oi)
                    except (ValueError, TypeError):
                        oi = None

            bars.append(Bar(
                instrument=instr,
                ts_open=ts,
                interval_min=1,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=int(row.volume),
                oi=oi,
                complete=True,
            ))

        return bars

    # ------------------------------------------------------ stamp detection

    def _detect_stamp_offset(self) -> None:
        """Detect Dhan timestamp mode (bar-start vs bar-end stamping).

        Probes using the most recent locally-cached NIFTY data.
        Falls back to offset=0 on any error (bar-start stamped — safe default).
        """
        assert self._fetcher is not None
        from algotrader.data.history import CACHE

        nifty_cache = CACHE / "NIFTY" / "1m"
        probe_day: date | None = None

        if nifty_cache.exists():
            import pandas as pd
            parquets = sorted(nifty_cache.glob("*.parquet"))
            if parquets:
                try:
                    df = pd.read_parquet(parquets[-1])
                    df["ts"] = pd.to_datetime(df["ts"])
                    if not df.empty:
                        probe_day = df["ts"].max().date()
                except Exception:
                    log.debug("stamp probe: could not read cache", exc_info=True)

        if probe_day is None:
            # Dhan end-stamps intraday bars, so bar-start needs offset=1. Use that
            # known default rather than a risky 0 when no cache probe day exists.
            self._stamp_offset = 1
            log.warning(
                "stamp detection: no cache probe day; defaulting to offset=1 (Dhan end-stamp)"
            )
            return

        spec = FetchSpec(
            symbol="NIFTY",
            security_id="13",
            exchange_segment="IDX_I",
            instrument="INDEX",
        )
        try:
            self._stamp_offset = self._fetcher.detect_stamp_mode(spec, probe_day)
            log.info(
                "stamp offset=%d min detected (probe_day=%s)", self._stamp_offset, probe_day
            )
        except Exception:
            log.warning(
                "stamp detection failed for probe_day=%s; defaulting to offset=1 (Dhan end-stamp)",
                probe_day, exc_info=True,
            )
            self._stamp_offset = 1

    # --------------------------------------------------------- no-bars alert

    def _check_no_bars_alert(self, session_date: date) -> None:
        """Fire a CRITICAL alert if no bars have been emitted by 09:35."""
        self._alert_checked = True
        with self._lock:
            total = self._total_emitted

        if total > 0:
            log.info("no-bars check OK: %d bar(s) emitted by 09:35", total)
            return

        msg = (
            f"NO BARS RECEIVED BY 09:35 IST on {session_date} — "
            "REST POLL FEED MAY BE DOWN. "
            "Check Dhan API status, access token, and network connectivity."
        )
        log.critical(msg)

        try:
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            alert_path = REPORTS_DIR / f"feed_alert_{session_date}.txt"
            alert_path.write_text(msg + "\n")
            log.critical("Feed-down alert written to %s", alert_path)
        except Exception:
            log.exception("could not write feed alert file")
