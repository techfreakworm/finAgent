"""
Margin availability checker.

Provides conservative margin estimates for option strangles and checks
whether sufficient capital is available before placing a trade.
"""

import logging

from config import config

logger = logging.getLogger(__name__)


class MarginChecker:
    """Pre-trade margin gatekeeper.

    Parameters
    ----------
    capital : float
        Total available capital in INR.
    max_utilization : float
        Maximum fraction of capital that may be deployed as margin
        (default from ``config.risk.max_margin_utilization``).
    """

    def __init__(
        self,
        capital: float,
        max_utilization: float = config.risk.max_margin_utilization,
    ) -> None:
        self._capital: float = capital
        self._max_utilization: float = max_utilization
        logger.info(
            "MarginChecker initialised: capital=%.2f  max_utilization=%.0f%%",
            capital, max_utilization * 100,
        )

    # ------------------------------------------------------------------
    # Trade-gate
    # ------------------------------------------------------------------
    def can_trade(self, margin_required: float) -> bool:
        """Check whether a new trade can be taken given margin needs.

        Parameters
        ----------
        margin_required : float
            Estimated margin required for the trade.

        Returns
        -------
        bool
            ``True`` if the trade fits within the utilisation cap.
        """
        allowed = self._capital * self._max_utilization
        ok = margin_required <= allowed
        if not ok:
            logger.warning(
                "Margin gate BLOCKED: required=%.2f > allowed=%.2f "
                "(capital=%.2f  util=%.0f%%)",
                margin_required, allowed, self._capital, self._max_utilization * 100,
            )
        else:
            logger.debug(
                "Margin gate OK: required=%.2f  allowed=%.2f",
                margin_required, allowed,
            )
        return ok

    # ------------------------------------------------------------------
    # Estimation
    # ------------------------------------------------------------------
    def estimate_strangle_margin(
        self,
        spot_price: float,
        lot_size: int,
        iv_pct: float = 15.0,
    ) -> float:
        """Rough margin estimate for a short-strangle on NIFTY / BANKNIFTY.

        The exchange SPAN margin is approximated as::

            spot * lot_size * max(0.12, iv/100 * 0.80)

        This is deliberately conservative; the actual SPAN margin depends
        on many more variables.

        Parameters
        ----------
        spot_price : float
            Current spot / underlying price.
        lot_size : int
            Number of units per lot.
        iv_pct : float
            Annualised implied volatility in percentage terms (e.g. 15.0
            for 15 %).

        Returns
        -------
        float
            Estimated margin requirement in INR.
        """
        margin_factor = max(0.12, (iv_pct / 100) * 0.80)
        margin = spot_price * lot_size * margin_factor
        logger.debug(
            "Strangle margin estimate: spot=%.1f  lot=%d  iv=%.1f%%  "
            "factor=%.4f => %.2f",
            spot_price, lot_size, iv_pct, margin_factor, margin,
        )
        return round(margin, 2)

    # ------------------------------------------------------------------
    # Available margin
    # ------------------------------------------------------------------
    def available_margin(self, current_margin_used: float = 0.0) -> float:
        """Return remaining deployable margin.

        Parameters
        ----------
        current_margin_used : float
            Margin already blocked by existing positions.

        Returns
        -------
        float
            INR amount still available for new trades.
        """
        allowed = self._capital * self._max_utilization
        available = max(0.0, allowed - current_margin_used)
        logger.debug(
            "Available margin: allowed=%.2f  used=%.2f => available=%.2f",
            allowed, current_margin_used, available,
        )
        return round(available, 2)

    # ------------------------------------------------------------------
    # Capital updates
    # ------------------------------------------------------------------
    def update_capital(self, new_capital: float) -> None:
        """Update capital base (e.g. after realised P&L is booked).

        Parameters
        ----------
        new_capital : float
            New capital value in INR.
        """
        old = self._capital
        self._capital = new_capital
        logger.info("MarginChecker capital updated: %.2f -> %.2f", old, new_capital)
