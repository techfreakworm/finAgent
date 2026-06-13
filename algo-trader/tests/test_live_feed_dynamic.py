"""Tests for LiveBarFeed.subscribe_dynamic().

All tests run without network access or a real WebSocket connection.
The SDK's MarketFeed is mocked at the feed object level.

Design constraints:
  - No order-placement imports.
  - All datetimes tz-aware Asia/Kolkata (not exercised here — no ticks sent).
  - Tests exercise the subscribe_dynamic contract:
      1. Not-started feed: instruments queued in _builders / _subs / _instr_by_sec.
      2. Running feed: SDK subscribe_symbols() is called with correct tuples.
      3. Duplicate subscriptions: no re-subscription or duplicate builder.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from algotrader.core import Instrument, Segment
from algotrader.data.live_feed import (
    LiveBarFeed,
    _BarBuilder,
    _SEG_TO_INT,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_instrument(symbol: str, security_id: str, seg: Segment) -> Instrument:
    return Instrument(
        symbol=symbol,
        security_id=security_id,
        segment=seg,
        tick_size=0.05,
    )


NIFTY_OPT_CE = _make_instrument("NIFTY-Jun2026-24350-CE", "50643", Segment.NSE_FNO)
NIFTY_OPT_PE = _make_instrument("NIFTY-Jun2026-24350-PE", "50644", Segment.NSE_FNO)
RELIANCE     = _make_instrument("RELIANCE", "2885", Segment.NSE_EQ)

_INITIAL_SUBS = [("2885", "NSE_EQ", RELIANCE)]


def _make_feed(running: bool = False) -> tuple[LiveBarFeed, MagicMock]:
    """Construct a LiveBarFeed bypassing the dhanhq import guard.

    Returns the feed and a mock on_bar callback.
    """
    on_bar = MagicMock()

    # Patch _DHANHQ_AVAILABLE so __init__ does not raise RuntimeError
    with patch("algotrader.data.live_feed._DHANHQ_AVAILABLE", True):
        feed = LiveBarFeed(
            subscriptions=_INITIAL_SUBS,
            on_bar=on_bar,
            client_id="test_client",
            access_token="test_token",
        )

    if running:
        # Simulate a running feed without a real WS connection
        feed._running = True
        mock_sdk_feed = MagicMock()
        feed._feed = mock_sdk_feed
    else:
        feed._running = False
        feed._feed = None

    return feed, on_bar


# ---------------------------------------------------------------------------
# subscribe_dynamic on a NOT-STARTED feed
# ---------------------------------------------------------------------------

class TestSubscribeDynamicNotStarted:
    """Before start() is called, new subscriptions are queued."""

    def test_new_builder_created(self):
        feed, _ = _make_feed(running=False)
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        assert "50643" in feed._builders
        assert isinstance(feed._builders["50643"], _BarBuilder)

    def test_instr_by_sec_updated(self):
        feed, _ = _make_feed(running=False)
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        assert "50643" in feed._instr_by_sec
        sec_id, seg, instr = feed._instr_by_sec["50643"]
        assert instr is NIFTY_OPT_CE
        assert seg == "NSE_FNO"

    def test_subs_list_updated(self):
        feed, _ = _make_feed(running=False)
        initial_len = len(feed._subs)
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        assert len(feed._subs) == initial_len + 1
        added = feed._subs[-1]
        assert added[0] == "50643"
        assert added[2] is NIFTY_OPT_CE

    def test_multiple_new_instruments(self):
        feed, _ = _make_feed(running=False)
        feed.subscribe_dynamic([
            ("50643", "NSE_FNO", NIFTY_OPT_CE),
            ("50644", "NSE_FNO", NIFTY_OPT_PE),
        ])
        assert "50643" in feed._builders
        assert "50644" in feed._builders
        assert len(feed._subs) == len(_INITIAL_SUBS) + 2

    def test_no_sdk_call_when_not_running(self):
        """subscribe_symbols() must NOT be called when the feed is stopped."""
        feed, _ = _make_feed(running=False)
        mock_sdk = MagicMock()
        feed._feed = mock_sdk  # feed object exists but _running=False
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        mock_sdk.subscribe_symbols.assert_not_called()

    def test_duplicate_not_added(self):
        """Subscribing an already-known security_id is idempotent."""
        feed, _ = _make_feed(running=False)
        # RELIANCE is in _INITIAL_SUBS so already in _builders
        initial_len = len(feed._subs)
        feed.subscribe_dynamic([("2885", "NSE_EQ", RELIANCE)])
        assert len(feed._subs) == initial_len   # unchanged
        assert len(feed._builders) == 1          # still just RELIANCE


# ---------------------------------------------------------------------------
# subscribe_dynamic on a RUNNING (mock) feed
# ---------------------------------------------------------------------------

class TestSubscribeDynamicRunning:
    """While the feed is running, subscribe_dynamic triggers SDK subscribe."""

    def test_sdk_subscribe_called(self):
        """subscribe_symbols() is called on the underlying SDK feed object."""
        feed, _ = _make_feed(running=True)
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        feed._feed.subscribe_symbols.assert_called_once()

    def test_sdk_subscribe_called_with_correct_tuple(self):
        """The SDK receives (seg_int, sec_id_str, 17) tuples."""
        feed, _ = _make_feed(running=True)
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        call_args = feed._feed.subscribe_symbols.call_args
        passed_tuples = call_args[0][0]   # first positional arg
        assert len(passed_tuples) == 1
        seg_int, sec_id, mode = passed_tuples[0]
        assert seg_int == _SEG_TO_INT["NSE_FNO"]   # 2
        assert sec_id == "50643"
        assert mode == 17                            # Quote

    def test_sdk_subscribe_multiple_instruments(self):
        """Two new instruments yield one subscribe_symbols call with 2 tuples."""
        feed, _ = _make_feed(running=True)
        feed.subscribe_dynamic([
            ("50643", "NSE_FNO", NIFTY_OPT_CE),
            ("50644", "NSE_FNO", NIFTY_OPT_PE),
        ])
        feed._feed.subscribe_symbols.assert_called_once()
        call_args = feed._feed.subscribe_symbols.call_args
        passed_tuples = call_args[0][0]
        assert len(passed_tuples) == 2

    def test_builder_created_even_when_running(self):
        """_BarBuilder must exist in _builders after a running-feed subscribe."""
        feed, _ = _make_feed(running=True)
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        assert "50643" in feed._builders

    def test_duplicate_on_running_feed_does_not_re_subscribe(self):
        """Calling subscribe_dynamic twice with the same ID is idempotent."""
        feed, _ = _make_feed(running=True)
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        feed._feed.reset_mock()
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        feed._feed.subscribe_symbols.assert_not_called()

    def test_sdk_error_does_not_propagate(self):
        """An exception in subscribe_symbols is caught and logged; no re-raise."""
        feed, _ = _make_feed(running=True)
        feed._feed.subscribe_symbols.side_effect = RuntimeError("WS closed")
        # Should not raise; builder was still inserted before the SDK call
        feed.subscribe_dynamic([("50643", "NSE_FNO", NIFTY_OPT_CE)])
        assert "50643" in feed._builders   # builder was added before SDK call

    def test_initial_subscription_not_re_sent(self):
        """The initially-subscribed RELIANCE is not re-sent to the SDK."""
        feed, _ = _make_feed(running=True)
        # Add a mix: one existing, one new
        feed.subscribe_dynamic([
            ("2885",  "NSE_EQ",  RELIANCE),      # already in _builders
            ("50643", "NSE_FNO", NIFTY_OPT_CE),  # new
        ])
        feed._feed.subscribe_symbols.assert_called_once()
        passed_tuples = feed._feed.subscribe_symbols.call_args[0][0]
        sec_ids = [t[1] for t in passed_tuples]
        assert "50643" in sec_ids
        assert "2885"  not in sec_ids   # RELIANCE already subscribed
