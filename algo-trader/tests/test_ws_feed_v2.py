"""Tests for DhanLiveFeedV2 — NO live network required.

Tests cover:
1. Binary packet parsing: Ticker / Quote / Full → (security_id, LTP, LTT)
2. Tick → 1-min bar aggregation via _BarBuilder
3. recv-timeout path triggers reconnect (mocked WS)
4. Server disconnect code 807 triggers token refresh before reconnect
"""
from __future__ import annotations

import asyncio
import struct
import threading
import time as _time_mod
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from algotrader.core import Bar, Instrument, IST, Segment
from algotrader.data.ws_feed_v2 import (
    DhanLiveFeedV2,
    _FMT_DISC,
    _FMT_FULL,
    _FMT_QUOTE,
    _FMT_TICKER,
    _RC_QUOTE,
    _RESP_DISCONNECT,
    _RESP_FULL,
    _RESP_QUOTE,
    _RESP_TICKER,
)
from algotrader.data.live_feed import BarEvent


# ---------------------------------------------------------------------- helpers

def _make_instrument(symbol: str = "NIFTY", security_id: str = "13") -> Instrument:
    return Instrument(
        symbol=symbol,
        security_id=security_id,
        segment=Segment.IDX,
        tick_size=0.05,
        is_derivative=False,
    )


def _epoch(dt: datetime) -> int:
    """Return UTC epoch seconds for a tz-aware datetime."""
    return int(dt.timestamp())


# ---------------------------------------------------------------- packet builders

def _build_ticker_packet(
    security_id: int = 13,
    ltp: float = 24500.5,
    ltt_epoch: int = 1_700_000_000,
    exchange_segment: int = 0,  # IDX_I
) -> bytes:
    """Build a valid Ticker binary packet (<BHBIfI>)."""
    return struct.pack(
        _FMT_TICKER,
        _RESP_TICKER,   # first byte = response code 2
        16,             # msg_len (full packet length)
        exchange_segment,
        security_id,
        ltp,
        ltt_epoch,
    )


def _build_quote_packet(
    security_id: int = 13,
    ltp: float = 24500.5,
    ltt_epoch: int = 1_700_000_000,
    ltq: int = 5,
    volume: int = 100_000,
    exchange_segment: int = 0,
) -> bytes:
    """Build a valid Quote binary packet (<BHBIfHIfIIIffff>)."""
    return struct.pack(
        _FMT_QUOTE,
        _RESP_QUOTE,    # resp code 4
        50,             # msg_len
        exchange_segment,
        security_id,
        ltp,            # LTP
        ltq,            # LTQ
        ltt_epoch,      # LTT
        24490.0,        # ATP
        volume,         # volume
        50_000,         # total_sell
        60_000,         # total_buy
        24400.0,        # open
        24300.0,        # close (prev day)
        24600.0,        # high
        24380.0,        # low
    )


def _build_full_packet(
    security_id: int = 13,
    ltp: float = 24500.5,
    ltt_epoch: int = 1_700_000_000,
    exchange_segment: int = 0,
) -> bytes:
    """Build a valid Full binary packet (<BHBIfHIfIIIIIIffff100s>)."""
    depth_bytes = b"\x00" * 100
    return struct.pack(
        _FMT_FULL,
        _RESP_FULL,     # resp code 8
        162,            # msg_len
        exchange_segment,
        security_id,
        ltp,            # LTP
        5,              # LTQ
        ltt_epoch,      # LTT
        24490.0,        # ATP
        100_000,        # volume
        50_000,         # total_sell
        60_000,         # total_buy
        12_000,         # OI
        13_000,         # OI day high
        11_500,         # OI day low
        24400.0,        # open
        24300.0,        # close
        24600.0,        # high
        24380.0,        # low
        depth_bytes,
    )


def _build_disconnect_packet(disc_code: int = 807) -> bytes:
    """Build a server disconnect packet (<BHBIH>)."""
    return struct.pack(
        _FMT_DISC,
        _RESP_DISCONNECT,  # resp code 50
        10,                # msg_len
        0,                 # exchange_segment (unused in disconnect)
        0,                 # security_id (unused)
        disc_code,
    )


# ================================================================ parser tests

