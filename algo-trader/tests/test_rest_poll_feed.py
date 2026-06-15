"""Tests for algotrader/data/rest_poll_feed.py.

Two test classes:
  TestRestPollFeedUnit        — fast unit tests with a STUBBED fetcher (no network).
                                Tests dedup/HWM, subscribe_dynamic, no-bars alert,
                                session filtering, global chronological ordering.
  TestRestPollFeedIntegration — real REST calls against 2026-06-15 data (the day
                                the WebSocket delivered ZERO bars).  Marked slow.
                                Tests ordering, dedup, bar counts, and LiveBreadth
                                at the 10:15 boundary.

Design constraints honoured:
  - No order-placement imports.
  - All datetimes tz-aware Asia/Kolkata.
  - Unit tests: zero network I/O.
  - Integration tests: real Dhan REST calls (token auto-mints; creds present).
"""
from __future__ import annotations

import threading
import time as _time_mod
from collections import deque
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from algotrader.core import Bar, Instrument, IST, Segment
from algotrader.data.live_feed import BarEvent
from algotrader.data.rest_poll_feed import RestPollingBarSource

# --------------------------------------------------------------------------- fixtures

_SESSION_DATE = date(2026, 6, 15)


def _ist(h: int, m: int, s: int = 0, d: date = _SESSION_DATE) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=IST)


def _instr(sym: str, sec_id: str = "9999", segment: Segment = Segment.NSE_EQ) -> Instrument:
    return Instrument(
        symbol=sym,
        security_id=sec_id,
        segment=segment,
        tick_size=0.05,
        is_derivative=False,
    )


def _bar(instr: Instrument, h: int, m: int, close: float = 100.0) -> Bar:
    ts = _ist(h, m)
    return Bar(
        instrument=instr,
        ts_open=ts,
        interval_min=1,
        open=close - 0.5,
        high=close + 0.5,
        low=close - 1.0,
        close=close,
        volume=1000,
        complete=True,
    )


def _df_from_bars(bars: list[Bar]) -> pd.DataFrame:
    """Convert a list of Bar objects to the DataFrame shape HistoryFetcher returns."""
    return pd.DataFrame(
        {
            "ts": pd.DatetimeIndex(
                [b.ts_open for b in bars], tz="Asia/Kolkata"
            ),
            "open":   [b.open   for b in bars],
            "high":   [b.high   for b in bars],
            "low":    [b.low    for b in bars],
            "close":  [b.close  for b in bars],
            "volume": [b.volume for b in bars],
        }
    )


# ----------------------------------------------------------------- StepClock

class _StepClock:
    """Injectable now_fn: advances to the next datetime on each call.

    After the sequence is exhausted every call returns the last element,
    so the poll loop exits cleanly on the last out-of-session value.
    """

    def __init__(self, steps: list[datetime]) -> None:
        assert steps, "steps must be non-empty"
        self._q: deque[datetime] = deque(steps)
        self._last = steps[-1]

    def __call__(self) -> datetime:
        if self._q:
            return self._q.popleft()
        return self._last


# ========================================================== unit tests (no network)

