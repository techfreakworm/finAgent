"""Tests for the BreadthRiderOptions strategy (OPTION-C PAPER COMPARISON).

Coverage
--------
1. test_signal_subscribe_pending:
   NIFTY-FUT signal at 10:15 → subscribe_cb called with option instrument,
   pending state set, [] returned (intent deferred).

2. test_intent_emitted_on_first_option_bar:
   After pending state is set, the first option bar → OrderIntent emitted
   with side=BUY, ref_price=bar.close, stop_price=0.55×ref.

3. test_executor_fills_next_bar:
   PaperExecutor with BreadthRiderOptions: intent emitted on first option
   bar → executor fills at NEXT option bar open (next-bar semantics).

4. test_premium_stop_exit:
   Open option position → option bar with low < stop_price → STOP exit.

5. test_1510_voluntary_exit:
   Open option position → manage() on bar at ts_close=15:10 → returns
   (None, True), triggering strategy exit.

6. test_no_signal_below_threshold:
   Breadth below 0.72 → subscribe_cb never called, no pending state.

7. test_account_registry_config:
   ACCOUNTS dict in paper_trade has correct configs for all four accounts.

8. test_replay_smoke_three_accounts:
   Full replay of 2026-06-10 with paper, paper-fut-c, paper-opt-c →
   no crash; all accounts report 0 trades (breadth=0.32 at 10:15 < 0.72).
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from algotrader.backtest.costs import DhanCosts
from algotrader.core import (
    Bar,
    ExitReason,
    IST,
    Instrument,
    OrderIntent,
    Position,
    RiskSnapshot,
    Segment,
    Side,
    SizedOrder,
)
from algotrader.data.instruments import FnoInstrument, NIFTY_FUT
from algotrader.paper.executor import PaperExecutor
from algotrader.paper.store import PaperStore
from algotrader.risk.engine import IntradayRiskEngine, RiskParams
from algotrader.strategies.breadth_rider_options import BreadthRiderOptions

_IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Synthetic test date and instruments
# ---------------------------------------------------------------------------

_SESSION_DATE = date(2026, 6, 13)

# Stub option instrument (no scrip master needed)
_STUB_OPTION = FnoInstrument(
    symbol="NIFTY-Jun2026-22300-CE",
    security_id="STUB_OPT_SEC_ID",
    segment=Segment.NSE_FNO,
    tick_size=0.05,
    is_derivative=True,
    underlying="NIFTY",
    can_short_intraday=True,
)


def _stub_resolver(underlying, strike, opt_type, on):
    """Test stub: returns _STUB_OPTION regardless of inputs."""
    assert underlying == "NIFTY", f"expected NIFTY, got {underlying}"
    assert opt_type in ("CE", "PE")
    return _STUB_OPTION


def _stub_nearest_atm(underlying, spot):
    return round(spot / 50) * 50.0


# ---------------------------------------------------------------------------
# Breadth lookup stub
# ---------------------------------------------------------------------------

def _make_breadth_stub(session_date: date, pct: float = 0.80):
    """Return a breadth_lookup callable that returns (pct, 50, pct-0.5) at
    the 10:15 bar boundary (ts_close = 10:15:00 IST on session_date)."""
    decision_dt = datetime(
        session_date.year, session_date.month, session_date.day,
        10, 15, 0, tzinfo=_IST,
    )
    epoch = int(decision_dt.timestamp())

    def _lookup(epoch_s: int):
        if epoch_s == epoch:
            return (pct, 50, pct - 0.5)
        return None

    return _lookup


# ---------------------------------------------------------------------------
# Synthetic NIFTY-FUT bars
# ---------------------------------------------------------------------------

def _nifty_bar(ts_open: datetime, close: float, n: int = 0) -> Bar:
    """Build a 1-min NIFTY-FUT bar with realistic OHLC around *close*."""
    o = close - 3.0
    return Bar(
        instrument=NIFTY_FUT,
        ts_open=ts_open,
        interval_min=1,
        open=o,
        high=close + 2.0,
        low=o - 1.0,
        close=close,
        volume=1000 + n,
        complete=True,
    )


def _opt_bar(ts_open: datetime, close: float) -> Bar:
    """Build a 1-min option bar with OHLC around *close*."""
    o = close * 0.97
    return Bar(
        instrument=_STUB_OPTION,
        ts_open=ts_open,
        interval_min=1,
        open=o,
        high=close * 1.02,
        low=o * 0.98,
        close=close,
        volume=200,
        complete=True,
    )


def _build_nifty_bars_upto_decision(session_date: date) -> list[Bar]:
    """Return NIFTY-FUT bars from 09:15 to 10:14 inclusive (60 bars).

    Bar 59 has ts_open=10:14, ts_close=10:15 — the decision bar.
    Prices trend upward (close > VWAP) to satisfy own-symbol VWAP agreement.
    """
    bars = []
    start = datetime(session_date.year, session_date.month, session_date.day,
                     9, 15, 0, tzinfo=_IST)
    for i in range(60):  # bars 0..59; bar 59 = 10:14 bar (closes at 10:15)
        ts = start + timedelta(minutes=i)
        # Monotonically increasing: 22000 + i*5 → VWAP will be below close
        close = 22000.0 + i * 5.0
        bars.append(_nifty_bar(ts, close, n=i))
    return bars


# ---------------------------------------------------------------------------
# Risk engine for options executor (small enough for 1-lot synthetic test)
# ---------------------------------------------------------------------------

def _opt_risk_engine() -> IntradayRiskEngine:
    """Risk engine sized for 0.75% risk on ₹5 L capital."""
    return IntradayRiskEngine(RiskParams(
        capital=500_000.0,
        hard_floor=400_000.0,
        max_daily_loss_pct=0.02,
        per_trade_risk_pct=0.0075,   # 0.75% → budget=₹3750
        max_open_positions=3,
        max_per_symbol=1,
    ))


# ===========================================================================
# Test 1: signal fires → subscribe_cb called + pending state set
# ===========================================================================

def test_signal_subscribe_pending() -> None:
    """At 10:15 breadth signal: subscribe_cb called with STUB_OPTION, [] returned."""
    subscribe_calls: list = []

    strat = BreadthRiderOptions(
        breadth_thr=0.72,
        decision_time="10:15",
        stop_atr_mult=2.0,
        trail_atr_mult=3.5,
        breadth_lookup=_make_breadth_stub(_SESSION_DATE, pct=0.80),
        subscribe_cb=subscribe_calls.append,
        option_resolver=_stub_resolver,
        nearest_atm=_stub_nearest_atm,
    )
    strat.strategy_id = "breadth_rider_options_nifty"

    bars = _build_nifty_bars_upto_decision(_SESSION_DATE)

    # Build a minimal executor to get a real SessionContext
    store = PaperStore(db_path=Path("/tmp/test_opts_subscribe.db"))
    executor = PaperExecutor(
        account_id="paper-opt-c-test",
        strategies=[strat],
        risk_engine=_opt_risk_engine(),
        cost_model=DhanCosts(),
        store=store,
    )

    # Feed all 60 NIFTY bars; the last bar (bar 59) closes at 10:15
    for bar in bars:
        executor.on_bar(bar)

    # subscribe_cb should have been called exactly once (the 10:15 decision bar)
    assert len(subscribe_calls) == 1, (
        f"Expected 1 subscribe call, got {len(subscribe_calls)}"
    )
    # The subscription tuple list should contain the stub option
    subs = subscribe_calls[0]
    assert isinstance(subs, list)
    assert len(subs) == 1
    sec_id, seg, instr = subs[0]
    assert sec_id == _STUB_OPTION.security_id
    assert instr.symbol == _STUB_OPTION.symbol

    # No trade yet (pending state — option bar not yet arrived)
    assert len(executor.open_positions) == 0
    assert len(executor.trade_records) == 0

    # Pending state should be set inside the strategy
    assert strat._pending_option_instr is not None
    assert strat._pending_option_instr.symbol == _STUB_OPTION.symbol


# ===========================================================================
# Test 2: first option bar → OrderIntent emitted
# ===========================================================================

def test_intent_emitted_on_first_option_bar() -> None:
    """Intent is emitted with correct side/stop when first option bar arrives."""
    subscribe_calls: list = []

    strat = BreadthRiderOptions(
        breadth_thr=0.72,
        decision_time="10:15",
        stop_atr_mult=2.0,
        trail_atr_mult=3.5,
        breadth_lookup=_make_breadth_stub(_SESSION_DATE, pct=0.80),
        subscribe_cb=subscribe_calls.append,
        option_resolver=_stub_resolver,
        nearest_atm=_stub_nearest_atm,
    )
    strat.strategy_id = "breadth_rider_options_nifty"

    store = PaperStore(db_path=Path("/tmp/test_opts_intent.db"))
    executor = PaperExecutor(
        account_id="paper-opt-c-test2",
        strategies=[strat],
        risk_engine=_opt_risk_engine(),
        cost_model=DhanCosts(),
        store=store,
    )

    # Feed NIFTY bars up to decision bar
    bars = _build_nifty_bars_upto_decision(_SESSION_DATE)
    for bar in bars:
        executor.on_bar(bar)

    assert strat._pending_option_instr is not None, "Pending state should be set"

    # Feed first option bar (ts_open = 10:15 — same minute as decision close)
    # Use ref_price = 90 so that risk_per_lot = 0.45 * 90 * 65 = 2632.5 < budget 3750
    # → 1 lot fits the 0.75% risk budget without rejection.
    opt_close = 90.0
    opt_ts = datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day,
                      10, 15, 0, tzinfo=_IST)
    first_opt_bar = _opt_bar(opt_ts, opt_close)
    executor.on_bar(first_opt_bar)

    # Pending state should now be cleared and trade in pending orders
    assert strat._pending_option_instr is None, "Pending state should be cleared after first opt bar"
    assert strat._traded is True

    # Executor should have a pending order (not yet filled — fill at next bar)
    # No open positions yet (fill is at next bar open)
    assert len(executor.open_positions) == 0
    assert len(executor._pending_orders) == 1

    order = executor._pending_orders[0]
    assert isinstance(order, SizedOrder)
    assert order.intent.side is Side.BUY
    assert abs(order.intent.ref_price - opt_close) < 0.01
    expected_stop = opt_close * 0.55
    assert abs(order.intent.stop_price - expected_stop) < 0.01, (
        f"stop_price={order.intent.stop_price:.2f} != 0.55×{opt_close}={expected_stop:.2f}"
    )
    assert order.intent.target_price is None
    assert order.intent.trail_atr_mult is None


# ===========================================================================
# Test 3: executor fills at NEXT option bar open
# ===========================================================================

def test_executor_fills_next_bar(tmp_path: Path) -> None:
    """PaperExecutor fills the option intent at the next option bar's open."""
    strat = BreadthRiderOptions(
        breadth_thr=0.72,
        decision_time="10:15",
        stop_atr_mult=2.0,
        trail_atr_mult=3.5,
        breadth_lookup=_make_breadth_stub(_SESSION_DATE, pct=0.80),
        subscribe_cb=lambda s: None,
        option_resolver=_stub_resolver,
        nearest_atm=_stub_nearest_atm,
    )
    strat.strategy_id = "breadth_rider_options_nifty"

    store = PaperStore(db_path=tmp_path / "paper_opt_fill.db")
    executor = PaperExecutor(
        account_id="paper-opt-c-fill",
        strategies=[strat],
        risk_engine=_opt_risk_engine(),
        cost_model=DhanCosts(),
        store=store,
    )

    # Feed NIFTY bars to trigger signal
    for bar in _build_nifty_bars_upto_decision(_SESSION_DATE):
        executor.on_bar(bar)

    # First option bar → pending intent queued
    opt_close = 120.0
    ts_opt_1 = datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day,
                        10, 15, 0, tzinfo=_IST)
    executor.on_bar(_opt_bar(ts_opt_1, opt_close))
    assert len(executor._pending_orders) == 1, "Intent should be pending after first opt bar"

    # Second option bar → executor fills at this bar's open
    ts_opt_2 = ts_opt_1 + timedelta(minutes=1)
    opt2_open = 118.0
    opt2_close = 125.0
    opt2_bar = Bar(
        instrument=_STUB_OPTION,
        ts_open=ts_opt_2,
        interval_min=1,
        open=opt2_open,
        high=130.0,
        low=115.0,
        close=opt2_close,
        volume=200,
        complete=True,
    )
    executor.on_bar(opt2_bar)

    # Now should have 1 open position filled at bar2's open (± slippage)
    assert len(executor.open_positions) == 1, "Should have 1 open position after fill"
    pos = next(iter(executor.open_positions.values()))
    assert pos.instrument.symbol == _STUB_OPTION.symbol
    assert pos.side is Side.BUY
    # Fill is at next-bar open ± slippage (BUY: open + slippage ≥ open)
    assert pos.entry_price >= opt2_open, (
        f"Entry {pos.entry_price:.2f} should be >= opt2 open {opt2_open:.2f} (BUY)"
    )
    assert abs(pos.entry_price - opt2_open) <= 5.0, (
        f"Slippage too large: entry={pos.entry_price:.2f}, open={opt2_open:.2f}"
    )
    # Stop should be near 0.55 × first opt bar close
    expected_stop = opt_close * 0.55
    assert abs(pos.stop_price - expected_stop) < 1.0, (
        f"stop_price={pos.stop_price:.2f} should be ~{expected_stop:.2f} (0.55×{opt_close})"
    )


