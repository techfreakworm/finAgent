"""
Hard-floor capital monitor.

Tracks real-time mark-to-market capital against the configured hard floor
(``config.risk.hard_floor``).  Emits WARNING when within 5 % of the floor
and CRITICAL when the floor is breached.
"""

import logging
from typing import Dict

from config import config

logger = logging.getLogger(__name__)

# Percentage proximity threshold at which a WARNING is emitted.
_WARNING_BUFFER_PCT = 0.05


class FloorMonitor:
    """Monitors live capital against a hard floor.

    Parameters
    ----------
    hard_floor : float
        The absolute INR floor below which trading must stop.
    capital : float
        Current available capital (pre-unrealised P&L).
    """

    def __init__(self, hard_floor: float, capital: float) -> None:
        self._hard_floor: float = hard_floor
        self._capital: float = capital
        self._breached: bool = False
        logger.info(
            "FloorMonitor initialised: capital=%.2f  hard_floor=%.2f",
            capital, hard_floor,
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def update_capital(self, new_capital: float) -> None:
        """Update the base capital (e.g. after a realised P&L book).

        Parameters
        ----------
        new_capital : float
            New capital value in INR.
        """
        old = self._capital
        self._capital = new_capital
        logger.info("Capital updated: %.2f -> %.2f", old, new_capital)

    def check_mtm(self, unrealized_pnl: float) -> Dict[str, object]:
        """Check projected capital against the hard floor.

        Parameters
        ----------
        unrealized_pnl : float
            Current mark-to-market unrealised P&L (positive = profit,
            negative = loss).

        Returns
        -------
        dict
            ``breached``   -- bool, True if projected capital <= floor.
            ``capital``    -- float, current base capital.
            ``projected``  -- float, capital + unrealised P&L.
            ``floor``      -- float, the hard floor value.
            ``action``     -- str, recommended action string.
        """
        projected = self._capital + unrealized_pnl
        breached = projected <= self._hard_floor

        # Compute how close we are to the floor
        buffer_zone = self._hard_floor * (1 + _WARNING_BUFFER_PCT)

        if breached:
            self._breached = True
            action = "EXIT_ALL"
            logger.critical(
                "FLOOR BREACHED: projected=%.2f  floor=%.2f  "
                "unrealised=%.2f -- immediate exit required",
                projected, self._hard_floor, unrealized_pnl,
            )
        elif projected <= buffer_zone:
            action = "REDUCE_RISK"
            logger.warning(
                "Approaching floor: projected=%.2f  buffer_zone=%.2f  "
                "floor=%.2f  unrealised=%.2f -- reduce risk",
                projected, buffer_zone, self._hard_floor, unrealized_pnl,
            )
        else:
            action = "NONE"
            logger.debug(
                "Floor check OK: projected=%.2f  floor=%.2f", projected, self._hard_floor,
            )

        return {
            "breached": breached,
            "capital": self._capital,
            "projected": projected,
            "floor": self._hard_floor,
            "action": action,
        }

    def should_exit_position(
        self, position_pnl: float, position_margin: float,
    ) -> bool:
        """Decide whether a single position should be force-closed.

        A position is flagged for exit if its loss exceeds the configured
        single-trade loss limit (``config.risk.max_single_trade_loss_pct``)
        or if the floor has already been breached.

        Parameters
        ----------
        position_pnl : float
            Current unrealised P&L of the position (negative = loss).
        position_margin : float
            Margin blocked by the position.

        Returns
        -------
        bool
            True if the position should be closed immediately.
        """
        if self._breached:
            logger.critical(
                "Floor already breached -- forcing exit (pnl=%.2f margin=%.2f)",
                position_pnl, position_margin,
            )
            return True

        max_loss = self._capital * config.risk.max_single_trade_loss_pct
        if position_pnl < 0 and abs(position_pnl) >= max_loss:
            logger.warning(
                "Single-trade loss limit hit: pnl=%.2f >= max_loss=%.2f",
                position_pnl, max_loss,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def is_breached(self) -> bool:
        """Return ``True`` if the hard floor has been breached at any
        point during this monitor's lifetime."""
        return self._breached
