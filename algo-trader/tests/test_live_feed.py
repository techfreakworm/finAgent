"""Tests for algotrader/data/live_feed.py.

All tests run without network access:
  - BarBuilder tests use synthetic tick dicts (no WS connection).
  - ReplayDriver test uses stored parquet bars from data/cache/RELIANCE/1m/.
  - Gap-recovery test mocks the requests.post call.

Design constraints honoured:
  - No order-placement imports.
  - All datetimes tz-aware Asia/Kolkata.
  - Tests do NOT start a real WS connection.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import requests

from algotrader.core import Bar, Instrument, IST, Segment
from algotrader.data.live_feed import (
    BarEvent,
    ReplayDriver,
    _BarBuilder,
    _fetch_gap_bars,
    _in_market_hours,
    _minute_floor,
    SESSION_START,
    SESSION_END,
)

# ----------------------------------------------------------------- fixtures

RELIANCE = Instrument(
    symbol="RELIANCE",
    security_id="2885",
    segment=Segment.NSE_EQ,
    tick_size=0.05,
)

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"


def _ts(h: int, m: int, s: int = 0, d: date = date(2026, 3, 2)) -> datetime:
    """Construct a tz-aware IST datetime for a given H:M:S on a session date."""
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=IST)


def _tick(ltp: float, ltq: int, h: int, m: int, s: int = 0,
          d: date = date(2026, 3, 2)) -> dict:
    return {"ltp": ltp, "ltq": ltq, "ts": _ts(h, m, s, d)}


# ============================================================ helpers / infra

class TestHelpers:
    def test_minute_floor_strips_seconds(self):
        ts = _ts(9, 15, 45)
        floored = _minute_floor(ts)
        assert floored.second == 0
        assert floored.microsecond == 0
        assert floored.hour == 9
        assert floored.minute == 15
        assert floored.tzinfo is not None

    def test_in_market_hours_true(self):
        assert _in_market_hours(_ts(9, 15)) is True
        assert _in_market_hours(_ts(12, 0)) is True
        assert _in_market_hours(_ts(15, 29)) is True

    def test_in_market_hours_false(self):
        assert _in_market_hours(_ts(9, 14)) is False
        assert _in_market_hours(_ts(15, 31)) is False


# ============================================================ BarBuilder

class TestBarBuilder:
    """Tick → bar aggregation logic."""

    def _make_builder(self, grace_sec: float = 10.0) -> tuple["_BarBuilder", list[BarEvent]]:
        emitted: list[BarEvent] = []
        bb = _BarBuilder(RELIANCE, on_bar=lambda ev: emitted.append(ev), grace_sec=grace_sec)
        return bb, emitted

    # ---- minute rollover

    def test_minute_rollover_emits_first_bar(self):
        bb, emitted = self._make_builder()
        bb.process(_tick(1364.0, 200, 9, 15, 10))
        bb.process(_tick(1368.0, 100, 9, 15, 30))
        assert len(emitted) == 0   # bar not closed yet

        # Tick in the next minute forces emission
        bb.process(_tick(1370.0, 50, 9, 16, 5))
        assert len(emitted) == 1
        bar = emitted[0].bar

        assert bar.ts_open == _ts(9, 15, 0)
        assert bar.open  == 1364.0
        assert bar.high  == 1368.0
        assert bar.low   == 1364.0
        assert bar.close == 1368.0
        assert bar.volume == 300
        assert bar.complete is True
        assert bar.interval_min == 1
        assert bar.instrument is RELIANCE

    def test_minute_rollover_emitted_is_not_catchup(self):
        bb, emitted = self._make_builder()
        bb.process(_tick(100.0, 10, 9, 15, 0))
        bb.process(_tick(101.0, 5,  9, 16, 0))
        assert emitted[0].is_catchup is False

    def test_consecutive_minutes_emit_consecutive_bars(self):
        bb, emitted = self._make_builder()
        bb.process(_tick(100.0, 10, 9, 15, 0))
        bb.process(_tick(101.0, 10, 9, 16, 0))
        bb.process(_tick(102.0, 10, 9, 17, 0))

        assert len(emitted) == 2
        assert emitted[0].bar.ts_open == _ts(9, 15, 0)
        assert emitted[1].bar.ts_open == _ts(9, 16, 0)

    def test_bar_high_low_tracked_correctly(self):
        bb, emitted = self._make_builder()
        for ltp in [100.0, 105.0, 98.0, 103.0]:
            bb.process(_tick(ltp, 10, 9, 15, 0))
        bb.process(_tick(104.0, 5, 9, 16, 0))   # trigger emission

        bar = emitted[0].bar
        assert bar.open  == 100.0
        assert bar.high  == 105.0
        assert bar.low   == 98.0
        assert bar.close == 103.0

    def test_volume_accumulated(self):
        bb, emitted = self._make_builder()
        for q in [100, 200, 150]:
            bb.process(_tick(100.0, q, 9, 15, 10))
        bb.process(_tick(100.0, 50, 9, 16, 0))
        assert emitted[0].bar.volume == 450   # only 09:15 ticks

    # ---- out-of-order ticks

    def test_out_of_order_tick_dropped_does_not_affect_bar(self):
        bb, emitted = self._make_builder()
        bb.process(_tick(100.0, 100, 9, 16, 0))
        # Stale tick with timestamp earlier than current bar
        bb.process(_tick(50.0, 9999, 9, 15, 0))   # should be dropped
        bb.process(_tick(102.0, 50, 9, 17, 0))     # triggers emission of 09:16 bar

        assert len(emitted) == 1
        bar = emitted[0].bar
        assert bar.open  == 100.0
        assert bar.high  == 100.0
        assert bar.low   == 100.0
        assert bar.volume == 100   # 9999 not added

    def test_same_minute_second_tick_in_order_accepted(self):
        bb, emitted = self._make_builder()
        bb.process(_tick(100.0, 10, 9, 15, 0))
        bb.process(_tick(105.0, 20, 9, 15, 30))    # same minute, later second
        bb.process(_tick(101.0, 5, 9, 16, 0))

        assert len(emitted) == 1
        bar = emitted[0].bar
        assert bar.close == 105.0
        assert bar.volume == 30

    # ---- session filter

    def test_pre_session_tick_not_emitted(self):
        bb, emitted = self._make_builder()
        bb.process(_tick(100.0, 100, 9, 10, 0))    # 09:10 — before 09:15
        bb.process(_tick(101.0, 50, 9, 15, 0))      # 09:15 — first valid
        bb.process(_tick(102.0, 30, 9, 16, 0))      # triggers 09:15 emission

        # The 09:10 bar is pre-session: should not be emitted; 09:15 should be
        assert len(emitted) == 1
        assert emitted[0].bar.ts_open == _ts(9, 15, 0)

    def test_post_session_tick_no_bar_emitted(self):
        bb, emitted = self._make_builder()
        bb.process(_tick(100.0, 100, 15, 30, 0))   # 15:30 — after last bar
        bb.process(_tick(101.0, 50, 15, 31, 0))
        assert len(emitted) == 0

    def test_last_valid_bar_ts_open_is_15_29(self):
        bb, emitted = self._make_builder()
        bb.process(_tick(100.0, 100, 15, 29, 30))  # in 15:29 minute
        bb.process(_tick(101.0, 50, 15, 30, 5))    # triggers 15:29 emission
        assert len(emitted) == 1
        assert emitted[0].bar.ts_open == _ts(15, 29, 0)

    # ---- grace timer

    def test_grace_timer_emits_open_bar(self):
        """Bar emitted when no new ticks arrive for grace_sec."""
        emitted: list[BarEvent] = []
        done = threading.Event()

        def on_bar(ev: BarEvent) -> None:
            emitted.append(ev)
            done.set()

        bb = _BarBuilder(RELIANCE, on_bar=on_bar, grace_sec=0.15)
        bb.process(_tick(100.0, 100, 9, 15, 10))
        bb.process(_tick(102.0, 50,  9, 15, 20))

        # No further ticks → grace timer should fire
        done.wait(timeout=2.0)

        assert len(emitted) == 1
        bar = emitted[0].bar
        assert bar.ts_open == _ts(9, 15, 0)
        assert bar.open   == 100.0
        assert bar.close  == 102.0
        assert bar.complete is True

    def test_grace_timer_reset_on_new_tick(self):
        """Grace timer is reset when a new tick arrives; bar not emitted early."""
        emitted: list[BarEvent] = []
        fired_early = threading.Event()

        def on_bar(ev: BarEvent) -> None:
            emitted.append(ev)

        bb = _BarBuilder(RELIANCE, on_bar=on_bar, grace_sec=0.3)
        bb.process(_tick(100.0, 10, 9, 15, 0))

        # Send another tick before grace fires
        import time as _tm
        _tm.sleep(0.1)
        bb.process(_tick(101.0, 10, 9, 15, 1))   # resets grace timer

        _tm.sleep(0.15)  # original timer would have fired; new one hasn't yet
        assert len(emitted) == 0   # bar not emitted yet (new timer still running)

        # Now let new grace timer fire
        _tm.sleep(0.25)
        assert len(emitted) == 1
        assert emitted[0].bar.close == 101.0

    def test_flush_emits_open_bar(self):
        bb, emitted = self._make_builder()
        bb.process(_tick(100.0, 100, 9, 15, 10))
        bb.flush()
        assert len(emitted) == 1
        assert emitted[0].bar.ts_open == _ts(9, 15, 0)

    def test_flush_empty_is_noop(self):
        bb, emitted = self._make_builder()
        bb.flush()   # no open bar
        assert len(emitted) == 0

    def test_stop_cancels_grace_timer(self):
        """stop() cancels any pending grace timer so the bar is not double-emitted."""
        emitted: list[BarEvent] = []
        bb = _BarBuilder(RELIANCE, on_bar=lambda ev: emitted.append(ev), grace_sec=0.2)
        bb.process(_tick(100.0, 10, 9, 15, 0))
        bb.stop()
        import time as _tm
        _tm.sleep(0.4)  # wait longer than grace period
        assert len(emitted) == 0   # timer was cancelled

    # ---- last_bar_ts tracking

    def test_last_bar_ts_updated_after_emission(self):
        bb, emitted = self._make_builder()
        assert bb.last_bar_ts is None
        bb.process(_tick(100.0, 10, 9, 15, 0))
        bb.process(_tick(101.0, 5,  9, 16, 0))   # triggers emission of 09:15 bar
        assert bb.last_bar_ts == _ts(9, 15, 0)


# ============================================================ ReplayDriver

class TestReplayDriver:
    """Replays parquet bars; no network required."""

    def test_replay_emits_bars_in_order(self):
        """All emitted bars are in chronological order."""
        emitted: list[BarEvent] = []
        driver = ReplayDriver(
            subscriptions=[("2885", "NSE_EQ", RELIANCE)],
            on_bar=lambda ev: emitted.append(ev),
            speed=0,
            cache_dir=CACHE,
            session_date=date(2026, 3, 2),
        )
        driver.run(session_date=date(2026, 3, 2))

        assert len(emitted) > 0
        tss = [ev.bar.ts_open for ev in emitted]
        assert tss == sorted(tss), "bars not in chronological order"

    def test_replay_all_bars_complete(self):
        emitted: list[BarEvent] = []
        driver = ReplayDriver(
            subscriptions=[("2885", "NSE_EQ", RELIANCE)],
            on_bar=lambda ev: emitted.append(ev),
            speed=0,
            cache_dir=CACHE,
        )
        driver.run(session_date=date(2026, 3, 2))
        assert all(ev.bar.complete for ev in emitted), "some bars not complete"

    def test_replay_bars_within_session(self):
        emitted: list[BarEvent] = []
        driver = ReplayDriver(
            subscriptions=[("2885", "NSE_EQ", RELIANCE)],
            on_bar=lambda ev: emitted.append(ev),
            speed=0,
            cache_dir=CACHE,
        )
        driver.run(session_date=date(2026, 3, 2))
        for ev in emitted:
            t = ev.bar.ts_open.time()
            assert SESSION_START <= t <= SESSION_END, f"bar outside session: {ev.bar.ts_open}"

    def test_replay_is_not_catchup(self):
        emitted: list[BarEvent] = []
        driver = ReplayDriver(
            subscriptions=[("2885", "NSE_EQ", RELIANCE)],
            on_bar=lambda ev: emitted.append(ev),
            speed=0,
            cache_dir=CACHE,
        )
        driver.run(session_date=date(2026, 3, 2))
        assert all(not ev.is_catchup for ev in emitted)

    def test_replay_bar_instrument_matches_subscription(self):
        emitted: list[BarEvent] = []
        driver = ReplayDriver(
            subscriptions=[("2885", "NSE_EQ", RELIANCE)],
            on_bar=lambda ev: emitted.append(ev),
            speed=0,
            cache_dir=CACHE,
        )
        driver.run(session_date=date(2026, 3, 2))
        assert all(ev.bar.instrument is RELIANCE for ev in emitted)

    def test_replay_session_date_filter(self):
        """Only bars from the requested date are emitted."""
        emitted: list[BarEvent] = []
        target = date(2026, 3, 2)
        driver = ReplayDriver(
            subscriptions=[("2885", "NSE_EQ", RELIANCE)],
            on_bar=lambda ev: emitted.append(ev),
            speed=0,
            cache_dir=CACHE,
        )
        driver.run(session_date=target)
        assert all(ev.bar.ts_open.date() == target for ev in emitted)

    def test_replay_tz_aware(self):
        emitted: list[BarEvent] = []
        driver = ReplayDriver(
            subscriptions=[("2885", "NSE_EQ", RELIANCE)],
            on_bar=lambda ev: emitted.append(ev),
            speed=0,
            cache_dir=CACHE,
        )
        driver.run(session_date=date(2026, 3, 2))
        for ev in emitted:
            assert ev.bar.ts_open.tzinfo is not None, "naive ts_open found"

    def test_replay_approx_bar_count(self):
        """A full session has close to 375 bars (09:15 to 15:29 inclusive)."""
        emitted: list[BarEvent] = []
        driver = ReplayDriver(
            subscriptions=[("2885", "NSE_EQ", RELIANCE)],
            on_bar=lambda ev: emitted.append(ev),
            speed=0,
            cache_dir=CACHE,
        )
        driver.run(session_date=date(2026, 3, 2))
        # Allow some missing bars but should be close to 374
        assert len(emitted) >= 300, f"unexpectedly few bars: {len(emitted)}"

    def test_replay_no_files_returns_empty(self):
        """Non-existent symbol returns empty (no exception)."""
        emitted: list[BarEvent] = []
        ghost = Instrument(symbol="GHOST_SYMBOL", security_id="9999",
                           segment=Segment.NSE_EQ, tick_size=0.05)
        driver = ReplayDriver(
            subscriptions=[("9999", "NSE_EQ", ghost)],
            on_bar=lambda ev: emitted.append(ev),
            speed=0,
            cache_dir=CACHE,
        )
        driver.run(session_date=date(2026, 3, 2))
        assert len(emitted) == 0


# ============================================================ ReplayDriver constructor session_date

class ReplayDriver:  # type: ignore[no-redef]
    """Thin re-export shim that adds *session_date* to constructor for test convenience.

    The production ReplayDriver.run() takes session_date.  This wrapper bakes it into
    the constructor so tests can call driver.run() without arguments, matching the
    same signature used in the production paper loop.
    """

    def __new__(cls, *args, session_date=None, **kwargs):
        from algotrader.data.live_feed import ReplayDriver as _Real
        instance = object.__new__(cls)
        instance._real = _Real(*args, **kwargs)
        instance._default_date = session_date
        return instance

    def run(self, session_date=None):
        self._real.run(session_date=session_date or self._default_date)


# ============================================================ Gap recovery

class TestGapRecovery:
    """Gap fill via mocked REST endpoint; no network required."""

    def _make_rest_response(self, session_date: date, start_h: int, start_m: int,
                            n_bars: int) -> dict:
        """Build a synthetic /charts/intraday response with *n_bars* 1-min bars."""
        import time as _tm
        timestamps = []
        opens, highs, lows, closes, volumes = [], [], [], [], []
        for i in range(n_bars):
            dt = datetime(session_date.year, session_date.month, session_date.day,
                          start_h, start_m + i, 0, tzinfo=IST)
            timestamps.append(int(dt.timestamp()))
            price = 1000.0 + i
            opens.append(price)
            highs.append(price + 2)
            lows.append(price - 1)
            closes.append(price + 1)
            volumes.append(10000 + i * 100)
        return {
            "timestamp": timestamps, "open": opens, "high": highs,
            "low": lows, "close": closes, "volume": volumes,
        }

    def test_gap_fetch_returns_sorted_bars(self):
        session = date(2026, 3, 2)
        fake_resp = self._make_rest_response(session, 9, 20, 3)

        with patch("algotrader.data.live_feed.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = fake_resp
            mock_post.return_value = mock_response

            from_dt = datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST)
            to_dt   = datetime(2026, 3, 2, 9, 22, 0, tzinfo=IST)
            bars = _fetch_gap_bars(
                security_id="2885",
                exchange_segment="NSE_EQ",
                instrument=RELIANCE,
                from_dt=from_dt,
                to_dt=to_dt,
                token="DUMMYTOKEN",
            )

        assert len(bars) == 3
        tss = [b.ts_open for b in bars]
        assert tss == sorted(tss)

    def test_gap_fetch_bars_are_complete(self):
        session = date(2026, 3, 2)
        fake_resp = self._make_rest_response(session, 9, 20, 2)

        with patch("algotrader.data.live_feed.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = fake_resp
            mock_post.return_value = mock_response

            bars = _fetch_gap_bars(
                security_id="2885",
                exchange_segment="NSE_EQ",
                instrument=RELIANCE,
                from_dt=datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST),
                to_dt=datetime(2026, 3, 2, 9, 21, 0, tzinfo=IST),
                token="DUMMYTOKEN",
            )

        assert all(b.complete for b in bars)

    def test_gap_fetch_catchup_flag(self):
        """_fetch_gap_bars returns Bars; wrapping as BarEvent with is_catchup=True is caller's job."""
        session = date(2026, 3, 2)
        fake_resp = self._make_rest_response(session, 9, 20, 1)

        with patch("algotrader.data.live_feed.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = fake_resp
            mock_post.return_value = mock_response

            bars = _fetch_gap_bars(
                security_id="2885",
                exchange_segment="NSE_EQ",
                instrument=RELIANCE,
                from_dt=datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST),
                to_dt=datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST),
                token="DUMMYTOKEN",
            )

        events = [BarEvent(bar=b, is_catchup=True) for b in bars]
        assert len(events) == 1
        assert events[0].is_catchup is True

    def test_gap_fetch_network_error_returns_empty(self):
        with patch("algotrader.data.live_feed.requests.post",
                   side_effect=requests.RequestException("timeout")):
            bars = _fetch_gap_bars(
                security_id="2885",
                exchange_segment="NSE_EQ",
                instrument=RELIANCE,
                from_dt=datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST),
                to_dt=datetime(2026, 3, 2, 9, 22, 0, tzinfo=IST),
                token="DUMMYTOKEN",
            )
        assert bars == []

    def test_gap_fetch_http_error_returns_empty(self):
        with patch("algotrader.data.live_feed.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_post.return_value = mock_response

            bars = _fetch_gap_bars(
                security_id="2885",
                exchange_segment="NSE_EQ",
                instrument=RELIANCE,
                from_dt=datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST),
                to_dt=datetime(2026, 3, 2, 9, 22, 0, tzinfo=IST),
                token="DUMMYTOKEN",
            )
        assert bars == []

    def test_gap_fetch_from_after_to_returns_empty(self):
        bars = _fetch_gap_bars(
            security_id="2885",
            exchange_segment="NSE_EQ",
            instrument=RELIANCE,
            from_dt=datetime(2026, 3, 2, 9, 22, 0, tzinfo=IST),
            to_dt=datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST),
            token="DUMMYTOKEN",
        )
        assert bars == []

    def test_gap_fetch_out_of_session_bars_filtered(self):
        """Bars outside session window are filtered by _fetch_gap_bars."""
        session = date(2026, 3, 2)
        # Include a pre-session bar (09:10) and a valid bar (09:20)
        pre_dt = datetime(2026, 3, 2, 9, 10, 0, tzinfo=IST)
        val_dt = datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST)

        fake_resp = {
            "timestamp": [int(pre_dt.timestamp()), int(val_dt.timestamp())],
            "open":   [990.0, 1000.0],
            "high":   [995.0, 1005.0],
            "low":    [988.0, 998.0],
            "close":  [993.0, 1002.0],
            "volume": [5000, 10000],
        }

        with patch("algotrader.data.live_feed.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = fake_resp
            mock_post.return_value = mock_response

            bars = _fetch_gap_bars(
                security_id="2885",
                exchange_segment="NSE_EQ",
                instrument=RELIANCE,
                from_dt=datetime(2026, 3, 2, 9, 10, 0, tzinfo=IST),
                to_dt=datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST),
                token="DUMMYTOKEN",
            )

        assert len(bars) == 1
        assert bars[0].ts_open == val_dt

    def test_gap_trigger_via_livefeed_internal(self):
        """LiveBarFeed._fill_gap_for invokes REST and emits BarEvents with is_catchup=True."""
        from algotrader.data.live_feed import LiveBarFeed

        session = date(2026, 3, 2)
        fake_resp = {
            "timestamp": [int(datetime(2026, 3, 2, 9, 21, 0, tzinfo=IST).timestamp())],
            "open":   [1000.0],
            "high":   [1005.0],
            "low":    [998.0],
            "close":  [1002.0],
            "volume": [50000],
        }

        catchup_events: list[BarEvent] = []

        feed = LiveBarFeed(
            subscriptions=[("2885", "NSE_EQ", RELIANCE)],
            on_bar=lambda ev: catchup_events.append(ev),
            client_id="TEST_CLIENT",
            access_token="TEST_TOKEN",
        )
        # Simulate: last bar was emitted at 09:20
        feed._builders["2885"]._last_bar_ts = datetime(2026, 3, 2, 9, 20, 0, tzinfo=IST)

        with patch("algotrader.data.live_feed.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = fake_resp
            mock_post.return_value = mock_response

            now_ist = datetime(2026, 3, 2, 9, 23, 0, tzinfo=IST)
            feed._fill_gap_for("2885", now_ist)

        assert len(catchup_events) == 1
        assert catchup_events[0].is_catchup is True
        assert catchup_events[0].bar.complete is True
        assert catchup_events[0].bar.ts_open == datetime(2026, 3, 2, 9, 21, 0, tzinfo=IST)