# ===========================================================================
# Test 4: premium stop exit
# ===========================================================================

def test_premium_stop_exit(tmp_path: Path) -> None:
    """Option position closes with ExitReason.STOP when bar.low < stop_price."""
    strat = BreadthRiderOptions(
        breadth_thr=0.72,
        decision_time="10:15",
        stop_atr_mult=2.0,
        trail_atr_mult=3.5,
        breadth_lookup=_make_breadth_stub(_SESSION_DATE, pct=0.80),
        subscribe_cb=lambda s: None,
        option_resolver=_stub_resolver,
        nearest_atm=_stub_nearest_atm,
    )
    strat.strategy_id = "breadth_rider_options_nifty"

    store = PaperStore(db_path=tmp_path / "paper_opt_stop.db")
    executor = PaperExecutor(
        account_id="paper-opt-c-stop",
        strategies=[strat],
        risk_engine=_opt_risk_engine(),
        cost_model=DhanCosts(),
        store=store,
    )

    # Feed NIFTY bars to trigger signal
    for bar in _build_nifty_bars_upto_decision(_SESSION_DATE):
        executor.on_bar(bar)

    # First option bar at ref_price = 100 → stop = 55
    opt_close = 100.0
    ts_opt_1 = datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day,
                        10, 15, 0, tzinfo=_IST)
    executor.on_bar(_opt_bar(ts_opt_1, opt_close))

    # Second option bar → fill at open (~97)
    ts_opt_2 = ts_opt_1 + timedelta(minutes=1)
    executor.on_bar(Bar(
        instrument=_STUB_OPTION,
        ts_open=ts_opt_2,
        interval_min=1,
        open=97.0, high=105.0, low=90.0, close=102.0,
        volume=200, complete=True,
    ))
    assert len(executor.open_positions) == 1, "Should have open position after fill"
    pos = next(iter(executor.open_positions.values()))
    stop_price = pos.stop_price  # should be ~55

    # Third option bar: low sweeps below stop → STOP triggered
    ts_opt_3 = ts_opt_2 + timedelta(minutes=1)
    # low < stop_price to force STOP
    low_val = stop_price - 5.0
    executor.on_bar(Bar(
        instrument=_STUB_OPTION,
        ts_open=ts_opt_3,
        interval_min=1,
        open=stop_price + 2.0,  # opens above stop
        high=stop_price + 3.0,
        low=low_val,             # sweeps through stop
        close=low_val + 1.0,
        volume=200, complete=True,
    ))

    assert len(executor.open_positions) == 0, "Position should be closed after stop"
    assert len(executor.trade_records) == 1
    trade = executor.trade_records[0]
    if hasattr(trade, "position"):
        assert trade.position.exit_reason is ExitReason.STOP, (
            f"Expected STOP exit, got {trade.position.exit_reason}"
        )