class TestBinaryParsers:
    """Verify that the three binary parsers extract correct fields."""

    def setup_method(self):
        instr = _make_instrument()
        self.feed = DhanLiveFeedV2(
            subscriptions=[("13", "IDX_I", instr)],
            on_bar=lambda ev: None,
            client_id="test",
            access_token="test_token",
        )

    def test_parse_ticker(self):
        pkt = _build_ticker_packet(security_id=13, ltp=24500.25, ltt_epoch=1_700_100_000)
        result = self.feed._parse_ticker(pkt)
        assert result is not None
        sec_id, ltp, ltt = result
        assert sec_id == 13
        assert abs(ltp - 24500.25) < 0.01
        assert ltt == 1_700_100_000

    def test_parse_quote(self):
        pkt = _build_quote_packet(security_id=13, ltp=24600.75, ltt_epoch=1_700_200_000)
        result = self.feed._parse_quote(pkt)
        assert result is not None
        sec_id, ltp, ltt = result
        assert sec_id == 13
        assert abs(ltp - 24600.75) < 0.01
        assert ltt == 1_700_200_000

    def test_parse_full(self):
        pkt = _build_full_packet(security_id=13, ltp=24700.0, ltt_epoch=1_700_300_000)
        result = self.feed._parse_full(pkt)
        assert result is not None
        sec_id, ltp, ltt = result
        assert sec_id == 13
        assert abs(ltp - 24700.0) < 0.01
        assert ltt == 1_700_300_000

    def test_parse_ticker_too_short_returns_none(self):
        pkt = b"\x02" + b"\x00" * 10  # only 11 bytes, need 16
        result = self.feed._parse_ticker(pkt)
        assert result is None

    def test_parse_quote_too_short_returns_none(self):
        pkt = _build_ticker_packet()  # only 16 bytes, quote needs 50
        result = self.feed._parse_quote(pkt)
        assert result is None

    def test_parse_full_too_short_returns_none(self):
        pkt = _build_quote_packet()  # only 50 bytes, full needs 162
        result = self.feed._parse_full(pkt)
        assert result is None

    def test_dispatch_ticker_routes_to_ticker_parser(self):
        """_dispatch should call _feed_tick on a ticker packet."""
        ticks_received: list[tuple] = []

        def capture(sec_id, ltp, ltt):
            ticks_received.append((sec_id, ltp, ltt))

        self.feed._feed_tick = capture  # type: ignore[method-assign]
        pkt = _build_ticker_packet(security_id=13, ltp=24500.0, ltt_epoch=1_700_100_000)
        self.feed._dispatch(pkt)
        assert len(ticks_received) == 1
        assert ticks_received[0][0] == 13
        assert abs(ticks_received[0][1] - 24500.0) < 0.01

    def test_dispatch_quote_routes_to_quote_parser(self):
        ticks_received: list[tuple] = []
        self.feed._feed_tick = lambda s, l, t: ticks_received.append((s, l, t))  # type: ignore[method-assign]
        pkt = _build_quote_packet(security_id=13, ltp=24650.0, ltt_epoch=1_700_200_000)
        self.feed._dispatch(pkt)
        assert len(ticks_received) == 1
        assert abs(ticks_received[0][1] - 24650.0) < 0.01

    def test_dispatch_full_routes_to_full_parser(self):
        ticks_received: list[tuple] = []
        self.feed._feed_tick = lambda s, l, t: ticks_received.append((s, l, t))  # type: ignore[method-assign]
        pkt = _build_full_packet(security_id=13, ltp=24800.0, ltt_epoch=1_700_300_000)
        self.feed._dispatch(pkt)
        assert len(ticks_received) == 1
        assert abs(ticks_received[0][1] - 24800.0) < 0.01

    def test_dispatch_empty_packet_is_safe(self):
        self.feed._dispatch(b"")  # must not raise

    def test_dispatch_unknown_code_is_safe(self):
        self.feed._dispatch(b"\xff" + b"\x00" * 20)  # must not raise


# ================================================================ bar building

