"""Tests for FeedManager — auto-failover from WS to REST.

No live network required.  All feeds are mocked or driven via injectable
now_fn and tiny failover_seconds.
"""
from __future__ import annotations

import threading
import time as _time_mod
from datetime import datetime, time
from typing import Callable
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from algotrader.core import Bar, Instrument, IST, Segment
from algotrader.data.feed_manager import FeedManager
from algotrader.data.live_feed import BarEvent

# ------------------------------------------------------------------- fixtures

def _make_instrument(symbol: str = "NIFTY", security_id: str = "13") -> Instrument:
    return Instrument(
        symbol=symbol,
        security_id=security_id,
        segment=Segment.IDX,
        tick_size=0.05,
        is_derivative=False,
    )


def _make_bar(instr: Instrument, hour: int = 9, minute: int = 15) -> Bar:
    ts = datetime(2024, 1, 15, hour, minute, 0, tzinfo=IST)
    return Bar(
        instrument=instr,
        ts_open=ts,
        interval_min=1,
        open=24500.0, high=24550.0, low=24480.0, close=24520.0,
        volume=1_000,
        complete=True,
    )


class _MockFeed:
    """Minimal mock feed matching the LiveBarFeed interface."""

    def __init__(self):
        self.started = False
        self.stopped = False
        self._on_bar: Callable | None = None
        self._dynamic_subs: list = []

    def set_on_bar(self, cb: Callable) -> None:
        self._on_bar = cb

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def subscribe_dynamic(self, subs: list) -> None:
        self._dynamic_subs.extend(subs)

    def emit(self, ev: BarEvent) -> None:
        """Simulate a bar arriving from this feed."""
        if self._on_bar:
            self._on_bar(ev)


# ================================================================ core tests

