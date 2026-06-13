"""Intraday risk engine — sizing, circuit breakers, session lifecycle.

Implements core.RiskEngine protocol (ARCHITECTURE §5).

Design notes:
- RiskParams is a pure dataclass with no core imports (operator-tunable).
- SessionRiskState carries the persisted crash-recovery fields (§5).
- IntradayRiskEngine owns the session latch logic; re-arms only on a new date
  (avoids the finAgent floor_monitor._breached one-way-but-never-resets bug).
- Equity margin: ref_price * qty / equity_mis_leverage (SEBI 5× MIS cap).
- Derivative margin: 12% × notional — placeholder SPAN proxy (TODO: live SPAN
  via Dhan margin API, ARCHITECTURE §5).
- Per-symbol open-count is tracked via register_fill / register_close; the
  backtest/paper engine must call these after confirmed fills and closes.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from algotrader.core import (
    ExitReason,
    Instrument,
    OrderIntent,
    Rejection,
    RiskSnapshot,
    Side,
    SizedOrder,
)

# ---------------------------------------------------------------------------
# Configuration dataclass (no core imports — operator-tunable)
# ---------------------------------------------------------------------------

@dataclass
class RiskParams:
    """Operator-tunable risk parameters for IntradayRiskEngine.

    All monetary values in INR.  Percentages as fractions (0.02 = 2 %).

    Attributes:
        capital:              Starting capital for the session (₹).
        hard_floor:           Account equity floor; breach halts trading (₹).
        max_daily_loss_pct:   Daily loss circuit-breaker as fraction of capital.
        per_trade_risk_pct:   Fraction of capital risked per trade signal.
        max_open_positions:   Global cap on simultaneous open positions.
        max_per_symbol:       Max open positions per instrument symbol.
        equity_mis_leverage:  SEBI-capped MIS leverage for equity (≤ 5×).
    """
    capital: float
    hard_floor: float
    max_daily_loss_pct: float
    per_trade_risk_pct: float
    max_open_positions: int
    max_per_symbol: int
    equity_mis_leverage: float = 5.0


# ---------------------------------------------------------------------------
# Session state — persisted to JSON for crash recovery (ARCHITECTURE §5)
# ---------------------------------------------------------------------------

@dataclass
class SessionRiskState:
    """Per-session mutable state persisted to SQLite/JSON for crash recovery.

    ARCHITECTURE §5: on restart the paper engine loads this state and feeds it
    back into the risk engine via IntradayRiskEngine.load_state().  The
    ``breaker_tripped`` latch is intentionally one-way within a session — it
    resets only when ``reset_for_session(new_date)`` is called with a date
    strictly different from the stored ``session_date``.

    Attributes:
        session_date:        The trading date this state belongs to.
        breaker_tripped:     Daily-loss circuit breaker latch (one-way).
        floor_breached:      Hard-floor latch (one-way per session).
        realized_pnl:        Running realized P&L cached from the last on_bar.
        committed_margin:    Sum of margin allocated to currently open orders.
        symbol_open_counts:  Live open-position count per instrument symbol.
    """
    session_date: date
    breaker_tripped: bool = False
    floor_breached: bool = False
    realized_pnl: float = 0.0
    committed_margin: float = 0.0
    symbol_open_counts: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to a JSON string for durable persistence (ARCHITECTURE §5)."""
        return json.dumps({
            "session_date": self.session_date.isoformat(),
            "breaker_tripped": self.breaker_tripped,
            "floor_breached": self.floor_breached,
            "realized_pnl": self.realized_pnl,
            "committed_margin": self.committed_margin,
            "symbol_open_counts": self.symbol_open_counts,
        })

    @classmethod
    def from_json(cls, raw: str) -> "SessionRiskState":
        """Deserialize from a JSON string produced by ``to_json``."""
        d = json.loads(raw)
        return cls(
            session_date=date.fromisoformat(d["session_date"]),
            breaker_tripped=bool(d["breaker_tripped"]),
            floor_breached=bool(d["floor_breached"]),
            realized_pnl=float(d["realized_pnl"]),
            committed_margin=float(d["committed_margin"]),
            symbol_open_counts={str(k): int(v) for k, v in d["symbol_open_counts"].items()},
        )


# ---------------------------------------------------------------------------
# Risk engine implementation
# ---------------------------------------------------------------------------