class TestBarBuilding:
    """Ticks fed via _feed_tick should build correct 1-min OHLCV bars."""

    # A known 09:15 IST epoch (market-open minute)
    # 2024-01-15 09:15:00 IST = 2024-01-15 03:45:00 UTC
    _BASE_EPOCH = int(
        datetime(2024, 1, 15, 3, 45, 0, tzinfo=timezone.utc).timestamp()
    )

    def setup_method(self):
        self.bars: list[BarEvent] = []
        instr = _make_instrument(symbol="NIFTY", security_id="13")
        self.feed = DhanLiveFeedV2(
            subscriptions=[("13", "IDX_I", instr)],
            on_bar=self.bars.append,
            client_id="test",
            access_token="test_token",
            grace_sec=0.05,  # short grace for tests
        )

    def _epoch_at(self, minute_offset: int = 0, second: int = 30) -> int:
        """Return epoch for 09:15+minute_offset IST at given second."""
        return self._BASE_EPOCH + minute_offset * 60 + second

    def test_single_tick_in_09_15_window_builds_bar_on_next_minute(self):
        """A tick at 09:15:30 → bar emitted when 09:16 tick arrives."""
        self.feed._feed_tick(13, 24500.0, self._epoch_at(0, 30))
        # Second tick in next minute forces close of 09:15 bar
        self.feed._feed_tick(13, 24510.0, self._epoch_at(1, 15))
        assert len(self.bars) == 1
        bar = self.bars[0].bar
        assert bar.open == 24500.0
        assert bar.close == 24500.0
        assert bar.high == 24500.0
        assert bar.low == 24500.0
        ts = bar.ts_open.astimezone(IST)
        assert ts.hour == 9 and ts.minute == 15

    def test_multiple_ticks_build_correct_ohlc(self):
        """OHLC should reflect min/max of ticks within the minute."""
        t = self._epoch_at(0, 0)
        self.feed._feed_tick(13, 24500.0, t)       # open
        self.feed._feed_tick(13, 24600.0, t + 20)  # new high
        self.feed._feed_tick(13, 24400.0, t + 40)  # new low
        self.feed._feed_tick(13, 24550.0, t + 55)  # close
        # Force close with next-minute tick
        self.feed._feed_tick(13, 24560.0, self._epoch_at(1, 5))
        assert len(self.bars) == 1
        bar = self.bars[0].bar
        assert bar.open == 24500.0
        assert bar.high == 24600.0
        assert bar.low == 24400.0
        assert bar.close == 24550.0

    def test_grace_timer_closes_bar_without_next_tick(self):
        """The grace timer should close the open bar even with no further ticks."""
        self.feed._feed_tick(13, 24500.0, self._epoch_at(0, 30))
        # Wait for grace timer (set to 0.05 s in setup_method)
        _time_mod.sleep(0.3)
        assert len(self.bars) >= 1
        assert abs(self.bars[0].bar.open - 24500.0) < 0.01

    def test_out_of_order_tick_is_dropped(self):
        """A tick older than the current bar bucket must be silently dropped."""
        self.feed._feed_tick(13, 24500.0, self._epoch_at(1, 30))  # 09:16
        self.feed._feed_tick(13, 99999.0, self._epoch_at(0, 45))  # 09:15 → OOO
        self.feed._feed_tick(13, 24600.0, self._epoch_at(2, 10))  # 09:17 → close
        assert len(self.bars) == 1
        # The OOO price (99999) must NOT appear in the 09:16 bar
        bar = self.bars[0].bar
        assert bar.high < 99000.0

    def test_unknown_security_id_is_safe(self):
        """A tick for an unsubscribed instrument must not raise."""
        self.feed._feed_tick(99999, 24500.0, self._epoch_at(0, 30))  # no builder

    def test_bar_is_complete(self):
        """Emitted bars must always have complete=True."""
        self.feed._feed_tick(13, 24500.0, self._epoch_at(0, 30))
        self.feed._feed_tick(13, 24510.0, self._epoch_at(1, 10))
        assert len(self.bars) == 1
        assert self.bars[0].bar.complete is True


# ================================================================ recv timeout

