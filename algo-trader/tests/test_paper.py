"""Paper trading executor + store tests.

Assignment assertions:
  (a) next-bar-open fill: entry fills at bar N+1 open ± slippage, not at bar N close.
  (b) stop exit: position eventually closes at ExitReason.STOP.
  (c) persistence: trades and positions rows exist in DB after fill/close.
  (d) SIGKILL-sim: new executor instance reconciles open position, does NOT
      duplicate fills or DB rows.
  (e) force-flat at 15:19:30: open positions square-off when ts_close ≥ 15:19:30.

Test strategy: replay real NIFTY 1-min bars from
  data/cache/NIFTY/1m/2026-06.parquet  (2026-06-09 session).

A stub strategy (StubStrategy) emits ONE BUY intent on bar-20 (09:35) with
stop 20 points below bar.close.  On the real NIFTY data for 2026-06-09:
  - bar-20 close ≈ 23236.65, stop ≈ 23216.65
  - bar-22 (09:37) open=23219.55 > stop; low=23209.00 < stop → STOP triggers
    via branch-B (intrabar sweep) at bar-22.

ARCHITECTURE §6: paper executor semantics must be identical to the backtest
engine (no look-ahead, next-bar fills, two-branch stops, clock guards).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from algotrader.backtest.costs import DhanCosts
from algotrader.core import (
    Bar,
    ExitReason,
    IST,
    Instrument,
    OrderIntent,
    Position,
    Rejection,
    RiskSnapshot,
    Segment,
    SessionContext,
    Side,
    SizedOrder,
    Strategy,
)
from algotrader.paper.executor import MinLotOverrideEngine, PaperExecutor
from algotrader.paper.store import PaperStore
from algotrader.risk.engine import IntradayRiskEngine, RiskParams

# ---------------------------------------------------------------------------
# Shared test fixtures and helpers
# ---------------------------------------------------------------------------

_IST = ZoneInfo("Asia/Kolkata")
_SESSION_DATE = date(2026, 6, 9)

# Instrument matching the NIFTY data in the parquet file (equity proxy — no
# derivative lot-size complexity needed for this test suite).
NIFTY_INSTR = Instrument(
    symbol="NIFTY",
    security_id="13",
    segment=Segment.NSE_EQ,
    tick_size=0.05,
    is_derivative=False,
    can_short_intraday=True,
)

_PARQUET_PATH = (
    Path(__file__).resolve().parent.parent
    / "data" / "cache" / "NIFTY" / "1m" / "2026-06.parquet"
)


def _load_nifty_day(session_date: date = _SESSION_DATE) -> list[Bar]:
    """Load 1-min bars for the NIFTY session from the parquet cache."""
    df = pd.read_parquet(_PARQUET_PATH)
    day = df[df["ts"].dt.date == session_date].copy().reset_index(drop=True)
    bars = []
    for _, row in day.iterrows():
        ts_open = pd.Timestamp(row["ts"]).to_pydatetime()
        if ts_open.tzinfo is None:
            ts_open = ts_open.replace(tzinfo=_IST)
        bars.append(
            Bar(
                instrument=NIFTY_INSTR,
                ts_open=ts_open,
                interval_min=1,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                complete=True,
            )
        )
    return bars


def _default_risk_engine() -> IntradayRiskEngine:
    # Capital is set generously so that equity MIS margin (price*qty/5) passes
    # for NIFTY-level (~23000) prices.  With 0.75% risk on 10M, risk_budget=75000;
    # qty = floor(75000/20) = 3750; margin = 23236*3750/5 = 17.4M > 10M → rejection.
    # Use 0.02% risk to get a small qty that fits: risk_budget=2000; qty=100;
    # margin=23236*100/5=464720 which fits 10M capital.
    return IntradayRiskEngine(
        RiskParams(
            capital=10_000_000.0,   # 10M — large so equity MIS margin fits NIFTY prices
            hard_floor=8_000_000.0,
            max_daily_loss_pct=0.02,
            per_trade_risk_pct=0.0002,  # 0.02% risk → small qty → margin fits
            max_open_positions=3,
            max_per_symbol=1,
        )
    )


# ---------------------------------------------------------------------------
# StubStrategy
# ---------------------------------------------------------------------------

class StubStrategy(Strategy):
    """Emits ONE BUY intent on bar-index 20 (09:35 on 2026-06-09) with a stop
    20 points below bar.close.  Will not re-enter if already in a position or
    if _fired is True.

    Implements the Strategy ABC (on_bar + manage).
    """

    strategy_id = "stub"
    warmup_bars = 0
    warmup_days = 0

    def __init__(self, fire_at_bar_index: int = 20, stop_distance: float = 20.0) -> None:
        self._fire_at = fire_at_bar_index
        self._stop_distance = stop_distance
        self._fired: bool = False
        self._session_date = None

    def on_bar(self, ctx: SessionContext, bar: Bar) -> list[OrderIntent]:
        # Session boundary reset
        d = bar.ts_open.date()
        if d != self._session_date:
            self._session_date = d
            self._fired = False

        if self._fired:
            return []
        # Only enter if no existing position for this strategy
        if ctx.open_positions("stub"):
            return []
        # Fire at the specific bar index: count bars seen today
        today_bars = ctx.bars(bar.instrument, 500)
        if len(today_bars) != self._fire_at + 1:
            # +1 because the current bar is already appended to history
            return []
        self._fired = True
        stop = bar.close - self._stop_distance
        return [
            OrderIntent(
                strategy_id="stub",
                instrument=bar.instrument,
                side=Side.BUY,
                ref_price=bar.close,
                stop_price=stop,
                target_price=None,
                reason="stub_test",
            )
        ]

    def manage(self, ctx, bar, position) -> tuple[float | None, bool]:
        return None, False


# ---------------------------------------------------------------------------
# Helpers for building a PaperExecutor
# ---------------------------------------------------------------------------

def _make_executor(
    account_id: str,
    store: PaperStore,
    strategy: Strategy | None = None,
    risk_engine: IntradayRiskEngine | None = None,
) -> PaperExecutor:
    return PaperExecutor(
        account_id=account_id,
        strategies=[strategy or StubStrategy()],
        risk_engine=risk_engine or _default_risk_engine(),
        cost_model=DhanCosts(),
        store=store,
    )


# ---------------------------------------------------------------------------
# (a) + (b) + (c): next-bar fill, stop exit, persistence
# ---------------------------------------------------------------------------

def test_next_bar_fill_stop_exit_and_persistence(tmp_path: Path) -> None:
    """(a) entry fills at next bar's open; (b) stop exits; (c) rows in DB."""
    bars = _load_nifty_day()
    store = PaperStore(db_path=tmp_path / "paper.db")
    exec1 = _make_executor("paper", store)

    # Feed bars 0..22 (bar-20 emits intent, bar-21 fills, bar-22 hits stop)
    fill_bar_open: float | None = None
    for i, bar in enumerate(bars[:23]):
        exec1.on_bar(bar)
        if i == 21:
            # After bar 21: entry should be filled
            assert len(exec1.open_positions) == 1, (
                "Expected 1 open position after bar 21 (entry bar)"
            )
            pos = next(iter(exec1.open_positions.values()))
            # (a): fill price is at bar-21 open ± slippage, NOT bar-20 close
            bar21 = bars[21]
            assert abs(pos.entry_price - bar21.open) <= 10.0, (
                f"Fill price {pos.entry_price:.2f} should be near bar-21 open "
                f"{bar21.open:.2f}, not bar-20 close {bars[20].close:.2f}"
            )
            assert pos.entry_price > bars[20].close - 15, (
                "Fill should be at bar-21 open, not bar-20 close"
            )
            # entry_price should be bar21.open + slippage (BUY)
            assert pos.entry_price >= bar21.open, (
                f"BUY fill {pos.entry_price} should be >= bar21.open {bar21.open}"
            )
            fill_bar_open = bar21.open

        if i == 22:
            # (b): stop should have triggered at bar-22
            assert len(exec1.open_positions) == 0, (
                "Expected position closed at bar 22 (stop trigger)"
            )
            assert len(exec1.trade_records) == 1, "Expected 1 trade record"

    assert fill_bar_open is not None, "Entry was never filled"

    # (b): verify stop exit
    assert len(exec1.trade_records) >= 1
    trade = exec1.trade_records[0]
    if hasattr(trade, 'position'):
        assert trade.position.exit_reason == ExitReason.STOP, (
            f"Expected STOP exit, got {trade.position.exit_reason}"
        )

    # (c): persistence — check DB rows
    trades = store.load_trades("paper", _SESSION_DATE)
    assert len(trades) == 1, f"Expected 1 trade row in DB, got {len(trades)}"
    t = trades[0]
    assert t["exit_reason"] == "stop"
    assert t["symbol"] == "NIFTY"
    assert t["side"] == "BUY"

    # open positions table should show the position as CLOSED
    open_rows = store.load_open_positions("paper", _SESSION_DATE)
    assert len(open_rows) == 0, "No open positions should remain after stop exit"

    # risk_state should be persisted
    state = store.load_risk_state("paper", _SESSION_DATE)
    assert state is not None
    assert state.session_date == _SESSION_DATE

    # events should contain fill and close entries
    events = store.load_events("paper")
    event_types = {e["event_type"] for e in events}
    assert "fill" in event_types
    assert "close" in event_types