class TestFeedManagerFailover:
    """FeedManager triggers REST failover after bar silence."""

    def _build_manager(
        self,
        failover_seconds: float = 0.5,
        monitor_interval: float = 0.05,
        in_market_hours: bool = True,
    ) -> tuple[FeedManager, list[BarEvent], _MockFeed, _MockFeed]:
        instr = _make_instrument()
        subs = [("13", "IDX_I", instr)]
        received: list[BarEvent] = []

        def _now_fn() -> datetime:
            if in_market_hours:
                return datetime(2024, 1, 15, 9, 20, 0, tzinfo=IST)
            else:
                return datetime(2024, 1, 15, 8, 0, 0, tzinfo=IST)

        mock_ws   = _MockFeed()
        mock_rest = _MockFeed()

        mgr = FeedManager(
            subscriptions=subs,
            on_bar=received.append,
            client_id="test",
            access_token="tok",
            failover_seconds=failover_seconds,
            monitor_interval_sec=monitor_interval,
            now_fn=_now_fn,
        )
        return mgr, received, mock_ws, mock_rest

    def test_ws_bars_flow_without_failover(self):
        """When WS emits bars promptly, REST must never start."""
        mgr, received, mock_ws, mock_rest = self._build_manager(failover_seconds=2.0)

        ws_on_bar_ref: list[Callable] = []
        rest_started = [False]

        def _fake_ws_init(subscriptions, on_bar, **kwargs):
            mock_ws.set_on_bar(on_bar)
            ws_on_bar_ref.append(on_bar)
            return mock_ws

        def _fake_rest_init(subscriptions, on_bar, **kwargs):
            rest_started[0] = True
            mock_rest.set_on_bar(on_bar)
            return mock_rest

        with patch("algotrader.data.feed_manager.DhanLiveFeedV2", _fake_ws_init), \
             patch("algotrader.data.feed_manager.RestPollingBarSource", _fake_rest_init):

            mgr.start()

            # Emit bars from WS every 0.1s — well within failover_seconds=2s
            instr = _make_instrument()
            for _ in range(5):
                ev = BarEvent(bar=_make_bar(instr), is_catchup=False)
                mock_ws.emit(ev)
                _time_mod.sleep(0.05)

            mgr.stop()

        assert len(received) == 5
        assert not rest_started[0], "REST should NOT have started when WS is healthy"

    def test_ws_silence_triggers_rest_failover(self):
        """No bars from WS within failover_seconds → REST must start."""
        mgr, received, mock_ws, mock_rest = self._build_manager(
            failover_seconds=0.2,
            monitor_interval=0.05,
        )

        rest_started = [False]
        rest_on_bar_ref: list[Callable] = []

        def _fake_ws_init(subscriptions, on_bar, **kwargs):
            mock_ws.set_on_bar(on_bar)
            return mock_ws

        def _fake_rest_init(subscriptions, on_bar, **kwargs):
            rest_started[0] = True
            mock_rest.set_on_bar(on_bar)
            rest_on_bar_ref.append(on_bar)
            return mock_rest

        with patch("algotrader.data.feed_manager.DhanLiveFeedV2", _fake_ws_init), \
             patch("algotrader.data.feed_manager.RestPollingBarSource", _fake_rest_init):

            mgr.start()

            # Don't emit any bars — let the silence timer fire
            _time_mod.sleep(0.8)

            assert rest_started[0], (
                "REST failover did NOT start after ws silence "
                f"(failover_seconds=0.2, waited 0.8s)"
            )

            # Now emit a bar from REST and verify it reaches the callback
            if rest_on_bar_ref:
                instr = _make_instrument()
                ev = BarEvent(bar=_make_bar(instr), is_catchup=False)
                mock_rest.emit(ev)

            mgr.stop()

        assert rest_started[0]
        if rest_on_bar_ref:
            assert len(received) >= 1

    def test_ws_bars_suppressed_after_rest_failover(self):
        """Once REST is active, WS bars must be suppressed (no duplicates)."""
        mgr, received, mock_ws, mock_rest = self._build_manager(
            failover_seconds=0.2,
            monitor_interval=0.05,
        )

        ws_on_bar_ref: list[Callable] = []
        rest_on_bar_ref: list[Callable] = []

        def _fake_ws_init(subscriptions, on_bar, **kwargs):
            mock_ws.set_on_bar(on_bar)
            ws_on_bar_ref.append(on_bar)
            return mock_ws

        def _fake_rest_init(subscriptions, on_bar, **kwargs):
            mock_rest.set_on_bar(on_bar)
            rest_on_bar_ref.append(on_bar)
            return mock_rest

        with patch("algotrader.data.feed_manager.DhanLiveFeedV2", _fake_ws_init), \
             patch("algotrader.data.feed_manager.RestPollingBarSource", _fake_rest_init):

            mgr.start()
            _time_mod.sleep(0.8)  # trigger failover

            instr = _make_instrument()
            rest_bar = BarEvent(bar=_make_bar(instr, 9, 20), is_catchup=False)
            ws_bar   = BarEvent(bar=_make_bar(instr, 9, 21), is_catchup=False)

            # Send a REST bar (should reach callback)
            if rest_on_bar_ref:
                mock_rest.emit(rest_bar)

            # Send a WS bar (should be SUPPRESSED)
            if ws_on_bar_ref:
                mock_ws.emit(ws_bar)

            mgr.stop()

        # Only REST bar should be in received; WS bar suppressed
        assert not any(
            ev.bar.ts_open.minute == 21 for ev in received
        ), "WS bar leaked through after REST failover — duplicate risk!"

    def test_no_failover_outside_market_hours(self):
        """Monitor must NOT trigger failover when outside market session."""
        mgr, received, mock_ws, mock_rest = self._build_manager(
            failover_seconds=0.2,
            monitor_interval=0.05,
            in_market_hours=False,  # outside session
        )

        rest_started = [False]

        def _fake_ws_init(subscriptions, on_bar, **kwargs):
            mock_ws.set_on_bar(on_bar)
            return mock_ws

        def _fake_rest_init(subscriptions, on_bar, **kwargs):
            rest_started[0] = True
            return mock_rest

        with patch("algotrader.data.feed_manager.DhanLiveFeedV2", _fake_ws_init), \
             patch("algotrader.data.feed_manager.RestPollingBarSource", _fake_rest_init):

            mgr.start()
            _time_mod.sleep(0.8)
            mgr.stop()

        assert not rest_started[0], "REST must NOT start when market is closed"

    def test_ws_init_error_falls_back_to_rest_immediately(self):
        """If DhanLiveFeedV2 raises on init, REST starts immediately."""
        instr = _make_instrument()
        subs = [("13", "IDX_I", instr)]
        received: list[BarEvent] = []
        rest_started = [False]

        def _bad_ws_init(*a, **kw):
            raise RuntimeError("websockets not installed")

        def _fake_rest_init(subscriptions, on_bar, **kwargs):
            rest_started[0] = True
            return mock_rest

        mock_rest = _MockFeed()

        def _now_fn():
            return datetime(2024, 1, 15, 9, 20, 0, tzinfo=IST)

        mgr = FeedManager(
            subscriptions=subs,
            on_bar=received.append,
            client_id="test",
            access_token="tok",
            failover_seconds=0.2,
            monitor_interval_sec=0.05,
            now_fn=_now_fn,
        )

        with patch("algotrader.data.feed_manager.DhanLiveFeedV2", _bad_ws_init), \
             patch("algotrader.data.feed_manager.RestPollingBarSource", _fake_rest_init):

            mgr.start()
            _time_mod.sleep(0.2)
            mgr.stop()

        assert rest_started[0], "REST must start immediately when WS init fails"