# ===========================================================================
# Test 5: 15:10 voluntary exit via manage()
# ===========================================================================

def test_1510_voluntary_exit(tmp_path: Path) -> None:
    """manage() returns (None, True) at ts_close >= 15:10, triggering exit."""
    strat = BreadthRiderOptions(
        breadth_thr=0.72,
        decision_time="10:15",
        stop_atr_mult=2.0,
        trail_atr_mult=3.5,
        breadth_lookup=_make_breadth_stub(_SESSION_DATE, pct=0.80),
        subscribe_cb=lambda s: None,
        option_resolver=_stub_resolver,
        nearest_atm=_stub_nearest_atm,
    )
    strat.strategy_id = "breadth_rider_options_nifty"

    store = PaperStore(db_path=tmp_path / "paper_opt_flat.db")
    executor = PaperExecutor(
        account_id="paper-opt-c-flat",
        strategies=[strat],
        risk_engine=_opt_risk_engine(),
        cost_model=DhanCosts(),
        store=store,
    )

    # Signal + fill
    # Use ref_price=100 → risk_per_lot=0.45*100*65=2925 < 3750 → 1 lot fits
    for bar in _build_nifty_bars_upto_decision(_SESSION_DATE):
        executor.on_bar(bar)

    opt_close = 100.0
    ts_opt_1 = datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day,
                        10, 15, 0, tzinfo=_IST)
    executor.on_bar(_opt_bar(ts_opt_1, opt_close))

    ts_opt_2 = ts_opt_1 + timedelta(minutes=1)
    executor.on_bar(_opt_bar(ts_opt_2, 98.0))
    assert len(executor.open_positions) == 1, "Should have open position"

    # The voluntary exit is QUEUED by manage() at the 15:09 bar (ts_close=15:10)
    # and is PROCESSED at the NEXT bar (step 2 of executor.on_bar).
    # So we need two more bars: the trigger bar + one flush bar.
    ts_1509 = datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day,
                       15, 9, 0, tzinfo=_IST)
    executor.on_bar(_opt_bar(ts_1509, 95.0))   # manage() queues STRATEGY exit here

    # Still open after the trigger bar — force exit processes on NEXT bar
    assert len(executor.open_positions) == 1, (
        "Position still open after trigger bar (exit is processed at next bar)"
    )

    # Feed a flush bar (15:10): force exits are processed at step 2
    ts_1510 = ts_1509 + timedelta(minutes=1)
    executor.on_bar(_opt_bar(ts_1510, 94.0))

    # Now position should be closed with STRATEGY exit
    assert len(executor.open_positions) == 0, (
        "Position should be closed at 15:10 voluntary exit (flushed on next bar)"
    )
    assert len(executor.trade_records) == 1
    trade = executor.trade_records[0]
    if hasattr(trade, "position"):
        assert trade.position.exit_reason is ExitReason.STRATEGY, (
            f"Expected STRATEGY exit (voluntary), got {trade.position.exit_reason}"
        )


