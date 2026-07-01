"""Scenario tests for the event-driven backtest engine and fill semantics.

Tests (per assignment):
  (a) entry fills at next-bar open not signal-bar close
  (b) gap-at-open stop → fill at open + gap_through_stop flag
  (c) intrabar sweep → stop±slip fill
  (d) stop+target same bar → stop wins (pessimistic tie-break)
  (e) square-off at first bar with ts_close ≥ 15:19:30
  (f) no entries generated after 14:44:30
  (g) breaker trip flattens all and blocks re-entry same session
  (h) incomplete bar raises ValueError

ARCHITECTURE §3: fill semantics, §3.5 session lifecycle, §3.4 slippage.
All tests are fixture-driven with no network calls.
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

import pytest

from algotrader.backtest.engine import BacktestEngine, ClockParams, DefaultSlippage
from algotrader.backtest.fills import (
    entry_fill,
    resolve_bar,
    stop_fill,
    target_fill,
)
from algotrader.backtest.costs import DhanCosts
from algotrader.core import (
    Bar,
    ExitReason,
    Instrument,
    IST,
    OrderIntent,
    Position,
    ProductType,
    Rejection,
    RiskSnapshot,
    Segment,
    SessionClock,
    Side,
    SizedOrder,
    Strategy,
)
from algotrader.risk.engine import IntradayRiskEngine, RiskParams
from tests.fixtures.synthetic import NIFTY_FUT_INSTRUMENT, TEST_INSTRUMENT

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SESSION_DATE = date(2024, 3, 4)   # arbitrary Monday

IST_TZ = ZoneInfo("Asia/Kolkata")


def _bar(
    ts_open: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    complete: bool = True,
    instrument: Instrument = TEST_INSTRUMENT,
) -> Bar:
    """Build a 1-min Bar with explicit OHLC for precise fill assertions."""
    return Bar(
        instrument=instrument,
        ts_open=ts_open,
        interval_min=1,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000,
        complete=complete,
    )


def _ts(h: int, m: int, s: int = 0) -> datetime:
    """Return an IST-aware datetime on _SESSION_DATE."""
    return datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day,
                    h, m, s, tzinfo=IST_TZ)


def _make_risk_engine(capital: float = 500_000.0) -> IntradayRiskEngine:
    # Hard floor is set well below capital to avoid immediate breach in tests.
    params = RiskParams(
        capital=capital,
        hard_floor=capital * 0.5,   # 50% of capital — well below for normal tests
        max_daily_loss_pct=0.02,
        per_trade_risk_pct=0.0075,
        max_open_positions=3,
        max_per_symbol=1,
    )
    return IntradayRiskEngine(params)


class _ZeroSlippage:
    """Slippage model that returns exactly zero (for exact price assertions)."""

    def entry_slippage(self, instrument: Instrument, bar_history: Sequence[Bar]) -> float:
        return 0.0

    def exit_slippage(self, instrument: Instrument, bar_history: Sequence[Bar],
                      reason: ExitReason) -> float:
        return 0.0


class _FixedSlippage:
    """Slippage model returning a fixed scalar (to test slippage direction)."""

    def __init__(self, amount: float) -> None:
        self._amount = amount

    def entry_slippage(self, instrument: Instrument, bar_history: Sequence[Bar]) -> float:
        return self._amount

    def exit_slippage(self, instrument: Instrument, bar_history: Sequence[Bar],
                      reason: ExitReason) -> float:
        return self._amount


# ---------------------------------------------------------------------------
# Minimal BarSource for engine tests
# ---------------------------------------------------------------------------

class _ListBarSource:
    """Adapts a list of bars into the BarSource protocol."""

    def __init__(self, sessions: dict[date, list[Bar]]) -> None:
        self._sessions = sessions

    def sessions(self):
        for d in sorted(self._sessions):
            yield d, self._sessions[d]


# ---------------------------------------------------------------------------
# Test strategy
# ---------------------------------------------------------------------------

class _TestStrategy(Strategy):
    """Trivial strategy: emits pre-baked intents at specified bar indices.

    ``intents_at`` maps 0-based bar index (within the session) to the list
    of OrderIntents to return from on_bar.

    All other bars return an empty list.  manage() always returns (None, False)
    unless ``exit_at`` bar index set.
    """
    strategy_id = "test_strat"
    warmup_bars = 0
    warmup_days = 0

    def __init__(
        self,
        intents_at: dict[int, list[OrderIntent]] | None = None,
        exit_at: dict[int, bool] | None = None,
    ) -> None:
        self._intents_at: dict[int, list[OrderIntent]] = intents_at or {}
        self._exit_at: dict[int, bool] = exit_at or {}
        self._bar_idx = 0

    def on_bar(self, ctx, bar: Bar) -> list[OrderIntent]:
        idx = self._bar_idx
        self._bar_idx += 1
        return list(self._intents_at.get(idx, []))

    def manage(self, ctx, bar: Bar, position: Position):
        idx = self._bar_idx - 1   # same as the on_bar index already incremented
        exit_now = self._exit_at.get(idx, False)
        return None, exit_now


def _buy_intent(
    ref_price: float,
    stop_price: float,
    target_price: float | None = None,
    instrument: Instrument = TEST_INSTRUMENT,
) -> OrderIntent:
    return OrderIntent(
        strategy_id="test_strat",
        instrument=instrument,
        side=Side.BUY,
        ref_price=ref_price,
        stop_price=stop_price,
        target_price=target_price,
    )


def _sell_intent(
    ref_price: float,
    stop_price: float,
    target_price: float | None = None,
    instrument: Instrument = TEST_INSTRUMENT,
) -> OrderIntent:
    return OrderIntent(
        strategy_id="test_strat",
        instrument=instrument,
        side=Side.SELL,
        ref_price=ref_price,
        stop_price=stop_price,
        target_price=target_price,
    )


def _run_engine(
    bars: list[Bar],
    strategy: _TestStrategy,
    slippage=None,
    capital: float = 500_000.0,
    clock_params: ClockParams | None = None,
):
    """Build and run engine, return (BacktestResult, risk_engine)."""
    risk = _make_risk_engine(capital)
    engine = BacktestEngine(
        strategies=[strategy],
        risk_engine=risk,
        cost_model=DhanCosts(),
        slippage_model=slippage or _ZeroSlippage(),
        clock_params=clock_params,
    )
    src = _ListBarSource({_SESSION_DATE: bars})
    result = engine.run(src)
    return result, risk


# ===========================================================================
# (a) Entry fills at next-bar open, NOT signal-bar close
# ===========================================================================

class TestEntryFillNextBar:

    def test_buy_entry_fills_at_next_bar_open_zero_slip(self):
        """Signal on bar 0 → position entry_price == bar1.open (zero slippage)."""
        bar0 = _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0)   # signal bar
        bar1 = _bar(_ts(9, 16), 104.0, 110.0, 102.0, 108.0)  # fill bar
        bar2_stop = _bar(_ts(9, 17), 90.0, 91.0, 89.0, 90.0)  # gaps below stop=95

        # Long signal: ref=103, stop=95
        intent = _buy_intent(ref_price=103.0, stop_price=95.0)
        # Fresh strategy instance for this 3-bar run
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine([bar0, bar1, bar2_stop], strat)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        # Fill must be bar1.open (bar after signal bar), NOT bar0.close.
        assert pos.entry_price == bar1.open, (
            f"Expected entry at bar1.open={bar1.open}, got {pos.entry_price}"
        )
        assert pos.entry_price != bar0.close, "Entry must not equal signal-bar close"

    def test_buy_entry_fills_at_next_bar_open_with_slippage(self):
        """With fixed slippage=2.0, BUY entry = next_bar.open + 2.0."""
        bar0 = _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0)
        bar1 = _bar(_ts(9, 16), 104.0, 110.0, 102.0, 108.0)
        bar2_stop = _bar(_ts(9, 17), 85.0, 86.0, 84.0, 85.0)  # stop hit

        intent = _buy_intent(ref_price=103.0, stop_price=90.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        slip = _FixedSlippage(2.0)
        result, _ = _run_engine([bar0, bar1, bar2_stop], strat, slippage=slip)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        assert pos.entry_price == pytest.approx(bar1.open + 2.0)

    def test_sell_entry_fills_at_next_bar_open_minus_slippage(self):
        """SELL entry = next_bar.open - slippage."""
        bar0 = _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0)
        bar1 = _bar(_ts(9, 16), 104.0, 110.0, 102.0, 108.0)
        bar2_stop = _bar(_ts(9, 17), 120.0, 125.0, 119.0, 122.0)  # gap above stop

        intent = _sell_intent(ref_price=103.0, stop_price=115.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        slip = _FixedSlippage(1.5)
        result, _ = _run_engine([bar0, bar1, bar2_stop], strat, slippage=slip)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        assert pos.entry_price == pytest.approx(bar1.open - 1.5)

    def test_pure_entry_fill_function(self):
        """Pure function: entry_fill(bar, side, slippage)."""
        bar = _bar(_ts(9, 16), 200.0, 210.0, 195.0, 205.0)
        assert entry_fill(bar, Side.BUY, 0.5) == pytest.approx(200.5)
        assert entry_fill(bar, Side.SELL, 0.5) == pytest.approx(199.5)
        assert entry_fill(bar, Side.BUY, 0.0) == pytest.approx(200.0)


# ===========================================================================
# (b) Gap-at-open stop → fill at open + gap_through_stop flag
# ===========================================================================

class TestGapThroughStop:

    def test_long_stop_gap_at_open(self):
        """Bar opens BELOW stop for a long position → gap fill at open, flag=True."""
        bar_entry = _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0)   # signal bar
        bar_fill  = _bar(_ts(9, 16), 102.0, 106.0, 101.0, 104.0)  # entry fill bar
        # bar opens below stop=99
        bar_gap   = _bar(_ts(9, 17), 95.0,  96.0,  94.0,  95.5)   # open=95 < stop=99

        intent = _buy_intent(ref_price=103.0, stop_price=99.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine([bar_entry, bar_fill, bar_gap], strat)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        assert pos.exit_reason is ExitReason.STOP
        assert pos.gap_through_stop is True
        # Fill at gap-open, NOT at stop price
        assert pos.exit_price == pytest.approx(bar_gap.open)
        assert pos.exit_price < pos.stop_price

    def test_short_stop_gap_at_open(self):
        """Bar opens ABOVE stop for a short position → gap fill at open, flag=True."""
        bar_entry = _bar(_ts(9, 15), 100.0, 105.0, 98.0, 97.0)    # signal bar close
        bar_fill  = _bar(_ts(9, 16), 97.0, 98.0, 95.0, 96.0)       # entry fill
        bar_gap   = _bar(_ts(9, 17), 108.0, 112.0, 107.0, 110.0)   # opens above stop=105

        intent = _sell_intent(ref_price=97.0, stop_price=105.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine([bar_entry, bar_fill, bar_gap], strat)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        assert pos.exit_reason is ExitReason.STOP
        assert pos.gap_through_stop is True
        assert pos.exit_price == pytest.approx(bar_gap.open)
        assert pos.exit_price > pos.stop_price

    def test_pure_stop_fill_gap_branch(self):
        """Pure function: gap-through sets flag, fills at open."""
        pos = Position(
            position_id="p1", strategy_id="s", instrument=TEST_INSTRUMENT,
            side=Side.BUY, quantity=1, entry_price=100.0,
            entry_ts=_ts(9, 16), stop_price=95.0, target_price=None,
            session_date=_SESSION_DATE,
        )
        # Bar opens below stop
        bar = _bar(_ts(9, 17), 90.0, 91.0, 89.5, 90.5)
        price, gap = stop_fill(bar, pos, slippage=1.0)
        assert gap is True
        assert price == pytest.approx(90.0)  # fill at open, NO slippage on gap

    def test_pure_stop_fill_gap_at_exact_stop(self):
        """Open exactly at stop price → gap-through branch (open <= stop)."""
        pos = Position(
            position_id="p1", strategy_id="s", instrument=TEST_INSTRUMENT,
            side=Side.BUY, quantity=1, entry_price=100.0,
            entry_ts=_ts(9, 16), stop_price=95.0, target_price=None,
            session_date=_SESSION_DATE,
        )
        bar = _bar(_ts(9, 17), 95.0, 96.0, 94.0, 95.5)  # open == stop
        price, gap = stop_fill(bar, pos, slippage=0.5)
        assert gap is True
        assert price == pytest.approx(95.0)


# ===========================================================================
# (c) Intrabar sweep → stop ± slip fill
# ===========================================================================

class TestIntradayStopSweep:

    def test_long_intrabar_stop(self):
        """Bar opens safe but low hits stop → fill at stop - slippage."""
        bar_entry = _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0)
        bar_fill  = _bar(_ts(9, 16), 102.0, 106.0, 101.0, 104.0)  # entry fill
        # open=100 > stop=99 (safe open), but low=97 < stop=99
        bar_sweep = _bar(_ts(9, 17), 100.0, 101.0, 97.0, 99.5)

        stop_px = 99.0
        intent = _buy_intent(ref_price=103.0, stop_price=stop_px)
        strat = _TestStrategy(intents_at={0: [intent]})
        slip = _FixedSlippage(0.5)
        result, _ = _run_engine([bar_entry, bar_fill, bar_sweep], strat, slippage=slip)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        assert pos.exit_reason is ExitReason.STOP
        assert pos.gap_through_stop is False
        # Fill = stop - slippage (adverse for long)
        assert pos.exit_price == pytest.approx(stop_px - 0.5)

    def test_short_intrabar_stop(self):
        """Bar opens safe but high hits stop → fill at stop + slippage."""
        bar_entry = _bar(_ts(9, 15), 100.0, 102.0, 96.0, 97.0)
        bar_fill  = _bar(_ts(9, 16), 97.0, 98.0, 96.0, 96.5)
        # open=96 < stop=105 (safe), high=106 > stop
        bar_sweep = _bar(_ts(9, 17), 96.0, 106.0, 95.0, 100.0)

        stop_px = 105.0
        intent = _sell_intent(ref_price=97.0, stop_price=stop_px)
        strat = _TestStrategy(intents_at={0: [intent]})
        slip = _FixedSlippage(0.5)
        result, _ = _run_engine([bar_entry, bar_fill, bar_sweep], strat, slippage=slip)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        assert pos.exit_reason is ExitReason.STOP
        assert pos.gap_through_stop is False
        assert pos.exit_price == pytest.approx(stop_px + 0.5)

    def test_pure_stop_fill_intrabar_branch(self):
        """Pure function: intrabar stop fills at stop ± slippage, gap=False."""
        pos = Position(
            position_id="p1", strategy_id="s", instrument=TEST_INSTRUMENT,
            side=Side.BUY, quantity=1, entry_price=100.0,
            entry_ts=_ts(9, 16), stop_price=95.0, target_price=None,
            session_date=_SESSION_DATE,
        )
        # Open above stop, but low dips below
        bar = _bar(_ts(9, 17), 97.0, 98.0, 93.0, 96.0)
        price, gap = stop_fill(bar, pos, slippage=1.0)
        assert gap is False
        assert price == pytest.approx(95.0 - 1.0)   # stop - slip


# ===========================================================================
# (d) Stop + target in same bar → stop wins
# ===========================================================================

class TestStopWinsOverTarget:

    def test_both_in_range_stop_wins(self):
        """When bar range covers BOTH stop and target, position exits as STOP."""
        bar_entry = _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0)
        bar_fill  = _bar(_ts(9, 16), 103.0, 108.0, 102.0, 106.0)
        # bar range 95..115 covers stop=98 AND target=110 simultaneously
        bar_both  = _bar(_ts(9, 17), 100.0, 115.0, 95.0, 107.0)

        intent = _buy_intent(ref_price=103.0, stop_price=98.0, target_price=110.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine([bar_entry, bar_fill, bar_both], strat)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        assert pos.exit_reason is ExitReason.STOP, (
            f"Expected STOP (pessimistic tie-break), got {pos.exit_reason}"
        )

    def test_resolve_bar_stop_beats_target(self):
        """Pure function resolve_bar: stop wins when both in range."""
        pos = Position(
            position_id="p1", strategy_id="s", instrument=TEST_INSTRUMENT,
            side=Side.BUY, quantity=1, entry_price=100.0,
            entry_ts=_ts(9, 16), stop_price=95.0, target_price=110.0,
            session_date=_SESSION_DATE,
        )
        bar = _bar(_ts(9, 17), 100.0, 115.0, 93.0, 107.0)
        assert resolve_bar(bar, pos) is ExitReason.STOP

    def test_resolve_bar_only_target(self):
        """resolve_bar returns TARGET when only target is in range."""
        pos = Position(
            position_id="p1", strategy_id="s", instrument=TEST_INSTRUMENT,
            side=Side.BUY, quantity=1, entry_price=100.0,
            entry_ts=_ts(9, 16), stop_price=90.0, target_price=110.0,
            session_date=_SESSION_DATE,
        )
        bar = _bar(_ts(9, 17), 100.0, 112.0, 97.0, 108.0)
        assert resolve_bar(bar, pos) is ExitReason.TARGET

    def test_resolve_bar_neither(self):
        """resolve_bar returns None when bar doesn't touch stop or target."""
        pos = Position(
            position_id="p1", strategy_id="s", instrument=TEST_INSTRUMENT,
            side=Side.BUY, quantity=1, entry_price=100.0,
            entry_ts=_ts(9, 16), stop_price=90.0, target_price=115.0,
            session_date=_SESSION_DATE,
        )
        bar = _bar(_ts(9, 17), 100.0, 108.0, 96.0, 104.0)
        assert resolve_bar(bar, pos) is None

    def test_target_fill_intrabar_buy(self):
        """target_fill for long intrabar: fill at target - slippage (conservative)."""
        pos = Position(
            position_id="p1", strategy_id="s", instrument=TEST_INSTRUMENT,
            side=Side.BUY, quantity=1, entry_price=100.0,
            entry_ts=_ts(9, 16), stop_price=95.0, target_price=110.0,
            session_date=_SESSION_DATE,
        )
        bar = _bar(_ts(9, 17), 106.0, 112.0, 105.0, 109.0)  # high > target, open < target
        price = target_fill(bar, pos, slippage=0.5)
        assert price == pytest.approx(110.0 - 0.5)

    def test_target_fill_gap_buy(self):
        """target_fill for long gap: fill at bar.open (better than target)."""
        pos = Position(
            position_id="p1", strategy_id="s", instrument=TEST_INSTRUMENT,
            side=Side.BUY, quantity=1, entry_price=100.0,
            entry_ts=_ts(9, 16), stop_price=95.0, target_price=110.0,
            session_date=_SESSION_DATE,
        )
        bar = _bar(_ts(9, 17), 115.0, 120.0, 114.0, 117.0)  # gaps above target
        price = target_fill(bar, pos, slippage=0.5)
        assert price == pytest.approx(115.0)  # filled at open, no extra slippage

    def test_target_fill_intrabar_sell(self):
        """target_fill for short intrabar: fill at target + slippage (conservative)."""
        pos = Position(
            position_id="p1", strategy_id="s", instrument=TEST_INSTRUMENT,
            side=Side.SELL, quantity=1, entry_price=100.0,
            entry_ts=_ts(9, 16), stop_price=110.0, target_price=90.0,
            session_date=_SESSION_DATE,
        )
        bar = _bar(_ts(9, 17), 94.0, 95.0, 88.0, 92.0)  # low < target, open > target
        price = target_fill(bar, pos, slippage=0.5)
        assert price == pytest.approx(90.0 + 0.5)


