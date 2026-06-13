"""Tests for ZerodteStraddleLive + ZerodteFixedLotEngine.

Coverage
--------
1. test_non_expiry_day_no_subscribe:
   Non-expiry day → subscribe_cb never called, no trades all session.

2. test_expiry_day_both_legs_subscribed_at_0920:
   Expiry day → both CE and PE subscribed exactly at 09:20 NIFTY bar.

3. test_two_sell_intents_one_lot_each:
   Two SELL OrderIntents emitted (1 lot each) on first option bars.

4. test_basket_stop_exits_both_legs:
   Combined premium >= 1.25 × entry → both legs exit via manage().

5. test_1510_flat_exits_both:
   15:10 voluntary flat exits both legs.

6. test_sizing_rejects_oversize_premium:
   ZerodteFixedLotEngine rejects when 0.25 × ref × lot > 1.2% capital.

7. test_replay_integration_win_day (skip if no data):
   Feed stored 2026-05-05 0DTE bars; assert P&L sign matches backtest (+).

8. test_replay_integration_loss_day (skip if no data):
   Feed stored 2025-02-06 0DTE bars; assert P&L sign matches backtest (-).

All 537 existing tests must remain green.
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
    OrderIntent,
    Position,
    RiskSnapshot,
    Segment,
    Side,
    SizedOrder,
)
from algotrader.data.instruments import FnoInstrument, NIFTY_FUT
from algotrader.paper.executor import PaperExecutor, ZerodteFixedLotEngine
from algotrader.paper.store import PaperStore
from algotrader.risk.engine import IntradayRiskEngine, RiskParams
from algotrader.strategies.zerodte_straddle_live import ZerodteStraddleLive

_IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Synthetic test date (a known Tuesday — NIFTY weekly expiry weekday)
# ---------------------------------------------------------------------------

_EXPIRY_DATE   = date(2026, 6, 17)   # synthetic expiry day for unit tests
_NON_EXPIRY    = date(2026, 6, 13)   # non-expiry day

# ---------------------------------------------------------------------------
# Stub option instruments
# ---------------------------------------------------------------------------

_STUB_CE = FnoInstrument(
    symbol="NIFTY-Jun2026-22000-CE",
    security_id="STUB_CE_0DTE",
    segment=Segment.NSE_FNO,
    tick_size=0.05,
    is_derivative=True,
    underlying="NIFTY",
    can_short_intraday=True,
    expiry_date=_EXPIRY_DATE,
)

_STUB_PE = FnoInstrument(
    symbol="NIFTY-Jun2026-22000-PE",
    security_id="STUB_PE_0DTE",
    segment=Segment.NSE_FNO,
    tick_size=0.05,
    is_derivative=True,
    underlying="NIFTY",
    can_short_intraday=True,
    expiry_date=_EXPIRY_DATE,
)

# Non-expiry stub (expiry_date != session_date)
_STUB_NEXT_EXPIRY = date(2026, 6, 24)
_STUB_CE_NEXT = FnoInstrument(
    symbol="NIFTY-Jun2026-22000-CE-next",
    security_id="STUB_CE_NEXT",
    segment=Segment.NSE_FNO,
    tick_size=0.05,
    is_derivative=True,
    underlying="NIFTY",
    can_short_intraday=True,
    expiry_date=_STUB_NEXT_EXPIRY,
)


# ---------------------------------------------------------------------------
# Resolver stubs
# ---------------------------------------------------------------------------

def _expiry_day_resolver(underlying, strike, opt_type, on):
    """Returns an option whose expiry_date == on (expiry day stub)."""
    assert underlying == "NIFTY"
    if opt_type == "CE":
        return _STUB_CE
    return _STUB_PE


def _non_expiry_resolver(underlying, strike, opt_type, on):
    """Returns an option whose expiry_date != on (non-expiry day stub)."""
    return _STUB_CE_NEXT


def _stub_nearest_atm(underlying, spot):
    return round(spot / 50) * 50.0


# ---------------------------------------------------------------------------
# Bar builders
# ---------------------------------------------------------------------------

def _nifty_bar(ts_open: datetime, close: float = 22000.0) -> Bar:
    o = close - 5.0
    return Bar(
        instrument=NIFTY_FUT,
        ts_open=ts_open,
        interval_min=1,
        open=o, high=close + 5.0, low=o - 3.0, close=close,
        volume=1000, complete=True,
    )


def _opt_bar(instr: FnoInstrument, ts_open: datetime, close: float) -> Bar:
    o = close * 0.97
    return Bar(
        instrument=instr,
        ts_open=ts_open,
        interval_min=1,
        open=o, high=close * 1.03, low=o * 0.97, close=close,
        volume=200, complete=True,
    )


def _make_nifty_bars_to_0919(session_date: date) -> list[Bar]:
    """NIFTY-FUT bars from 09:15 to 09:18 (ts_close up to 09:19)."""
    bars = []
    start = datetime(session_date.year, session_date.month, session_date.day,
                     9, 15, 0, tzinfo=_IST)
    for i in range(4):   # 09:15, 09:16, 09:17, 09:18 → ts_close 09:16..09:19
        bars.append(_nifty_bar(start + timedelta(minutes=i)))
    return bars


def _nifty_0919_bar(session_date: date, close: float = 22000.0) -> Bar:
    """The NIFTY-FUT bar whose ts_close == 09:20 (entry trigger)."""
    ts = datetime(session_date.year, session_date.month, session_date.day,
                  9, 19, 0, tzinfo=_IST)
    return _nifty_bar(ts, close)


# ---------------------------------------------------------------------------
# Risk engine for tests
# ---------------------------------------------------------------------------

def _test_risk_engine(capital: float = 500_000.0) -> IntradayRiskEngine:
    return IntradayRiskEngine(RiskParams(
        capital=capital,
        hard_floor=400_000.0,
        max_daily_loss_pct=0.03,
        per_trade_risk_pct=0.0075,
        max_open_positions=4,
        max_per_symbol=1,
    ))


def _test_executor(
    session_date: date,
    tmp_path: Path,
    resolver=None,
    subscribe_cb=None,
    capital: float = 500_000.0,
    account_id: str = "paper-0dte-test",
) -> PaperExecutor:
    strat = ZerodteStraddleLive(
        subscribe_cb=subscribe_cb,
        option_resolver=resolver or _expiry_day_resolver,
        nearest_atm=_stub_nearest_atm,
    )
    inner = _test_risk_engine(capital)
    risk  = ZerodteFixedLotEngine(inner)
    store = PaperStore(db_path=tmp_path / f"{account_id}.db")
    return PaperExecutor(
        account_id=account_id,
        strategies=[strat],
        risk_engine=risk,
        cost_model=DhanCosts(),
        store=store,
    )


# ===========================================================================
# Test 1: non-expiry day → no subscribe, no trade
# ===========================================================================

def test_non_expiry_day_no_subscribe(tmp_path: Path) -> None:
    """On a non-expiry day subscribe_cb is never called and no trade fires."""
    subscribe_calls: list = []

    executor = _test_executor(
        _NON_EXPIRY, tmp_path,
        resolver=_non_expiry_resolver,
        subscribe_cb=subscribe_calls.append,
        account_id="test-nonexp",
    )
    strat = executor._strategies[0]

    # Feed NIFTY bars including the 09:19 bar (ts_close=09:20)
    for bar in _make_nifty_bars_to_0919(_NON_EXPIRY):
        executor.on_bar(bar)
    executor.on_bar(_nifty_0919_bar(_NON_EXPIRY))

    # Feed a few more bars well into the session
    start = datetime(_NON_EXPIRY.year, _NON_EXPIRY.month, _NON_EXPIRY.day,
                     9, 20, 0, tzinfo=_IST)
    for i in range(10):
        executor.on_bar(_nifty_bar(start + timedelta(minutes=i)))

    assert len(subscribe_calls) == 0, "subscribe_cb must not be called on non-expiry day"
    assert strat._is_expiry_day is False
    assert strat._entry_triggered is False
    assert len(executor.trade_records) == 0


# ===========================================================================
# Test 2: expiry day → both legs subscribed at 09:20
# ===========================================================================

def test_expiry_day_both_legs_subscribed_at_0920(tmp_path: Path) -> None:
    """On expiry day, subscribe_cb is called with both CE and PE at 09:20."""
    subscribe_calls: list = []

    executor = _test_executor(
        _EXPIRY_DATE, tmp_path,
        subscribe_cb=subscribe_calls.append,
        account_id="test-sub",
    )
    strat = executor._strategies[0]

    # Feed bars up to 09:18 (ts_close 09:19) — no entry yet
    for bar in _make_nifty_bars_to_0919(_EXPIRY_DATE):
        executor.on_bar(bar)
    assert len(subscribe_calls) == 0, "No subscribe before 09:19 bar"

    # Feed the 09:19 bar (ts_close=09:20) — entry trigger
    executor.on_bar(_nifty_0919_bar(_EXPIRY_DATE))

    assert strat._is_expiry_day is True
    assert strat._entry_triggered is True
    assert len(subscribe_calls) == 1, f"Expected 1 subscribe call, got {len(subscribe_calls)}"

    # subscribe_cb receives a list of (sec_id, exchange, instr) tuples — both legs
    subs = subscribe_calls[0]
    assert isinstance(subs, list)
    assert len(subs) == 2, f"Expected 2 subscriptions (CE + PE), got {len(subs)}"

    syms = {instr.symbol for _, _, instr in subs}
    assert _STUB_CE.symbol in syms, f"CE not subscribed, got {syms}"
    assert _STUB_PE.symbol in syms, f"PE not subscribed, got {syms}"


# ===========================================================================
# Test 3: two SELL intents, 1 lot each, on first option bars
# ===========================================================================

def test_two_sell_intents_one_lot_each(tmp_path: Path) -> None:
    """First option bars → two SELL intents emitted, each sized to 1 lot."""
    executor = _test_executor(
        _EXPIRY_DATE, tmp_path,
        subscribe_cb=lambda s: None,
        account_id="test-intents",
    )
    strat = executor._strategies[0]

    # Feed NIFTY up to entry trigger
    for bar in _make_nifty_bars_to_0919(_EXPIRY_DATE):
        executor.on_bar(bar)
    executor.on_bar(_nifty_0919_bar(_EXPIRY_DATE))

    assert strat._entry_triggered

    # First CE bar
    ts_opt = datetime(_EXPIRY_DATE.year, _EXPIRY_DATE.month, _EXPIRY_DATE.day,
                      9, 20, 0, tzinfo=_IST)
    ce_prem = 80.0
    executor.on_bar(_opt_bar(_STUB_CE, ts_opt, ce_prem))

    # Still no open position — waiting for PE leg
    assert strat._entry_ce == ce_prem
    assert strat._entry_pe is None
    assert strat._traded is False

    # First PE bar → both entries known → intents emitted
    pe_prem = 75.0
    executor.on_bar(_opt_bar(_STUB_PE, ts_opt, pe_prem))

    assert strat._entry_pe == pe_prem
    assert strat._traded is True

    # There should be 2 pending orders (not yet filled — fill at next bar)
    assert len(executor._pending_orders) == 2, (
        f"Expected 2 pending SELL orders, got {len(executor._pending_orders)}"
    )

    for order in executor._pending_orders:
        assert isinstance(order, SizedOrder)
        assert order.intent.side is Side.SELL, "Expected SELL intents"
        assert order.quantity == 1, f"Expected 1 lot, got {order.quantity}"
        # Catastrophic stop must be ABOVE ref for SELL
        assert order.intent.stop_price > order.intent.ref_price, (
            f"stop {order.intent.stop_price} must be > ref {order.intent.ref_price} for SELL"
        )

    # No second set of intents on the next bar (already traded)
    ts2 = ts_opt + timedelta(minutes=1)
    executor.on_bar(_opt_bar(_STUB_CE, ts2, 78.0))
    executor.on_bar(_opt_bar(_STUB_PE, ts2, 73.0))
    # pending_orders cleared after fill; no new ones added
    assert len(executor._pending_orders) == 0


# ===========================================================================
# Test 4: combined 25% stop exits BOTH legs
# ===========================================================================

def test_basket_stop_exits_both_legs(tmp_path: Path) -> None:
    """Combined premium >= 1.25 × entry triggers exit on both legs."""
    executor = _test_executor(
        _EXPIRY_DATE, tmp_path,
        subscribe_cb=lambda s: None,
        account_id="test-basket",
    )
    strat = executor._strategies[0]

    # Enter the straddle
    for bar in _make_nifty_bars_to_0919(_EXPIRY_DATE):
        executor.on_bar(bar)
    executor.on_bar(_nifty_0919_bar(_EXPIRY_DATE))

    ts0 = datetime(_EXPIRY_DATE.year, _EXPIRY_DATE.month, _EXPIRY_DATE.day,
                   9, 20, 0, tzinfo=_IST)
    ce_entry, pe_entry = 80.0, 75.0
    executor.on_bar(_opt_bar(_STUB_CE, ts0, ce_entry))
    executor.on_bar(_opt_bar(_STUB_PE, ts0, pe_entry))

    # Fill on next bar
    ts1 = ts0 + timedelta(minutes=1)
    executor.on_bar(_opt_bar(_STUB_CE, ts1, 78.0))
    executor.on_bar(_opt_bar(_STUB_PE, ts1, 72.0))
    assert len(executor.open_positions) == 2, "Should have 2 open positions after fill"

    # Simulate bars where combined premium stays below stop
    ts2 = ts1 + timedelta(minutes=1)
    entry_combined = ce_entry + pe_entry   # 155.0
    stop_level     = entry_combined * 1.25  # 193.75

    # Feed bars with rising premiums, just below stop
    executor.on_bar(_opt_bar(_STUB_CE, ts2, 95.0))
    executor.on_bar(_opt_bar(_STUB_PE, ts2, 95.0))
    # ce_last=95, pe_last=95, combined=190 < 193.75 — not stopped yet
    assert len(executor.open_positions) == 2, "Positions should still be open"
    assert not strat._basket_exit

    # Push combined above stop: ce=100, pe=100 → combined=200 >= 193.75
    ts3 = ts2 + timedelta(minutes=1)
    executor.on_bar(_opt_bar(_STUB_CE, ts3, 100.0))
    # After this CE bar: ce_last=100, pe_last=95 → combined=195 >= 193.75 → STOP
    assert strat._basket_exit, "Basket exit should be latched after combined > stop"

    # Both positions should be in the force-exit queue; they close on next bar
    ts4 = ts3 + timedelta(minutes=1)
    executor.on_bar(_opt_bar(_STUB_CE, ts4, 100.0))
    executor.on_bar(_opt_bar(_STUB_PE, ts4, 100.0))

    # Both positions must now be closed
    assert len(executor.open_positions) == 0, (
        f"Expected 0 open positions after basket stop, got {len(executor.open_positions)}"
    )
    assert len(executor.trade_records) == 2, (
        f"Expected 2 closed trades, got {len(executor.trade_records)}"
    )


# ===========================================================================
# Test 5: 15:10 flat exits both legs
# ===========================================================================

def test_1510_flat_exits_both(tmp_path: Path) -> None:
    """manage() at ts_close >= 15:10 triggers voluntary exit for both legs."""
    executor = _test_executor(
        _EXPIRY_DATE, tmp_path,
        subscribe_cb=lambda s: None,
        account_id="test-flat",
    )
    strat = executor._strategies[0]

    # Enter
    for bar in _make_nifty_bars_to_0919(_EXPIRY_DATE):
        executor.on_bar(bar)
    executor.on_bar(_nifty_0919_bar(_EXPIRY_DATE))

    ts0 = datetime(_EXPIRY_DATE.year, _EXPIRY_DATE.month, _EXPIRY_DATE.day,
                   9, 20, 0, tzinfo=_IST)
    executor.on_bar(_opt_bar(_STUB_CE, ts0, 80.0))
    executor.on_bar(_opt_bar(_STUB_PE, ts0, 75.0))

    # Fill on next bar
    ts1 = ts0 + timedelta(minutes=1)
    executor.on_bar(_opt_bar(_STUB_CE, ts1, 78.0))
    executor.on_bar(_opt_bar(_STUB_PE, ts1, 72.0))
    assert len(executor.open_positions) == 2

    # Feed bars through the session; combined stays well below stop (< 1.25×)
    # Jump to 15:09 bar (ts_close=15:10) — manage() queues exit
    ts_trigger = datetime(_EXPIRY_DATE.year, _EXPIRY_DATE.month, _EXPIRY_DATE.day,
                          15, 9, 0, tzinfo=_IST)
    executor.on_bar(_opt_bar(_STUB_CE, ts_trigger, 40.0))
    # manage() for CE position fires at ts_close=15:10 → _basket_exit latched
    assert strat._basket_exit, "basket_exit should be latched at 15:10"

    # Force exits process at the NEXT bar
    ts_flush_ce = ts_trigger + timedelta(minutes=1)
    ts_flush_pe = ts_trigger + timedelta(minutes=1)
    executor.on_bar(_opt_bar(_STUB_CE, ts_flush_ce, 38.0))
    executor.on_bar(_opt_bar(_STUB_PE, ts_flush_pe, 36.0))

    assert len(executor.open_positions) == 0, (
        "All positions should be closed after 15:10 flat"
    )
    assert len(executor.trade_records) == 2

    # Both exits should be strategy exits (voluntary)
    for tr in executor.trade_records:
        assert tr.position.exit_reason is ExitReason.STRATEGY, (
            f"Expected STRATEGY exit, got {tr.position.exit_reason}"
        )


# ===========================================================================
# Test 6: sizing rejects oversize premium
# ===========================================================================

def test_sizing_rejects_oversize_premium() -> None:
    """ZerodteFixedLotEngine rejects when 0.25 × ref × lot > 1.2% capital."""
    from algotrader.core import Rejection, RiskSnapshot
    from algotrader.risk.engine import RiskParams, SessionRiskState

    capital = 200_000.0   # small capital so 1.2% = ₹2400
    inner = IntradayRiskEngine(RiskParams(
        capital=capital,
        hard_floor=150_000.0,
        max_daily_loss_pct=0.03,
        per_trade_risk_pct=0.0075,
        max_open_positions=4,
        max_per_symbol=1,
    ))
    inner.reset_for_session(date(2026, 6, 17))
    engine = ZerodteFixedLotEngine(inner)

    # ref_price=200 → implied_risk = 0.25 × 200 × 65 = 3250 > 2400 (1.2% × 200k)
    instr = _STUB_CE  # lot_size(on=2026-06-17) = 65
    intent = OrderIntent(
        strategy_id="zerodte_straddle",
        instrument=instr,
        side=Side.SELL,
        ref_price=200.0,
        stop_price=600.0,   # 3× ref (catastrophic stop for SELL)
        reason="test",
    )
    snap = RiskSnapshot(
        capital=capital,
        realized_pnl_today=0.0,
        unrealized_pnl=0.0,
        breaker_tripped=False,
        floor_breached=False,
        open_position_count=0,
    )
    result = engine.size(intent, snap, date(2026, 6, 17))
    assert isinstance(result, Rejection), (
        f"Expected Rejection for oversize premium, got {type(result).__name__}"
    )
    assert result.rule == "zerodte_oversize", (
        f"Expected 'zerodte_oversize' rule, got '{result.rule}'"
    )

    # ref_price=50 → implied_risk = 0.25 × 50 × 65 = 812.5 < 2400 → approved
    intent_ok = OrderIntent(
        strategy_id="zerodte_straddle",
        instrument=instr,
        side=Side.SELL,
        ref_price=50.0,
        stop_price=150.0,
        reason="test_ok",
    )
    result_ok = engine.size(intent_ok, snap, date(2026, 6, 17))
    assert isinstance(result_ok, SizedOrder), (
        f"Expected SizedOrder for small premium, got {type(result_ok).__name__}"
    )
    assert result_ok.quantity == 1


# ===========================================================================
# Replay integration helpers
# ===========================================================================

_DTE_DIR   = _PROJECT_ROOT / "data" / "cache" / "_OPTIONS" / "NIFTY_0DTE"
_NIFTY_DIR = _PROJECT_ROOT / "data" / "cache" / "NIFTY" / "1m"


def _load_0dte_bars(
    session_date_str: str,
    ce_instr: FnoInstrument,
    pe_instr: FnoInstrument,
) -> tuple[list[Bar], list[Bar]]:
    """Load stored 0DTE parquet bars and map them to stub instruments."""
    import pandas as pd

    def _read(leg: str, instr: FnoInstrument) -> list[Bar]:
        path = _DTE_DIR / f"{session_date_str}_{leg}.parquet"
        df = pd.read_parquet(path)
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(_IST)
        df["t"]  = df["ts"].dt.strftime("%H:%M")
        # Entry bar is 09:20; start from 09:20 (first option bar the strategy sees)
        df = df[df["t"] >= "09:20"].copy()
        bars = []
        for _, row in df.iterrows():
            c = float(row["close"])
            o = float(row["open"])
            h = float(row["high"])
            lo = float(row["low"])
            bars.append(Bar(
                instrument=instr,
                ts_open=row["ts"],
                interval_min=1,
                open=o, high=h, low=lo, close=c,
                volume=int(row.get("volume", 100)),
                complete=True,
            ))
        return bars

    return _read("CE", ce_instr), _read("PE", pe_instr)


def _load_nifty_bars(session_date_str: str) -> list[Bar]:
    """Load stored NIFTY 1m bars for the given date."""
    import pandas as pd

    year_month = session_date_str[:7]   # "YYYY-MM"
    path = _NIFTY_DIR / f"{year_month}.parquet"
    df   = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(_IST)
    df = df[df["ts"].dt.date.astype(str) == session_date_str].copy()
    df = df.sort_values("ts")

    bars = []
    for _, row in df.iterrows():
        bars.append(Bar(
            instrument=NIFTY_FUT,
            ts_open=row["ts"],
            interval_min=1,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row.get("volume", 1000)),
            complete=True,
        ))
    return bars


def _run_replay_integration(
    session_date_str: str,
    tmp_path: Path,
    backtest_sign: int,   # +1 for win, -1 for loss
) -> None:
    """End-to-end replay: feed stored NIFTY + 0DTE bars through executor.

    Asserts that net P&L sign matches backtest sign (identical stop logic
    confirms live strategy reproduces the validated backtest behaviour).
    """
    d = date.fromisoformat(session_date_str)

    # The stub resolver returns options with expiry_date == session date,
    # making every test day an "expiry day".
    ce_instr = FnoInstrument(
        symbol=f"NIFTY-0DTE-{session_date_str}-CE",
        security_id=f"CE_{session_date_str}",
        segment=Segment.NSE_FNO,
        tick_size=0.05,
        is_derivative=True,
        underlying="NIFTY",
        can_short_intraday=True,
        expiry_date=d,
    )
    pe_instr = FnoInstrument(
        symbol=f"NIFTY-0DTE-{session_date_str}-PE",
        security_id=f"PE_{session_date_str}",
        segment=Segment.NSE_FNO,
        tick_size=0.05,
        is_derivative=True,
        underlying="NIFTY",
        can_short_intraday=True,
        expiry_date=d,
    )

    # Resolver stubs: use strike to pick CE vs PE
    def _resolver(underlying, strike, opt_type, on):
        return ce_instr if opt_type == "CE" else pe_instr

    strat = ZerodteStraddleLive(
        subscribe_cb=lambda s: None,   # replay: no live feed
        option_resolver=_resolver,
        nearest_atm=_stub_nearest_atm,
    )

    # Use generous capital so sizing never hits margin cap
    inner = IntradayRiskEngine(RiskParams(
        capital=500_000.0,
        hard_floor=400_000.0,
        max_daily_loss_pct=0.10,
        per_trade_risk_pct=0.0075,
        max_open_positions=4,
        max_per_symbol=2,   # allow 2 positions (CE + PE same symbol class)
    ))
    risk  = ZerodteFixedLotEngine(inner)
    store = PaperStore(db_path=tmp_path / f"replay_{session_date_str}.db")
    executor = PaperExecutor(
        account_id=f"replay-{session_date_str}",
        strategies=[strat],
        risk_engine=risk,
        cost_model=DhanCosts(),
        store=store,
    )

    nifty_bars               = _load_nifty_bars(session_date_str)
    ce_bars, pe_bars         = _load_0dte_bars(session_date_str, ce_instr, pe_instr)

    # Merge NIFTY bars (09:15–15:29) with option bars (09:20–) sorted by ts_open
    all_bars = sorted(nifty_bars + ce_bars + pe_bars, key=lambda b: b.ts_open)

    for bar in all_bars:
        executor.on_bar(bar)

    trades = executor.trade_records
    assert len(trades) == 2, (
        f"Expected 2 closed trades (CE + PE) for {session_date_str}, got {len(trades)}"
    )

    net_pnl = sum(
        t.net_pnl if hasattr(t, "net_pnl") else t.position.gross_pnl()
        for t in trades
    )
    assert (net_pnl > 0) == (backtest_sign > 0), (
        f"P&L sign mismatch for {session_date_str}: live_net={net_pnl:.2f} "
        f"expected_sign={'+' if backtest_sign > 0 else '-'}"
    )


# ===========================================================================
# Test 7: replay integration — win day (2026-05-05, backtest net=+7424)
# ===========================================================================

@pytest.mark.skipif(
    not (_DTE_DIR / "2026-05-05_CE.parquet").exists()
    or not (_NIFTY_DIR / "2026-05.parquet").exists(),
    reason="0DTE / NIFTY 1m cache not available for 2026-05-05",
)
def test_replay_integration_win_day(tmp_path: Path) -> None:
    """Replay 2026-05-05 (backtest net=+₹7424, square_off) and assert P&L > 0."""
    _run_replay_integration("2026-05-05", tmp_path, backtest_sign=+1)


# ===========================================================================
# Test 8: replay integration — loss day (2025-02-06, backtest net=−2559)
#
# 2025-02-06 chosen because the actual market option prices clearly hit the
# basket stop (PE spikes from 57.95 to 89.05 at 09:31, combined=147.90 which
# is above the 145.88 stop level).  The actual market data aligns with the
# backtest model on this day, making it a reliable regression anchor.
# (2026-06-09 was originally planned but the parquet PE data showed stale
# prices at 11:22 — PE=57 when intrinsic was 129 — so the combined market
# price never reached the stop level even though the BS model did.)
# ===========================================================================

@pytest.mark.skipif(
    not (_DTE_DIR / "2025-02-06_CE.parquet").exists()
    or not (_NIFTY_DIR / "2025-02.parquet").exists(),
    reason="0DTE / NIFTY 1m cache not available for 2025-02-06",
)
def test_replay_integration_loss_day(tmp_path: Path) -> None:
    """Replay 2025-02-06 (backtest net=−₹2559, stop at 09:31) and assert P&L < 0."""
    _run_replay_integration("2025-02-06", tmp_path, backtest_sign=-1)


# ===========================================================================
# Test 9: account registry includes paper-0dte with correct config
# ===========================================================================

def test_paper_0dte_in_account_registry() -> None:
    """ACCOUNTS dict has paper-0dte with zerodte_straddle expression."""
    from scripts.paper_trade import ACCOUNTS
    assert "paper-0dte" in ACCOUNTS, "'paper-0dte' missing from ACCOUNTS registry"
    cfg = ACCOUNTS["paper-0dte"]
    assert cfg["expression"] == "zerodte_straddle"
    assert cfg["minlot"] is False


# ===========================================================================
# Test 10: paper-0dte in default --accounts list
# ===========================================================================

def test_paper_0dte_in_default_accounts() -> None:
    """'paper-0dte' appears in the default --accounts list."""
    import scripts.paper_trade as pt_mod
    import argparse
    ns = pt_mod._parse_args([])
    assert "paper-0dte" in ns.accounts, (
        f"paper-0dte not in default accounts: {ns.accounts}"
    )