# ===========================================================================
# Test 6: no signal below threshold
# ===========================================================================

def test_no_signal_below_threshold() -> None:
    """subscribe_cb is NOT called when breadth is below the 0.72 threshold."""
    subscribe_calls: list = []

    strat = BreadthRiderOptions(
        breadth_thr=0.72,
        decision_time="10:15",
        stop_atr_mult=2.0,
        trail_atr_mult=3.5,
        breadth_lookup=_make_breadth_stub(_SESSION_DATE, pct=0.32),  # below 0.72
        subscribe_cb=subscribe_calls.append,
        option_resolver=_stub_resolver,
        nearest_atm=_stub_nearest_atm,
    )
    strat.strategy_id = "breadth_rider_options_nifty"

    store = PaperStore(db_path=Path("/tmp/test_opts_nosignal.db"))
    executor = PaperExecutor(
        account_id="paper-opt-c-nosig",
        strategies=[strat],
        risk_engine=_opt_risk_engine(),
        cost_model=DhanCosts(),
        store=store,
    )

    for bar in _build_nifty_bars_upto_decision(_SESSION_DATE):
        executor.on_bar(bar)

    assert len(subscribe_calls) == 0, "No subscribe call for breadth < threshold"
    assert strat._pending_option_instr is None
    assert strat._traded is False
    assert len(executor.trade_records) == 0


