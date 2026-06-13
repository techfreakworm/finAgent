"""Point-in-time lot-size schedule for NSE F&O instruments (ARCHITECTURE §2).

NSE revises lot sizes at series rollover per circulars FAOP64506/FAOP70616.
Boundary dates are the first expiry date of the new series (Thursday of the
last week of the month in question — approximate; verify empirically against
NSE circulars before trading live).

Schedule (from docs/finagent-assessment.md §Post-review corrections):
    NIFTY     : 25 → 75 (Nov-2024 series) → 65 (Jan-2026 series)
    BANKNIFTY : 15 → 30 (Nov-2024 series) → 35 (Jul-2025 series) → 30 (Jan-2026 series)

Lot sizes apply from the START of the named series through the expiry of the
series BEFORE the next change.  The boundary dates below are the approximate
start-of-series dates; adjust once NSE circulars are confirmed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from algotrader.core import Instrument, Segment


# ---------------------------------------------------------------------------
# Lot-size boundary dates (series start, approximate — verify vs NSE circulars)
# ---------------------------------------------------------------------------

# NIFTY: Nov-2024 series start → Jan-2026 series start
_NIFTY_NOV2024 = date(2024, 10, 25)   # first day of the Nov-2024 expiry series
_NIFTY_JAN2026 = date(2025, 12, 26)   # first day of the Jan-2026 expiry series

# BANKNIFTY: Nov-2024 → Jul-2025 → Jan-2026 series starts
_BNIFTY_NOV2024 = date(2024, 10, 25)  # first day of the Nov-2024 expiry series
_BNIFTY_JUL2025 = date(2025, 6, 27)   # first day of the Jul-2025 expiry series
_BNIFTY_JAN2026 = date(2025, 12, 26)  # first day of the Jan-2026 expiry series


def _nifty_lot(on: date) -> int:
    """Point-in-time NIFTY lot size.

    Pre Nov-2024 series : 25
    Nov-2024 series     : 75
    Jan-2026 series+    : 65
    """
    if on < _NIFTY_NOV2024:
        return 25
    if on < _NIFTY_JAN2026:
        return 75
    return 65


def _banknifty_lot(on: date) -> int:
    """Point-in-time BANKNIFTY lot size.

    Pre Nov-2024 series : 15
    Nov-2024 series     : 30
    Jul-2025 series     : 35
    Jan-2026 series+    : 30
    """
    if on < _BNIFTY_NOV2024:
        return 15
    if on < _BNIFTY_JUL2025:
        return 30
    if on < _BNIFTY_JAN2026:
        return 35
    return 30


# ---------------------------------------------------------------------------
# FnoInstrument — overrides lot_size() with the dated schedule
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FnoInstrument(Instrument):
    """Derivative instrument whose lot_size() follows the NSE point-in-time
    schedule (ARCHITECTURE §2, docs/finagent-assessment.md §Post-review).

    The `underlying` field controls which schedule is used:
      "NIFTY"     → _nifty_lot()
      "BANKNIFTY" → _banknifty_lot()
      anything else → raises ValueError so unknown schedules fail loudly.

    For equities (is_derivative=False) use the base Instrument directly.
    """

    def lot_size(self, on: date) -> int:  # type: ignore[override]
        """Return the point-in-time lot size for this derivative on *on*."""
        ul = (self.underlying or "").upper()
        if ul == "NIFTY":
            return _nifty_lot(on)
        if ul in ("BANKNIFTY", "BANKEX"):
            return _banknifty_lot(on)
        raise ValueError(
            f"FnoInstrument.lot_size: no dated schedule for underlying={self.underlying!r}. "
            "Add the schedule to algotrader/data/instruments.py or use the base "
            "Instrument class for instruments with a fixed lot size."
        )


# ---------------------------------------------------------------------------
# Pre-built canonical instrument singletons
# ---------------------------------------------------------------------------

NIFTY_FUT = FnoInstrument(
    symbol="NIFTY-FUT",
    security_id="13",          # Dhan scrip master ID — confirm via instruments API
    segment=Segment.NSE_FNO,
    tick_size=0.05,
    is_derivative=True,
    underlying="NIFTY",
    can_short_intraday=True,
)

BANKNIFTY_FUT = FnoInstrument(
    symbol="BANKNIFTY-FUT",
    security_id="25",          # Dhan scrip master ID — confirm via instruments API
    segment=Segment.NSE_FNO,
    tick_size=0.05,
    is_derivative=True,
    underlying="BANKNIFTY",
    can_short_intraday=True,
)
