"""Feed manager with automatic REST failover for the paper-trading system.

Starts ``DhanLiveFeedV2`` as primary; monitors bar flow; if no bars arrive
within ``failover_seconds`` of market open (or the WS errors repeatedly) it
transparently starts ``RestPollingBarSource`` and routes its bars to the same
``on_bar`` callback.  Every state transition is logged at WARNING/CRITICAL level.

Design invariants
-----------------
- PAPER/DATA ONLY.  No order-placement imports or calls.
- Same public interface as ``LiveBarFeed`` and ``DhanLiveFeedV2``.
- A silent WS stall CANNOT blind the system for more than ~failover_seconds.
- ``subscribe_dynamic()`` is forwarded to both feeds (WS + REST), keeping both
  in sync so the REST feed can serve option legs if WS never recovers.
- Fail-back (REST → WS) is NOT attempted mid-session; a restart is required.
  This avoids duplicate-bar races and keeps the logic simple.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, time
from typing import Callable
from zoneinfo import ZoneInfo

from algotrader.core import Instrument, IST
from algotrader.data.live_feed import BarEvent

try:
    from algotrader.data.ws_feed_v2 import DhanLiveFeedV2
    _WS_V2_AVAILABLE = True
except ImportError:
    DhanLiveFeedV2 = None  # type: ignore[assignment,misc]
    _WS_V2_AVAILABLE = False

try:
    from algotrader.data.rest_poll_feed import RestPollingBarSource
    _REST_AVAILABLE = True
except ImportError:
    RestPollingBarSource = None  # type: ignore[assignment,misc]
    _REST_AVAILABLE = False

log = logging.getLogger(__name__)

_SESSION_START = time(9, 15, 0)
_SESSION_END   = time(15, 30, 0)

# Max WS errors before triggering REST failover unconditionally
_MAX_WS_ERRORS = 3


def _ist_now() -> datetime:
    return datetime.now(IST)


class FeedManager:
    """Automatic-failover feed: DhanLiveFeedV2 primary + REST fallback.

    Parameters
    ----------
    subscriptions:
        List of ``(security_id_str, exchange_segment_str, Instrument)`` triples.
    on_bar:
        Callback ``(BarEvent) -> None`` invoked for each completed bar.
    client_id / access_token:
        Dhan credentials; passed through to both underlying feeds.
    grace_sec / watchdog_sec:
        Passed to DhanLiveFeedV2.
    failover_seconds:
        Seconds of bar silence during market hours before REST failover fires.
        Default 90 — matches the existing REST-watchdog cadence.
    recv_timeout_sec:
        Passed to DhanLiveFeedV2 (core zombie-socket fix).
    monitor_interval_sec:
        How often the monitor thread checks bar flow (default 10 s).
    now_fn:
        Injectable clock for deterministic tests.
    """

    def __init__(
        self,
        subscriptions: list[tuple[str, str, Instrument]],
        on_bar: Callable[[BarEvent], None],
        client_id: str = "",
        access_token: str = "",
        grace_sec: float = 5.0,
        watchdog_sec: float = 90.0,
        failover_seconds: float = 90.0,
        recv_timeout_sec: float = 15.0,
        monitor_interval_sec: float = 10.0,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._subs      = list(subscriptions)
        self._on_bar_cb = on_bar
        self._client_id = client_id
        self._access_token = access_token
        self._grace     = grace_sec
        self._watchdog  = watchdog_sec
        self._failover_secs = failover_seconds
        self._recv_timeout  = recv_timeout_sec
        self._monitor_interval = monitor_interval_sec
        self._now_fn: Callable[[], datetime] = now_fn or _ist_now

        self._lock = threading.Lock()

        # State
        self._ws_feed   = None
        self._rest_feed = None
        self._rest_active = False  # True once REST failover is live

        # Bar flow tracking
        self._last_bar_time: datetime | None = None  # guarded by _lock
        self._ws_error_count: int = 0                # guarded by _lock

        # Monitor thread
        self._stop_evt     = threading.Event()
        self._monitor_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Start WS feed and the monitor thread."""
        self._stop_evt.clear()

        # Build WS feed
        try:
            if not _WS_V2_AVAILABLE or DhanLiveFeedV2 is None:
                raise ImportError("DhanLiveFeedV2 not available")

            def _ws_on_bar(ev: BarEvent) -> None:
                self._on_bar_from_ws(ev)

            self._ws_feed = DhanLiveFeedV2(
                subscriptions=self._subs,
                on_bar=_ws_on_bar,
                client_id=self._client_id,
                access_token=self._access_token,
                grace_sec=self._grace,
                watchdog_sec=self._watchdog,
                recv_timeout_sec=self._recv_timeout,
            )
            self._ws_feed.start()
            log.info(
                "FeedManager: WS feed (DhanLiveFeedV2) started — "
                "failover to REST in %.0fs of bar silence",
                self._failover_secs,
            )
        except Exception:
            log.exception(
                "FeedManager: failed to start DhanLiveFeedV2 — starting REST immediately"
            )
            self._start_rest_failover(reason="ws_init_error")
            return

        # Start monitor
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="FeedManagerMonitor"
        )
        self._monitor_thread.start()

    def stop(self) -> None:
        """Stop all feeds and the monitor thread."""
        self._stop_evt.set()

        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=5)

        if self._ws_feed is not None:
            try:
                self._ws_feed.stop()
            except Exception:
                log.exception("FeedManager: error stopping WS feed")

        if self._rest_feed is not None:
            try:
                self._rest_feed.stop()
            except Exception:
                log.exception("FeedManager: error stopping REST feed")

        log.info("FeedManager: stopped")

    def subscribe_dynamic(
        self, subscriptions: list[tuple[str, str, Instrument]]
    ) -> None:
        """Forward new subscriptions to both feeds (thread-safe)."""
        with self._lock:
            # Update our own list so REST feed on failover gets full picture
            existing_ids = {str(s) for s, _, _ in self._subs}
            new_subs = [
                (s, seg, instr)
                for s, seg, instr in subscriptions
                if str(s) not in existing_ids
            ]
            self._subs.extend(new_subs)

        if not new_subs:
            return

        if self._ws_feed is not None:
            try:
                self._ws_feed.subscribe_dynamic(new_subs)
            except Exception:
                log.exception("FeedManager: subscribe_dynamic error on WS feed")

        if self._rest_feed is not None:
            try:
                self._rest_feed.subscribe_dynamic(new_subs)
            except Exception:
                log.exception("FeedManager: subscribe_dynamic error on REST feed")

    # --------------------------------------------------------------- monitor

    def _monitor_loop(self) -> None:
        """Periodically check bar flow; trigger REST failover if stalled."""
        while not self._stop_evt.is_set():
            self._stop_evt.wait(timeout=self._monitor_interval)
            if self._stop_evt.is_set():
                break

            with self._lock:
                if self._rest_active:
                    continue  # already failed over; nothing more to do
                last = self._last_bar_time
                err_count = self._ws_error_count

            now = self._now_fn()
            t   = now.astimezone(IST).time()

            # Only check during market hours
            if not (_SESSION_START <= t <= _SESSION_END):
                continue

            # --- check for too many WS errors
            if err_count >= _MAX_WS_ERRORS:
                log.critical(
                    "FeedManager: WS feed has %d errors — TRIGGERING REST FAILOVER",
                    err_count,
                )
                self._start_rest_failover(reason=f"ws_errors_{err_count}")
                continue

            # --- check for bar silence
            if last is None:
                # No bar since start — how long have we been in market hours?
                # Use now as proxy (we can't know exact market-open start time here)
                # so compute silence from session start if now > session start
                session_start_today = now.astimezone(IST).replace(
                    hour=9, minute=15, second=0, microsecond=0
                )
                silence = (now - session_start_today).total_seconds()
            else:
                silence = (now - last).total_seconds()

            if silence >= self._failover_secs:
                log.critical(
                    "FeedManager: NO BARS for %.0fs during market hours — "
                    "TRIGGERING REST FAILOVER (last_bar=%s)",
                    silence,
                    last.isoformat() if last else "never",
                )
                self._start_rest_failover(reason=f"silence_{silence:.0f}s")

    def _start_rest_failover(self, reason: str = "unknown") -> None:
        """Activate RestPollingBarSource as the active bar source."""
        with self._lock:
            if self._rest_active:
                return  # idempotent
            self._rest_active = True

        log.critical(
            "FeedManager: === REST FAILOVER ACTIVATED (reason=%s) === "
            "REST will take over bar delivery; WS remains connected but "
            "bars from WS are suppressed to avoid duplicates.",
            reason,
        )

        try:
            if not _REST_AVAILABLE or RestPollingBarSource is None:
                raise ImportError("RestPollingBarSource not available")

            def _rest_on_bar(ev: BarEvent) -> None:
                self._on_bar_from_rest(ev)

            with self._lock:
                subs_snapshot = list(self._subs)
                token = self._access_token

            self._rest_feed = RestPollingBarSource(
                subscriptions=subs_snapshot,
                on_bar=_rest_on_bar,
                access_token=token,
            )
            self._rest_feed.start()
            log.critical(
                "FeedManager: RestPollingBarSource started with %d subscriptions — "
                "bars will now flow via REST; WS bars suppressed.",
                len(subs_snapshot),
            )
        except Exception:
            log.exception(
                "FeedManager: CRITICAL — REST failover failed to start! "
                "System may be blind. Check Dhan API and token."
            )

    # --------------------------------------------------------- bar routing

    def _on_bar_from_ws(self, ev: BarEvent) -> None:
        """Handle a bar from DhanLiveFeedV2.

        Suppressed once REST failover is active (to avoid duplicate bars).
        """
        with self._lock:
            rest_active = self._rest_active

        if rest_active:
            # REST has taken over; suppress WS bars to avoid duplicates
            log.debug(
                "FeedManager: suppressing WS bar %s @ %s (REST is active)",
                ev.bar.instrument.symbol, ev.bar.ts_open,
            )
            return

        with self._lock:
            self._last_bar_time = self._now_fn()

        try:
            self._on_bar_cb(ev)
        except Exception:
            log.exception("FeedManager: on_bar_cb raised for WS bar")

    def _on_bar_from_rest(self, ev: BarEvent) -> None:
        """Handle a bar from RestPollingBarSource (failover path)."""
        with self._lock:
            self._last_bar_time = self._now_fn()

        try:
            self._on_bar_cb(ev)
        except Exception:
            log.exception("FeedManager: on_bar_cb raised for REST bar")

    # ----------------------------------------------------- status properties

    @property
    def is_rest_active(self) -> bool:
        """True if REST failover is currently serving bars."""
        with self._lock:
            return self._rest_active

    @property
    def last_bar_time(self) -> datetime | None:
        """Timestamp of the most recent bar emitted (any source)."""
        with self._lock:
            return self._last_bar_time
