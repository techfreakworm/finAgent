"""Tests for IntradayRiskEngine (algotrader/risk/engine.py).

Coverage (per assignment):
- Sizing math: equity and derivatives, including lot rounding
- 0-lot rejection for derivatives
- Every rejection rule: breaker_tripped, floor_breached, t2t_short,
  max_open_positions, per_symbol_cap, zero_qty, margin_exceeded
- Breaker trips exactly at threshold (≤ boundary inclusive)
- Breaker does NOT reset within same session (latch-bug regression)
- reset_for_session with same date is a no-op (latch preserved)
- reset_for_session with new date resets all latches
- SessionRiskState JSON round-trip

ARCHITECTURE §5: all values are fixture-driven; no network calls.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pytest

from algotrader.core import (
    ExitReason,
    Instrument,
    OrderIntent,
    Rejection,
    RiskSnapshot,
    Segment,
    Side,
    SizedOrder,
)
from algotrader.risk.engine import IntradayRiskEngine, RiskParams, SessionRiskState

# ===========================================================================
# Shared fixtures and helpers
# ===========================================================================

SESSION_DATE = date(2026, 1, 15)
NEXT_DATE = date(2026, 1, 16)

DEFAULT_PARAMS = RiskParams(
    capital=500_000.0,
    hard_floor=400_000.0,
    max_daily_loss_pct=0.02,
    per_trade_risk_pct=0.0075,
    max_open_positions=3,
    max_per_symbol=1,
    equity_mis_leverage=5.0,
)


@dataclass(frozen=True)
class _FixedLotInstrument(Instrument):
    """Test-only subclass with a fixed point-in-time lot size."""
    _lot: int = 1

    def lot_size(self, on: date) -> int:  # type: ignore[override]
        return self._lot


def _eq_instr(symbol: str = "RELIANCE", can_short: bool = True) -> Instrument:
    """Standard NSE equity instrument (lot_size=1 by default)."""
    return Instrument(
        symbol=symbol,
        security_id="1333",
        segment=Segment.NSE_EQ,
        tick_size=0.05,
        is_derivative=False,
        can_short_intraday=can_short,
    )


def _fut_instr(symbol: str = "NIFTY-FUT", lot: int = 75) -> _FixedLotInstrument:
    """NSE_FNO futures instrument with a fixed lot size."""
    return _FixedLotInstrument(
        symbol=symbol,
        security_id="13",
        segment=Segment.NSE_FNO,
        tick_size=0.05,
        is_derivative=True,
        underlying="NIFTY",
        can_short_intraday=True,
        _lot=lot,
    )


def _buy_intent(
    instrument: Instrument,
    ref_price: float = 1000.0,
    stop_price: float = 980.0,
    strategy_id: str = "test-strat",
) -> OrderIntent:
    """BUY intent: stop_price < ref_price (validated by OrderIntent)."""
    return OrderIntent(
        strategy_id=strategy_id,
        instrument=instrument,
        side=Side.BUY,
        ref_price=ref_price,
        stop_price=stop_price,
    )


def _sell_intent(
    instrument: Instrument,
    ref_price: float = 1000.0,
    stop_price: float = 1020.0,
    strategy_id: str = "test-strat",
) -> OrderIntent:
    """SELL intent: stop_price > ref_price (validated by OrderIntent)."""
    return OrderIntent(
        strategy_id=strategy_id,
        instrument=instrument,
        side=Side.SELL,
        ref_price=ref_price,
        stop_price=stop_price,
    )


def _snapshot(
    capital: float = 500_000.0,
    realized: float = 0.0,
    unrealized: float = 0.0,
    breaker_tripped: bool = False,
    floor_breached: bool = False,
    open_positions: int = 0,
) -> RiskSnapshot:
    return RiskSnapshot(
        capital=capital,
        realized_pnl_today=realized,
        unrealized_pnl=unrealized,
        breaker_tripped=breaker_tripped,
        floor_breached=floor_breached,
        open_position_count=open_positions,
    )


def _fresh_engine(params: RiskParams = DEFAULT_PARAMS) -> IntradayRiskEngine:
    """Create an engine with session reset to SESSION_DATE."""
    engine = IntradayRiskEngine(params)
    engine.reset_for_session(SESSION_DATE)
    return engine


# ===========================================================================
# SIZING MATH — equity
# ===========================================================================

class TestEquitySizingMath:
    """Verify qty = floor(risk_budget / stop_dist) and margin formula."""

    def test_buy_qty_floor_division(self):
        """Exact floor-division sizing for equity BUY.

        capital=500000, per_trade=0.0075, budget=3750
        ref=1000, stop=980, dist=20
        qty = floor(3750/20) = 187
        """
        engine = _fresh_engine()
        intent = _buy_intent(_eq_instr(), ref_price=1000.0, stop_price=980.0)
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder), f"expected SizedOrder, got {result}"
        assert result.quantity == 187

    def test_buy_margin_equity(self):
        """Equity margin = ref_price * qty / leverage.

        ref=1000, qty=187, leverage=5.0 → margin=37400.0
        """
        engine = _fresh_engine()
        intent = _buy_intent(_eq_instr(), ref_price=1000.0, stop_price=980.0)
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)
        assert result.margin_required == pytest.approx(1000.0 * 187 / 5.0, abs=1e-6)

    def test_sell_qty_same_formula(self):
        """SELL uses same stop_dist formula (stop_price > ref_price for sells).

        ref=1000, stop=1020, dist=20 → qty=187
        """
        engine = _fresh_engine()
        intent = _sell_intent(_eq_instr(), ref_price=1000.0, stop_price=1020.0)
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)
        assert result.quantity == 187

    def test_qty_truncated_not_rounded(self):
        """floor() truncates, not rounds.

        budget=3750, dist=29 → 3750/29 = 129.31... → floor = 129
        """
        engine = _fresh_engine()
        intent = _buy_intent(_eq_instr(), ref_price=1000.0, stop_price=971.0)  # dist=29
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)
        expected_qty = math.floor(3750.0 / 29.0)
        assert result.quantity == expected_qty
        assert result.quantity == 129

    def test_sized_order_carries_intent_reference(self):
        """SizedOrder.intent is the same object passed in."""
        engine = _fresh_engine()
        intent = _buy_intent(_eq_instr())
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)
        assert result.intent is intent


# ===========================================================================
# SIZING MATH — derivatives (lot rounding)
# ===========================================================================

class TestDerivativeSizingMath:
    """Derivatives: qty = floor(budget / (dist * lot_size)), in contracts."""

    def test_derivative_qty_in_contracts_one_lot(self):
        """Exactly 1 contract when budget matches risk per lot.

        budget=3750, lot=75, dist=50 → risk_per_lot=50×75=3750
        floor(3750/3750) = 1 contract
        """
        engine = _fresh_engine()
        instr = _fut_instr(lot=75)
        intent = _buy_intent(instr, ref_price=24000.0, stop_price=23950.0)  # dist=50
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder), f"expected SizedOrder, got {result}"
        assert result.quantity == 1

    def test_derivative_qty_two_contracts(self):
        """2 contracts when budget is exactly 2× risk per lot.

        budget=3750, lot=75, dist=25 → risk_per_lot=25×75=1875
        floor(3750/1875) = 2 contracts
        """
        engine = _fresh_engine()
        instr = _fut_instr(lot=75)
        intent = _buy_intent(instr, ref_price=24000.0, stop_price=23975.0)  # dist=25
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)
        assert result.quantity == 2

    def test_derivative_margin_span_proxy_12pct_notional(self):
        """Derivative margin = 12% × ref × qty_contracts × lot_size.

        qty=1, lot=75, ref=24000 → notional=1800000
        margin = 0.12 × 1800000 = 216000
        """
        engine = _fresh_engine()
        instr = _fut_instr(lot=75)
        intent = _buy_intent(instr, ref_price=24000.0, stop_price=23950.0)
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)
        assert result.margin_required == pytest.approx(0.12 * 24000.0 * 1 * 75, abs=1e-6)

    def test_derivative_floor_division_truncates(self):
        """floor() truncates for derivatives too.

        budget=3750, lot=75, dist=40 → risk_per_lot=40×75=3000
        floor(3750/3000) = 1 contract  (not 1.25, not 2)
        """
        engine = _fresh_engine()
        instr = _fut_instr(lot=75)
        intent = _buy_intent(instr, ref_price=24000.0, stop_price=23960.0)  # dist=40
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)
        assert result.quantity == 1

    def test_small_lot_size_allows_more_contracts(self):
        """Smaller lot sizes increase contract count.

        lot=25, dist=50 → risk_per_lot=25×50=1250
        floor(3750/1250) = 3 contracts
        """
        engine = _fresh_engine()
        instr = _fut_instr(lot=25)
        intent = _buy_intent(instr, ref_price=24000.0, stop_price=23950.0)
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)
        assert result.quantity == 3


# ===========================================================================
# REJECTION: zero_qty (derivatives — 0-lot rejection)
# ===========================================================================

class TestZeroLotRejection:
    """Derivatives must be rejected if computed contracts = 0."""

    def test_derivative_zero_lots_rejected(self):
        """0-lot rejection: budget < risk per single lot.

        budget=3750, lot=75, dist=100 → risk_per_lot=100×75=7500
        floor(3750/7500) = 0 → Rejection("zero_qty")
        """
        engine = _fresh_engine()
        instr = _fut_instr(lot=75)
        intent = _buy_intent(instr, ref_price=24000.0, stop_price=23900.0)  # dist=100
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "zero_qty"
        assert result.intent is intent

    def test_derivative_just_at_threshold_one_lot(self):
        """Exactly at 1-lot threshold: budget == risk_per_lot → 1 contract.

        budget=3750, lot=75, dist=50 → risk_per_lot=3750 → exactly 1
        """
        engine = _fresh_engine()
        instr = _fut_instr(lot=75)
        intent = _buy_intent(instr, ref_price=24000.0, stop_price=23950.0)  # dist=50
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)
        assert result.quantity == 1

    def test_equity_zero_qty_rejected_when_stop_too_wide(self):
        """Equity: very wide stop → floor rounds to 0 → Rejection.

        budget=3750, dist=5000 (stop far away) → floor(3750/5000)=0
        """
        engine = _fresh_engine()
        instr = _eq_instr()
        # BUY at 10000, stop at 5001 → dist=4999
        intent = _buy_intent(instr, ref_price=10000.0, stop_price=5001.0)
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "zero_qty"


# ===========================================================================
# REJECTION RULES — all rules
# ===========================================================================

class TestRejectionBreakerTripped:
    """Rejection when the daily-loss circuit breaker is active."""

    def test_snapshot_breaker_tripped_rejects(self):
        """Snapshot has breaker_tripped=True → Rejection("breaker_tripped")."""
        engine = _fresh_engine()
        snap = _snapshot(breaker_tripped=True)
        result = engine.size(_buy_intent(_eq_instr()), snap, SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "breaker_tripped"

    def test_internal_breaker_tripped_rejects_despite_clean_snapshot(self):
        """Internal latch trumps a stale (False) snapshot value.

        Regression for finAgent latch-bug: if the snapshot is stale but the
        engine's own state says tripped, sizing must still reject.
        """
        engine = _fresh_engine()
        # Trip the breaker via on_bar
        snap_trip = _snapshot(realized=-5000.0, unrealized=-5000.0)
        engine.on_bar(snap_trip)  # realized+unrealized = -10000 = -2% of 500k → trips
        # Now pass a snapshot that (incorrectly) says breaker_tripped=False
        snap_stale = _snapshot(breaker_tripped=False)
        result = engine.size(_buy_intent(_eq_instr()), snap_stale, SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "breaker_tripped"


class TestRejectionFloorBreached:
    """Rejection when the hard-floor latch is active."""

    def test_snapshot_floor_breached_rejects(self):
        snap = _snapshot(floor_breached=True)
        engine = _fresh_engine()
        result = engine.size(_buy_intent(_eq_instr()), snap, SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "floor_breached"

    def test_internal_floor_latch_rejects(self):
        """Internal floor latch rejects even with stale snapshot.

        pnl = -100001 crosses BOTH the daily-loss breaker (-10000) AND the
        hard floor (equity 399999 <= 400000).  The breaker is checked first in
        size(), so the returned rule is "breaker_tripped", but the floor latch
        is also set.  What matters is that neither latch is cleared and that
        size() returns a Rejection.
        """
        engine = _fresh_engine()
        # pnl = -100001: breaker threshold -10000 also breached.
        snap_trip = _snapshot(realized=-60000.0, unrealized=-40001.0)
        engine.on_bar(snap_trip)  # sets both breaker_tripped and floor_breached
        assert engine.state.breaker_tripped is True
        assert engine.state.floor_breached is True
        result = engine.size(_buy_intent(_eq_instr()), _snapshot(), SESSION_DATE)
        assert isinstance(result, Rejection)
        # Breaker is checked first in size(), but the important assertion is
        # that the floor latch caused a rejection (rule is one of the two latches).
        assert result.rule in {"breaker_tripped", "floor_breached"}


class TestRejectionT2TShort:
    """T2T/BE-series equities cannot be shorted intraday."""

    def test_sell_t2t_rejected(self):
        engine = _fresh_engine()
        t2t = _eq_instr(symbol="SOMEBE", can_short=False)
        intent = _sell_intent(t2t, ref_price=200.0, stop_price=220.0)
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "t2t_short"

    def test_buy_t2t_allowed(self):
        """BUY on a T2T instrument is still allowed (long only)."""
        engine = _fresh_engine()
        t2t = _eq_instr(symbol="SOMEBE", can_short=False)
        intent = _buy_intent(t2t, ref_price=200.0, stop_price=180.0)
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)

    def test_sell_normal_equity_allowed(self):
        """SELL on a normal (can_short=True) equity is not rejected by this rule."""
        engine = _fresh_engine()
        instr = _eq_instr(can_short=True)
        intent = _sell_intent(instr, ref_price=1000.0, stop_price=1020.0)
        result = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result, SizedOrder)


class TestRejectionMaxOpenPositions:
    """Global cap on simultaneous open positions."""

    def test_at_cap_rejects(self):
        """open_position_count >= max_open_positions → Rejection."""
        engine = _fresh_engine()  # max_open_positions=3
        snap = _snapshot(open_positions=3)
        result = engine.size(_buy_intent(_eq_instr()), snap, SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "max_open_positions"

    def test_one_below_cap_allowed(self):
        """open_position_count = max − 1 → allowed."""
        engine = _fresh_engine()
        snap = _snapshot(open_positions=2)
        result = engine.size(_buy_intent(_eq_instr()), snap, SESSION_DATE)
        assert isinstance(result, SizedOrder)

    def test_zero_positions_allowed(self):
        """Zero open positions → allowed (baseline)."""
        engine = _fresh_engine()
        snap = _snapshot(open_positions=0)
        result = engine.size(_buy_intent(_eq_instr()), snap, SESSION_DATE)
        assert isinstance(result, SizedOrder)


class TestRejectionPerSymbolCap:
    """Per-symbol open position cap."""

    def test_at_per_symbol_cap_rejects(self):
        """After registering max_per_symbol fills for a symbol, next rejects.

        DEFAULT_PARAMS.max_per_symbol = 1.
        """
        engine = _fresh_engine()
        instr = _eq_instr(symbol="RELIANCE")
        intent = _buy_intent(instr, ref_price=1000.0, stop_price=980.0)
        # First size — should succeed
        result1 = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(result1, SizedOrder)
        # Register fill to consume the per-symbol slot
        engine.register_fill(result1)
        # Second size for SAME symbol — should be rejected
        result2 = engine.size(intent, _snapshot(open_positions=1), SESSION_DATE)
        assert isinstance(result2, Rejection)
        assert result2.rule == "per_symbol_cap"

    def test_different_symbol_not_blocked(self):
        """Hitting cap on symbol X does not block symbol Y."""
        engine = _fresh_engine()
        instr_x = _eq_instr(symbol="RELIANCE")
        instr_y = _eq_instr(symbol="TCS")
        intent_x = _buy_intent(instr_x, ref_price=1000.0, stop_price=980.0)
        intent_y = _buy_intent(instr_y, ref_price=3000.0, stop_price=2970.0)
        r1 = engine.size(intent_x, _snapshot(), SESSION_DATE)
        assert isinstance(r1, SizedOrder)
        engine.register_fill(r1)
        # RELIANCE is at cap, TCS is not
        r2 = engine.size(intent_y, _snapshot(open_positions=1), SESSION_DATE)
        assert isinstance(r2, SizedOrder)

    def test_register_close_frees_symbol_slot(self):
        """After register_close, the symbol slot becomes available again."""
        engine = _fresh_engine()
        instr = _eq_instr(symbol="RELIANCE")
        intent = _buy_intent(instr, ref_price=1000.0, stop_price=980.0)
        r1 = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(r1, SizedOrder)
        engine.register_fill(r1)
        engine.register_close("RELIANCE", r1.margin_required)
        # Now the symbol slot is free; open_positions back to 0 (simulate)
        r2 = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(r2, SizedOrder)


class TestRejectionMarginExceeded:
    """Margin exceeds available capital + realized − committed."""

    def test_margin_exceeded_rejects(self):
        """After committing most of the capital as margin, next order rejected.

        Available = capital + realized - committed_margin.
        Strategy: register a large fill, then try another.
        """
        params = RiskParams(
            capital=100_000.0,
            hard_floor=80_000.0,
            max_daily_loss_pct=0.02,
            per_trade_risk_pct=0.0075,
            max_open_positions=10,
            max_per_symbol=10,
            equity_mis_leverage=5.0,
        )
        engine = IntradayRiskEngine(params)
        engine.reset_for_session(SESSION_DATE)

        # Fabricate a SizedOrder that commits 98000 of the 100000 capital
        instr = _eq_instr(symbol="AAPL")
        intent_fake = _buy_intent(instr, ref_price=1000.0, stop_price=980.0)
        big_order = SizedOrder(
            intent=intent_fake,
            quantity=490,
            margin_required=98_000.0,  # leaves only 2000 available
        )
        engine.register_fill(big_order)

        # Try to size a new order: equity trade at ref=1000, stop=990 (dist=10)
        # qty = floor(750/10) = 75; margin = 1000*75/5 = 15000 >> 2000
        instr2 = _eq_instr(symbol="INFY")
        intent2 = _buy_intent(instr2, ref_price=1000.0, stop_price=990.0)
        snap = _snapshot(capital=100_000.0, realized=0.0, open_positions=1)
        result = engine.size(intent2, snap, SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "margin_exceeded"

    def test_margin_available_includes_realized_pnl(self):
        """Positive realized_pnl_today increases available margin headroom."""
        params = RiskParams(
            capital=50_000.0,
            hard_floor=30_000.0,
            max_daily_loss_pct=0.02,
            per_trade_risk_pct=0.0075,
            max_open_positions=10,
            max_per_symbol=10,
            equity_mis_leverage=5.0,
        )
        engine = IntradayRiskEngine(params)
        engine.reset_for_session(SESSION_DATE)
        # No committed margin, positive realized
        snap = _snapshot(capital=50_000.0, realized=10_000.0, open_positions=0)
        instr = _eq_instr()
        intent = _buy_intent(instr, ref_price=1000.0, stop_price=990.0)
        # qty = floor(375/10) = 37; margin = 37*1000/5 = 7400
        # available = 50000 + 10000 - 0 = 60000 >> 7400
        result = engine.size(intent, snap, SESSION_DATE)
        assert isinstance(result, SizedOrder)


# ===========================================================================
# CIRCUIT BREAKER — on_bar behaviour
# ===========================================================================

class TestBreakerTripAtThreshold:
    """Breaker trips when realized + unrealized <= -max_daily_loss_pct * capital."""

    def test_trips_exactly_at_threshold(self):
        """Exactly −2% of capital (₹10,000 on ₹5L) trips the breaker.

        realized=-5000, unrealized=-5000 → pnl=-10000 = -0.02 × 500000
        """
        engine = _fresh_engine()
        snap = _snapshot(realized=-5000.0, unrealized=-5000.0)
        reasons = engine.on_bar(snap)
        assert ExitReason.BREAKER in reasons

    def test_does_not_trip_one_rupee_above_threshold(self):
        """pnl = threshold + 0.01 → no trip."""
        engine = _fresh_engine()
        # threshold = -10000; pnl = -9999.99
        snap = _snapshot(realized=-5000.0, unrealized=-4999.99)
        reasons = engine.on_bar(snap)
        assert ExitReason.BREAKER not in reasons

    def test_trips_just_below_threshold(self):
        """pnl = threshold − 0.01 → trips."""
        engine = _fresh_engine()
        snap = _snapshot(realized=-5000.0, unrealized=-5000.01)
        reasons = engine.on_bar(snap)
        assert ExitReason.BREAKER in reasons

    def test_trips_only_once(self):
        """Once tripped, on_bar does NOT emit BREAKER again on subsequent calls.

        The latch is set; the backtest engine already knows to halt entries.
        """
        engine = _fresh_engine()
        snap_bad = _snapshot(realized=-5000.0, unrealized=-5000.0)
        reasons1 = engine.on_bar(snap_bad)
        assert ExitReason.BREAKER in reasons1
        # Same bad snapshot again
        reasons2 = engine.on_bar(snap_bad)
        assert ExitReason.BREAKER not in reasons2

    def test_state_breaker_tripped_set_after_trip(self):
        """Internal state.breaker_tripped is True after on_bar trips it."""
        engine = _fresh_engine()
        engine.on_bar(_snapshot(realized=-5000.0, unrealized=-5000.0))
        assert engine.state.breaker_tripped is True

    def test_no_trip_with_zero_pnl(self):
        """Zero pnl → no trip."""
        engine = _fresh_engine()
        reasons = engine.on_bar(_snapshot())
        assert reasons == []


class TestBreakerLatch:
    """Breaker does not reset within the same session; resets on new date."""

    def test_breaker_persists_within_session(self):
        """Breaker stays latched even when pnl recovers within the same session.

        Regression test for finAgent floor_monitor._breached one-way latch bug
        (finagent-assessment.md §"Known bugs" — the latch never reset across
        sessions, but the reverse bug — resetting within a session — is equally
        dangerous for live trading).
        """
        engine = _fresh_engine()
        # Trip the breaker
        engine.on_bar(_snapshot(realized=-5000.0, unrealized=-5000.0))
        assert engine.state.breaker_tripped is True
        # Simulate pnl "recovery" (e.g., unrealized turns positive)
        engine.on_bar(_snapshot(realized=-5000.0, unrealized=6000.0))
        # Latch must remain
        assert engine.state.breaker_tripped is True

    def test_size_rejected_after_breaker_trip(self):
        """size() must reject after on_bar tripped the breaker."""
        engine = _fresh_engine()
        engine.on_bar(_snapshot(realized=-5000.0, unrealized=-5000.0))
        result = engine.size(_buy_intent(_eq_instr()), _snapshot(), SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "breaker_tripped"

    def test_reset_same_date_does_not_clear_breaker(self):
        """reset_for_session with the SAME date must NOT clear the breaker.

        This is the finAgent latch-bug regression — the bug was a breacher
        that was never re-armed; we also guard against the inverse: a breacher
        reset too eagerly on same-day restarts.
        """
        engine = _fresh_engine()
        # Trip the breaker
        engine.on_bar(_snapshot(realized=-5000.0, unrealized=-5000.0))
        assert engine.state.breaker_tripped is True
        # Call reset_for_session with the SAME session date
        engine.reset_for_session(SESSION_DATE)
        # Breaker must still be tripped
        assert engine.state.breaker_tripped is True

    def test_reset_new_date_clears_breaker(self):
        """reset_for_session with a NEW date resets the breaker latch."""
        engine = _fresh_engine()
        engine.on_bar(_snapshot(realized=-5000.0, unrealized=-5000.0))
        assert engine.state.breaker_tripped is True
        # New trading day
        engine.reset_for_session(NEXT_DATE)
        assert engine.state.breaker_tripped is False
        assert engine.state.session_date == NEXT_DATE

    def test_size_allowed_after_reset_to_new_date(self):
        """After new-date reset, size() succeeds (breaker cleared)."""
        engine = _fresh_engine()
        engine.on_bar(_snapshot(realized=-5000.0, unrealized=-5000.0))
        engine.reset_for_session(NEXT_DATE)
        result = engine.size(_buy_intent(_eq_instr()), _snapshot(), NEXT_DATE)
        assert isinstance(result, SizedOrder)

    def test_size_rejects_on_same_date_after_trip(self):
        """Even after reset_for_session(same_date), breaker still rejects size()."""
        engine = _fresh_engine()
        engine.on_bar(_snapshot(realized=-5000.0, unrealized=-5000.0))
        engine.reset_for_session(SESSION_DATE)  # no-op
        result = engine.size(_buy_intent(_eq_instr()), _snapshot(), SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "breaker_tripped"


# ===========================================================================
# FLOOR BREACHER — on_bar behaviour
# ===========================================================================

class TestRejectionFloorOnlyEngine:
    """Floor-only rejection using a custom engine where the floor trips
    without triggering the daily-loss breaker simultaneously.

    By setting max_daily_loss_pct very high (e.g. 0.99), the breaker
    never fires at realistic loss levels, so floor is the sole rejector.
    """

    def _floor_only_engine(self) -> IntradayRiskEngine:
        params = RiskParams(
            capital=500_000.0,
            hard_floor=400_000.0,
            max_daily_loss_pct=0.99,     # breaker at 99% loss — never reached
            per_trade_risk_pct=0.0075,
            max_open_positions=3,
            max_per_symbol=1,
            equity_mis_leverage=5.0,
        )
        engine = IntradayRiskEngine(params)
        engine.reset_for_session(SESSION_DATE)
        return engine

    def test_floor_latch_alone_causes_rejection(self):
        """Floor trips without breaker → size() returns 'floor_breached'."""
        engine = self._floor_only_engine()
        # pnl = -100001: equity = 399999 ≤ 400000; breaker at 495000 loss not reached
        snap_trip = _snapshot(realized=-60000.0, unrealized=-40001.0)
        engine.on_bar(snap_trip)
        assert engine.state.floor_breached is True
        assert engine.state.breaker_tripped is False  # breaker NOT tripped
        result = engine.size(_buy_intent(_eq_instr()), _snapshot(), SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "floor_breached"


class TestFloorBreacherOnBar:
    """Floor trips when capital + realized + unrealized <= hard_floor."""

    def test_floor_trips_exactly_at_threshold(self):
        """equity = hard_floor exactly → trip.

        capital=500000, hard_floor=400000
        pnl = realized + unrealized = -100000
        equity = 500000 - 100000 = 400000 = hard_floor → trip
        """
        engine = _fresh_engine()
        snap = _snapshot(realized=-60000.0, unrealized=-40000.0)
        reasons = engine.on_bar(snap)
        assert ExitReason.FLOOR in reasons

    def test_floor_does_not_trip_one_rupee_above(self):
        """equity = hard_floor + 0.01 → no trip."""
        engine = _fresh_engine()
        snap = _snapshot(realized=-60000.0, unrealized=-39999.99)
        reasons = engine.on_bar(snap)
        assert ExitReason.FLOOR not in reasons

    def test_floor_and_breaker_can_trip_simultaneously(self):
        """Both thresholds crossed in one bar → both reasons returned."""
        engine = _fresh_engine()
        # pnl = -110000 → breaker: -110000 <= -10000 ✓; floor: 390000 <= 400000 ✓
        snap = _snapshot(realized=-60000.0, unrealized=-50000.0)
        reasons = engine.on_bar(snap)
        assert ExitReason.BREAKER in reasons
        assert ExitReason.FLOOR in reasons

    def test_floor_latch_persists_within_session(self):
        """Floor latch does not reset when pnl recovers within same session."""
        engine = _fresh_engine()
        engine.on_bar(_snapshot(realized=-60000.0, unrealized=-40000.0))
        assert engine.state.floor_breached is True
        engine.on_bar(_snapshot(realized=-60000.0, unrealized=0.0))
        assert engine.state.floor_breached is True

    def test_floor_latch_cleared_on_new_date(self):
        """reset_for_session(new_date) clears the floor latch too."""
        engine = _fresh_engine()
        engine.on_bar(_snapshot(realized=-60000.0, unrealized=-40000.0))
        engine.reset_for_session(NEXT_DATE)
        assert engine.state.floor_breached is False


# ===========================================================================
# SESSION STATE — JSON round-trip
# ===========================================================================

class TestSessionRiskStateJson:
    """to_json / from_json produce byte-for-byte round-trips."""

    def test_default_state_round_trip(self):
        """Fresh state serializes and deserializes with all defaults."""
        original = SessionRiskState(session_date=SESSION_DATE)
        restored = SessionRiskState.from_json(original.to_json())
        assert restored.session_date == original.session_date
        assert restored.breaker_tripped == original.breaker_tripped
        assert restored.floor_breached == original.floor_breached
        assert restored.realized_pnl == original.realized_pnl
        assert restored.committed_margin == original.committed_margin
        assert restored.symbol_open_counts == original.symbol_open_counts

    def test_tripped_state_round_trip(self):
        """Latched breaker and floor survive JSON serialization."""
        state = SessionRiskState(
            session_date=SESSION_DATE,
            breaker_tripped=True,
            floor_breached=True,
            realized_pnl=-12_000.5,
            committed_margin=85_000.0,
            symbol_open_counts={"RELIANCE": 1, "NIFTY-FUT": 2},
        )
        restored = SessionRiskState.from_json(state.to_json())
        assert restored.breaker_tripped is True
        assert restored.floor_breached is True
        assert restored.realized_pnl == pytest.approx(-12_000.5)
        assert restored.committed_margin == pytest.approx(85_000.0)
        assert restored.symbol_open_counts == {"RELIANCE": 1, "NIFTY-FUT": 2}
        assert restored.session_date == SESSION_DATE

    def test_from_json_produces_independent_dicts(self):
        """Deserialized symbol_open_counts is a new dict (no aliasing)."""
        state = SessionRiskState(
            session_date=SESSION_DATE,
            symbol_open_counts={"X": 1},
        )
        restored = SessionRiskState.from_json(state.to_json())
        restored.symbol_open_counts["Y"] = 2
        original2 = SessionRiskState.from_json(state.to_json())
        assert "Y" not in original2.symbol_open_counts

    def test_load_state_restores_breaker_for_crash_recovery(self):
        """load_state() with a tripped state correctly blocks new entries.

        Simulates paper-trading restart after a SIGKILL mid-session.
        """
        # Engine was tripped, state saved to JSON
        engine_before = _fresh_engine()
        engine_before.on_bar(_snapshot(realized=-5000.0, unrealized=-5000.0))
        saved_json = engine_before.state.to_json()

        # New engine instance (restart simulation)
        engine_after = IntradayRiskEngine(DEFAULT_PARAMS)
        engine_after.load_state(SessionRiskState.from_json(saved_json))

        # Entries must still be blocked
        result = engine_after.size(_buy_intent(_eq_instr()), _snapshot(), SESSION_DATE)
        assert isinstance(result, Rejection)
        assert result.rule == "breaker_tripped"

    def test_to_json_produces_valid_json_string(self):
        """to_json() output is parseable by json.loads (no exceptions)."""
        import json as _json
        state = SessionRiskState(session_date=SESSION_DATE, breaker_tripped=True)
        raw = state.to_json()
        parsed = _json.loads(raw)
        assert parsed["breaker_tripped"] is True
        assert parsed["session_date"] == SESSION_DATE.isoformat()


# ===========================================================================
# REGISTER FILL / CLOSE
# ===========================================================================

class TestRegisterFillClose:
    """register_fill and register_close maintain committed_margin and symbol counts."""

    def test_register_fill_increments_committed_margin(self):
        engine = _fresh_engine()
        instr = _eq_instr()
        intent = _buy_intent(instr, ref_price=1000.0, stop_price=980.0)
        order = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(order, SizedOrder)
        engine.register_fill(order)
        assert engine.state.committed_margin == pytest.approx(order.margin_required)

    def test_register_fill_increments_symbol_count(self):
        engine = _fresh_engine()
        instr = _eq_instr(symbol="TCS")
        intent = _buy_intent(instr, ref_price=3000.0, stop_price=2970.0)
        order = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(order, SizedOrder)
        engine.register_fill(order)
        assert engine.state.symbol_open_counts.get("TCS", 0) == 1

    def test_register_close_decrements_committed_margin(self):
        engine = _fresh_engine()
        instr = _eq_instr()
        intent = _buy_intent(instr, ref_price=1000.0, stop_price=980.0)
        order = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(order, SizedOrder)
        engine.register_fill(order)
        engine.register_close(instr.symbol, order.margin_required)
        assert engine.state.committed_margin == pytest.approx(0.0, abs=1e-6)

    def test_register_close_decrements_symbol_count(self):
        engine = _fresh_engine()
        instr = _eq_instr(symbol="TCS")
        intent = _buy_intent(instr, ref_price=3000.0, stop_price=2970.0)
        order = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(order, SizedOrder)
        engine.register_fill(order)
        assert engine.state.symbol_open_counts["TCS"] == 1
        engine.register_close("TCS", order.margin_required)
        assert engine.state.symbol_open_counts.get("TCS", 0) == 0

    def test_register_close_does_not_go_negative(self):
        """Over-calling register_close should clamp to 0, not raise."""
        engine = _fresh_engine()
        engine.register_close("NONEXISTENT", 99999.0)
        assert engine.state.committed_margin == pytest.approx(0.0, abs=1e-6)


# ===========================================================================
# RESET FOR SESSION
# ===========================================================================

class TestResetForSession:
    """reset_for_session lifecycle semantics."""

    def test_reset_clears_symbol_counts(self):
        """After reset to new date, per-symbol counts start at 0."""
        engine = _fresh_engine()
        instr = _eq_instr(symbol="RELIANCE")
        intent = _buy_intent(instr, ref_price=1000.0, stop_price=980.0)
        order = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(order, SizedOrder)
        engine.register_fill(order)
        assert engine.state.symbol_open_counts.get("RELIANCE", 0) == 1
        engine.reset_for_session(NEXT_DATE)
        assert engine.state.symbol_open_counts.get("RELIANCE", 0) == 0

    def test_reset_clears_committed_margin(self):
        engine = _fresh_engine()
        instr = _eq_instr()
        intent = _buy_intent(instr, ref_price=1000.0, stop_price=980.0)
        order = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(order, SizedOrder)
        engine.register_fill(order)
        assert engine.state.committed_margin > 0
        engine.reset_for_session(NEXT_DATE)
        assert engine.state.committed_margin == pytest.approx(0.0)

    def test_same_date_reset_preserves_symbol_counts(self):
        """Same-date reset is idempotent — does NOT clear symbol counts."""
        engine = _fresh_engine()
        instr = _eq_instr(symbol="RELIANCE")
        intent = _buy_intent(instr, ref_price=1000.0, stop_price=980.0)
        order = engine.size(intent, _snapshot(), SESSION_DATE)
        assert isinstance(order, SizedOrder)
        engine.register_fill(order)
        engine.reset_for_session(SESSION_DATE)  # no-op
        assert engine.state.symbol_open_counts.get("RELIANCE", 0) == 1