# ---------------------------------------------------------------------------
# (d): SIGKILL simulation — reconcile open position, no duplicate
# ---------------------------------------------------------------------------

def test_sigkill_reconcile_no_duplicate(tmp_path: Path) -> None:
    """New executor instance after a 'crash' reconciles the open position
    from DB and continues processing.  No duplicate position or trade row."""
    bars = _load_nifty_day()
    db_path = tmp_path / "paper.db"

    # Phase 1: feed bars 0..21 — strategy fires on bar-20, fills on bar-21
    store1 = PaperStore(db_path=db_path)
    strat1 = StubStrategy()
    exec1 = _make_executor("paper", store1, strategy=strat1)
    for bar in bars[:22]:  # bars 0..21 inclusive
        exec1.on_bar(bar)

    # After bar-21: should have exactly 1 open position
    assert len(exec1.open_positions) == 1, (
        f"Expected 1 open position after bar 21, got {len(exec1.open_positions)}"
    )
    orig_pid = next(iter(exec1.open_positions))
    orig_pos = exec1.open_positions[orig_pid]

    # Phase 2: simulate SIGKILL — create a brand-new executor with the same DB
    store2 = PaperStore(db_path=db_path)
    strat2 = StubStrategy()   # fresh strategy, _fired=False
    exec2 = _make_executor("paper", store2, strategy=strat2)

    # Feed bar-22 — reconcile happens on first on_bar, then stop triggers
    exec2.on_bar(bars[22])

    # Reconcile should have restored the position before bar-22 processing
    # After bar-22 stop trigger, position should be closed
    assert len(exec2.open_positions) == 0, (
        "Position should be closed after bar-22 stop trigger"
    )
    assert len(exec2.trade_records) >= 1

    # DB: exactly 1 trade row (no duplicate)
    trades = store2.load_trades("paper", _SESSION_DATE)
    assert len(trades) == 1, (
        f"Expected exactly 1 trade row (no duplicate after restart), got {len(trades)}"
    )

    # The trade's position_id should match the original (not a new one)
    assert trades[0]["position_id"] == orig_pid, (
        f"Trade position_id {trades[0]['position_id']} != original {orig_pid}"
    )

    # (d): reconcile events logged
    events = store2.load_events("paper", event_type="reconcile")
    reconcile_msgs = [e["message"] for e in events]
    assert any("Reconciled 1 open position" in m for m in reconcile_msgs), (
        f"Expected reconcile event, got: {reconcile_msgs}"
    )

    # The new stub strategy should NOT have re-fired (no new position created)
    # Check no positions were created by strat2 by verifying only 1 trade total
    assert len(trades) == 1