# ===========================================================================
# Test 7: account registry config
# ===========================================================================

def test_account_registry_config() -> None:
    """ACCOUNTS dict contains correct configs for all four canonical accounts."""
    from scripts.paper_trade import ACCOUNTS

    # 'paper' baseline
    assert "paper" in ACCOUNTS
    p = ACCOUNTS["paper"]
    assert p["per_trade_risk_pct"] == 0.0075
    assert p["minlot"] is False
    assert p["expression"] == "futures"

    # 'paper-minlot' — legacy min-lot override
    assert "paper-minlot" in ACCOUNTS
    pm = ACCOUNTS["paper-minlot"]
    assert pm["per_trade_risk_pct"] == 0.0075
    assert pm["minlot"] is True
    assert pm["expression"] == "futures"

    # 'paper-fut-c' — comparison leg A: 1.75% risk, minlot, 2% cap
    assert "paper-fut-c" in ACCOUNTS
    pfc = ACCOUNTS["paper-fut-c"]
    assert abs(pfc["per_trade_risk_pct"] - 0.0175) < 1e-9
    assert pfc["minlot"] is True
    assert abs(pfc["minlot_cap_pct"] - 0.02) < 1e-9
    assert pfc["expression"] == "futures"

    # 'paper-opt-c' — comparison leg B: options expression, 0.75% risk
    assert "paper-opt-c" in ACCOUNTS
    poc = ACCOUNTS["paper-opt-c"]
    assert poc["per_trade_risk_pct"] == 0.0075
    assert poc["minlot"] is False
    assert poc["expression"] == "options"