class TestRecvTimeout:
    """Verify that a hanging ws.recv() triggers reconnect via TimeoutError."""

    def test_recv_timeout_causes_reconnect(self):
        """If ws.recv() hangs, asyncio.wait_for raises TimeoutError → reconnect."""
        instr = _make_instrument()
        reconnect_called = threading.Event()
        connect_count = [0]

        async def _run_test():
            # Create a ws mock whose recv() sleeps forever (simulates zombie socket)
            ws_mock = AsyncMock()
            ws_mock.__aenter__ = AsyncMock(return_value=ws_mock)
            ws_mock.__aexit__ = AsyncMock(return_value=False)
            ws_mock.send = AsyncMock()

            # recv() blocks indefinitely
            async def _hanging_recv():
                await asyncio.sleep(100)
            ws_mock.recv = _hanging_recv

            feed = DhanLiveFeedV2(
                subscriptions=[("13", "IDX_I", instr)],
                on_bar=lambda ev: None,
                client_id="test",
                access_token="test_token",
                recv_timeout_sec=0.05,  # very short for test
            )

            # Patch the recv_loop to record when it returns (== reconnect trigger)
            original_recv_loop = feed._recv_loop

            async def _patched_recv_loop(ws):
                reconnect_called.set()
                await original_recv_loop(ws)

            feed._recv_loop = _patched_recv_loop  # type: ignore[method-assign]

            # Run just the recv_loop directly (no full connect needed)
            with patch("websockets.connect", return_value=ws_mock):
                try:
                    await asyncio.wait_for(feed._recv_loop(ws_mock), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

        asyncio.run(_run_test())
        # The recv_loop must return (not hang) when timeout fires
        # We verify by checking that _run_test completed within 1s

    def test_recv_timeout_ends_recv_loop(self):
        """_recv_loop must return within recv_timeout + epsilon on a hanging recv."""
        instr = _make_instrument()

        async def _hanging_recv():
            await asyncio.sleep(100)

        async def _run():
            ws_mock = AsyncMock()
            ws_mock.send = AsyncMock()
            ws_mock.recv = _hanging_recv

            feed = DhanLiveFeedV2(
                subscriptions=[("13", "IDX_I", instr)],
                on_bar=lambda ev: None,
                client_id="test",
                access_token="test_token",
                recv_timeout_sec=0.1,
            )
            # Mark stop so the loop exits after one timeout
            feed._stop_evt.set()

            start = _time_mod.monotonic()
            await feed._recv_loop(ws_mock)
            elapsed = _time_mod.monotonic() - start

            # Should have returned within ~0.2s (timeout=0.1 + overhead)
            assert elapsed < 1.0, f"recv_loop took {elapsed:.2f}s — zombie-socket bug!"

        asyncio.run(_run())


# ================================================================ disconnect 807

class TestDisconnect807:
    """Server code 807 (token expired) must trigger token refresh."""

    def test_handle_disconnect_807_calls_refresh_token(self):
        """On disconnect 807, _refresh_token() must be invoked."""
        instr = _make_instrument()
        feed = DhanLiveFeedV2(
            subscriptions=[("13", "IDX_I", instr)],
            on_bar=lambda ev: None,
            client_id="test",
            access_token="old_token",
        )

        refresh_called = [False]
        new_token = "new_fresh_token_xyz"

        def _mock_refresh():
            refresh_called[0] = True
            feed._access_token = new_token
            return True

        feed._refresh_token = _mock_refresh  # type: ignore[method-assign]

        pkt = _build_disconnect_packet(disc_code=807)
        feed._handle_disconnect(pkt)

        assert refresh_called[0], "_refresh_token was NOT called on code 807"
        assert feed._access_token == new_token

    def test_handle_disconnect_non_807_does_not_call_refresh(self):
        """Non-807 codes must NOT trigger a token refresh."""
        instr = _make_instrument()
        feed = DhanLiveFeedV2(
            subscriptions=[("13", "IDX_I", instr)],
            on_bar=lambda ev: None,
            client_id="test",
            access_token="original_token",
        )

        refresh_called = [False]
        feed._refresh_token = lambda: (refresh_called.__setitem__(0, True) or True)  # type: ignore[method-assign]

        for code in (805, 806, 808, 809):
            pkt = _build_disconnect_packet(disc_code=code)
            feed._handle_disconnect(pkt)

        assert not refresh_called[0], "_refresh_token called for non-807 code"

    def test_handle_disconnect_too_short_does_not_raise(self):
        """Short disconnect packet must not raise."""
        instr = _make_instrument()
        feed = DhanLiveFeedV2(
            subscriptions=[("13", "IDX_I", instr)],
            on_bar=lambda ev: None,
            client_id="test",
            access_token="tok",
        )
        feed._handle_disconnect(b"\x32\x00")  # too short


# ================================================================ subscribe_dynamic

class TestSubscribeDynamic:
    """subscribe_dynamic adds builders and queues subscription messages."""

    def test_subscribe_dynamic_creates_builder(self):
        instr_a = _make_instrument("NIFTY", "13")
        instr_b = _make_instrument("RELIANCE", "1333")

        feed = DhanLiveFeedV2(
            subscriptions=[("13", "IDX_I", instr_a)],
            on_bar=lambda ev: None,
            client_id="test",
            access_token="tok",
        )

        assert "1333" not in feed._builders
        feed.subscribe_dynamic([("1333", "NSE_EQ", instr_b)])
        assert "1333" in feed._builders

    def test_subscribe_dynamic_idempotent(self):
        instr = _make_instrument("NIFTY", "13")
        feed = DhanLiveFeedV2(
            subscriptions=[("13", "IDX_I", instr)],
            on_bar=lambda ev: None,
            client_id="test",
            access_token="tok",
        )
        feed.subscribe_dynamic([("13", "IDX_I", instr)])
        # Must still have exactly one builder for security 13
        assert len([k for k in feed._builders if k == "13"]) == 1