# ===========================================================================
# (e) Square-off at first bar with ts_close ≥ 15:19:30
# ===========================================================================

class TestSquareOff:

    def _session_with_position(self, extra_bars_before_sq: int = 0):
        """Build a session: signal at bar 0, fill at bar 1, then bars, then sq-off bar."""
        bars = []
        t = _ts(9, 15)
        # Signal bar (bar 0)
        bars.append(_bar(t, 100.0, 105.0, 98.0, 103.0))
        t += timedelta(minutes=1)
        # Fill bar (bar 1)
        bars.append(_bar(t, 104.0, 108.0, 103.0, 106.0))

        # Filler bars between fill and square-off
        for _ in range(extra_bars_before_sq):
            t += timedelta(minutes=1)
            bars.append(_bar(t, 106.0, 107.0, 105.0, 106.5))

        # The square-off bar: ts_open = 15:19, ts_close = 15:20 ≥ 15:19:30
        sq_open_ts = _ts(15, 19)
        sq_bar = _bar(sq_open_ts, 108.0, 109.0, 107.0, 108.5)
        bars.append(sq_bar)
        return bars, sq_bar

    def test_position_closed_at_square_off(self):
        """Position not stopped/targeted → closed via SQUARE_OFF at 15:19 bar."""
        bars, sq_bar = self._session_with_position()
        intent = _buy_intent(ref_price=103.0, stop_price=90.0, target_price=200.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine(bars, strat)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        assert pos.exit_reason is ExitReason.SQUARE_OFF
        # Fill at sq_bar.open (zero slippage → exact open)
        assert pos.exit_price == pytest.approx(sq_bar.open)

    def test_square_off_with_slippage(self):
        """SQUARE_OFF exit price = sq_bar.open - slippage for a long."""
        bars, sq_bar = self._session_with_position()
        intent = _buy_intent(ref_price=103.0, stop_price=90.0, target_price=200.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        slip = _FixedSlippage(1.0)
        result, _ = _run_engine(bars, strat, slippage=slip)

        pos = result.trades[0].position
        assert pos.exit_reason is ExitReason.SQUARE_OFF
        assert pos.exit_price == pytest.approx(sq_bar.open - 1.0)

    def test_no_new_entries_on_square_off_bar(self):
        """Pending entries at 15:19 bar are cancelled (square-off takes priority)."""
        # Signal at bar 0, fill would be at sq_bar but sq_bar IS the sq-off bar.
        bars = [
            _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0),  # signal bar
            _bar(_ts(15, 19), 104.0, 108.0, 103.0, 106.0),  # sq-off bar
        ]
        intent = _buy_intent(ref_price=103.0, stop_price=90.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine(bars, strat)

        # Intent was collected from bar 0, but fill would be at the sq-off bar.
        # sq-off bar triggers SQUARE_OFF logic FIRST → entry cancelled.
        assert len(result.trades) == 0, (
            "Entry at sq-off bar must be cancelled — square-off has priority"
        )

    def test_ts_close_determines_sq_off_bar(self):
        """ts_close ≥ 15:19:30 check: 15:18 bar (ts_close=15:19) is NOT sq-off."""
        # Bar at 15:18 has ts_close=15:19:00, which is < 15:19:30 → NOT sq-off.
        # Bar at 15:19 has ts_close=15:20:00, which is ≥ 15:19:30 → sq-off.
        bars = []
        # signal + fill
        bars.append(_bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0))
        bars.append(_bar(_ts(9, 16), 104.0, 108.0, 103.0, 106.0))
        # 15:18 bar — NOT sq-off
        bars.append(_bar(_ts(15, 18), 106.0, 107.0, 105.0, 106.5))
        # 15:19 bar — sq-off
        sq_bar = _bar(_ts(15, 19), 107.0, 108.0, 106.5, 107.5)
        bars.append(sq_bar)

        intent = _buy_intent(ref_price=103.0, stop_price=90.0, target_price=200.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine(bars, strat)

        assert len(result.trades) == 1
        pos = result.trades[0].position
        assert pos.exit_reason is ExitReason.SQUARE_OFF
        assert pos.exit_price == pytest.approx(sq_bar.open)


# ===========================================================================
# (f) No entries after 14:44:30
# ===========================================================================

class TestEntryClockGate:

    def test_no_entry_when_bar_ts_close_at_or_after_1444(self):
        """on_bar at a bar whose ts_close = 14:45:00 ≥ 14:44:30 → no entry."""
        # ts_open=14:44, ts_close=14:45 (14:45 >= 14:44:30) → can_enter=False
        bar_signal = _bar(_ts(14, 44), 100.0, 105.0, 98.0, 103.0)
        bar_fill   = _bar(_ts(14, 45), 104.0, 108.0, 103.0, 106.0)

        intent = _buy_intent(ref_price=103.0, stop_price=90.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine([bar_signal, bar_fill], strat)

        assert len(result.trades) == 0
        assert len(result.sessions[0].rejections) == 0, (
            "Intents after cutoff should be silently dropped, not rejected"
        )

    def test_entry_allowed_just_before_1444(self):
        """on_bar at ts_close = 14:44:00 < 14:44:30 → entry IS allowed."""
        # ts_open=14:43, ts_close=14:44:00 < 14:44:30 → can_enter=True
        bar_signal = _bar(_ts(14, 43), 100.0, 105.0, 98.0, 103.0)
        bar_fill   = _bar(_ts(14, 44), 104.0, 108.0, 103.0, 106.0)
        # Stop bar to close position
        bar_stop   = _bar(_ts(14, 45), 85.0, 86.0, 84.0, 85.0)

        intent = _buy_intent(ref_price=103.0, stop_price=90.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine([bar_signal, bar_fill, bar_stop], strat)

        assert len(result.trades) == 1, "Entry at 14:43 bar should be allowed"
        pos = result.trades[0].position
        assert pos.entry_price == pytest.approx(bar_fill.open)

    def test_no_entry_at_exactly_1444_30(self):
        """Bar with ts_open=14:44:30 → ts_close=14:45:30 ≥ 14:44:30 → no entry."""
        ts_open = datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day,
                           14, 44, 30, tzinfo=IST_TZ)
        bar_signal = _bar(ts_open, 100.0, 105.0, 98.0, 103.0)
        bar_fill   = _bar(_ts(14, 46), 104.0, 108.0, 103.0, 106.0)

        intent = _buy_intent(ref_price=103.0, stop_price=90.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine([bar_signal, bar_fill], strat)

        assert len(result.trades) == 0


# ===========================================================================
# (g) Breaker trip flattens all and blocks re-entry same session
# ===========================================================================

class TestBreakerTrip:

    def _session_with_big_loss(self):
        """Set up a session where a large gap-stop loss trips the 2% breaker.

        Capital ₹100_000. Breaker at 2% = ₹2_000 loss.
        Position size from risk engine: 0.75% × 100_000 = 750 risk budget.
        stop_dist = 10; qty = 750 / 10 = 75.
        Gap loss per share = (105.0 open) - (95.0 gap) = 10 (beyond stop),
        Actually: stop=95, entry=105 (approx), gap to 80 means loss = 105-80 = 25 per share.
        Total loss = 25 × 75 = 1875 < 2000.

        Let's use larger gap: entry≈105, gap to 60: loss = 45 × 75 = 3375 > 2000.
        """
        bars = []
        # bar 0: signal bar → intent at bar 0
        bars.append(_bar(_ts(9, 15), 100.0, 106.0, 99.0, 105.0))
        # bar 1: entry fill at open=105
        bars.append(_bar(_ts(9, 16), 105.0, 108.0, 104.0, 107.0))
        # bar 2: gap far below stop → triggers stop, loss > 2%
        bars.append(_bar(_ts(9, 17), 60.0, 62.0, 59.0, 61.0))
        # bar 3: new signal would be here — breaker should block it
        bars.append(_bar(_ts(9, 18), 61.0, 62.0, 60.0, 61.5))
        return bars

    def test_breaker_trip_flattens_and_blocks(self):
        """After breaker trips, open positions exit and new intents are rejected.

        Scenario:
          bar 0: signal → BUY, ref=105, stop=95
          bar 1: entry fill at open=105
          bar 2: gap to 60 (below stop=95) → stop triggered, loss = (105-60)*75 = 3375
          bar 3: breaker already tripped → no new entries

        With capital=100_000, 2% breaker threshold = -2_000.  Loss of 3_375 > 2_000.
        """
        bars = self._session_with_big_loss()
        capital = 100_000.0

        intent0 = _buy_intent(ref_price=105.0, stop_price=95.0)
        strat = _TestStrategy(intents_at={0: [intent0]})
        result, risk = _run_engine(bars, strat, capital=capital)

        session = result.sessions[0]
        # Position was closed due to stop (gap) at bar 2.
        assert len(result.trades) >= 1, "Expected at least one trade (the gap-stop exit)"
        # Breaker event should be recorded in the session
        assert len(session.risk_events) >= 1, (
            f"Expected breaker risk event; got: {session.risk_events}"
        )
        assert risk.state.breaker_tripped, "Breaker latch must be set in risk engine state"

    def test_breaker_blocks_re_entry(self):
        """After breaker trip, further intents in same session are blocked."""
        bars = self._session_with_big_loss()
        capital = 100_000.0

        # Signal at bar 0 AND bar 3 (after the gap-loss bar)
        intent0 = _buy_intent(ref_price=105.0, stop_price=95.0)
        intent3 = _buy_intent(ref_price=61.5, stop_price=55.0)

        strat = _TestStrategy(intents_at={0: [intent0], 3: [intent3]})
        result, risk = _run_engine(bars, strat, capital=capital)

        assert risk.state.breaker_tripped, "Breaker must be tripped after large loss"

        # Exactly one trade (the original position exited via gap-stop).
        # The second intent (bar 3) must not result in a second open position.
        assert len(result.trades) == 1, (
            f"Expected only 1 trade, got {len(result.trades)}. "
            "New entries after breaker trip must be blocked."
        )

    def test_breaker_session_reset(self):
        """Breaker resets on new session date (different date)."""
        risk = _make_risk_engine(100_000.0)
        risk.reset_for_session(date(2024, 3, 4))
        risk._state.breaker_tripped = True

        # New date → should reset
        risk.reset_for_session(date(2024, 3, 5))
        assert not risk.state.breaker_tripped


# ===========================================================================
# (h) Incomplete bar raises ValueError
# ===========================================================================

class TestIncompletBarRaises:

    def test_incomplete_bar_raises_value_error(self):
        """Engine must raise ValueError when any bar has complete=False."""
        bar_ok = _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0, complete=True)
        bar_incomplete = _bar(_ts(9, 16), 104.0, 108.0, 103.0, 106.0, complete=False)

        strat = _TestStrategy()
        risk = _make_risk_engine()
        engine = BacktestEngine(
            strategies=[strat],
            risk_engine=risk,
            cost_model=DhanCosts(),
            slippage_model=_ZeroSlippage(),
        )
        src = _ListBarSource({_SESSION_DATE: [bar_ok, bar_incomplete]})

        with pytest.raises(ValueError, match="(?i)(incomplete|complete=False)"):
            engine.run(src)

    def test_all_incomplete_raises(self):
        """Even the first bar being incomplete raises immediately."""
        bar_incomplete = _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0, complete=False)

        strat = _TestStrategy()
        risk = _make_risk_engine()
        engine = BacktestEngine(
            strategies=[strat],
            risk_engine=risk,
            cost_model=DhanCosts(),
            slippage_model=_ZeroSlippage(),
        )
        src = _ListBarSource({_SESSION_DATE: [bar_incomplete]})

        with pytest.raises(ValueError):
            engine.run(src)


# ===========================================================================
# DefaultSlippage unit tests
# ===========================================================================

class TestDefaultSlippage:

    def test_fallback_to_one_tick_when_insufficient_bars(self):
        """Less than 14 bars → ATR undefined → return tick_size."""
        slip = DefaultSlippage()
        bars = [_bar(_ts(9, 15 + i), 100.0, 101.0, 99.0, 100.5) for i in range(5)]
        result = slip.entry_slippage(TEST_INSTRUMENT, bars)
        assert result == TEST_INSTRUMENT.tick_size

    def test_returns_max_of_tick_and_atr_fraction(self):
        """With enough bars, returns max(tick, 0.05*ATR)."""
        from tests.fixtures.synthetic import trend_day
        slip = DefaultSlippage()
        day_bars = trend_day(TEST_INSTRUMENT, _SESSION_DATE, seed=42, bar_sigma=20.0)
        # Use 20 bars (enough for ATR(14))
        bars = day_bars[:20]
        result = slip.entry_slippage(TEST_INSTRUMENT, bars)
        # Should be at least tick_size
        assert result >= TEST_INSTRUMENT.tick_size
        # Should not be NaN
        assert not math.isnan(result)

    def test_slippage_is_causal(self):
        """Adding future bars does NOT change slippage at a given bar index.

        Causal invariant (ARCHITECTURE §3.4): the ATR (and therefore slippage)
        at bar index N depends only on bars 0..N, never on bars N+1+.

        The engine always calls entry_slippage(instrument, bar_history[:-1])
        where bar_history contains exactly the bars up to (not including) the
        fill bar.  This test verifies the underlying ATR invariant: ATR[19]
        computed from bars[:20] must equal ATR[19] computed from bars[:200]
        (appending 180 future bars must not change the value at index 19).

        Previously this test passed the same slice twice — it could not detect
        a regression where future bars caused the ATR result to change.
        """
        from algotrader.strategies.indicators import atr as _atr
        from tests.fixtures.synthetic import trend_day

        day_bars = trend_day(TEST_INSTRUMENT, _SESSION_DATE, seed=99)

        # ATR[19] from a 20-bar prefix
        atr_20 = _atr(day_bars[:20], period=14)
        val_at_20 = atr_20[-1]  # ATR at index 19

        # ATR[19] from a 200-bar sequence — must be identical (causal)
        atr_200 = _atr(day_bars[:200], period=14)
        val_at_20_extended = atr_200[19]

        assert val_at_20 == pytest.approx(val_at_20_extended), (
            "ATR at index 19 must not change when future bars are appended; "
            "look-ahead regression detected"
        )

        # Also verify via entry_slippage using explicit prefix lengths:
        # slippage at a given fill bar must equal slippage from a longer history
        # truncated to the same bar index (engine.py always passes exact prefix).
        slip = DefaultSlippage()
        result_prefix = slip.entry_slippage(TEST_INSTRUMENT, day_bars[:20])
        # Use bars[:20] from the longer array — same bars, same result
        result_same_bars = slip.entry_slippage(TEST_INSTRUMENT, day_bars[:200][:20])
        assert result_prefix == pytest.approx(result_same_bars), (
            "entry_slippage must return the same value for the same bar history "
            "regardless of how that history was obtained"
        )


# ===========================================================================
# MeasuredOptionSlippage unit tests (FG-3 measured fill model)
# ===========================================================================

class TestMeasuredOptionSlippage:
    """FG-3 half-spread option fill model (replaces flat ₹650/leg 2026-07-02).

    Reference numbers (lot=75, premium≈60):
      entry state   0.0013 → offset 0.078, round-trip ₹11.70
      intraday      0.0014 → offset 0.084, round-trip ₹12.60
      rupee floor   max(hsf×price, 0.025) → 0.025 for tiny premiums
    """

    _OPT_DATE = date(2026, 6, 30)   # a NIFTY weekly-expiry Tuesday

    @staticmethod
    def _opt(symbol: str = "NIFTY-Jun2026-24100-CE") -> Instrument:
        """A NIFTY option leg (symbol ends -CE/-PE, tick 0.05)."""
        return Instrument(
            symbol=symbol,
            security_id="opt1",
            segment=Segment.NSE_FNO,
            tick_size=0.05,
            is_derivative=True,
            underlying="NIFTY",
        )

    def _bar_closing_at(
        self, h: int, m: int, close: float, instrument: Instrument
    ) -> Bar:
        """A 1-min bar whose ts_close is h:m (== the fill bar's open time).

        The fill happens at the NEXT bar's open; MeasuredOptionSlippage reads
        the state from bar_history[-1].ts_close, so this bar's close time is
        the fill time-of-day.  ts_open = ts_close − 1 min (rollover-safe).
        """
        ts_open = _ts(h, m) - timedelta(minutes=1)
        return _bar(ts_open, close, close, close, close, instrument=instrument)

    # --- (a) entry window: 0.0013 fraction, floored at 0.025 --------------

    def test_entry_window_fraction(self):
        from algotrader.backtest.engine import MeasuredOptionSlippage
        slip = MeasuredOptionSlippage()
        opt = self._opt()
        # Fill bar opens 09:20 → prior bar closes 09:20 → entry window (t<09:30).
        offset = slip.entry_slippage(opt, [self._bar_closing_at(9, 20, 60.0, opt)])
        assert offset == pytest.approx(0.0013 * 60.0)   # 0.078, above floor
        assert offset > 0.025

    # --- (b) intraday state: 0.0014 fraction ------------------------------

    def test_intraday_fraction(self):
        from algotrader.backtest.engine import MeasuredOptionSlippage
        slip = MeasuredOptionSlippage()
        opt = self._opt()
        offset = slip.entry_slippage(opt, [self._bar_closing_at(12, 0, 60.0, opt)])
        assert offset == pytest.approx(0.0014 * 60.0)   # 0.084

    def test_late_state_uses_intraday_fraction_not_inflated_last15(self):
        from algotrader.backtest.engine import MeasuredOptionSlippage
        slip = MeasuredOptionSlippage()
        opt = self._opt()
        # ≥15:00 deliberately keeps 0.0014 (not the inflated last15 fraction).
        offset = slip.exit_slippage(
            opt, [self._bar_closing_at(15, 5, 60.0, opt)], ExitReason.STOP
        )
        assert offset == pytest.approx(0.0014 * 60.0)

    # --- (c) rupee floor kicks in for tiny premiums -----------------------

    def test_rupee_floor_for_tiny_premium(self):
        from algotrader.backtest.engine import MeasuredOptionSlippage
        slip = MeasuredOptionSlippage()
        opt = self._opt()
        # premium 10 intraday → 0.0014×10 = 0.014 < 0.025 → floored.
        offset = slip.entry_slippage(opt, [self._bar_closing_at(12, 0, 10.0, opt)])
        assert offset == pytest.approx(0.025)

    def test_empty_history_returns_rupee_floor(self):
        from algotrader.backtest.engine import MeasuredOptionSlippage
        slip = MeasuredOptionSlippage()
        opt = self._opt()
        # No causal reference price available → best we can do is the floor.
        assert slip.entry_slippage(opt, []) == pytest.approx(0.025)
        assert slip.exit_slippage(opt, [], ExitReason.SQUARE_OFF) == pytest.approx(0.025)

    def test_put_leg_also_detected(self):
        from algotrader.backtest.engine import MeasuredOptionSlippage
        slip = MeasuredOptionSlippage()
        opt = self._opt("NIFTY-Jun2026-24100-PE")
        offset = slip.entry_slippage(opt, [self._bar_closing_at(12, 0, 60.0, opt)])
        assert offset == pytest.approx(0.0014 * 60.0)

    # --- (d) non-option instruments delegate to DefaultSlippage -----------

    def test_futures_delegates_to_default_identically(self):
        from algotrader.backtest.engine import MeasuredOptionSlippage
        from tests.fixtures.synthetic import trend_day
        measured = MeasuredOptionSlippage()
        default = DefaultSlippage()
        fut = NIFTY_FUT_INSTRUMENT
        day_bars = trend_day(fut, _SESSION_DATE, seed=7, bar_sigma=20.0)
        for n in (0, 3, 20, 50):
            hist = day_bars[:n]
            assert measured.entry_slippage(fut, hist) == pytest.approx(
                default.entry_slippage(fut, hist)
            ), f"futures entry must match DefaultSlippage at n={n}"
            assert measured.exit_slippage(
                fut, hist, ExitReason.STOP
            ) == pytest.approx(default.exit_slippage(fut, hist, ExitReason.STOP))

    def test_equity_delegates_to_default_identically(self):
        from algotrader.backtest.engine import MeasuredOptionSlippage
        from tests.fixtures.synthetic import trend_day
        measured = MeasuredOptionSlippage()
        default = DefaultSlippage()
        eq = TEST_INSTRUMENT   # is_derivative=False, symbol "TESTSYM"
        day_bars = trend_day(eq, _SESSION_DATE, seed=11, bar_sigma=1.5)
        hist = day_bars[:30]
        assert measured.entry_slippage(eq, hist) == pytest.approx(
            default.entry_slippage(eq, hist)
        )

    # --- spread-only default: stop exits carry NO drift -------------------

    def test_default_path_stop_exit_is_pure_spread(self):
        """Default (live) path: a STOP exit uses the plain time-of-day spread,
        with no stop-continuation drift baked in (spread-only guarantee)."""
        from algotrader.backtest.engine import MeasuredOptionSlippage
        slip = MeasuredOptionSlippage()   # stop_drift_frac defaults to 0.0
        opt = self._opt()
        hist = [self._bar_closing_at(12, 0, 60.0, opt)]
        stop_exit = slip.exit_slippage(opt, hist, ExitReason.STOP)
        entry = slip.entry_slippage(opt, hist)
        assert stop_exit == pytest.approx(0.0014 * 60.0)
        assert stop_exit == pytest.approx(entry)   # identical to spread, no drift

    # --- optional research stress toggle ----------------------------------

    def test_stop_drift_toggle_adds_cost_only_on_stop_like_exits(self):
        """Research-only stop_drift_frac adds an extra adverse cost to STOP/TRAIL
        exits; entries and non-stop exits stay pure spread."""
        from algotrader.backtest.engine import MeasuredOptionSlippage
        drift = 0.002
        slip = MeasuredOptionSlippage(stop_drift_frac=drift)
        opt = self._opt()
        hist = [self._bar_closing_at(12, 0, 60.0, opt)]
        base = 0.0014 * 60.0            # intraday spread
        # STOP + TRAIL get the extra drift.
        assert slip.exit_slippage(opt, hist, ExitReason.STOP) == pytest.approx(
            base + drift * 60.0
        )
        assert slip.exit_slippage(opt, hist, ExitReason.TRAIL) == pytest.approx(
            base + drift * 60.0
        )
        # Entry and non-stop exits (square-off, strategy, target) stay spread-only.
        assert slip.entry_slippage(opt, hist) == pytest.approx(base)
        for r in (ExitReason.SQUARE_OFF, ExitReason.STRATEGY, ExitReason.TARGET):
            assert slip.exit_slippage(opt, hist, r) == pytest.approx(base), r

    # --- (e) slippage_paid scale: ~40-60× below the old ₹650 --------------

    def test_slippage_paid_scale_vs_flat_650(self):
        """A 75-lot NIFTY option round trip at premium ~60 costs ~₹11-17/leg —
        i.e. ~40-60× below the old flat ₹650 (verifies the accounting path)."""
        from algotrader.backtest.engine import MeasuredOptionSlippage
        slip = MeasuredOptionSlippage()
        opt = self._opt()
        lot = 75
        qty = 1
        entry = slip.entry_slippage(opt, [self._bar_closing_at(12, 0, 60.0, opt)])
        exit_ = slip.exit_slippage(
            opt, [self._bar_closing_at(14, 30, 60.0, opt)], ExitReason.STRATEGY
        )
        # Same accounting formula as PaperExecutor._close_pos.
        slippage_paid = (entry + exit_) * qty * lot
        assert 10.0 <= slippage_paid <= 17.0, slippage_paid
        assert 40 <= (650.0 / slippage_paid) <= 60


# ===========================================================================
# Integration: multi-bar session with stop then target
# ===========================================================================

class TestIntegrationFullSession:

    def test_pnl_attribution(self):
        """Closed trade has non-zero costs and slippage correctly attributed."""
        bars = [
            _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0),   # signal bar
            _bar(_ts(9, 16), 104.0, 108.0, 103.0, 106.0),  # fill bar, entry=104
            _bar(_ts(9, 17), 110.0, 115.0, 109.0, 112.0),  # target hit (open gap)
        ]
        # Target=108 (below fill open 110 → gap above target, fills at 110)
        intent = _buy_intent(ref_price=103.0, stop_price=90.0, target_price=108.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine(bars, strat)

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.position.exit_reason is ExitReason.TARGET
        assert t.costs.total > 0, "Should have non-zero costs"
        assert t.position.gross_pnl() > 0, "Should have positive gross PnL"

    def test_session_result_aggregates_correctly(self):
        """SessionResult gross/net/cost aggregates match sum of trades."""
        bars = [
            _bar(_ts(9, 15), 100.0, 105.0, 98.0, 103.0),
            _bar(_ts(9, 16), 104.0, 108.0, 103.0, 106.0),
            _bar(_ts(9, 17), 110.0, 115.0, 109.0, 112.0),
        ]
        intent = _buy_intent(ref_price=103.0, stop_price=90.0, target_price=108.0)
        strat = _TestStrategy(intents_at={0: [intent]})
        result, _ = _run_engine(bars, strat)

        session = result.sessions[0]
        assert session.gross_pnl == pytest.approx(
            sum(t.position.gross_pnl() for t in session.trades)
        )
        assert session.cost_total == pytest.approx(
            sum(t.costs.total for t in session.trades)
        )
        assert session.net_pnl == pytest.approx(
            sum(t.net_pnl for t in session.trades)
        )


def test_slippage_not_double_counted_and_in_rupees():
    """Regression (2026-06-11): fills embed slippage, so net_pnl == gross - costs,
    and TradeRecord.slippage_paid is the TOTAL rupee slippage, not per-unit."""
    from algotrader.core import CostBreakdown, Position, Side, TradeRecord
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    pos = Position(
        position_id="x", strategy_id="s", instrument=TEST_INSTRUMENT, side=Side.BUY,
        quantity=100, entry_price=100.10, entry_ts=datetime(2026, 1, 5, 10, 0, tzinfo=ist),
        stop_price=99.0, target_price=None, session_date=date(2026, 1, 5),
        exit_price=101.0, exit_ts=datetime(2026, 1, 5, 11, 0, tzinfo=ist),
    )
    costs = CostBreakdown(brokerage=40, stt=2.5, exchange_txn=0.6, sebi=0.02,
                          stamp=0.3, ipft=0.01, gst=7.3)
    tr = TradeRecord(position=pos, costs=costs, slippage_paid=20.0)  # ₹ total
    assert tr.net_pnl == pos.gross_pnl() - costs.total  # no slippage subtraction


def test_time_stop_exits_position():
    """Regression: OrderIntent.time_stop_min must produce a TIME_STOP exit at
    the first bar-close past the deadline, filled at the NEXT bar's open
    (was silently unwired — found in Gen-1 sweep, 2026-06-12)."""
    from algotrader.core import ExitReason

    # 20 quiet 1-min bars: price pinned at 100 so neither stop nor target hits.
    bars = [_bar(_ts(10, i), 100.0, 100.2, 99.8, 100.0) for i in range(20)]
    from dataclasses import replace as _dc_replace
    intent = _dc_replace(_buy_intent(ref_price=100.0, stop_price=90.0), time_stop_min=5)
    strat = _TestStrategy(intents_at={1: [intent]})
    result, _ = _run_engine(bars, strat)

    trades = result.trades
    assert len(trades) == 1
    t = trades[0]
    assert t.position.exit_reason is ExitReason.TIME_STOP
    held_min = (t.position.exit_ts - t.position.entry_ts).total_seconds() / 60
    assert 5 <= held_min <= 7  # deadline reached + next-bar-open fill