# ===========================================================================
# Test 8: replay smoke — 3 accounts, no signal day, pipeline must not crash
# ===========================================================================

_REPLAY_DATE = date(2026, 6, 10)
_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
_BREADTH_PARQUET = _CACHE / "_BREADTH" / "5m" / "breadth.parquet"


@pytest.mark.skipif(
    not (_CACHE / "NIFTY" / "1m" / "2026-06.parquet").exists(),
    reason="NIFTY 1m cache not available",
)
def test_replay_smoke_three_accounts(tmp_path: Path) -> None:
    """Replay 2026-06-10 with paper, paper-fut-c, paper-opt-c.

    Breadth at 10:15 is 0.32 — below the 0.72 threshold — so no signal fires
    for any account.  All three executors must report 0 trades and the replay
    must complete without error.
    """
    import scripts.paper_trade as _pt_mod
    from scripts.paper_trade import _run_replay
    from algotrader.paper.executor import PaperExecutor

    original_build = _pt_mod._build_executor

    def _patched_build(account_id, session_date, store_path=None, **kwargs):
        return original_build(
            account_id, session_date,
            store_path=tmp_path / f"paper_{account_id}.db",
            **kwargs,
        )

    _pt_mod._build_executor = _patched_build
    try:
        args = argparse.Namespace(
            accounts=["paper", "paper-fut-c", "paper-opt-c"],
        )
        executors = _run_replay(args, _REPLAY_DATE)
    finally:
        _pt_mod._build_executor = original_build

    assert len(executors) == 3, f"Expected 3 executors, got {len(executors)}"

    for exec_ in executors:
        assert isinstance(exec_, PaperExecutor)
        trades = exec_.trade_records
        assert len(trades) == 0, (
            f"Expected 0 trades for {exec_.account_id} on 2026-06-10 "
            f"(breadth=0.32 < 0.72 threshold), got {len(trades)}"
        )

    # Verify account IDs are correct
    account_ids = {e.account_id for e in executors}
    assert account_ids == {"paper", "paper-fut-c", "paper-opt-c"}
