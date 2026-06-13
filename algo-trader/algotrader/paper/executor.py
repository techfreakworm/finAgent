"""Paper trading executor — live-bar driver with identical on_bar semantics
to the backtest engine (ARCHITECTURE §6).

Key design invariants (mirrors algotrader/backtest/engine.py):
- Pending entries fill at NEXT bar open ± DefaultSlippage (never same-bar).
- Two-branch stop/target via algotrader/backtest/fills.py.
- Clock guards: no entries after 14:44:30; hard force-flat at 15:19:30.
- Risk engine integration: register_fill / register_close / on_bar breaker.
- Every fill, close, and risk-event is persisted to the store immediately.
- STARTUP RECONCILIATION: on first bar, open positions + risk state + per-
  strategy _traded flags are loaded from SQLite before any bar is processed.
  If the first bar arrives at or after 15:19:30, any reconciled open positions
  are force-flattened immediately (ARCHITECTURE §5 crash-recovery protocol).

PAPER ONLY — no order-placement methods are imported or called anywhere in
this module.  The only broker surface is the Instrument objects that arrive
with each Bar (from the live feed or test replay).

Multiple accounts / min-lot shim
---------------------------------
To run the same strategy under two account_ids (e.g. 'paper' at 0.75% risk
and 'paper-minlot' where derivatives always get at least 1 contract), create
two PaperExecutor instances:

    exec_normal  = PaperExecutor('paper',        strategies, risk_engine_normal, ...)
    exec_minlot  = PaperExecutor('paper-minlot', strategies,
                                 MinLotOverrideEngine(risk_engine_minlot), ...)

MinLotOverrideEngine wraps an IntradayRiskEngine and promotes zero_qty
Rejections on derivative intents to qty=1 SizedOrders, provided the
risk_budget × 2.7 is sufficient to cover one lot's stop-distance risk.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Sequence

from algotrader.backtest.engine import DefaultSlippage
from algotrader.backtest.fills import entry_fill, resolve_bar, stop_fill, target_fill
from algotrader.config import HARD_FLAT, NO_NEW_ENTRIES_AFTER, VOLUNTARY_EXIT_FROM
from algotrader.core import (
    Bar,
    ExitReason,
    IST,
    Instrument,
    OrderIntent,
    Position,
    ProductType,
    Rejection,
    RiskEngine,
    RiskSnapshot,
    SessionClock,
    Side,
    SizedOrder,
    SlippageModel,
    Strategy,
    TradeRecord,
    require_ist,
)
from algotrader.risk.engine import IntradayRiskEngine, RiskParams, SessionRiskState
from algotrader.strategies.indicators import atr as _atr_indicator

from .store import PaperStore


# ---------------------------------------------------------------------------
# MinLotOverrideEngine — RiskParams-wrapping shim
# ---------------------------------------------------------------------------

class MinLotOverrideEngine:
    """Wraps IntradayRiskEngine to enforce min 1 lot for derivatives.

    When the inner engine returns a zero_qty Rejection for a derivative
    intent AND the risk_budget × 2.7 would cover one lot's stop-distance
    risk, promote the result to SizedOrder(qty=1).

    This implements the 'paper-minlot' account variant without touching
    algotrader/risk/engine.py (ARCHITECTURE §5 note).

    The factor 2.7 gives approximately a 2.7× risk-budget override ceiling;
    if even 2.7× the nominal budget cannot afford a single lot, the position
    is still rejected (truly underfunded).
    """

    MIN_LOT_BUDGET_FACTOR: float = 2.7

    def __init__(self, engine: IntradayRiskEngine) -> None:
        self._engine = engine

    # Forward all non-size calls to the wrapped engine.

    def size(
        self,
        intent: OrderIntent,
        snapshot: RiskSnapshot,
        on: date,
    ) -> SizedOrder | Rejection:
        result = self._engine.size(intent, snapshot, on)
        if (
            isinstance(result, Rejection)
            and result.rule == "zero_qty"
            and intent.instrument.is_derivative
        ):
            params = self._engine._params
            lot_sz = intent.instrument.lot_size(on)
            stop_dist = abs(intent.ref_price - intent.stop_price)
            risk_per_lot = stop_dist * lot_sz
            risk_budget = params.capital * params.per_trade_risk_pct
            if risk_budget * self.MIN_LOT_BUDGET_FACTOR >= risk_per_lot and risk_per_lot > 0:
                # Override: 1 lot, with the same margin formula as the engine.
                notional = intent.ref_price * 1 * lot_sz
                margin = 0.12 * notional
                # Check margin availability (same rule as inner engine).
                available = (
                    snapshot.capital
                    + snapshot.realized_pnl_today
                    - self._engine.state.committed_margin
                )
                if margin <= available:
                    return SizedOrder(
                        intent=intent,
                        quantity=1,
                        margin_required=margin,
                    )
        return result

    def on_bar(self, snapshot: RiskSnapshot) -> list[ExitReason]:
        return self._engine.on_bar(snapshot)

    def register_fill(self, order: SizedOrder) -> None:
        self._engine.register_fill(order)

    def register_close(self, symbol: str, margin_freed: float) -> None:
        self._engine.register_close(symbol, margin_freed)

    def reset_for_session(self, session_date: date) -> None:
        self._engine.reset_for_session(session_date)

    def load_state(self, state: SessionRiskState) -> None:
        self._engine.load_state(state)

    @property
    def state(self) -> SessionRiskState:
        return self._engine.state


# ---------------------------------------------------------------------------
# PaperExecutor
# ---------------------------------------------------------------------------

class PaperExecutor:
    """Live-bar paper trading executor.

    Maintains the same internal state machine as BacktestEngine._run_session
    but driven one bar at a time via on_bar().  All fills, closes, and
    risk events are persisted to store immediately so a SIGKILL between any
    two bars leaves the DB consistent.

    Args:
        account_id:       Unique identifier for this paper account
                          (e.g. 'paper', 'paper-minlot').
        strategies:       Strategy list (same contract as BacktestEngine).
        risk_engine:      IntradayRiskEngine or MinLotOverrideEngine.
        cost_model:       Round-trip cost calculator (DhanCosts or compatible).
        slippage_model:   Causal slippage; defaults to DefaultSlippage.
        store:            PaperStore instance; defaults to the project-level DB.
        clock_params:     Override session clock times (testing only).
    """

    def __init__(
        self,
        account_id: str,
        strategies: list[Strategy],
        risk_engine: Any,           # IntradayRiskEngine | MinLotOverrideEngine
        cost_model: Any,            # core.CostModel protocol
        slippage_model: SlippageModel | None = None,
        store: PaperStore | None = None,
        clock_no_new_entries: time = NO_NEW_ENTRIES_AFTER,
        clock_voluntary_exit: time = VOLUNTARY_EXIT_FROM,
        clock_hard_flat: time = HARD_FLAT,
    ) -> None:
        self.account_id = account_id
        self._strategies = strategies
        self._strat_by_id: dict[str, Strategy] = {s.strategy_id: s for s in strategies}
        self._risk = risk_engine
        self._costs = cost_model
        self._slip = slippage_model or DefaultSlippage()
        self._store = store or PaperStore()

        # Clock times (IST naive)
        self._no_new_entries = clock_no_new_entries
        self._voluntary_exit = clock_voluntary_exit
        self._hard_flat = clock_hard_flat

        # Session state — initialised fresh; populated by reconciliation.
        self._session_date: date | None = None
        self._reconciled: bool = False

        # Per-instrument bar history (causal)
        self._bar_history: dict[Instrument, list[Bar]] = defaultdict(list)

        # Open positions: position_id → Position
        self._open_positions: dict[str, Position] = {}

        # Ancillary position metadata
        self._pos_margin: dict[str, float] = {}
        self._pos_entry_slip: dict[str, float] = {}
        self._pos_time_deadline: dict[str, datetime] = {}
        self._pos_trail_mult: dict[str, float | None] = {}

        # Pending entries filled at NEXT bar open
        self._pending_orders: list[SizedOrder] = []

        # Force-exit queue: (position_id, reason)
        self._force_exits: list[tuple[str, ExitReason]] = []

        # Accumulated session results
        self._trade_records: list[TradeRecord] = []
        self._risk_events: list[str] = []

        # Force-flat latch (once squared off, nothing else happens this session)
        self._squared_off: bool = False

        # Running max-DD tracking for daily_pnl
        self._peak_equity: float | None = None

        # Cache capital for RiskSnapshot (extracted once from risk engine params).
        self._capital: float = self._extract_capital(risk_engine)

    # ------------------------------------------------------------------
    # Public API: feed one bar at a time
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        """Process one complete bar.

        Mirrors BacktestEngine._run_session's per-bar loop body exactly,
        with persistence calls inserted at fill/close/risk-event points.

        Raises:
            ValueError: if bar.complete is False or datetime is naive.
        """
        if not bar.complete:
            raise ValueError(f"Incomplete bar served to PaperExecutor: {bar}")
        require_ist(bar.ts_open)

        session_date = bar.ts_open.date()

        # ── Reconcile on the FIRST bar of a session ──────────────────
        if not self._reconciled or session_date != self._session_date:
            self._start_session(session_date, bar)

        # ── Helpers ──────────────────────────────────────────────────
        instr = bar.instrument
        self._bar_history[instr].append(bar)
        hist_before = self._bar_history[instr][:-1]

        def _hist_before_for(instrument: Instrument) -> list[Bar]:
            h = self._bar_history.get(instrument, [])
            return h[:-1] if instrument is instr else list(h)

        def _ist_time(ts: datetime) -> time:
            return ts.astimezone(IST).replace(tzinfo=None).time()

        ts_close_naive = _ist_time(bar.ts_close)

        # ── (1) HARD FLAT ─────────────────────────────────────────────
        if not self._squared_off and ts_close_naive >= self._hard_flat:
            for pid in list(self._open_positions):
                pos = self._open_positions[pid]
                exit_slip = self._slip.exit_slippage(
                    pos.instrument,
                    _hist_before_for(pos.instrument),
                    ExitReason.SQUARE_OFF,
                )
                ep = (bar.open - exit_slip) if pos.side is Side.BUY else (bar.open + exit_slip)
                pos.exit_price = ep
                pos.exit_ts = bar.ts_open
                pos.exit_reason = ExitReason.SQUARE_OFF
                self._close_pos(pos, exit_slip, bar.ts_open)

            self._pending_orders.clear()
            self._force_exits.clear()
            self._squared_off = True
            self._persist_risk_state()
            return  # nothing else this bar

        # ── (2) PROCESS FORCE-EXITS ───────────────────────────────────
        for pid, reason in list(self._force_exits):
            if pid not in self._open_positions:
                continue
            pos = self._open_positions[pid]
            exit_slip = self._slip.exit_slippage(
                pos.instrument,
                _hist_before_for(pos.instrument),
                reason,
            )
            ep = (bar.open - exit_slip) if pos.side is Side.BUY else (bar.open + exit_slip)
            pos.exit_price = ep
            pos.exit_ts = bar.ts_open
            pos.exit_reason = reason
            self._close_pos(pos, exit_slip, bar.ts_open)
        self._force_exits.clear()

        # ── (3) FILL PENDING ENTRY ORDERS ─────────────────────────────
        for order in self._pending_orders:
            e_slip = self._slip.entry_slippage(order.intent.instrument, hist_before)
            e_price = entry_fill(bar, order.intent.side, e_slip)
            pid = str(uuid.uuid4())
            pos = Position(
                position_id=pid,
                strategy_id=order.intent.strategy_id,
                instrument=order.intent.instrument,
                side=order.intent.side,
                quantity=order.quantity,
                entry_price=e_price,
                entry_ts=bar.ts_open,
                stop_price=order.intent.stop_price,
                target_price=order.intent.target_price,
                session_date=session_date,
                product=ProductType.INTRADAY,
            )
            self._open_positions[pid] = pos
            self._pos_margin[pid] = order.margin_required
            self._pos_entry_slip[pid] = e_slip
            self._pos_trail_mult[pid] = order.intent.trail_atr_mult
            self._risk.register_fill(order)

            if order.intent.time_stop_min:
                self._pos_time_deadline[pid] = bar.ts_open + timedelta(
                    minutes=order.intent.time_stop_min
                )

            # Persist open position immediately
            self._store.save_open_position(
                account_id=self.account_id,
                pos=pos,
                margin_required=order.margin_required,
                entry_slip=e_slip,
                trail_atr_mult=order.intent.trail_atr_mult,
                time_stop_min=order.intent.time_stop_min,
            )
            self._store.append_event(
                account_id=self.account_id,
                event_type="fill",
                message=f"FILL {pos.side.value} {pos.quantity}x{pos.instrument.symbol} "
                        f"@ {e_price:.2f} stop={pos.stop_price:.2f}",
                ts=bar.ts_open,
                data={
                    "position_id": pid,
                    "symbol": pos.instrument.symbol,
                    "side": pos.side.value,
                    "entry_price": e_price,
                    "stop_price": pos.stop_price,
                },
            )

        self._pending_orders.clear()

        # ── (4) CHECK STOP / TARGET ────────────────────────────────────
        for pid in list(self._open_positions):
            pos = self._open_positions[pid]
            pos_hist_before = _hist_before_for(pos.instrument)
            triggered = resolve_bar(bar, pos)
            if triggered is ExitReason.STOP:
                exit_slip = self._slip.exit_slippage(
                    pos.instrument, pos_hist_before, ExitReason.STOP
                )
                fp, gap = stop_fill(bar, pos, exit_slip)
                pos.exit_price = fp
                pos.exit_ts = bar.ts_open
                pos.exit_reason = ExitReason.STOP
                pos.gap_through_stop = gap
                self._close_pos(pos, exit_slip, bar.ts_open)
            elif triggered is ExitReason.TARGET:
                exit_slip = self._slip.exit_slippage(
                    pos.instrument, pos_hist_before, ExitReason.TARGET
                )
                fp = target_fill(bar, pos, exit_slip)
                pos.exit_price = fp
                pos.exit_ts = bar.ts_open
                pos.exit_reason = ExitReason.TARGET
                self._close_pos(pos, exit_slip, bar.ts_open)

        # ── (4b) TIME STOPS ───────────────────────────────────────────
        for pid, deadline in list(self._pos_time_deadline.items()):
            if pid in self._open_positions and bar.ts_close >= deadline:
                if all(pid != qpid for qpid, _ in self._force_exits):
                    self._force_exits.append((pid, ExitReason.TIME_STOP))

        # ── (5) RISK ENGINE EVALUATION ────────────────────────────────
        def _last_close(instrument: Instrument) -> float:
            h = self._bar_history.get(instrument)
            return h[-1].close if h else 0.0

        unrealized = sum(
            (1.0 if p.side is Side.BUY else -1.0)
            * (_last_close(p.instrument) - p.entry_price)
            * p.quantity
            * (p.instrument.lot_size(session_date) if p.instrument.is_derivative else 1)
            for p in self._open_positions.values()
        )
        realized = sum(t.position.gross_pnl() for t in self._trade_records)

        # Obtain capital from the risk engine (handles both direct and shim wrappers).
        capital_val = self._capital

        # Update running max-DD
        equity_val = capital_val + realized + unrealized
        if self._peak_equity is None or equity_val > self._peak_equity:
            self._peak_equity = equity_val
        current_dd = self._peak_equity - equity_val

        snap = RiskSnapshot(
            capital=capital_val,
            realized_pnl_today=realized,
            unrealized_pnl=unrealized,
            breaker_tripped=self._risk.state.breaker_tripped,
            floor_breached=self._risk.state.floor_breached,
            open_position_count=len(self._open_positions),
        )
        reasons = self._risk.on_bar(snap)
        if reasons:
            reason_str = ",".join(r.value for r in reasons)
            self._risk_events.append(f"{bar.ts_close.isoformat()}: {reason_str}")
            self._store.append_event(
                account_id=self.account_id,
                event_type="risk_breaker",
                message=f"Risk breaker: {reason_str}",
                ts=bar.ts_close,
                data={"reasons": reason_str},
            )
            for pid in list(self._open_positions):
                self._force_exits.append((pid, reasons[0]))
            self._pending_orders.clear()

        # ── (6) STRATEGY MANAGE ───────────────────────────────────────
        force_exit_pids = {pid for pid, _ in self._force_exits}
        clock = SessionClock(
            now=bar.ts_close,
            no_new_entries_after=self._no_new_entries,
            voluntary_exit_from=self._voluntary_exit,
            hard_flat_at=self._hard_flat,
        )
        ctx = _PaperSessionCtx(
            bar_history=dict(self._bar_history),
            open_positions=dict(self._open_positions),
            risk_snap=snap,
            clock=clock,
        )
        for pid in list(self._open_positions):
            if pid in force_exit_pids:
                continue
            pos = self._open_positions[pid]
            strat = self._strat_by_id.get(pos.strategy_id)
            if strat is None:
                continue
            new_stop, exit_now = strat.manage(ctx, bar, pos)
            if exit_now:
                self._force_exits.append((pid, ExitReason.STRATEGY))
            elif new_stop is not None:
                pos.stop_price = new_stop
                # Persist trailing stop update
                self._store.update_position_stop(
                    account_id=self.account_id,
                    position_id=pid,
                    new_stop=new_stop,
                )

        # ── (7) STRATEGY ON_BAR ───────────────────────────────────────
        if (
            clock.can_enter
            and not self._risk.state.breaker_tripped
            and not self._risk.state.floor_breached
        ):
            for strat in self._strategies:
                ctx_entry = _PaperSessionCtx(
                    bar_history=dict(self._bar_history),
                    open_positions=dict(self._open_positions),
                    risk_snap=snap,
                    clock=clock,
                )
                intents: list[OrderIntent] = strat.on_bar(ctx_entry, bar)
                for intent in intents:
                    result = self._risk.size(intent, snap, session_date)
                    if isinstance(result, SizedOrder):
                        self._pending_orders.append(result)
                    else:
                        self._store.append_event(
                            account_id=self.account_id,
                            event_type="rejection",
                            message=f"REJECTED {intent.instrument.symbol}: {result.rule}",
                            ts=bar.ts_close,
                            data={"rule": result.rule, "detail": result.detail},
                        )

        # ── Persist risk state at end of every bar ────────────────────
        self._persist_risk_state()

        # ── Upsert daily P&L summary ──────────────────────────────────
        self._store.upsert_daily_pnl(
            account_id=self.account_id,
            session_date=session_date,
            realized=realized,
            n_trades=len(self._trade_records),
            max_dd_intraday=current_dd,
        )

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _start_session(self, session_date: date, first_bar: Bar) -> None:
        """Start or restart a session — reconcile DB state before first bar."""
        if session_date != self._session_date:
            # New calendar day: clear per-session state.
            self._session_date = session_date
            self._bar_history.clear()
            self._open_positions.clear()
            self._pos_margin.clear()
            self._pos_entry_slip.clear()
            self._pos_time_deadline.clear()
            self._pos_trail_mult.clear()
            self._pending_orders.clear()
            self._force_exits.clear()
            self._trade_records.clear()
            self._risk_events.clear()
            self._squared_off = False
            self._peak_equity = None

            # Reset risk engine for new session.
            self._risk.reset_for_session(session_date)

        # ── Load persisted state (crash recovery) ─────────────────────
        stored_state = self._store.load_risk_state(self.account_id, session_date)
        if stored_state is not None:
            self._risk.load_state(stored_state)
            self._store.append_event(
                account_id=self.account_id,
                event_type="reconcile",
                message=f"Loaded risk state: breaker={stored_state.breaker_tripped} "
                        f"floor={stored_state.floor_breached} "
                        f"realized={stored_state.realized_pnl:.2f}",
                ts=first_bar.ts_open,
            )
            # Reload closed trades count so _trade_records length is consistent
            # with the stored realized_pnl.  We load the actual TradeRecord objects
            # from the DB as lightweight placeholders (for net_pnl / costs we only
            # need gross_pnl here).
            closed_today = self._store.load_trades(self.account_id, session_date)
            # Reconstruct minimal TradeRecord shells for realized-pnl accounting.
            # The executor only uses _trade_records for realized-pnl sum + count;
            # full cost breakdown is not needed post-restart.
            self._trade_records = [
                _ClosedTradeProxy(float(r["gross_pnl"]), float(r["net_pnl"]))
                for r in closed_today
            ]

        # ── Reconcile open positions ──────────────────────────────────
        rows = self._store.load_open_positions(self.account_id, session_date)
        for row in rows:
            pos, margin, entry_slip, trail_mult = self._store.reconstruct_position(row)
            self._open_positions[pos.position_id] = pos
            self._pos_margin[pos.position_id] = margin
            self._pos_entry_slip[pos.position_id] = entry_slip
            self._pos_trail_mult[pos.position_id] = trail_mult
            if row.get("time_stop_min"):
                self._pos_time_deadline[pos.position_id] = (
                    pos.entry_ts + timedelta(minutes=int(row["time_stop_min"]))
                )

        if rows:
            self._store.append_event(
                account_id=self.account_id,
                event_type="reconcile",
                message=f"Reconciled {len(rows)} open position(s) from DB",
                ts=first_bar.ts_open,
                data={"position_ids": [r["position_id"] for r in rows]},
            )

        # ── Per-strategy traded-flag reconciliation ───────────────────
        # Infer _traded from positions or trades already on record today.
        traded_strategies: set[str] = set()
        for pos in self._open_positions.values():
            traded_strategies.add(pos.strategy_id)
        for row in self._store.load_trades(self.account_id, session_date):
            traded_strategies.add(row["strategy_id"])
        for strat in self._strategies:
            if strat.strategy_id in traded_strategies:
                # Restore strategy state so it doesn't re-enter.
                if hasattr(strat, "_traded"):
                    strat._traded = True
                if hasattr(strat, "_session_date"):
                    strat._session_date = session_date

        self._reconciled = True

    # ------------------------------------------------------------------
    # Position close helper
    # ------------------------------------------------------------------

    def _close_pos(
        self, pos: Position, exit_slip: float, bar_ts: datetime
    ) -> None:
        """Finalise a closed position, persist trade record, update store."""
        assert pos.exit_price is not None, "exit_price must be set before _close_pos"
        pid = pos.position_id
        margin = self._pos_margin.pop(pid, 0.0)
        entry_slip = self._pos_entry_slip.pop(pid, 0.0)
        self._pos_time_deadline.pop(pid, None)
        self._pos_trail_mult.pop(pid, None)
        self._risk.register_close(pos.instrument.symbol, margin)
        self._open_positions.pop(pid, None)

        session_date = pos.session_date
        costs = self._costs.round_trip(
            instrument=pos.instrument,
            side=pos.side,
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=pos.exit_price,
            on=session_date,
            product=pos.product,
        )
        ls = pos.instrument.lot_size(session_date) if pos.instrument.is_derivative else 1
        slippage_paid = (entry_slip + exit_slip) * pos.quantity * ls
        trade = TradeRecord(position=pos, costs=costs, slippage_paid=slippage_paid)
        self._trade_records.append(trade)

        # Persist to DB
        trade_dict = {
            "position_id": pid,
            "session_date": session_date.isoformat(),
            "strategy_id": pos.strategy_id,
            "symbol": pos.instrument.symbol,
            "side": pos.side.value,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "entry_ts": pos.entry_ts.astimezone(IST).isoformat(timespec="seconds"),
            "exit_price": pos.exit_price,
            "exit_ts": bar_ts.astimezone(IST).isoformat(timespec="seconds"),
            "exit_reason": pos.exit_reason.value,
            "gross_pnl": pos.gross_pnl(),
            "net_pnl": trade.net_pnl,
            "costs_total": costs.total,
            "slippage_paid": slippage_paid,
        }
        self._store.save_trade(account_id=self.account_id, trade_dict=trade_dict)
        self._store.close_open_position(account_id=self.account_id, position_id=pid)
        self._store.append_event(
            account_id=self.account_id,
            event_type="close",
            message=f"CLOSE {pos.side.value} {pos.quantity}x{pos.instrument.symbol} "
                    f"@ {pos.exit_price:.2f} reason={pos.exit_reason.value} "
                    f"pnl={trade.net_pnl:.2f}",
            ts=bar_ts,
            data={
                "position_id": pid,
                "exit_reason": pos.exit_reason.value,
                "net_pnl": trade.net_pnl,
            },
        )

    # ------------------------------------------------------------------
    # Capital helper
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_capital(risk_engine: Any) -> float:
        """Extract capital from a risk engine (direct or wrapped via shim)."""
        # MinLotOverrideEngine wraps an IntradayRiskEngine at _engine.
        if hasattr(risk_engine, "_engine"):
            inner = risk_engine._engine
            if hasattr(inner, "_params"):
                return float(inner._params.capital)
        # IntradayRiskEngine directly.
        if hasattr(risk_engine, "_params"):
            return float(risk_engine._params.capital)
        return 0.0

    # ------------------------------------------------------------------
    # Persist risk state
    # ------------------------------------------------------------------

    def _persist_risk_state(self) -> None:
        """Write current risk engine state to SQLite (per-bar crash recovery)."""
        if self._session_date is not None:
            self._store.save_risk_state(self.account_id, self._risk.state)

    # ------------------------------------------------------------------
    # Read-only accessors (for testing / reporting)
    # ------------------------------------------------------------------

    @property
    def open_positions(self) -> dict[str, Position]:
        return dict(self._open_positions)

    @property
    def trade_records(self) -> list:
        return list(self._trade_records)

    @property
    def risk_events(self) -> list[str]:
        return list(self._risk_events)


# ---------------------------------------------------------------------------
# _PaperSessionCtx — read-only view for strategies
# ---------------------------------------------------------------------------

class _PaperSessionCtx:
    """Concrete SessionContext for paper trading.

    Identical to the backtest engine's _SessionCtx but without prior_sessions
    (live paper trading does not replay historical sessions in this context;
    the live feed's bar history provides context as it accumulates).
    """

    def __init__(
        self,
        bar_history: dict[Instrument, list[Bar]],
        open_positions: dict[str, Position],
        risk_snap: RiskSnapshot,
        clock: SessionClock,
    ) -> None:
        self._history = bar_history
        self._positions = open_positions
        self._risk = risk_snap
        self._clock = clock

    @property
    def clock(self) -> SessionClock:
        return self._clock

    @property
    def risk(self) -> RiskSnapshot:
        return self._risk

    def bars(self, instrument: Instrument, n: int) -> Sequence[Bar]:
        hist = self._history.get(instrument, [])
        if n >= len(hist):
            return list(hist)
        return hist[-n:]

    def prior_sessions(self, instrument: Instrument, n_days: int):
        # Paper executor does not maintain prior session history in this context.
        return {}

    def open_positions(self, strategy_id: str | None = None) -> Sequence[Position]:
        positions = list(self._positions.values())
        if strategy_id is not None:
            positions = [p for p in positions if p.strategy_id == strategy_id]
        return positions


# ---------------------------------------------------------------------------
# _ClosedTradeProxy — lightweight placeholder for reconciled closed trades
# ---------------------------------------------------------------------------

class _ClosedTradeProxy:
    """Minimal stand-in for TradeRecord used only for pnl aggregation after
    a crash-recovery restart.  The executor needs sum(t.position.gross_pnl())
    for the RiskSnapshot; we store gross_pnl directly to avoid reconstructing
    a full Position/TradeRecord chain from the DB.
    """

    class _FakePosition:
        def __init__(self, gross: float) -> None:
            self._gross = gross

        def gross_pnl(self) -> float:
            return self._gross

    def __init__(self, gross_pnl: float, net_pnl: float) -> None:
        self.position = self._FakePosition(gross_pnl)
        self.net_pnl = net_pnl
