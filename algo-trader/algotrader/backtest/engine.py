"""Event-driven intraday backtest engine (ARCHITECTURE §3).

Design invariants (§3, §5):
  - Strategies see only CLOSED bars (Bar.complete asserted before every on_bar).
  - Signals on bar t fill at bar t+1 open ± slippage (never same-bar).
  - Each trading session is an isolated episode; risk engine is reset per session.
  - Clock enforcement: no new entries after 14:44:30; voluntary exits from 15:15;
    hard force-flat at first bar whose ts_close ≥ 15:19:30 (ExitReason.SQUARE_OFF).
  - Breaker/floor trip → flatten all open positions at NEXT bar open, halt entries.
  - Costs and slippage are fully attributed in every TradeRecord.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from algotrader.core import (
    Bar,
    BacktestResult,
    BarSource,
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
    SessionResult,
    Side,
    SizedOrder,
    SlippageModel,
    Strategy,
    TradeRecord,
)
from algotrader.strategies.indicators import atr as _atr_indicator

from .fills import entry_fill, resolve_bar, stop_fill, target_fill


# ---------------------------------------------------------------------------
# Clock defaults (ARCHITECTURE §0, §3.5)
# ---------------------------------------------------------------------------

_NO_NEW_ENTRIES = time(14, 44, 30)
_VOLUNTARY_EXIT = time(15, 15, 0)
_HARD_FLAT      = time(15, 19, 30)


@dataclass(frozen=True)
class ClockParams:
    """Session clock boundary times (IST, naive).

    Defaults match ARCHITECTURE §0.
    """
    no_new_entries_after: time = _NO_NEW_ENTRIES
    voluntary_exit_from:  time = _VOLUNTARY_EXIT
    hard_flat_at:         time = _HARD_FLAT


# ---------------------------------------------------------------------------
# Default slippage model (ARCHITECTURE §3.4)
# ---------------------------------------------------------------------------

class DefaultSlippage:
    """max(1 tick, 0.05 × causal ATR(14)) from bars strictly before the fill bar.

    Implements core.SlippageModel.  ATR is computed from *bar_history* which
    the engine always provides as the bars up to (not including) the fill bar.
    Falls back to 1 tick when ATR is not yet defined (< 14 bars warm-up).
    """

    def entry_slippage(
        self, instrument: Instrument, bar_history: Sequence[Bar]
    ) -> float:
        """Causal entry slippage for *instrument* given bars before fill bar."""
        return self._compute(instrument, bar_history)

    def exit_slippage(
        self,
        instrument: Instrument,
        bar_history: Sequence[Bar],
        reason: ExitReason,
    ) -> float:
        """Causal exit slippage (same formula as entry in v1)."""
        return self._compute(instrument, bar_history)

    @staticmethod
    def _compute(instrument: Instrument, bar_history: Sequence[Bar]) -> float:
        if not bar_history:
            return instrument.tick_size
        atr_vals = _atr_indicator(bar_history, 14)
        last = atr_vals[-1]
        if math.isnan(last):
            return instrument.tick_size
        return max(instrument.tick_size, 0.05 * last)


# ---------------------------------------------------------------------------
# Session context (read-only view for strategies)
# ---------------------------------------------------------------------------

class _SessionCtx:
    """Concrete SessionContext served to strategies during on_bar / manage.

    Never exposes future bars or incomplete bars.
    """

    def __init__(
        self,
        bar_history: dict[Instrument, list[Bar]],
        open_positions: dict[str, Position],
        risk_snap: RiskSnapshot,
        clock: SessionClock,
        prior: dict[date, dict[Instrument, list[Bar]]] | None = None,
    ) -> None:
        self._history = bar_history
        self._positions = open_positions
        self._risk = risk_snap
        self._clock = clock
        self._prior = prior or {}

    # --- SessionContext protocol ---

    @property
    def clock(self) -> SessionClock:
        return self._clock

    @property
    def risk(self) -> RiskSnapshot:
        return self._risk

    def bars(self, instrument: Instrument, n: int) -> Sequence[Bar]:
        """Last *n* closed bars (inclusive of current bar) for *instrument*."""
        hist = self._history.get(instrument, [])
        if n >= len(hist):
            return list(hist)
        return hist[-n:]

    def prior_sessions(
        self, instrument: Instrument, n_days: int
    ) -> Mapping[date, Sequence[Bar]]:
        """Closed bars from the most recent *n_days* prior sessions."""
        result: dict[date, Sequence[Bar]] = {}
        for d in sorted(self._prior.keys(), reverse=True)[:n_days]:
            per_instr = self._prior[d]
            if instrument in per_instr:
                result[d] = per_instr[instrument]
        return result

    def open_positions(self, strategy_id: str | None = None) -> Sequence[Position]:
        positions = list(self._positions.values())
        if strategy_id is not None:
            positions = [p for p in positions if p.strategy_id == strategy_id]
        return positions


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """Event-driven intraday backtest engine.

    ARCHITECTURE §3: entry fills at next-bar open; two-branch stop/target;
    session lifecycle with clock guards; breaker/floor circuit trips.

    Args:
        strategies:      Strategy objects implementing the ABC.
        risk_engine:     Sizing + circuit-breaker engine (IntradayRiskEngine).
        cost_model:      Round-trip cost calculator (DhanCosts).
        slippage_model:  Causal fill-slippage model (DefaultSlippage or custom).
        clock_params:    Session clock boundaries; defaults to ARCHITECTURE §0.
    """

    def __init__(
        self,
        strategies: list[Strategy],
        risk_engine: RiskEngine,
        cost_model,            # core.CostModel (Protocol — no ABC to inherit)
        slippage_model: SlippageModel | None = None,
        clock_params: ClockParams | None = None,
    ) -> None:
        self._strategies = strategies
        self._risk = risk_engine
        self._costs = cost_model
        self._slip = slippage_model or DefaultSlippage()
        self._clock = clock_params or ClockParams()
        self._strat_by_id: dict[str, Strategy] = {
            s.strategy_id: s for s in strategies
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, bar_source: BarSource) -> BacktestResult:
        """Replay all sessions from *bar_source* and return a BacktestResult.

        Raises:
            ValueError: if any bar has ``complete=False``.
        """
        sessions: list[SessionResult] = []
        prior_sessions: dict[date, dict[Instrument, list[Bar]]] = {}

        for session_date, session_bars in bar_source.sessions():
            result = self._run_session(
                session_date, list(session_bars), prior_sessions
            )
            sessions.append(result)
            # Record this session's bars as prior for future sessions
            day_hist: dict[Instrument, list[Bar]] = defaultdict(list)
            for b in session_bars:
                day_hist[b.instrument].append(b)
            prior_sessions[session_date] = dict(day_hist)

        return BacktestResult(sessions=sessions)

    # ------------------------------------------------------------------
    # Per-session logic
    # ------------------------------------------------------------------

    def _run_session(
        self,
        session_date: date,
        session_bars: list[Bar],
        prior_sessions: dict[date, dict[Instrument, list[Bar]]],
    ) -> SessionResult:
        # Validate completeness upfront (test (h)).
        for b in session_bars:
            if not b.complete:
                raise ValueError(
                    f"Incomplete bar (complete=False) served to engine on "
                    f"{session_date}: {b}"
                )

        self._risk.reset_for_session(session_date)

        # Per-instrument bar history buffer (causal: grows one bar at a time).
        bar_history: dict[Instrument, list[Bar]] = defaultdict(list)

        # Open positions: position_id → Position
        open_positions: dict[str, Position] = {}

        # Ancillary position metadata.
        pos_margin:    dict[str, float] = {}          # margin to free on close
        pos_entry_slip: dict[str, float] = {}         # entry slippage paid
        pos_time_deadline: dict = {}                  # pid -> datetime time-stop

        # Pending entry orders: filled at NEXT bar open.
        pending_orders: list[SizedOrder] = []

        # Force-exit queue: (position_id, reason) to exit at NEXT bar open.
        force_exits: list[tuple[str, ExitReason]] = []

        # Closed trade records for this session.
        trade_records: list[TradeRecord] = []
        rejections:    list[Rejection]   = []
        risk_events:   list[str]         = []

        squared_off = False

        # Counter for deterministic position IDs within a session.
        _pos_counter = 0

        def _new_pos_id() -> str:
            nonlocal _pos_counter
            _pos_counter += 1
            return f"{session_date.isoformat()}_pos_{_pos_counter:04d}"

        def _ist_time(ts) -> time:
            """Strip tz from IST datetime → naive time for comparison."""
            return ts.astimezone(IST).replace(tzinfo=None).time()

        def _close_pos(
            pos: Position,
            exit_slip: float,
        ) -> None:
            """Finalise a closed position: register with risk, build TradeRecord."""
            assert pos.exit_price is not None, "exit_price must be set before _close_pos"
            pid = pos.position_id
            margin = pos_margin.pop(pid, 0.0)
            entry_slip = pos_entry_slip.pop(pid, 0.0)
            pos_time_deadline.pop(pid, None)
            self._risk.register_close(pos.instrument.symbol, margin)
            open_positions.pop(pid, None)

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
            # TOTAL rupee slippage embedded in the fills (attribution metadata;
            # fills already carry it, so net_pnl must NOT subtract it again)
            slippage_paid = (entry_slip + exit_slip) * pos.quantity * ls
            trade_records.append(
                TradeRecord(position=pos, costs=costs, slippage_paid=slippage_paid)
            )

        # ----------------------------------------------------------------
        # Main bar loop
        # ----------------------------------------------------------------
        for bar in session_bars:
            # Completeness guard (belt + suspenders — already checked above).
            if not bar.complete:
                raise ValueError(f"Incomplete bar in session loop: {bar}")

            instr = bar.instrument
            bar_history[instr].append(bar)
            hist_before = bar_history[instr][:-1]   # bars strictly before this bar

            def _hist_before_for(instrument: Instrument) -> list[Bar]:
                """Causal history for *instrument* relative to the current bar.

                For the current bar's instrument we just appended this bar, so
                [:-1] correctly excludes it.  For any OTHER instrument, the last
                append happened on a PREVIOUS iteration — [:-1] would silently
                drop one valid bar from the ATR window (multi-instrument bug,
                ARCHITECTURE §3.4).
                """
                h = bar_history.get(instrument, [])
                return h[:-1] if instrument is instr else h

            ts_close_naive = _ist_time(bar.ts_close)

            # ────────────────────────────────────────────────────────────
            # (1) HARD FLAT — first bar whose ts_close ≥ 15:19:30
            # ────────────────────────────────────────────────────────────
            if not squared_off and ts_close_naive >= self._clock.hard_flat_at:
                # Exit all open positions at this bar's open ± exit slippage.
                for pid in list(open_positions):
                    pos = open_positions[pid]
                    exit_slip = self._slip.exit_slippage(
                        pos.instrument,
                        _hist_before_for(pos.instrument),
                        ExitReason.SQUARE_OFF,
                    )
                    if pos.side is Side.BUY:
                        ep = bar.open - exit_slip
                    else:
                        ep = bar.open + exit_slip
                    pos.exit_price = ep
                    pos.exit_ts    = bar.ts_open
                    pos.exit_reason = ExitReason.SQUARE_OFF
                    _close_pos(pos, exit_slip)

                # Cancel pending entries and any queued force-exits.
                pending_orders.clear()
                force_exits.clear()
                squared_off = True
                continue  # nothing else to do this bar

            # ────────────────────────────────────────────────────────────
            # (2) PROCESS FORCE-EXITS from previous bar (breaker/floor/manage)
            # ────────────────────────────────────────────────────────────
            for pid, reason in list(force_exits):
                if pid not in open_positions:
                    continue
                pos = open_positions[pid]
                exit_slip = self._slip.exit_slippage(
                    pos.instrument,
                    _hist_before_for(pos.instrument),
                    reason,
                )
                if pos.side is Side.BUY:
                    ep = bar.open - exit_slip
                else:
                    ep = bar.open + exit_slip
                pos.exit_price  = ep
                pos.exit_ts     = bar.ts_open
                pos.exit_reason = reason
                _close_pos(pos, exit_slip)
            force_exits.clear()

            # ────────────────────────────────────────────────────────────
            # (3) FILL PENDING ENTRY ORDERS at this bar's open
            # ────────────────────────────────────────────────────────────
            for order in pending_orders:
                e_slip = self._slip.entry_slippage(order.intent.instrument, hist_before)
                e_price = entry_fill(bar, order.intent.side, e_slip)
                pid = _new_pos_id()
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
                open_positions[pid] = pos
                pos_margin[pid]      = order.margin_required
                pos_entry_slip[pid]  = e_slip
                self._risk.register_fill(order)
                if order.intent.time_stop_min:
                    from datetime import timedelta as _td
                    pos_time_deadline[pid] = bar.ts_open + _td(
                        minutes=order.intent.time_stop_min)
            pending_orders.clear()

            # ────────────────────────────────────────────────────────────
            # (4) CHECK STOP / TARGET for all open positions
            # ────────────────────────────────────────────────────────────
            for pid in list(open_positions):
                pos = open_positions[pid]
                # Use each position's instrument history (matters for multi-instrument).
                # _hist_before_for correctly handles both single- and multi-instrument
                # sessions: for the current bar's instrument [:-1] excludes today's
                # bar; for other instruments the list is already up to date (§3.4).
                pos_hist_before = _hist_before_for(pos.instrument)
                triggered = resolve_bar(bar, pos)
                if triggered is ExitReason.STOP:
                    exit_slip_val = self._slip.exit_slippage(
                        pos.instrument, pos_hist_before, ExitReason.STOP
                    )
                    fp, gap = stop_fill(bar, pos, exit_slip_val)
                    pos.exit_price       = fp
                    pos.exit_ts          = bar.ts_open
                    pos.exit_reason      = ExitReason.STOP
                    pos.gap_through_stop = gap
                    _close_pos(pos, exit_slip_val)

                elif triggered is ExitReason.TARGET:
                    exit_slip_val = self._slip.exit_slippage(
                        pos.instrument, pos_hist_before, ExitReason.TARGET
                    )
                    fp = target_fill(bar, pos, exit_slip_val)
                    pos.exit_price  = fp
                    pos.exit_ts     = bar.ts_open
                    pos.exit_reason = ExitReason.TARGET
                    _close_pos(pos, exit_slip_val)

            # ────────────────────────────────────────────────────────────
            # (4b) TIME STOPS — queue exit at NEXT bar open once deadline passes
            #      (was an unwired OrderIntent field — bug found in Gen-1 sweep)
            # ────────────────────────────────────────────────────────────
            for pid, deadline in list(pos_time_deadline.items()):
                if pid in open_positions and bar.ts_close >= deadline:
                    if all(pid != qpid for qpid, _ in force_exits):
                        force_exits.append((pid, ExitReason.TIME_STOP))

            # ────────────────────────────────────────────────────────────
            # (5) RISK ENGINE EVALUATION
            # ────────────────────────────────────────────────────────────
            def _last_close(instrument: Instrument) -> float:
                """Last known close price for *instrument* (causal)."""
                h = bar_history.get(instrument)
                return h[-1].close if h else 0.0

            unrealized = sum(
                (1.0 if p.side is Side.BUY else -1.0)
                * (_last_close(p.instrument) - p.entry_price)
                * p.quantity
                * (p.instrument.lot_size(session_date) if p.instrument.is_derivative else 1)
                for p in open_positions.values()
            )
            realized = sum(t.position.gross_pnl() for t in trade_records)

            snap = RiskSnapshot(
                capital=self._risk._params.capital,
                realized_pnl_today=realized,
                unrealized_pnl=unrealized,
                breaker_tripped=self._risk.state.breaker_tripped,
                floor_breached=self._risk.state.floor_breached,
                open_position_count=len(open_positions),
            )
            reasons = self._risk.on_bar(snap)
            if reasons:
                reason_str = ",".join(r.value for r in reasons)
                risk_events.append(f"{bar.ts_close}: {reason_str}")
                # Queue all open positions for exit at NEXT bar open.
                for pid in list(open_positions):
                    force_exits.append((pid, reasons[0]))
                # Cancel pending entries — no new entries after breaker.
                pending_orders.clear()

            # ────────────────────────────────────────────────────────────
            # (6) STRATEGY MANAGE — trailing stops / voluntary exits
            # ────────────────────────────────────────────────────────────
            force_exit_pids = {pid for pid, _ in force_exits}
            clock = SessionClock(
                now=bar.ts_close,
                no_new_entries_after=self._clock.no_new_entries_after,
                voluntary_exit_from=self._clock.voluntary_exit_from,
                hard_flat_at=self._clock.hard_flat_at,
            )
            ctx = _SessionCtx(
                bar_history=dict(bar_history),
                open_positions=dict(open_positions),
                risk_snap=snap,
                clock=clock,
                prior=prior_sessions,
            )
            for pid in list(open_positions):
                if pid in force_exit_pids:
                    continue  # already queued for exit
                pos = open_positions[pid]
                strat = self._strat_by_id.get(pos.strategy_id)
                if strat is None:
                    continue
                new_stop, exit_now = strat.manage(ctx, bar, pos)
                if exit_now:
                    force_exits.append((pid, ExitReason.STRATEGY))
                elif new_stop is not None:
                    pos.stop_price = new_stop

            # ────────────────────────────────────────────────────────────
            # (7) STRATEGY ON_BAR — collect new entry intents
            # ────────────────────────────────────────────────────────────
            # Entries are blocked if clock says so or if breaker tripped.
            if (
                clock.can_enter
                and not self._risk.state.breaker_tripped
                and not self._risk.state.floor_breached
            ):
                for strat in self._strategies:
                    ctx_entry = _SessionCtx(
                        bar_history=dict(bar_history),
                        open_positions=dict(open_positions),
                        risk_snap=snap,
                        clock=clock,
                        prior=prior_sessions,
                    )
                    intents: list[OrderIntent] = strat.on_bar(ctx_entry, bar)
                    for intent in intents:
                        result = self._risk.size(intent, snap, session_date)
                        if isinstance(result, SizedOrder):
                            pending_orders.append(result)
                        else:
                            rejections.append(result)

        # ────────────────────────────────────────────────────────────────
        # Assemble SessionResult
        # ────────────────────────────────────────────────────────────────
        gross = sum(t.position.gross_pnl() for t in trade_records)
        costs = sum(t.costs.total for t in trade_records)
        net   = sum(t.net_pnl for t in trade_records)

        return SessionResult(
            session_date=session_date,
            trades=trade_records,
            rejections=rejections,
            risk_events=risk_events,
            gross_pnl=gross,
            net_pnl=net,
            cost_total=costs,
        )
