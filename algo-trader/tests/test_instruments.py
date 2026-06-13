"""Tests for algotrader/data/instruments.py — FnoInstrument lot-size schedule.

Verifies the point-in-time lot sizes from:
    docs/finagent-assessment.md §Post-review corrections
    NSE circulars FAOP64506/FAOP70616

Schedule under test:
    NIFTY:     25 (pre Nov-2024) → 75 (Nov-2024 series) → 65 (Jan-2026 series)
    BANKNIFTY: 15 (pre Nov-2024) → 30 (Nov-2024 series) → 35 (Jul-2025 series)
                                 → 30 (Jan-2026 series)
"""
from __future__ import annotations

from datetime import date

import pytest

from algotrader.data.instruments import (
    BANKNIFTY_FUT,
    NIFTY_FUT,
    FnoInstrument,
    _banknifty_lot,
    _nifty_lot,
)
from algotrader.core import Segment


# ---------------------------------------------------------------------------
# NIFTY lot-size schedule
# ---------------------------------------------------------------------------

class TestNiftyLotSize:
    """NIFTY schedule: 25 → 75 (Nov-2024) → 65 (Jan-2026)."""

    def test_pre_nov2024(self):
        assert _nifty_lot(date(2024, 10, 24)) == 25   # day before boundary

    def test_boundary_nov2024(self):
        assert _nifty_lot(date(2024, 10, 25)) == 75   # first day of new series

    def test_mid_nov2024_to_dec2025(self):
        assert _nifty_lot(date(2024, 11, 1)) == 75
        assert _nifty_lot(date(2025, 6, 15)) == 75
        assert _nifty_lot(date(2025, 12, 25)) == 75   # day before Jan-2026 boundary

    def test_boundary_jan2026(self):
        assert _nifty_lot(date(2025, 12, 26)) == 65   # first day of Jan-2026 series

    def test_post_jan2026(self):
        assert _nifty_lot(date(2026, 3, 1)) == 65
        assert _nifty_lot(date(2027, 1, 1)) == 65


class TestNiftyFutInstrument:
    """NIFTY_FUT singleton uses the same schedule."""

    def test_pre_nov2024(self):
        assert NIFTY_FUT.lot_size(date(2024, 10, 24)) == 25

    def test_nov2024(self):
        assert NIFTY_FUT.lot_size(date(2024, 11, 1)) == 75

    def test_jan2026(self):
        assert NIFTY_FUT.lot_size(date(2026, 1, 15)) == 65


# ---------------------------------------------------------------------------
# BANKNIFTY lot-size schedule
# ---------------------------------------------------------------------------

class TestBankniftyLotSize:
    """BANKNIFTY schedule: 15 → 30 (Nov-2024) → 35 (Jul-2025) → 30 (Jan-2026)."""

    def test_pre_nov2024(self):
        assert _banknifty_lot(date(2024, 10, 24)) == 15

    def test_boundary_nov2024(self):
        assert _banknifty_lot(date(2024, 10, 25)) == 30

    def test_mid_nov2024_to_jun2025(self):
        assert _banknifty_lot(date(2024, 11, 1)) == 30
        assert _banknifty_lot(date(2025, 6, 26)) == 30  # day before Jul-2025 boundary

    def test_boundary_jul2025(self):
        assert _banknifty_lot(date(2025, 6, 27)) == 35

    def test_mid_jul2025_to_dec2025(self):
        assert _banknifty_lot(date(2025, 7, 1)) == 35
        assert _banknifty_lot(date(2025, 12, 25)) == 35  # day before Jan-2026 boundary

    def test_boundary_jan2026(self):
        assert _banknifty_lot(date(2025, 12, 26)) == 30

    def test_post_jan2026(self):
        assert _banknifty_lot(date(2026, 3, 1)) == 30


class TestBankniftyFutInstrument:
    """BANKNIFTY_FUT singleton uses the same schedule."""

    def test_pre_nov2024(self):
        assert BANKNIFTY_FUT.lot_size(date(2024, 10, 24)) == 15

    def test_nov2024(self):
        assert BANKNIFTY_FUT.lot_size(date(2024, 11, 1)) == 30

    def test_jul2025(self):
        assert BANKNIFTY_FUT.lot_size(date(2025, 7, 1)) == 35

    def test_jan2026(self):
        assert BANKNIFTY_FUT.lot_size(date(2026, 1, 15)) == 30


# ---------------------------------------------------------------------------
# FnoInstrument: unknown underlying raises ValueError
# ---------------------------------------------------------------------------

class TestFnoInstrumentUnknownUnderlying:

    def test_unknown_underlying_raises(self):
        instr = FnoInstrument(
            symbol="MIDCPNIFTY-FUT",
            security_id="999",
            segment=Segment.NSE_FNO,
            tick_size=0.05,
            is_derivative=True,
            underlying="MIDCPNIFTY",
        )
        with pytest.raises(ValueError, match="no dated schedule"):
            instr.lot_size(date(2025, 1, 1))

    def test_none_underlying_raises(self):
        instr = FnoInstrument(
            symbol="UNKNOWN-FUT",
            security_id="888",
            segment=Segment.NSE_FNO,
            tick_size=0.05,
            is_derivative=True,
            underlying=None,
        )
        with pytest.raises(ValueError, match="no dated schedule"):
            instr.lot_size(date(2025, 1, 1))


# ---------------------------------------------------------------------------
# Schedule is monotone (lot sizes don't jump to wrong tier within a period)
# ---------------------------------------------------------------------------

class TestNiftyScheduleIsMonotone:
    """All dates in [2024-10-25, 2025-12-25] should return 75."""

    def test_sample_dates_in_75_band(self):
        sample = [
            date(2024, 10, 25),
            date(2024, 12, 1),
            date(2025, 1, 1),
            date(2025, 6, 15),
            date(2025, 12, 25),
        ]
        for d in sample:
            assert _nifty_lot(d) == 75, f"expected 75 on {d}"


class TestBankniftyScheduleIsMonotone:
    """Verify each band returns the expected value for representative dates."""

    def test_band_30(self):
        for d in [date(2024, 10, 25), date(2025, 3, 1), date(2025, 6, 26)]:
            assert _banknifty_lot(d) == 30, f"expected 30 on {d}"

    def test_band_35(self):
        for d in [date(2025, 6, 27), date(2025, 9, 1), date(2025, 12, 25)]:
            assert _banknifty_lot(d) == 35, f"expected 35 on {d}"


def test_derivative_realized_pnl_includes_lot_size():
    """Regression (contract v1.1): gross_pnl for derivatives = (exit-entry) * contracts * lot_size.
    Guards the 65x-understatement bug found in adversarial review."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    from algotrader.core import Position, Side
    from algotrader.data.instruments import NIFTY_FUT

    ist = ZoneInfo("Asia/Kolkata")
    fut = NIFTY_FUT
    on = date(2026, 6, 11)
    assert fut.lot_size(on) == 65  # Jan-2026 series onward
    pos = Position(
        position_id="t1", strategy_id="orb", instrument=fut, side=Side.BUY,
        quantity=2, entry_price=25000.0, entry_ts=datetime(2026, 6, 11, 9, 30, tzinfo=ist),
        stop_price=24900.0, target_price=None, session_date=on,
        exit_price=25050.0, exit_ts=datetime(2026, 6, 11, 10, 30, tzinfo=ist),
    )
    assert pos.gross_pnl() == 50.0 * 2 * 65  # 6500, not 100
