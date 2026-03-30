"""NIFTY Weekly Short Strangle — Primary validated strategy (XIRR 42-95%)."""

import logging
from datetime import datetime
from config import config
from strategies.base import BaseStrategy, Signal
from data.option_chain import compute_max_pain, compute_pcr, get_strangle_strikes

logger = logging.getLogger(__name__)


class NiftyStrangle(BaseStrategy):
    """
    Sell weekly OTM strangle on NIFTY.
    Entry: Monday (sell ATM+2 CE and ATM-2 PE)
    Exit: Thursday expiry, stop-loss at 2x premium, or floor breach
    """

    def __init__(self):
        self.cfg = config.nifty

    def generate_signals(self, data: dict) -> list[Signal]:
        """
        Generate short strangle signal if conditions are met.

        Expected data keys:
            option_chain: dict — Dhan option chain response
            vix: float — current India VIX
            spot: float — NIFTY spot price
            current_date: datetime
            open_positions: list — existing positions for this strategy
        """
        chain = data.get("option_chain")
        vix = data.get("vix", 0)
        spot = data.get("spot", 0)
        current_date = data.get("current_date", datetime.now())
        open_positions = data.get("open_positions", [])

        if not chain or spot == 0:
            logger.debug("No option chain or spot data")
            return []

        # Check: VIX above minimum
        if vix < self.cfg.min_vix_for_entry:
            logger.info("VIX %.1f < %.1f minimum, skipping", vix, self.cfg.min_vix_for_entry)
            return []

        # Check: Must be Monday (or first trading day of week)
        if current_date.weekday() != 0:
            logger.debug("Not Monday (%d), skipping strangle entry", current_date.weekday())
            return []

        # Check: No existing open position
        if open_positions:
            logger.debug("Already have %d open positions, skipping", len(open_positions))
            return []

        # Select strikes
        strikes = get_strangle_strikes(chain, spot, offset=self.cfg.strangle_offset)
        ce_strike = strikes["ce_strike"]
        pe_strike = strikes["pe_strike"]
        ce_premium = strikes["ce_premium"]
        pe_premium = strikes["pe_premium"]

        if ce_premium <= 0 and pe_premium <= 0:
            logger.warning("Zero premiums for strikes CE %.0f / PE %.0f", ce_strike, pe_strike)
            return []

        total_premium = ce_premium + pe_premium
        lot_size = self.cfg.lot_size(current_date)

        # Estimate margin
        margin = spot * lot_size * max(0.12, vix / 100 * 0.8)

        # Compute analytics
        max_pain = compute_max_pain(chain)
        pcr = compute_pcr(chain)

        # Stop loss: exit if combined premium doubles (2x entry premium)
        stop_loss = total_premium * 2

        signal = Signal(
            strategy="nifty_strangle",
            symbol="NIFTY",
            direction="SELL",
            entry_price=total_premium,
            stop_loss=stop_loss,
            target=0,  # target is full premium decay at expiry
            lot_size=lot_size,
            margin_required=margin,
            confidence=min(0.9, vix / 20),  # higher VIX = higher confidence in premium selling
            reasoning=(
                f"NIFTY strangle: sell {ce_strike:.0f}CE @ ₹{ce_premium:.1f} + "
                f"{pe_strike:.0f}PE @ ₹{pe_premium:.1f}. "
                f"VIX={vix:.1f}, MaxPain={max_pain:.0f}, PCR={pcr['pcr_oi']:.2f}. "
                f"Total premium=₹{total_premium:.1f}/unit, ₹{total_premium * lot_size:,.0f} total."
            ),
            metadata={
                "ce_strike": ce_strike,
                "pe_strike": pe_strike,
                "ce_premium": ce_premium,
                "pe_premium": pe_premium,
                "max_pain": max_pain,
                "pcr_oi": pcr["pcr_oi"],
                "vix": vix,
                "spot": spot,
            },
        )

        logger.info("Signal: SELL NIFTY strangle CE%.0f/PE%.0f, premium ₹%.1f, margin ₹%s",
                     ce_strike, pe_strike, total_premium, f"{margin:,.0f}")
        return [signal]

    def should_exit(self, position, current_data: dict) -> tuple[bool, str]:
        """
        Check if an open strangle position should be exited.

        Expected current_data keys:
            current_date: datetime
            ce_current_premium: float
            pe_current_premium: float
            floor_breached: bool (optional)
        """
        current_date = current_data.get("current_date", datetime.now())
        ce_current = current_data.get("ce_current_premium", 0)
        pe_current = current_data.get("pe_current_premium", 0)
        floor_breached = current_data.get("floor_breached", False)

        # Floor breach — immediate exit
        if floor_breached:
            return True, "floor_breach"

        # Thursday expiry
        if current_date.weekday() == 3:
            return True, "expiry"

        # Stop loss: combined premium exceeds 2x entry
        entry_premium = position.entry_price
        current_premium = ce_current + pe_current
        if current_premium > entry_premium * 2:
            logger.warning("Strangle stop hit: current ₹%.1f > 2x entry ₹%.1f",
                           current_premium, entry_premium)
            return True, "stop_loss"

        return False, ""