# ================================================================ subscribe_dynamic

class TestFeedManagerSubscribeDynamic:
    """subscribe_dynamic must be forwarded to both feeds."""

    def test_subscribe_dynamic_forwarded_to_both_feeds(self):
        instr_a = _make_instrument("NIFTY", "13")
        instr_b = _make_instrument("RELIANCE", "1333")

        subs = [("13", "IDX_I", instr_a)]
        received: list[BarEvent] = []

        mock_ws   = _MockFeed()
        mock_rest = _MockFeed()
        rest_started = [False]

        def _fake_ws_init(subscriptions, on_bar, **kwargs):
            mock_ws.set_on_bar(on_bar)
            return mock_ws

        def _fake_rest_init(subscriptions, on_bar, **kwargs):
            rest_started[0] = True
            mock_rest.set_on_bar(on_bar)
            return mock_rest

        def _now_fn():
            return datetime(2024, 1, 15, 9, 20, 0, tzinfo=IST)

        mgr = FeedManager(
            subscriptions=subs,
            on_bar=received.append,
            client_id="test",
            access_token="tok",
            failover_seconds=0.1,
            monitor_interval_sec=0.05,
            now_fn=_now_fn,
        )

        with patch("algotrader.data.feed_manager.DhanLiveFeedV2", _fake_ws_init), \
             patch("algotrader.data.feed_manager.RestPollingBarSource", _fake_rest_init):

            mgr.start()
            _time_mod.sleep(0.5)  # trigger REST failover

            # Now call subscribe_dynamic
            mgr.subscribe_dynamic([("1333", "NSE_EQ", instr_b)])

            mgr.stop()

        # Both feeds should have received the subscribe_dynamic call
        assert any(str(s) == "1333" for s, _, _ in mock_ws._dynamic_subs), \
            "subscribe_dynamic not forwarded to WS feed"
        assert any(str(s) == "1333" for s, _, _ in mock_rest._dynamic_subs), \
            "subscribe_dynamic not forwarded to REST feed"

    def test_subscribe_dynamic_before_rest_failover_queued_for_rest(self):
        """New subs added before failover must reach REST once failover starts."""
        instr_a = _make_instrument("NIFTY", "13")
        instr_b = _make_instrument("RELIANCE", "1333")

        subs = [("13", "IDX_I", instr_a)]
        received: list[BarEvent] = []
        rest_subs_at_init: list = []

        mock_ws = _MockFeed()

        def _fake_ws_init(subscriptions, on_bar, **kwargs):
            mock_ws.set_on_bar(on_bar)
            return mock_ws

        def _fake_rest_init(subscriptions, on_bar, **kwargs):
            rest_subs_at_init.extend(subscriptions)
            mock_rest = _MockFeed()
            mock_rest.set_on_bar(on_bar)
            return mock_rest

        def _now_fn():
            return datetime(2024, 1, 15, 9, 20, 0, tzinfo=IST)

        mgr = FeedManager(
            subscriptions=subs,
            on_bar=received.append,
            client_id="test",
            access_token="tok",
            failover_seconds=0.2,
            monitor_interval_sec=0.05,
            now_fn=_now_fn,
        )

        with patch("algotrader.data.feed_manager.DhanLiveFeedV2", _fake_ws_init), \
             patch("algotrader.data.feed_manager.RestPollingBarSource", _fake_rest_init):

            mgr.start()

            # Subscribe new instrument before REST starts
            mgr.subscribe_dynamic([("1333", "NSE_EQ", instr_b)])

            _time_mod.sleep(0.8)  # trigger failover
            mgr.stop()

        # REST should have been initialised with both instruments
        rest_sec_ids = {str(s) for s, _, _ in rest_subs_at_init}
        assert "1333" in rest_sec_ids, (
            "Instrument subscribed before REST failover was not included "
            "in REST subscription list"
        )


# ================================================================ is_rest_active

class TestFeedManagerProperties:
    def test_is_rest_active_false_initially(self):
        instr = _make_instrument()
        subs = [("13", "IDX_I", instr)]

        mock_ws = _MockFeed()

        def _fake_ws_init(subscriptions, on_bar, **kwargs):
            mock_ws.set_on_bar(on_bar)
            return mock_ws

        def _now_fn():
            return datetime(2024, 1, 15, 8, 0, 0, tzinfo=IST)  # pre-market

        mgr = FeedManager(
            subscriptions=subs,
            on_bar=lambda ev: None,
            client_id="test",
            access_token="tok",
            failover_seconds=10.0,
            monitor_interval_sec=0.1,
            now_fn=_now_fn,
        )

        with patch("algotrader.data.feed_manager.DhanLiveFeedV2", _fake_ws_init):
            mgr.start()
            assert mgr.is_rest_active is False
            mgr.stop()