# ---------------------------------------------------------------------------
# (e): Force-flat at 15:19:30
# ---------------------------------------------------------------------------

def test_force_flat_at_square_off_time(tmp_path: Path) -> None:
    """Positions are forcibly closed when ts_close >= 15:19:30 (SQUARE_OFF)."""
    bars = _load_nifty_day()
    store = PaperStore(db_path=tmp_path / "paper.db")
    exec1 = _make_executor("paper", store)

    # Feed bars 0..21 so we have an open position after the entry fill
    for bar in bars[:22]:
        exec1.on_bar(bar)

    # After bar-21 fill, before bar-22 stop, we're in a position
    # But bar-22 triggers the stop (branch B), so position is already closed
    # Let's verify stop is closed and then test force-flat with a separate scenario

    # For the force-flat test, we feed bars up to bar-21 (have open position),
    # then feed a bar whose ts_close >= 15:19:30.
    store_ff = PaperStore(db_path=tmp_path / "paper_ff.db")
    strat_ff = StubStrategy()
    exec_ff = _make_executor("paper", store_ff, strategy=strat_ff)

    # Feed bars 0..21 (entry fills at bar 21, stop NOT yet hit because bar 22 is not fed)
    for bar in bars[:22]:
        exec_ff.on_bar(bar)

    assert len(exec_ff.open_positions) == 1, (
        "Expected open position after bar-21 fill"
    )

    # Now synthesise a force-flat bar: ts_open=15:19:00, ts_close=15:20:00 >= 15:19:30
    # Use actual NIFTY 15:19 bar from the parquet for realistic prices
    # (bar index 364 is 15:19:00 → ts_close=15:20:00)
    flat_bar_raw = bars[364]  # 15:19:00 bar
    # Confirm this bar has ts_close >= 15:19:30
    assert flat_bar_raw.ts_close.timetz().replace(tzinfo=None) >= time(15, 19, 30), (
        f"Expected force-flat bar ts_close >= 15:19:30, got "
        f"{flat_bar_raw.ts_close.timetz().replace(tzinfo=None)}"
    )

    exec_ff.on_bar(flat_bar_raw)

    # All positions should be closed with SQUARE_OFF
    assert len(exec_ff.open_positions) == 0, (
        "All positions should be closed after force-flat bar"
    )
    assert len(exec_ff.trade_records) >= 1
    last_trade = exec_ff.trade_records[-1]
    if hasattr(last_trade, 'position'):
        assert last_trade.position.exit_reason == ExitReason.SQUARE_OFF, (
            f"Expected SQUARE_OFF, got {last_trade.position.exit_reason}"
        )

    # Verify in DB
    trades = store_ff.load_trades("paper", _SESSION_DATE)
    assert len(trades) >= 1
    assert trades[-1]["exit_reason"] == "square_off"

    # After force-flat, further bars should NOT re-open positions
    exec_ff.on_bar(bars[365])
    assert len(exec_ff.open_positions) == 0, "No new positions after force-flat"


