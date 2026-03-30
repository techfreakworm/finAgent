"""
Zerodha / Indian exchange cost model for options and delivery trades.

All amounts are in INR.  The rates below reflect the SEBI / NSE fee schedule
as of late-2024 (post the STT hike on option sells).
"""

import logging

from config import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SEBI_TURNOVER_FEE = 10 / 1_00_00_000  # Rs 10 per crore
_GST_RATE = 0.18                        # 18 %


class ZerodhaCosts:
    """Static helpers that return the *total* transaction cost for a leg or
    a complete round-trip, inclusive of brokerage, STT, exchange txn charges,
    stamp duty, SEBI turnover fee and GST."""

    # ------------------------------------------------------------------
    # Single-leg option costs
    # ------------------------------------------------------------------
    @staticmethod
    def options_sell_cost(premium_value_inr: float) -> float:
        """Cost of *selling* (writing) an option position.

        Parameters
        ----------
        premium_value_inr : float
            Total premium value = premium_per_unit * quantity.

        Returns
        -------
        float
            Total transaction cost in INR.
        """
        brokerage = 20.0
        stt = premium_value_inr * 0.000625          # 0.0625 % on sell
        exchange_txn = premium_value_inr * 0.0005    # 0.05 %
        sebi = premium_value_inr * _SEBI_TURNOVER_FEE
        gst = (brokerage + exchange_txn) * _GST_RATE
        total = brokerage + stt + exchange_txn + sebi + gst
        logger.debug(
            "Options SELL cost on %.2f: brok=%.2f stt=%.2f exch=%.2f "
            "sebi=%.4f gst=%.2f => %.2f",
            premium_value_inr, brokerage, stt, exchange_txn, sebi, gst, total,
        )
        return round(total, 2)

    @staticmethod
    def options_buy_cost(premium_value_inr: float) -> float:
        """Cost of *buying* an option position.

        Parameters
        ----------
        premium_value_inr : float
            Total premium value = premium_per_unit * quantity.

        Returns
        -------
        float
            Total transaction cost in INR.
        """
        brokerage = 20.0
        exchange_txn = premium_value_inr * 0.0005    # 0.05 %
        stamp_duty = premium_value_inr * 0.00003     # 0.003 %
        sebi = premium_value_inr * _SEBI_TURNOVER_FEE
        gst = (brokerage + exchange_txn) * _GST_RATE
        total = brokerage + exchange_txn + stamp_duty + sebi + gst
        logger.debug(
            "Options BUY cost on %.2f: brok=%.2f exch=%.2f stamp=%.4f "
            "sebi=%.4f gst=%.2f => %.2f",
            premium_value_inr, brokerage, exchange_txn, stamp_duty, sebi, gst, total,
        )
        return round(total, 2)

    # ------------------------------------------------------------------
    # Multi-leg helpers
    # ------------------------------------------------------------------
    @staticmethod
    def strangle_round_trip_cost(
        ce_sell_val: float,
        pe_sell_val: float,
        ce_buy_val: float,
        pe_buy_val: float,
    ) -> float:
        """Total cost for a short-strangle round-trip (4 legs).

        Parameters
        ----------
        ce_sell_val : float
            CE premium value at entry (sell).
        pe_sell_val : float
            PE premium value at entry (sell).
        ce_buy_val : float
            CE premium value at exit (buy-back).
        pe_buy_val : float
            PE premium value at exit (buy-back).

        Returns
        -------
        float
            Combined cost of all four legs.
        """
        total = (
            ZerodhaCosts.options_sell_cost(ce_sell_val)
            + ZerodhaCosts.options_sell_cost(pe_sell_val)
            + ZerodhaCosts.options_buy_cost(ce_buy_val)
            + ZerodhaCosts.options_buy_cost(pe_buy_val)
        )
        logger.info(
            "Strangle round-trip cost: sell(CE=%.0f PE=%.0f) "
            "buy(CE=%.0f PE=%.0f) => %.2f",
            ce_sell_val, pe_sell_val, ce_buy_val, pe_buy_val, total,
        )
        return round(total, 2)

    # ------------------------------------------------------------------
    # Equity delivery
    # ------------------------------------------------------------------
    @staticmethod
    def delivery_round_trip_cost(buy_value: float, sell_value: float) -> float:
        """Cost of a delivery equity round-trip (buy + sell).

        Parameters
        ----------
        buy_value : float
            Total buy consideration (price * qty).
        sell_value : float
            Total sell consideration (price * qty).

        Returns
        -------
        float
            Combined cost of buy and sell legs including DP charges.
        """
        brokerage = 0.0  # Zerodha: zero brokerage on delivery

        # STT: 0.1 % on both buy and sell
        stt = (buy_value + sell_value) * 0.001

        # Exchange txn charges: 0.00345 % on both sides
        exchange_txn = (buy_value + sell_value) * 0.0000345

        # Stamp duty: 0.015 % on buy side only
        stamp_duty = buy_value * 0.00015

        # SEBI turnover fee on both sides
        sebi = (buy_value + sell_value) * _SEBI_TURNOVER_FEE

        # GST 18 % on (brokerage + exchange txn)
        gst = (brokerage + exchange_txn) * _GST_RATE

        # DP (depository participant) charge -- flat per sell scrip
        dp_charges = 15.93

        total = brokerage + stt + exchange_txn + stamp_duty + sebi + gst + dp_charges
        logger.info(
            "Delivery round-trip cost: buy=%.0f sell=%.0f => %.2f",
            buy_value, sell_value, total,
        )
        return round(total, 2)

    # ------------------------------------------------------------------
    # Slippage estimate
    # ------------------------------------------------------------------
    @staticmethod
    def estimate_slippage(
        premium_per_unit: float,
        lot_size: int,
        n_legs: int = 4,
        emergency: bool = False,
    ) -> float:
        """Conservative slippage estimate.

        Parameters
        ----------
        premium_per_unit : float
            Approximate premium per unit (used only for context /
            logging; the estimate is purely quantity-based).
        lot_size : int
            Number of units in one lot.
        n_legs : int
            Number of legs in the trade (default 4 for a strangle round-trip).
        emergency : bool
            If ``True`` (e.g. forced exit on floor breach), assume wider
            spreads -- Rs 2 per unit per leg instead of the normal Rs 1.

        Returns
        -------
        float
            Estimated slippage cost in INR.
        """
        per_unit = 2.0 if emergency else 1.0
        slippage = per_unit * lot_size * n_legs
        logger.debug(
            "Slippage estimate: prem=%.1f lot=%d legs=%d emergency=%s => %.2f",
            premium_per_unit, lot_size, n_legs, emergency, slippage,
        )
        return round(slippage, 2)