class TestRestPollFeedUnit:
    """Deterministic unit tests.  HistoryFetcher is stubbed; zero network I/O."""

    # ---------------------------------------------------------------- helpers

    def _run_with_stub(
        self,
        subscriptions: list[tuple[str, str, Instrument]],
        emitted: list[BarEvent],
        steps: list[datetime],
        mock_fetcher: MagicMock,
    ) -> RestPollingBarSource:
        """Build a feed with stubbed HistoryFetcher and step-clock now_fn,
        then call _run() directly (no background thread) so the test is synchronous."""
        clock = _StepClock(steps)
        feed = RestPollingBarSource(
            subscriptions=subscriptions,
            on_bar=lambda ev: emitted.append(ev),
            poll_seconds=0,      # no real sleep between polls
            now_fn=clock,
        )
        # Patch HistoryFetcher constructor so _run() gets our mock
        with (
            patch("algotrader.data.rest_poll_feed.HistoryFetcher", return_value=mock_fetcher),
            patch.object(feed, "_detect_stamp_offset"),
        ):
            feed._stamp_offset = 0
            feed._run()
        return feed

    # ---------------------------------------------------------------- tests

    def test_dedup_high_water_mark(self):
        """Each bar is emitted exactly once even if it appears in multiple polls."""
        sym = "ALPHA"
        instr = _instr(sym, "1111")
        subs = [("1111", "NSE_EQ", instr)]
        emitted: list[BarEvent] = []

        # Poll 1: bars at 09:15, 09:16
        # Poll 2: same bars + 09:17 (simulates re-fetching a growing dataset)
        bars_p1 = [_bar(instr, 9, 15, 100.0), _bar(instr, 9, 16, 101.0)]
        bars_p2 = bars_p1 + [_bar(instr, 9, 17, 102.0)]

        df1 = _df_from_bars(bars_p1)
        df2 = _df_from_bars(bars_p2)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_window.side_effect = [df1, df2]
        mock_fetcher.normalize.side_effect = lambda df, offset: df  # identity

        steps = [
            _ist(10, 16),   # poll 1 inside session
            _ist(10, 17),   # poll 2 inside session
            _ist(15, 31),   # exit condition
        ]
        feed = self._run_with_stub(subs, emitted, steps, mock_fetcher)

        assert len(emitted) == 3, f"Expected 3, got {len(emitted)}"
        ts_list = [ev.bar.ts_open for ev in emitted]
        assert ts_list == sorted(ts_list), "Bars not in chronological order"
        assert len(set(ts_list)) == 3, "Duplicate ts emitted"
        assert all(ev.bar.instrument.symbol == sym for ev in emitted)

    def test_subscribe_dynamic_picked_up_on_next_poll(self):
        """subscribe_dynamic adds an instrument; its bars appear in next poll."""
        instr_a = _instr("ALPHA", "1111")
        instr_b = _instr("BETA",  "2222")
        subs = [("1111", "NSE_EQ", instr_a)]
        emitted: list[BarEvent] = []

        bars_a = [_bar(instr_a, 9, 15, 100.0)]
        bars_b = [_bar(instr_b, 9, 15, 200.0)]

        df_a = _df_from_bars(bars_a)
        df_b = _df_from_bars(bars_b)
        empty_df = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

        def fetch_side_effect(spec, frm, to):
            if spec.security_id == "1111":
                return df_a
            if spec.security_id == "2222":
                return df_b
            return empty_df

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_window.side_effect = fetch_side_effect
        mock_fetcher.normalize.side_effect = lambda df, offset: df

        # We'll inject a subscribe midway by using a custom now_fn that adds BETA
        # between poll 1 and poll 2.
        subscribe_called = [False]
        feed_holder: list[RestPollingBarSource] = []

        class _ClockWithSub:
            def __init__(self) -> None:
                self._steps = deque([
                    _ist(10, 16),  # poll 1 — only ALPHA
                    _ist(10, 17),  # poll 2 — BETA now subscribed
                    _ist(15, 31),  # exit
                ])
                self._last = _ist(15, 31)

            def __call__(self) -> datetime:
                if not self._steps:
                    return self._last
                val = self._steps.popleft()
                # Between poll 1 (10:16) and poll 2 (10:17): subscribe BETA
                if val == _ist(10, 17) and not subscribe_called[0] and feed_holder:
                    subscribe_called[0] = True
                    feed_holder[0].subscribe_dynamic([("2222", "NSE_EQ", instr_b)])
                return val

        clock = _ClockWithSub()
        feed = RestPollingBarSource(
            subscriptions=subs,
            on_bar=lambda ev: emitted.append(ev),
            poll_seconds=0,
            now_fn=clock,
        )
        feed_holder.append(feed)

        with (
            patch("algotrader.data.rest_poll_feed.HistoryFetcher", return_value=mock_fetcher),
            patch.object(feed, "_detect_stamp_offset"),
        ):
            feed._stamp_offset = 0
            feed._run()

        symbols_emitted = [ev.bar.instrument.symbol for ev in emitted]
        assert "ALPHA" in symbols_emitted
        assert "BETA" in symbols_emitted, f"BETA not emitted; got {symbols_emitted}"

    def test_global_chronological_order_across_instruments(self):
        """Bars from multiple instruments are emitted in global ts order."""
        instr_a = _instr("ALPHA", "1111")
        instr_b = _instr("BETA",  "2222")
        subs = [("1111", "NSE_EQ", instr_a), ("2222", "NSE_EQ", instr_b)]
        emitted: list[BarEvent] = []

        # ALPHA has bars at 09:15 and 09:17; BETA at 09:16
        bars_a = [_bar(instr_a, 9, 15, 100.0), _bar(instr_a, 9, 17, 102.0)]
        bars_b = [_bar(instr_b, 9, 16, 200.0)]

        def fetch_side_effect(spec, frm, to):
            if spec.security_id == "1111":
                return _df_from_bars(bars_a)
            if spec.security_id == "2222":
                return _df_from_bars(bars_b)
            return pd.DataFrame()

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_window.side_effect = fetch_side_effect
        mock_fetcher.normalize.side_effect = lambda df, offset: df

        steps = [_ist(10, 16), _ist(15, 31)]
        feed = self._run_with_stub(subs, emitted, steps, mock_fetcher)

        assert len(emitted) == 3
        ts_list = [ev.bar.ts_open for ev in emitted]
        assert ts_list == sorted(ts_list), f"Not sorted: {ts_list}"

    def test_session_filter_out_of_session_bars_dropped(self):
        """normalize() is called and filters out-of-session bars.

        We stub normalize to return only the in-session bar and verify only that
        bar is emitted (proving normalize participates in the pipeline).
        """
        instr_a = _instr("ALPHA", "1111")
        subs = [("1111", "NSE_EQ", instr_a)]
        emitted: list[BarEvent] = []

        in_session_bar = _bar(instr_a, 9, 15, 100.0)

        norm_called = [False]

        def normalize_side_effect(df, offset):
            norm_called[0] = True
            # Simulate real normalize: return only the session bar
            return _df_from_bars([in_session_bar])

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_window.return_value = _df_from_bars([in_session_bar])
        mock_fetcher.normalize.side_effect = normalize_side_effect

        steps = [_ist(10, 16), _ist(15, 31)]
        feed = self._run_with_stub(subs, emitted, steps, mock_fetcher)

        assert norm_called[0], "normalize was never called"
        assert len(emitted) == 1
        assert emitted[0].bar.ts_open == _ist(9, 15)

    def test_no_bars_alert_fires_when_zero_emitted_by_0935(self, tmp_path):
        """CRITICAL is logged and alert file written when no bars by 09:35."""
        instr_a = _instr("ALPHA", "1111")
        subs = [("1111", "NSE_EQ", instr_a)]
        emitted: list[BarEvent] = []

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_window.return_value = pd.DataFrame()
        mock_fetcher.normalize.return_value = pd.DataFrame()

        steps = [_ist(9, 36), _ist(15, 31)]  # 09:36 > 09:35 alert threshold

        import algotrader.data.rest_poll_feed as rpf_mod
        original_dir = rpf_mod.REPORTS_DIR
        rpf_mod.REPORTS_DIR = tmp_path
        try:
            feed = self._run_with_stub(subs, emitted, steps, mock_fetcher)
        finally:
            rpf_mod.REPORTS_DIR = original_dir

        assert feed._total_emitted == 0
        alert_files = list(tmp_path.glob(f"feed_alert_{_SESSION_DATE}*.txt"))
        assert alert_files, f"No alert file in {tmp_path}: {list(tmp_path.iterdir())}"
        content = alert_files[0].read_text()
        assert "NO BARS RECEIVED" in content

    def test_no_bars_alert_does_not_fire_when_bars_emitted(self, tmp_path):
        """Alert is NOT triggered if at least one bar was emitted before 09:35."""
        instr_a = _instr("ALPHA", "1111")
        subs = [("1111", "NSE_EQ", instr_a)]
        emitted: list[BarEvent] = []

        bars = [_bar(instr_a, 9, 15, 100.0)]
        mock_fetcher = MagicMock()
        # First call returns data; second call (at 09:36 step) returns same data
        mock_fetcher.fetch_window.return_value = _df_from_bars(bars)
        mock_fetcher.normalize.side_effect = lambda df, offset: df

        # First poll at 09:16 (bar emitted); second at 09:36 (alert threshold)
        steps = [_ist(9, 16), _ist(9, 36), _ist(15, 31)]

        import algotrader.data.rest_poll_feed as rpf_mod
        original_dir = rpf_mod.REPORTS_DIR
        rpf_mod.REPORTS_DIR = tmp_path
        try:
            feed = self._run_with_stub(subs, emitted, steps, mock_fetcher)
        finally:
            rpf_mod.REPORTS_DIR = original_dir

        assert feed._total_emitted >= 1
        alert_files = list(tmp_path.glob(f"feed_alert_{_SESSION_DATE}*.txt"))
        assert not alert_files, f"Alert file created unexpectedly: {alert_files}"

    def test_stop_exits_cleanly(self):
        """stop() sets the stop event and the thread terminates."""
        instr_a = _instr("ALPHA", "1111")
        subs = [("1111", "NSE_EQ", instr_a)]
        emitted: list[BarEvent] = []

        # Infinite clock — thread must terminate via stop()
        def always_market() -> datetime:
            return _ist(10, 0)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_window.return_value = pd.DataFrame()
        mock_fetcher.normalize.return_value = pd.DataFrame()

        feed = RestPollingBarSource(
            subscriptions=subs,
            on_bar=lambda ev: emitted.append(ev),
            poll_seconds=0,
            now_fn=always_market,
        )

        with (
            patch("algotrader.data.rest_poll_feed.HistoryFetcher", return_value=mock_fetcher),
            patch.object(feed, "_detect_stamp_offset"),
        ):
            feed.start()
            _time_mod.sleep(0.05)  # let it spin a couple of times
            feed.stop()

        assert not (feed._thread and feed._thread.is_alive()), "thread still running after stop()"

    def test_subscribe_dynamic_idempotent(self):
        """subscribe_dynamic with an already-subscribed id is a no-op."""
        instr_a = _instr("ALPHA", "1111")
        subs = [("1111", "NSE_EQ", instr_a)]
        feed = RestPollingBarSource(
            subscriptions=subs,
            on_bar=lambda ev: None,
        )
        assert len(feed._subs) == 1
        # Adding the same security_id again
        feed.subscribe_dynamic([("1111", "NSE_EQ", instr_a)])
        assert len(feed._subs) == 1, "Idempotency violated: duplicate added"

    def test_bar_event_is_catchup_false(self):
        """REST-polled bars carry is_catchup=False (they are real-time via REST)."""
        instr_a = _instr("ALPHA", "1111")
        subs = [("1111", "NSE_EQ", instr_a)]
        emitted: list[BarEvent] = []

        bars = [_bar(instr_a, 9, 15, 100.0)]
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_window.return_value = _df_from_bars(bars)
        mock_fetcher.normalize.side_effect = lambda df, offset: df

        steps = [_ist(9, 16), _ist(15, 31)]
        feed = self._run_with_stub(subs, emitted, steps, mock_fetcher)

        assert emitted, "No bars emitted"
        assert all(not ev.is_catchup for ev in emitted), "Expected is_catchup=False"


