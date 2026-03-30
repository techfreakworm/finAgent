"""
In-memory position book and P&L tracker.

Tracks open / closed positions, computes unrealised and realised P&L,
and provides per-strategy statistics.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

from config import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position data class
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """Represents a single trading position (one leg)."""

    symbol: str
    strategy: str
    entry_date: datetime
    entry_price: float
    quantity: int
    direction: str  # "SHORT" or "LONG"
    stop_loss: float = 0.0
    target: float = 0.0
    current_price: float = 0.0
    status: str = "OPEN"  # "OPEN" | "CLOSED"
    position_id: str = field(default_factory=lambda: uuid4().hex[:8])
    exit_price: float = 0.0
    exit_date: Optional[datetime] = None
    exit_reason: str = ""
    metadata: Dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # P&L helpers
    # ------------------------------------------------------------------
    @property
    def unrealized_pnl(self) -> float:
        """Unrealised mark-to-market P&L for this position."""
        if self.status != "OPEN":
            return 0.0
        return self._compute_pnl(self.current_price)

    @property
    def realized_pnl(self) -> float:
        """Realised P&L (only meaningful once the position is CLOSED)."""
        if self.status != "CLOSED":
            return 0.0
        return self._compute_pnl(self.exit_price)

    def _compute_pnl(self, ref_price: float) -> float:
        if self.direction == "LONG":
            return (ref_price - self.entry_price) * self.quantity
        else:  # SHORT
            return (self.entry_price - ref_price) * self.quantity


# ---------------------------------------------------------------------------
# Position Manager
# ---------------------------------------------------------------------------
class PositionManager:
    """Manages the lifecycle of trading positions.

    Keeps an in-memory book of open and closed positions, exposes P&L
    queries and per-strategy statistics.
    """

    def __init__(self) -> None:
        self._positions: Dict[str, Position] = {}
        self._closed_trades: List[Dict] = []
        logger.info("PositionManager initialised")

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def add_position(self, position: Position) -> str:
        """Register a new position and return its id.

        Parameters
        ----------
        position : Position
            A fully populated ``Position`` dataclass instance.

        Returns
        -------
        str
            The ``position_id`` assigned to the position.
        """
        self._positions[position.position_id] = position
        logger.info(
            "Position added: id=%s  sym=%s  dir=%s  qty=%d  entry=%.2f  "
            "strategy=%s",
            position.position_id,
            position.symbol,
            position.direction,
            position.quantity,
            position.entry_price,
            position.strategy,
        )
        return position.position_id

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str,
    ) -> Dict:
        """Close an open position and record P&L.

        Parameters
        ----------
        position_id : str
            Id of the position to close.
        exit_price : float
            Price at which the position was exited.
        exit_reason : str
            Human-readable reason (e.g. ``"stop_loss"``, ``"target"``,
            ``"floor_breach"``, ``"expiry"``).

        Returns
        -------
        dict
            Summary with keys: ``position_id``, ``symbol``, ``direction``,
            ``entry_price``, ``exit_price``, ``quantity``, ``pnl``,
            ``exit_reason``, ``entry_date``, ``exit_date``, ``strategy``.

        Raises
        ------
        KeyError
            If ``position_id`` is not found.
        ValueError
            If the position is already closed.
        """
        if position_id not in self._positions:
            raise KeyError(f"Position {position_id} not found")

        pos = self._positions[position_id]
        if pos.status == "CLOSED":
            raise ValueError(f"Position {position_id} is already closed")

        pos.exit_price = exit_price
        pos.exit_date = datetime.now()
        pos.exit_reason = exit_reason
        pos.status = "CLOSED"

        pnl = pos.realized_pnl

        trade_record = {
            "position_id": pos.position_id,
            "symbol": pos.symbol,
            "direction": pos.direction,
            "entry_price": pos.entry_price,
            "exit_price": pos.exit_price,
            "quantity": pos.quantity,
            "pnl": round(pnl, 2),
            "exit_reason": exit_reason,
            "entry_date": pos.entry_date,
            "exit_date": pos.exit_date,
            "strategy": pos.strategy,
        }
        self._closed_trades.append(trade_record)

        logger.info(
            "Position closed: id=%s  sym=%s  dir=%s  pnl=%.2f  reason=%s",
            position_id, pos.symbol, pos.direction, pnl, exit_reason,
        )
        return trade_record

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get_open_positions(self) -> List[Position]:
        """Return all currently open positions.

        Returns
        -------
        list[Position]
        """
        return [p for p in self._positions.values() if p.status == "OPEN"]

    def get_unrealized_pnl(self) -> float:
        """Sum of unrealised P&L across all open positions.

        Returns
        -------
        float
        """
        total = sum(p.unrealized_pnl for p in self._positions.values())
        return round(total, 2)

    def get_total_margin_used(self) -> float:
        """Rough estimate of total margin consumed by open positions.

        Uses ``entry_price * quantity`` as a proxy (conservative for
        options; the real SPAN margin is typically lower for hedged
        positions).

        Returns
        -------
        float
        """
        total = sum(
            p.entry_price * p.quantity
            for p in self._positions.values()
            if p.status == "OPEN"
        )
        return round(total, 2)

    def get_closed_trades(self) -> List[Dict]:
        """Return the list of all closed trade records.

        Returns
        -------
        list[dict]
        """
        return list(self._closed_trades)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------
    def get_strategy_stats(self, strategy_name: str) -> Dict:
        """Compute aggregate statistics for a given strategy.

        Parameters
        ----------
        strategy_name : str
            Name of the strategy (must match ``Position.strategy``).

        Returns
        -------
        dict
            ``total_trades``    -- int
            ``winners``         -- int
            ``losers``          -- int
            ``win_rate``        -- float (0.0 -- 1.0)
            ``total_pnl``      -- float
            ``avg_pnl``        -- float
            ``max_win``        -- float
            ``max_loss``       -- float
            ``avg_win``        -- float
            ``avg_loss``       -- float
        """
        trades = [t for t in self._closed_trades if t["strategy"] == strategy_name]
        total = len(trades)

        if total == 0:
            return {
                "total_trades": 0,
                "winners": 0,
                "losers": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "max_win": 0.0,
                "max_loss": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
            }

        pnls = [t["pnl"] for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        stats = {
            "total_trades": total,
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": round(len(winners) / total, 4),
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / total, 2),
            "max_win": round(max(pnls), 2),
            "max_loss": round(min(pnls), 2),
            "avg_win": round(sum(winners) / len(winners), 2) if winners else 0.0,
            "avg_loss": round(sum(losers) / len(losers), 2) if losers else 0.0,
        }

        logger.info(
            "Strategy stats [%s]: trades=%d  win_rate=%.1f%%  total_pnl=%.2f",
            strategy_name, total, stats["win_rate"] * 100, stats["total_pnl"],
        )
        return stats