# ---------------------------------------------------------------------------
# Extra: MinLotOverrideEngine unit test
# ---------------------------------------------------------------------------

def test_min_lot_override_engine(tmp_path: Path) -> None:
    """MinLotOverrideEngine promotes zero_qty Rejection to SizedOrder(qty=1)
    for derivatives when risk_budget * 2.7 >= risk_per_lot."""
    from algotrader.core import OrderIntent, Side, Segment

    deriv_instr = Instrument(
        symbol="NIFTY-FUT",
        security_id="1234",
        segment=Segment.NSE_FNO,
        tick_size=0.05,
        is_derivative=True,
        underlying="NIFTY",
        can_short_intraday=True,
    )

    # Very small per_trade_risk_pct so normal sizing yields qty=0
    inner = IntradayRiskEngine(
        RiskParams(
            capital=100_000.0,
            hard_floor=80_000.0,
            max_daily_loss_pct=0.02,
            per_trade_risk_pct=0.0001,   # tiny → qty=0 for big stop
            max_open_positions=3,
            max_per_symbol=1,
        )
    )
    inner.reset_for_session(date(2026, 6, 9))

    shim = MinLotOverrideEngine(inner)
    snap = RiskSnapshot(
        capital=100_000.0,
        realized_pnl_today=0.0,
        unrealized_pnl=0.0,
        breaker_tripped=False,
        floor_breached=False,
        open_position_count=0,
    )

    # stop_distance=200 * lot_size(65) = 13000 risk per lot
    # risk_budget = 100_000 * 0.0001 = 10
    # 10 * 2.7 = 27 < 13000 → still rejected (truly underfunded)
    intent_too_big = OrderIntent(
        strategy_id="test",
        instrument=deriv_instr,
        side=Side.BUY,
        ref_price=23000.0,
        stop_price=22800.0,   # 200 points stop
    )
    result = shim.size(intent_too_big, snap, date(2026, 6, 9))
    # Should still reject (budget too small even with 2.7x factor)
    assert isinstance(result, Rejection), (
        "Should reject when risk_budget*2.7 < risk_per_lot"
    )

    # Now set risk_budget in (risk_per_lot/2.7, risk_per_lot) → qty=0 but 2.7x promotes.
    # risk_per_lot = stop_dist * lot_size = 30 * 1 = 30.
    # Need: risk_budget < 30 AND risk_budget * 2.7 >= 30.
    # risk_budget = 100_000 * 0.0002 = 20; 20 < 30 ✓; 20*2.7=54 >= 30 ✓ → promote.
    inner2 = IntradayRiskEngine(
        RiskParams(
            capital=100_000.0,
            hard_floor=80_000.0,
            max_daily_loss_pct=0.02,
            per_trade_risk_pct=0.0002,   # risk_budget=20 < 30 → qty=0; 20*2.7=54>=30 → promote
            max_open_positions=3,
            max_per_symbol=1,
        )
    )
    inner2.reset_for_session(date(2026, 6, 9))
    shim2 = MinLotOverrideEngine(inner2)

    # Derivative instrument with lot_size=1 (default Instrument.lot_size returns 1).
    eq_deriv = Instrument(
        symbol="TESTFUT",
        security_id="9999",
        segment=Segment.NSE_FNO,
        tick_size=0.05,
        is_derivative=True,
        underlying="TEST",
        can_short_intraday=True,
    )
    intent_small = OrderIntent(
        strategy_id="test",
        instrument=eq_deriv,
        side=Side.BUY,
        ref_price=1000.0,
        stop_price=970.0,   # 30 points stop; risk_per_lot=30*1=30
    )
    result2 = shim2.size(intent_small, snap, date(2026, 6, 9))
    assert isinstance(result2, SizedOrder), (
        f"Expected SizedOrder(qty=1) from min_lot shim, got {result2!r}"
    )
    assert result2.quantity == 1