# ========================================================== integration tests (real REST)

@pytest.mark.slow
class TestRestPollFeedIntegration:
    """Real Dhan REST calls against 2026-06-15 (the failed WS session).

    These tests make ~5-50 network requests and take ~15-60 s.
    They verify the complete path: fetch → normalize → dedup → BarEvent.

    Credential requirement: DHAN_ACCESS_TOKEN or DHAN_CLIENT_ID+DHAN_PIN+DHAN_TOTP_SECRET
    must be configured (reads from ~/.env or project .env automatically).
    """

    #: Small instrument universe with known Dhan security_ids.
    _SMALL_SUBS = [
        ("13",    "IDX_I",  _instr("NIFTY",    "13",    Segment.IDX)),
        ("2885",  "NSE_EQ", _instr("RELIANCE",  "2885")),
        ("1594",  "NSE_EQ", _instr("INFY",      "1594")),
        ("11536", "NSE_EQ", _instr("TCS",       "11536")),
        ("1333",  "NSE_EQ", _instr("HDFCBANK",  "1333")),
    ]

    #: Full NIFTY-50 universe for the breadth test (49 stocks; TATAMOTORS absent
    #: from current scrip master — excluded silently as in the breadth parquet).
    _NIFTY50_SUBS: list[tuple[str, str, Instrument]] = [
        ("2885",  "NSE_EQ", _instr("RELIANCE",    "2885")),
        ("1594",  "NSE_EQ", _instr("INFY",        "1594")),
        ("11536", "NSE_EQ", _instr("TCS",         "11536")),
        ("1333",  "NSE_EQ", _instr("HDFCBANK",    "1333")),
        ("4963",  "NSE_EQ", _instr("ICICIBANK",   "4963")),
        ("5900",  "NSE_EQ", _instr("AXISBANK",    "5900")),
        ("3787",  "NSE_EQ", _instr("WIPRO",       "3787")),
        ("3045",  "NSE_EQ", _instr("SBIN",        "3045")),
        ("1922",  "NSE_EQ", _instr("KOTAKBANK",   "1922")),
        ("11483", "NSE_EQ", _instr("LT",          "11483")),
        ("317",   "NSE_EQ", _instr("BAJFINANCE",  "317")),
        ("7229",  "NSE_EQ", _instr("HCLTECH",     "7229")),
        ("10999", "NSE_EQ", _instr("MARUTI",      "10999")),
        ("3506",  "NSE_EQ", _instr("TITAN",       "3506")),
        ("236",   "NSE_EQ", _instr("ASIANPAINT",  "236")),
        ("1394",  "NSE_EQ", _instr("HINDUNILVR",  "1394")),
        ("15083", "NSE_EQ", _instr("ADANIPORTS",  "15083")),
        ("157",   "NSE_EQ", _instr("APOLLOHOSP",  "157")),
        ("16669", "NSE_EQ", _instr("BAJAJ-AUTO",  "16669")),
        ("16675", "NSE_EQ", _instr("BAJAJFINSV",  "16675")),
        ("383",   "NSE_EQ", _instr("BEL",         "383")),
        ("10604", "NSE_EQ", _instr("BHARTIARTL",  "10604")),
        ("526",   "NSE_EQ", _instr("BPCL",        "526")),
        ("547",   "NSE_EQ", _instr("BRITANNIA",   "547")),
        ("694",   "NSE_EQ", _instr("CIPLA",       "694")),
        ("20374", "NSE_EQ", _instr("COALINDIA",   "20374")),
        ("881",   "NSE_EQ", _instr("DRREDDY",     "881")),
        ("910",   "NSE_EQ", _instr("EICHERMOT",   "910")),
        ("5097",  "NSE_EQ", _instr("ETERNAL",     "5097")),
        ("1232",  "NSE_EQ", _instr("GRASIM",      "1232")),
        ("467",   "NSE_EQ", _instr("HDFCLIFE",    "467")),
        ("1348",  "NSE_EQ", _instr("HEROMOTOCO",  "1348")),
        ("1363",  "NSE_EQ", _instr("HINDALCO",    "1363")),
        ("5258",  "NSE_EQ", _instr("INDUSINDBK",  "5258")),
        ("1660",  "NSE_EQ", _instr("ITC",         "1660")),
        ("11723", "NSE_EQ", _instr("JSWSTEEL",    "11723")),
        ("2031",  "NSE_EQ", _instr("M&M",         "2031")),
        ("17963", "NSE_EQ", _instr("NESTLEIND",   "17963")),
        ("11630", "NSE_EQ", _instr("NTPC",        "11630")),
        ("2475",  "NSE_EQ", _instr("ONGC",        "2475")),
        ("14977", "NSE_EQ", _instr("POWERGRID",   "14977")),
        ("21808", "NSE_EQ", _instr("SBILIFE",     "21808")),
        ("4306",  "NSE_EQ", _instr("SHRIRAMFIN",  "4306")),
        ("3351",  "NSE_EQ", _instr("SUNPHARMA",   "3351")),
        ("3432",  "NSE_EQ", _instr("TATACONSUM",  "3432")),
        ("3499",  "NSE_EQ", _instr("TATASTEEL",   "3499")),
        ("13538", "NSE_EQ", _instr("TECHM",       "13538")),
        ("1964",  "NSE_EQ", _instr("TRENT",       "1964")),
        ("11532", "NSE_EQ", _instr("ULTRACEMCO",  "11532")),
    ]

    # ---------------------------------------------------------------- tests

    def test_real_data_2026_06_15_ordering_and_dedup(self):
        """5-stock universe, stepping clock: bars ordered, no dupes, within session.

        Polling steps: 10:16 → 10:21 → 10:31 → 15:31 (exit).
        First poll (10:16) fetches 09:15-10:15 for each instrument.
        Second poll (10:21) fetches the same window + 4 new bars (10:16-10:20).
        Third poll (10:31) fetches + 9 more bars.
        HWM must prevent re-emission of bars seen in earlier polls.
        """
        SESSION_START_T = time(9, 15)
        SESSION_END_T   = time(15, 29)

        steps = [
            _ist(10, 16),
            _ist(10, 21),
            _ist(10, 31),
            _ist(15, 31),  # exit condition
        ]
        emitted: list[BarEvent] = []

        feed = RestPollingBarSource(
            subscriptions=list(self._SMALL_SUBS),
            on_bar=lambda ev: emitted.append(ev),
            poll_seconds=0,
            now_fn=_StepClock(steps),
        )

        # Run in a thread so we can use a timeout guard
        done = threading.Event()
        exc_holder: list[BaseException] = []

        def _target():
            try:
                feed.start()
                feed._thread.join(timeout=180)  # up to 3 min for real API calls
            except BaseException as exc:
                exc_holder.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        done.wait(timeout=200)
        feed.stop()

        if exc_holder:
            raise exc_holder[0]

        assert emitted, "No bars were emitted — check API credentials and connectivity"

        # (a) Chronological order across all instruments
        ts_all = [ev.bar.ts_open for ev in emitted]
        assert ts_all == sorted(ts_all), (
            f"Bars not in chronological order.  First disorder at "
            f"{next((i, ts_all[i], ts_all[i+1]) for i in range(len(ts_all)-1) if ts_all[i] > ts_all[i+1])}"
        )

        # (b) No duplicate (instrument, ts) pairs
        seen: set[tuple[str, datetime]] = set()
        for ev in emitted:
            key = (ev.bar.instrument.symbol, ev.bar.ts_open)
            assert key not in seen, f"Duplicate bar: {key}"
            seen.add(key)

        # (c) All ts within session boundaries on 2026-06-15
        for ev in emitted:
            t_open = ev.bar.ts_open.astimezone(IST).time()
            assert SESSION_START_T <= t_open <= SESSION_END_T, (
                f"Bar outside session: {ev.bar.instrument.symbol} @ {ev.bar.ts_open}"
            )
            assert ev.bar.ts_open.date() == _SESSION_DATE

        # (d) Approximate bar count per instrument (expect 60-76 bars up to ~10:30)
        from collections import Counter
        counts = Counter(ev.bar.instrument.symbol for ev in emitted)
        for sym, cnt in counts.items():
            assert cnt >= 55, f"{sym}: only {cnt} bars emitted (expected ≥55 by 10:30)"
            assert cnt <= 376, f"{sym}: {cnt} bars — exceeds full session (376)"

        # (e) is_catchup=False for all REST-polled bars
        assert all(not ev.is_catchup for ev in emitted), "Expected is_catchup=False"

    def test_breadth_at_10_15_matches_expected(self):
        """Full 49-stock NIFTY-50 universe polled to 10:16; breadth at 10:15 ~0.388.

        This value was computed from real 2026-06-15 REST data:
            pct_above_vwap at 10:15 = 0.388 (19/49 stocks above intraday VWAP).

        Tolerance ±0.05 (±2-3 stocks) accommodates minor variations in the
        session's first bar timestamp across API calls.
        """
        from algotrader.data.live_breadth import LiveBreadth

        EPOCH_10_15 = 1781498700  # datetime(2026,6,15, 10,15, tzinfo=IST).timestamp()
        EXPECTED_PCT = 0.388
        TOLERANCE    = 0.05

        steps = [
            _ist(10, 16),   # one poll fetches all bars 09:15-10:15
            _ist(15, 31),   # exit
        ]
        emitted: list[BarEvent] = []

        feed = RestPollingBarSource(
            subscriptions=list(self._NIFTY50_SUBS),
            on_bar=lambda ev: emitted.append(ev),
            poll_seconds=0,
            now_fn=_StepClock(steps),
        )

        done = threading.Event()
        exc_holder: list[BaseException] = []

        def _target():
            try:
                feed.start()
                feed._thread.join(timeout=300)  # up to 5 min for 49 stocks
            except BaseException as exc:
                exc_holder.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        done.wait(timeout=320)
        feed.stop()

        if exc_holder:
            raise exc_holder[0]

        assert emitted, "No bars emitted — check API credentials"

        # Feed emitted bars through LiveBreadth
        symbols = [instr.symbol for _, _, instr in self._NIFTY50_SUBS]
        breadth = LiveBreadth(symbols)

        cutoff = _ist(10, 16)   # only bars with ts_open < 10:16 = the 09:15-10:15 batch
        for ev in emitted:
            bar = ev.bar
            if bar.ts_open < cutoff:
                breadth.on_bar(bar.instrument.symbol, bar)

        snap = breadth.at(EPOCH_10_15)
        assert snap is not None, (
            "LiveBreadth returned None for 10:15 snapshot; "
            "check that bars at the 10:15 decision boundary were ingested."
        )

        pct_above_vwap, n_stocks, net_breadth = snap
        assert n_stocks >= 45, f"Only {n_stocks} stocks in breadth (expected ≥45)"
        assert abs(pct_above_vwap - EXPECTED_PCT) <= TOLERANCE, (
            f"Breadth at 10:15: got {pct_above_vwap:.4f}, expected {EXPECTED_PCT} ±{TOLERANCE}"
        )