class IntradayRiskEngine:
    """Intraday sizing, margin, and circuit-breaker engine.

    Implements the ``core.RiskEngine`` protocol plus session lifecycle helpers.

    Usage (backtest / paper driver):
    ::

        engine = IntradayRiskEngine(params)
        engine.reset_for_session(today)

        # per bar:
        reasons = engine.on_bar(snapshot)
        if reasons:
            # flatten all, halt new entries

        # per signal:
        result = engine.size(intent, snapshot, today)
        if isinstance(result, SizedOrder):
            engine.register_fill(result)          # after confirmed fill
        ...

        # when a position closes:
        engine.register_close(symbol, margin_freed)
    """

    def __init__(self, config: RiskParams) -> None:
        self._params = config
        # Sentinel date: session must be started via reset_for_session() before use.
        self._state = SessionRiskState(session_date=date.min)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def reset_for_session(self, session_date: date) -> None:
        """Start a new trading session.

        Clears all latches and counters.  Idempotent for the SAME date — the
        breaker cannot be un-tripped within a session (finAgent latch-bug
        regression guard).

        Args:
            session_date: The calendar date of the new session.
        """
        if session_date == self._state.session_date:
            # Same date → no-op: preserve breaker/floor latches.
            return
        self._state = SessionRiskState(session_date=session_date)

    def load_state(self, state: SessionRiskState) -> None:
        """Replace internal state (crash-recovery startup path, ARCHITECTURE §5).

        The paper engine calls this after reading today's state from SQLite.
        """
        self._state = state

    @property
    def state(self) -> SessionRiskState:
        """Read-only view of current session state (for persistence)."""
        return self._state

    # ------------------------------------------------------------------
    # core.RiskEngine protocol — on_bar
    # ------------------------------------------------------------------

    def on_bar(self, snapshot: RiskSnapshot) -> list[ExitReason]:
        """Evaluate circuit-breaker and floor conditions for the current bar.

        Returns a non-empty list **only on the first bar** that crosses each
        threshold (latch transition).  Subsequent bars that remain below the
        threshold also return [] — the size() method enforces the halt via the
        latched flags.

        Evaluation order: breaker first, then floor.  Both may appear in one
        result if they trip simultaneously.

        Args:
            snapshot: Current bar's risk snapshot from the session driver.

        Returns:
            List of ExitReasons (may be empty).  Non-empty → caller must
            flatten all open positions and halt new entries.
        """
        # Sync realized_pnl cache for crash recovery persistence.
        self._state.realized_pnl = snapshot.realized_pnl_today

        reasons: list[ExitReason] = []
        pnl = snapshot.realized_pnl_today + snapshot.unrealized_pnl

        # --- Daily-loss circuit breaker ---
        breaker_threshold = -(self._params.max_daily_loss_pct * self._params.capital)
        if not self._state.breaker_tripped and pnl <= breaker_threshold:
            self._state.breaker_tripped = True
            reasons.append(ExitReason.BREAKER)

        # --- Hard floor ---
        current_equity = self._params.capital + pnl
        if not self._state.floor_breached and current_equity <= self._params.hard_floor:
            self._state.floor_breached = True
            reasons.append(ExitReason.FLOOR)

        return reasons

    # ------------------------------------------------------------------
    # core.RiskEngine protocol — size
    # ------------------------------------------------------------------

    def size(
        self,
        intent: OrderIntent,
        snapshot: RiskSnapshot,
        on: date,
    ) -> SizedOrder | Rejection:
        """Compute position size for an OrderIntent and validate risk limits.

        Rejection rules are checked in priority order (cheapest first):

        1. breaker_tripped  — daily-loss latch (internal or snapshot)
        2. floor_breached   — hard-floor latch (internal or snapshot)
        3. t2t_short        — SELL on a T2T/BE-series equity
        4. max_open_positions — global position count cap
        5. per_symbol_cap   — per-instrument symbol cap
        6. zero_qty         — derivative lot rounding yields 0 contracts
        7. margin_exceeded  — computed margin exceeds available cash

        Sizing formula:
            risk_budget = params.capital × per_trade_risk_pct
            # equity:
            qty = floor(risk_budget / |ref_price − stop_price|)
            margin = ref_price × qty / equity_mis_leverage
            # derivatives (per contract = lot_size units):
            risk_per_lot = |ref_price − stop_price| × lot_size(on)
            qty = floor(risk_budget / risk_per_lot)   # number of contracts
            margin = 0.12 × ref_price × qty × lot_size(on)  # SPAN proxy TODO

        Args:
            intent:   The trade signal from a strategy.
            snapshot: Current risk snapshot (capital, pnl, position counts).
            on:       Trade date for lot-size lookup.

        Returns:
            SizedOrder on approval; Rejection with a rule string on failure.
        """
        params = self._params
        instr: Instrument = intent.instrument

        # ---- 1. Breaker check (internal latch takes priority over snapshot) ----
        if self._state.breaker_tripped or snapshot.breaker_tripped:
            return Rejection(
                intent=intent,
                rule="breaker_tripped",
                detail="daily-loss circuit breaker is active for this session",
            )

        # ---- 2. Floor check ----
        if self._state.floor_breached or snapshot.floor_breached:
            return Rejection(
                intent=intent,
                rule="floor_breached",
                detail="hard-floor breach — operator intervention required",
            )

        # ---- 3. T2T / BE-series short rejection ----
        if intent.side is Side.SELL and not instr.can_short_intraday:
            return Rejection(
                intent=intent,
                rule="t2t_short",
                detail=f"{instr.symbol} is T2T/BE-series — intraday short not permitted",
            )

        # ---- 4. Global position cap ----
        if snapshot.open_position_count >= params.max_open_positions:
            return Rejection(
                intent=intent,
                rule="max_open_positions",
                detail=(
                    f"open_position_count={snapshot.open_position_count} "
                    f">= max={params.max_open_positions}"
                ),
            )

        # ---- 5. Per-symbol cap ----
        sym = instr.symbol
        sym_count = self._state.symbol_open_counts.get(sym, 0)
        if sym_count >= params.max_per_symbol:
            return Rejection(
                intent=intent,
                rule="per_symbol_cap",
                detail=f"{sym} already has {sym_count} open position(s); max={params.max_per_symbol}",
            )

        # ---- Sizing math ----
        stop_dist = abs(intent.ref_price - intent.stop_price)
        risk_budget = params.capital * params.per_trade_risk_pct

        if instr.is_derivative:
            lot_sz = instr.lot_size(on)
            risk_per_lot = stop_dist * lot_sz
            qty = math.floor(risk_budget / risk_per_lot)   # number of contracts
        else:
            qty = math.floor(risk_budget / stop_dist)       # number of shares

        # ---- 6. Zero-quantity check ----
        if qty == 0:
            return Rejection(
                intent=intent,
                rule="zero_qty",
                detail=(
                    f"risk_budget={risk_budget:.2f} / "
                    f"stop_dist={stop_dist:.4f}"
                    + (f" * lot_sz={instr.lot_size(on)}" if instr.is_derivative else "")
                    + " rounds to 0 — lot/quantity too small for risk budget"
                ),
            )

        # ---- Margin computation ----
        if instr.is_derivative:
            lot_sz = instr.lot_size(on)
            notional = intent.ref_price * qty * lot_sz
            # TODO: replace with live SPAN via Dhan margin API (ARCHITECTURE §5)
            margin = 0.12 * notional
        else:
            margin = intent.ref_price * qty / params.equity_mis_leverage

        # ---- 7. Margin available check ----
        available = (
            snapshot.capital
            + snapshot.realized_pnl_today
            - self._state.committed_margin
        )
        if margin > available:
            return Rejection(
                intent=intent,
                rule="margin_exceeded",
                detail=(
                    f"need ₹{margin:.2f}, available ₹{available:.2f} "
                    f"(capital={snapshot.capital:.2f} + realized={snapshot.realized_pnl_today:.2f} "
                    f"- committed={self._state.committed_margin:.2f})"
                ),
            )

        return SizedOrder(intent=intent, quantity=qty, margin_required=margin)

    # ------------------------------------------------------------------
    # Fill / close registration (called by backtest/paper driver)
    # ------------------------------------------------------------------

    def register_fill(self, order: SizedOrder) -> None:
        """Record a confirmed fill so per-symbol and margin counters stay accurate.

        The backtest / paper engine must call this after every confirmed entry
        fill.  Matching ``register_close`` must be called when the position closes.

        Args:
            order: The SizedOrder that was actually filled.
        """
        sym = order.intent.instrument.symbol
        self._state.committed_margin += order.margin_required
        self._state.symbol_open_counts[sym] = (
            self._state.symbol_open_counts.get(sym, 0) + 1
        )

    def register_close(self, symbol: str, margin_freed: float) -> None:
        """Record a confirmed position close.

        Decrements the per-symbol open count and releases committed margin.

        Args:
            symbol:       The instrument symbol of the closed position.
            margin_freed: The margin_required from the original SizedOrder.
        """
        self._state.committed_margin = max(
            0.0, self._state.committed_margin - margin_freed
        )
        if symbol in self._state.symbol_open_counts:
            self._state.symbol_open_counts[symbol] = max(
                0, self._state.symbol_open_counts[symbol] - 1
            )
