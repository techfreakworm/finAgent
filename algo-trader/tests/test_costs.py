"""Unit tests for DhanCosts — date-banded Dhan cost model.

Each test asserts INDIVIDUAL components for hand-computed examples.
Date-band boundaries (2024-10-01, 2026-04-01) are tested on both sides
and on the boundary date itself (boundary is INCLUSIVE for the new rate).

Hand-computed reference values are documented inline so they can be
independently verified against dhan.co/pricing and the regulatory circulars
cited in ARCHITECTURE §3.3.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from algotrader.backtest.costs import DhanCosts
from algotrader.core import (
    CostBreakdown,
    Instrument,
    ProductType,
    Segment,
    Side,
)

# ---------------------------------------------------------------------------
# Fixture instruments — frozen dataclass subclasses with a fixed lot size.
# The base Instrument.lot_size() returns 1 (equities); futures/options need
# a concrete lot size.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FixedLotInstrument(Instrument):
    """Test-only subclass that returns a fixed lot size."""
    _lot: int = 1

    def lot_size(self, on: date) -> int:  # type: ignore[override]
        return self._lot


def _eq_instr(symbol: str = "RELIANCE") -> Instrument:
    """Standard NSE equity instrument (lot_size always 1)."""
    return Instrument(
        symbol=symbol,
        security_id="1333",
        segment=Segment.NSE_EQ,
        tick_size=0.05,
        is_derivative=False,
    )


def _fut_instr(symbol: str = "NIFTYFUT", lot: int = 25) -> _FixedLotInstrument:
    """NSE_FNO futures instrument with a given lot size."""
    return _FixedLotInstrument(
        symbol=symbol,
        security_id="13",
        segment=Segment.NSE_FNO,
        tick_size=0.05,
        is_derivative=True,
        underlying="NIFTY",
        _lot=lot,
    )


def _opt_instr(symbol: str = "NIFTY25500CE", lot: int = 75) -> _FixedLotInstrument:
    """NSE_FNO options instrument (symbol ends in CE/PE) with a given lot size."""
    return _FixedLotInstrument(
        symbol=symbol,
        security_id="999",
        segment=Segment.NSE_FNO,
        tick_size=0.05,
        is_derivative=True,
        underlying="NIFTY",
        _lot=lot,
    )


COSTS = DhanCosts()

# ===========================================================================
# Helper: tolerance for floating-point comparisons
# ===========================================================================
ABS_TOL = 1e-9   # all values are rupees; sub-paisa precision is sufficient


# ===========================================================================
# EQUITY INTRADAY — component-level assertions
# ===========================================================================

class TestEquityIntradayBuyComponents:
    """Equity INTRADAY BUY entry, SMALL order (brokerage % applies).

    Setup:
      symbol  RELIANCE, lot_size=1
      side    BUY (entry), SELL (exit)
      qty     100 shares
      entry   ₹500  → entry_val  = 100 × 500 = 50,000
      exit    ₹510  → exit_val   = 100 × 510 = 51,000
      date    2024-01-15

    Derived:
      buy_val  = 50,000  (entry)
      sell_val = 51,000  (exit)

    Hand-computed components:
      brokerage  = min(20, 0.0003×50000) + min(20, 0.0003×51000)
                 = min(20, 15.0) + min(20, 15.3) = 15.0 + 15.3 = 30.3
      stt        = 0.00025 × 51,000 = 12.75
      txn        = 0.000030699 × 101,000 = 3.1005990
      sebi       = 1e-6 × 101,000 = 0.101
      stamp      = 0.00003 × 50,000 = 1.5
      ipft       = 1e-6 × 101,000 = 0.101
      gst_base   = 30.3 + 3.1005990 + 0.101 + 0.101 = 33.6025990
      gst        = 0.18 × 33.6025990 = 6.04846782
    """

    @pytest.fixture(autouse=True)
    def _compute(self):
        self.cb = COSTS.round_trip(
            instrument=_eq_instr(),
            side=Side.BUY,
            quantity=100,
            entry_price=500.0,
            exit_price=510.0,
            on=date(2024, 1, 15),
        )

    def test_brokerage(self):
        assert self.cb.brokerage == pytest.approx(30.3, abs=ABS_TOL)

    def test_stt(self):
        assert self.cb.stt == pytest.approx(12.75, abs=ABS_TOL)

    def test_exchange_txn(self):
        assert self.cb.exchange_txn == pytest.approx(3.1005990, abs=ABS_TOL)

    def test_sebi(self):
        assert self.cb.sebi == pytest.approx(0.101, abs=ABS_TOL)

    def test_stamp(self):
        assert self.cb.stamp == pytest.approx(1.5, abs=ABS_TOL)

    def test_ipft(self):
        assert self.cb.ipft == pytest.approx(0.101, abs=ABS_TOL)

    def test_gst(self):
        # gst_base = brokerage + txn + sebi + ipft (NOT stt, NOT stamp)
        gst_base = 30.3 + 3.1005990 + 0.101 + 0.101
        assert self.cb.gst == pytest.approx(0.18 * gst_base, abs=ABS_TOL)

    def test_total_equals_sum_of_components(self):
        expected = (
            self.cb.brokerage + self.cb.stt + self.cb.exchange_txn
            + self.cb.sebi + self.cb.stamp + self.cb.ipft + self.cb.gst
        )
        assert self.cb.total == pytest.approx(expected, abs=ABS_TOL)


class TestEquityGstBaseIncludesIpft:
    """GST base MUST include IPFT — explicit check that IPFT is not omitted.

    If IPFT were excluded from the GST base, gst would be lower by
    0.18 × 0.101 = 0.01818 on a ₹101,000 turnover trade.  This test
    verifies the correct (inclusive) value.
    """

    def test_gst_includes_ipft(self):
        cb = COSTS.round_trip(
            instrument=_eq_instr(),
            side=Side.BUY,
            quantity=100,
            entry_price=500.0,
            exit_price=510.0,
            on=date(2024, 1, 15),
        )
        turnover = 101_000.0
        ipft = 1e-6 * turnover           # 0.101
        txn  = 0.000030699 * turnover    # 3.1005990
        sebi = 1e-6 * turnover           # 0.101
        brokerage = 30.3
        gst_with_ipft    = 0.18 * (brokerage + txn + sebi + ipft)
        gst_without_ipft = 0.18 * (brokerage + txn + sebi)
        # The model must use the with-IPFT base
        assert cb.gst == pytest.approx(gst_with_ipft, abs=ABS_TOL)
        assert cb.gst != pytest.approx(gst_without_ipft, abs=1e-6)


# ===========================================================================
# EQUITY — brokerage cap behaviour
# ===========================================================================

class TestEquityBrokerageCap:
    """Brokerage cap: min(₹20, 0.03%) per order.

    Cap threshold: 0.0003 × val = 20 ⟹ val = 66,666.67
      - val < 66,666.67 → percentage applies
      - val ≥ 66,666.67 → ₹20 cap applies
    """

    def test_small_order_percentage_applies(self):
        """qty=100, price=500 → leg=50,000 < 66,667 → 0.03% applies."""
        cb = COSTS.round_trip(
            instrument=_eq_instr(),
            side=Side.BUY,
            quantity=100,
            entry_price=500.0,
            exit_price=510.0,
            on=date(2024, 1, 15),
        )
        entry_broker = 0.0003 * 50_000   # 15.0
        exit_broker  = 0.0003 * 51_000   # 15.3
        assert cb.brokerage == pytest.approx(entry_broker + exit_broker, abs=ABS_TOL)
        assert cb.brokerage < 40.0       # both legs below cap

    def test_large_order_cap_applies(self):
        """qty=1000, price=500 → leg=500,000 > 66,667 → ₹20 cap per order."""
        cb = COSTS.round_trip(
            instrument=_eq_instr(),
            side=Side.BUY,
            quantity=1000,
            entry_price=500.0,
            exit_price=510.0,
            on=date(2024, 1, 15),
        )
        # Both legs capped at ₹20 each
        assert cb.brokerage == pytest.approx(40.0, abs=ABS_TOL)

    def test_large_order_cap_components(self):
        """Verify STT/txn/sebi/stamp scale with larger turnover, not brokerage."""
        cb = COSTS.round_trip(
            instrument=_eq_instr(),
            side=Side.BUY,
            quantity=1000,
            entry_price=500.0,
            exit_price=510.0,
            on=date(2024, 1, 15),
        )
        # entry_val = 500,000; exit_val = 510,000
        # buy_val = 500,000 (BUY entry); sell_val = 510,000
        assert cb.stt      == pytest.approx(0.00025 * 510_000, abs=ABS_TOL)   # 127.5
        assert cb.stamp    == pytest.approx(0.00003 * 500_000, abs=ABS_TOL)   # 15.0
        assert cb.exchange_txn == pytest.approx(
            0.000030699 * 1_010_000, abs=ABS_TOL
        )  # 31.005990

    def test_brokerage_at_exact_cap_boundary(self):
        """Leg value exactly at cap boundary (₹66,666) → percentage applies."""
        cb = COSTS.round_trip(
            instrument=_eq_instr(),
            side=Side.BUY,
            quantity=1,
            entry_price=66_666.0,
            exit_price=66_666.0,
            on=date(2024, 1, 15),
        )
        # 0.0003 × 66666 = 19.9998 < 20 → percentage applies on each leg
        assert cb.brokerage == pytest.approx(2 * 0.0003 * 66_666.0, abs=ABS_TOL)

    def test_brokerage_just_above_cap_boundary(self):
        """Leg value just above cap boundary → ₹20 cap per order."""
        cb = COSTS.round_trip(
            instrument=_eq_instr(),
            side=Side.BUY,
            quantity=1,
            entry_price=66_700.0,
            exit_price=66_700.0,
            on=date(2024, 1, 15),
        )
        # 0.0003 × 66700 = 20.01 > 20 → capped at ₹20 per leg
        assert cb.brokerage == pytest.approx(40.0, abs=ABS_TOL)


# ===========================================================================
# EQUITY — SELL entry (short intraday)
# ===========================================================================

class TestEquitySellEntry:
    """SELL entry: STT on the SELL (entry) leg; stamp on the BUY (exit) leg.

    Setup:
      side    SELL (entry at 510), BUY (exit at 500)
      qty     100, date 2024-01-15
      sell_val = 100 × 510 = 51,000  (entry = sell leg)
      buy_val  = 100 × 500 = 50,000  (exit  = buy  leg)
    """

    @pytest.fixture(autouse=True)
    def _compute(self):
        self.cb = COSTS.round_trip(
            instrument=_eq_instr(),
            side=Side.SELL,
            quantity=100,
            entry_price=510.0,
            exit_price=500.0,
            on=date(2024, 1, 15),
        )

    def test_stt_on_sell_entry_leg(self):
        # sell_val = 51,000 (entry is the sell leg for SELL side)
        assert self.cb.stt == pytest.approx(0.00025 * 51_000, abs=ABS_TOL)

    def test_stamp_on_buy_exit_leg(self):
        # buy_val = 50,000 (exit is the buy leg for SELL side)
        assert self.cb.stamp == pytest.approx(0.00003 * 50_000, abs=ABS_TOL)

    def test_txn_both_legs(self):
        turnover = 51_000 + 50_000
        assert self.cb.exchange_txn == pytest.approx(
            0.000030699 * turnover, abs=ABS_TOL
        )

    def test_brokerage_both_legs(self):
        # entry=510*100=51000 → min(20, 15.3)=15.3
        # exit=500*100=50000  → min(20, 15.0)=15.0
        assert self.cb.brokerage == pytest.approx(15.3 + 15.0, abs=ABS_TOL)


# ===========================================================================
# INDEX FUTURES — STT date bands
# ===========================================================================

# Shared futures setup: lot=25, qty=1, entry=22000, exit=22100
# entry_val = 22000 × 1 × 25 = 550,000
# exit_val  = 22100 × 1 × 25 = 552,500
# buy_val   = 550,000 (BUY side)
# sell_val  = 552,500
_FUT_INSTR = _fut_instr(lot=25)
_FUT_ENTRY = 22_000.0
_FUT_EXIT  = 22_100.0
_FUT_QTY   = 1
_FUT_LS    = 25
_FUT_ENTRY_VAL = _FUT_ENTRY * _FUT_QTY * _FUT_LS   # 550,000
_FUT_EXIT_VAL  = _FUT_EXIT  * _FUT_QTY * _FUT_LS   # 552,500
_FUT_BUY_VAL   = _FUT_ENTRY_VAL                     # BUY entry
_FUT_SELL_VAL  = _FUT_EXIT_VAL                      # SELL exit


@pytest.mark.parametrize("trade_date,expected_stt_rate,band_label", [
    (date(2024, 9, 29), 0.000125, "pre-band1"),
    (date(2024, 9, 30), 0.000125, "last day before band1"),
    (date(2024, 10, 1), 0.0002,   "band1 boundary (first day)"),
    (date(2024, 10, 2), 0.0002,   "inside band1"),
    (date(2025, 6, 15), 0.0002,   "mid-band1"),
    (date(2026, 3, 31), 0.0002,   "last day before band2"),
    (date(2026, 4, 1),  0.0005,   "band2 boundary (first day)"),
    (date(2026, 4, 2),  0.0005,   "inside band2"),
    (date(2026, 5, 1),  0.0005,   "post-band2"),
])
def test_futures_stt_band(trade_date, expected_stt_rate, band_label):
    """Futures STT rate is correct for every band and boundary date."""
    cb = COSTS.round_trip(
        instrument=_FUT_INSTR,
        side=Side.BUY,
        quantity=_FUT_QTY,
        entry_price=_FUT_ENTRY,
        exit_price=_FUT_EXIT,
        on=trade_date,
    )
    expected_stt = expected_stt_rate * _FUT_SELL_VAL
    assert cb.stt == pytest.approx(expected_stt, abs=ABS_TOL), (
        f"Futures STT wrong for {band_label} ({trade_date}): "
        f"expected rate {expected_stt_rate}"
    )


class TestFuturesAllComponents:
    """Full component breakdown for futures BUY, pre-2024-10-01.

    date      2024-09-30  (STT rate 0.0125%)
    lot=25, qty=1, entry=22000, exit=22100
    entry_val = 550,000; exit_val = 552,500
    buy_val   = 550,000; sell_val = 552,500

    Hand-computed:
      brokerage  = min(20, 0.0003×550000) + min(20, 0.0003×552500)
                 = 20 + 20 = 40.0  (both legs above ₹66,667 threshold)
      stt        = 0.000125 × 552,500 = 69.0625
      txn        = 0.0000173 × 1,102,500 = 19.073250
      sebi       = 1e-6 × 1,102,500 = 1.1025
      stamp      = 0.00002 × 550,000 = 11.0
      ipft       = 1e-6 × 1,102,500 = 1.1025
      gst_base   = 40.0 + 19.073250 + 1.1025 + 1.1025 = 61.278
      gst        = 0.18 × 61.278 = 11.030040
    """

    @pytest.fixture(autouse=True)
    def _compute(self):
        self.cb = COSTS.round_trip(
            instrument=_FUT_INSTR,
            side=Side.BUY,
            quantity=1,
            entry_price=22_000.0,
            exit_price=22_100.0,
            on=date(2024, 9, 30),
        )

    def test_brokerage(self):
        # Both legs > 66,667 → capped at ₹20 each
        assert self.cb.brokerage == pytest.approx(40.0, abs=ABS_TOL)

    def test_stt(self):
        # 0.0125% on sell_val = 552,500
        assert self.cb.stt == pytest.approx(0.000125 * 552_500, abs=ABS_TOL)

    def test_exchange_txn(self):
        # 0.00173% on turnover = 1,102,500
        assert self.cb.exchange_txn == pytest.approx(
            0.0000173 * 1_102_500, abs=ABS_TOL
        )

    def test_sebi(self):
        assert self.cb.sebi == pytest.approx(1e-6 * 1_102_500, abs=ABS_TOL)

    def test_stamp(self):
        # 0.002% on buy_val = 550,000
        assert self.cb.stamp == pytest.approx(0.00002 * 550_000, abs=ABS_TOL)

    def test_ipft(self):
        assert self.cb.ipft == pytest.approx(1e-6 * 1_102_500, abs=ABS_TOL)

    def test_gst(self):
        gst_base = 40.0 + 0.0000173 * 1_102_500 + 1e-6 * 1_102_500 + 1e-6 * 1_102_500
        assert self.cb.gst == pytest.approx(0.18 * gst_base, abs=ABS_TOL)

    def test_total(self):
        expected = (
            self.cb.brokerage + self.cb.stt + self.cb.exchange_txn
            + self.cb.sebi + self.cb.stamp + self.cb.ipft + self.cb.gst
        )
        assert self.cb.total == pytest.approx(expected, abs=ABS_TOL)


class TestFuturesBand2Boundary:
    """Futures STT on the band-2 boundary date 2024-10-01 (0.02%)."""

    def test_stt_on_boundary_date_2024_10_01(self):
        cb = COSTS.round_trip(
            instrument=_FUT_INSTR,
            side=Side.BUY,
            quantity=1,
            entry_price=22_000.0,
            exit_price=22_100.0,
            on=date(2024, 10, 1),
        )
        # Boundary is inclusive: 0.02% applies FROM 2024-10-01
        assert cb.stt == pytest.approx(0.0002 * 552_500, abs=ABS_TOL)
        # Confirm it is NOT the old 0.0125% rate
        assert cb.stt != pytest.approx(0.000125 * 552_500, abs=1e-6)


class TestFuturesBand3Boundary:
    """Futures STT on the band-3 boundary date 2026-04-01 (0.05%)."""

    def test_stt_on_boundary_date_2026_04_01(self):
        cb = COSTS.round_trip(
            instrument=_FUT_INSTR,
            side=Side.BUY,
            quantity=1,
            entry_price=22_000.0,
            exit_price=22_100.0,
            on=date(2026, 4, 1),
        )
        # Boundary is inclusive: 0.05% applies FROM 2026-04-01
        assert cb.stt == pytest.approx(0.0005 * 552_500, abs=ABS_TOL)
        # Confirm it is NOT the mid-band 0.02% rate
        assert cb.stt != pytest.approx(0.0002 * 552_500, abs=1e-6)


class TestFuturesSellEntry:
    """For a SELL (short) futures entry, STT is on the ENTRY (sell) leg."""

    def test_stt_on_sell_entry_leg(self):
        # SELL 1 NIFTY fut at 22100, exit (buy) at 22000 — profit trade
        cb = COSTS.round_trip(
            instrument=_FUT_INSTR,
            side=Side.SELL,
            quantity=1,
            entry_price=22_100.0,
            exit_price=22_000.0,
            on=date(2025, 6, 15),
        )
        # sell_val = entry (22100 × 1 × 25 = 552,500); STT on sell side
        assert cb.stt == pytest.approx(0.0002 * 552_500, abs=ABS_TOL)

    def test_stamp_on_buy_exit_leg(self):
        cb = COSTS.round_trip(
            instrument=_FUT_INSTR,
            side=Side.SELL,
            quantity=1,
            entry_price=22_100.0,
            exit_price=22_000.0,
            on=date(2025, 6, 15),
        )
        # buy_val = exit (22000 × 1 × 25 = 550,000); stamp on buy side
        assert cb.stamp == pytest.approx(0.00002 * 550_000, abs=ABS_TOL)


# ===========================================================================
# INDEX OPTIONS — STT date bands
# ===========================================================================

# Shared options setup: lot=75, qty=1, entry_premium=150, exit_premium=200
# entry_premium_val = 150 × 1 × 75 = 11,250
# exit_premium_val  = 200 × 1 × 75 = 15,000
# For BUY entry: buy_premium=11,250, sell_premium=15,000 (exit is sell)
_OPT_INSTR       = _opt_instr(lot=75)
_OPT_ENTRY_PREM  = 150.0
_OPT_EXIT_PREM   = 200.0
_OPT_QTY         = 1
_OPT_LS          = 75
_OPT_ENTRY_VAL   = _OPT_ENTRY_PREM * _OPT_QTY * _OPT_LS  # 11,250
_OPT_EXIT_VAL    = _OPT_EXIT_PREM  * _OPT_QTY * _OPT_LS  # 15,000
_OPT_BUY_PREM    = _OPT_ENTRY_VAL                          # BUY entry
_OPT_SELL_PREM   = _OPT_EXIT_VAL                           # SELL exit


@pytest.mark.parametrize("trade_date,expected_stt_rate,band_label", [
    (date(2025, 1, 1),  0.001,  "pre-2026-04-01 (0.10%)"),
    (date(2026, 3, 31), 0.001,  "last day before band boundary (0.10%)"),
    (date(2026, 4, 1),  0.0015, "boundary date inclusive (0.15%)"),
    (date(2026, 4, 2),  0.0015, "post-boundary (0.15%)"),
    (date(2026, 5, 1),  0.0015, "well after boundary (0.15%)"),
])
def test_options_stt_band(trade_date, expected_stt_rate, band_label):
    """Options STT rate (on premium sell-side) is correct per band."""
    cb = COSTS.round_trip(
        instrument=_OPT_INSTR,
        side=Side.BUY,
        quantity=_OPT_QTY,
        entry_price=_OPT_ENTRY_PREM,
        exit_price=_OPT_EXIT_PREM,
        on=trade_date,
    )
    expected_stt = expected_stt_rate * _OPT_SELL_PREM
    assert cb.stt == pytest.approx(expected_stt, abs=ABS_TOL), (
        f"Options STT wrong for {band_label} ({trade_date}): "
        f"expected rate {expected_stt_rate}"
    )


class TestOptionsAllComponents:
    """Full component breakdown for options BUY, pre-2026-04-01.

    date      2025-06-15  (STT rate 0.10% on premium)
    lot=75, qty=1, entry_premium=150, exit_premium=200
    entry_prem_val = 11,250; exit_prem_val = 15,000
    buy_prem = 11,250; sell_prem = 15,000

    Hand-computed:
      brokerage  = ₹20 × 2 = 40.0 (flat, no percentage for options)
      stt        = 0.001 × 15,000 = 15.0  (sell-side on premium)
      txn        = 0.0003503 × 26,250 = 9.195750
      sebi       = 1e-6 × 26,250 = 0.02625
      stamp      = 0.00003 × 11,250 = 0.3375  (buy-side on premium)
      ipft       = 1e-6 × 26,250 = 0.02625
      gst_base   = 40.0 + 9.195750 + 0.02625 + 0.02625 = 49.24800
      gst        = 0.18 × 49.24800 = 8.864640
    """

    @pytest.fixture(autouse=True)
    def _compute(self):
        self.cb = COSTS.round_trip(
            instrument=_OPT_INSTR,
            side=Side.BUY,
            quantity=1,
            entry_price=150.0,
            exit_price=200.0,
            on=date(2025, 6, 15),
        )
        self.turnover = 11_250.0 + 15_000.0   # 26,250

    def test_brokerage_flat(self):
        # Options brokerage is flat ₹20/order — never percentage
        assert self.cb.brokerage == pytest.approx(40.0, abs=ABS_TOL)

    def test_stt_pre_2026(self):
        # 0.10% on sell_premium = 15,000
        assert self.cb.stt == pytest.approx(0.001 * 15_000, abs=ABS_TOL)

    def test_exchange_txn(self):
        # 0.03503% on total premium turnover 26,250
        assert self.cb.exchange_txn == pytest.approx(
            0.0003503 * self.turnover, abs=ABS_TOL
        )

    def test_sebi(self):
        assert self.cb.sebi == pytest.approx(1e-6 * self.turnover, abs=ABS_TOL)

    def test_stamp(self):
        # 0.003% on buy_premium = 11,250
        assert self.cb.stamp == pytest.approx(0.00003 * 11_250, abs=ABS_TOL)

    def test_ipft(self):
        assert self.cb.ipft == pytest.approx(1e-6 * self.turnover, abs=ABS_TOL)

    def test_gst(self):
        gst_base = (
            40.0
            + 0.0003503 * self.turnover
            + 1e-6 * self.turnover
            + 1e-6 * self.turnover
        )
        assert self.cb.gst == pytest.approx(0.18 * gst_base, abs=ABS_TOL)

    def test_total(self):
        expected = (
            self.cb.brokerage + self.cb.stt + self.cb.exchange_txn
            + self.cb.sebi + self.cb.stamp + self.cb.ipft + self.cb.gst
        )
        assert self.cb.total == pytest.approx(expected, abs=ABS_TOL)


class TestOptionsBrokerageAlwaysFlat:
    """Options brokerage must be ₹40 (2 × ₹20) for ANY premium value."""

    @pytest.mark.parametrize("premium", [1.0, 10.0, 100.0, 1_000.0, 50_000.0])
    def test_flat_brokerage_at_any_premium(self, premium):
        cb = COSTS.round_trip(
            instrument=_OPT_INSTR,
            side=Side.BUY,
            quantity=1,
            entry_price=premium,
            exit_price=premium,
            on=date(2025, 1, 1),
        )
        assert cb.brokerage == pytest.approx(40.0, abs=ABS_TOL)


class TestOptionsBand2Boundary:
    """Options STT on boundary date 2026-04-01 must use 0.15% (inclusive)."""

    def test_stt_on_boundary_date_2026_04_01(self):
        cb = COSTS.round_trip(
            instrument=_OPT_INSTR,
            side=Side.BUY,
            quantity=1,
            entry_price=150.0,
            exit_price=200.0,
            on=date(2026, 4, 1),
        )
        # 0.15% rate; sell_premium = 200 × 1 × 75 = 15,000
        assert cb.stt == pytest.approx(0.0015 * 15_000, abs=ABS_TOL)
        # Must NOT be the old 0.10% rate
        assert cb.stt != pytest.approx(0.001 * 15_000, abs=1e-6)


class TestOptionsSellEntry:
    """For a SELL options entry, STT is on the ENTRY (sell) leg premium."""

    def test_stt_on_sell_entry_leg(self):
        # Writer (SELL entry): entry premium is the sell leg
        cb = COSTS.round_trip(
            instrument=_OPT_INSTR,
            side=Side.SELL,
            quantity=1,
            entry_price=200.0,   # sell (write) at 200
            exit_price=150.0,    # buy back at 150 — profit
            on=date(2025, 6, 15),
        )
        # sell_prem = entry × qty × lot = 200 × 1 × 75 = 15,000
        assert cb.stt == pytest.approx(0.001 * 15_000, abs=ABS_TOL)

    def test_stamp_on_buy_exit_leg(self):
        cb = COSTS.round_trip(
            instrument=_OPT_INSTR,
            side=Side.SELL,
            quantity=1,
            entry_price=200.0,
            exit_price=150.0,
            on=date(2025, 6, 15),
        )
        # buy_prem = exit × qty × lot = 150 × 1 × 75 = 11,250
        assert cb.stamp == pytest.approx(0.00003 * 11_250, abs=ABS_TOL)


# ===========================================================================
# CNC raises NotImplementedError
# ===========================================================================

def test_cnc_raises_not_implemented():
    """CNC product is not implemented in v1 — must raise NotImplementedError."""
    with pytest.raises(NotImplementedError):
        COSTS.round_trip(
            instrument=_eq_instr(),
            side=Side.BUY,
            quantity=10,
            entry_price=500.0,
            exit_price=510.0,
            on=date(2025, 1, 15),
            product=ProductType.CNC,
        )


# ===========================================================================
# Instrument type detection — ensure options/futures routing works
# ===========================================================================

def test_ce_symbol_routed_as_option():
    """Symbol ending in 'CE' is treated as option (flat brokerage)."""
    instr = _opt_instr(symbol="NIFTY25500CE", lot=75)
    cb = COSTS.round_trip(
        instrument=instr,
        side=Side.BUY,
        quantity=1,
        entry_price=100.0,
        exit_price=100.0,
        on=date(2025, 1, 1),
    )
    assert cb.brokerage == pytest.approx(40.0, abs=ABS_TOL)


def test_pe_symbol_routed_as_option():
    """Symbol ending in 'PE' is treated as option (flat brokerage)."""
    instr = _opt_instr(symbol="BANKNIFTY48000PE", lot=30)
    cb = COSTS.round_trip(
        instrument=instr,
        side=Side.BUY,
        quantity=1,
        entry_price=100.0,
        exit_price=100.0,
        on=date(2025, 1, 1),
    )
    assert cb.brokerage == pytest.approx(40.0, abs=ABS_TOL)


def test_fut_symbol_not_option_uses_percentage_brokerage():
    """Futures symbol (no CE/PE suffix) uses min(₹20, 0.03%) brokerage."""
    instr = _fut_instr(symbol="NIFTYFUT", lot=75)
    # Small lot value: 22000 × 1 × 1 = 22000 with lot=1 to stay below cap
    small_instr = _fut_instr(symbol="NIFTYFUT", lot=1)
    cb = COSTS.round_trip(
        instrument=small_instr,
        side=Side.BUY,
        quantity=1,
        entry_price=100.0,
        exit_price=100.0,
        on=date(2025, 1, 1),
    )
    # val = 100 × 1 × 1 = 100; 0.0003 × 100 = 0.03 → percentage applies
    assert cb.brokerage == pytest.approx(0.03 + 0.03, abs=ABS_TOL)
    assert cb.brokerage < 40.0   # not flat like options


# ===========================================================================
# Lot size is used in notional calculation
# ===========================================================================

def test_lot_size_scales_all_components():
    """Doubling lot size doubles all cost components (linear in notional)."""
    cb1 = COSTS.round_trip(
        instrument=_fut_instr(lot=25),
        side=Side.BUY,
        quantity=1,
        entry_price=22_000.0,
        exit_price=22_100.0,
        on=date(2025, 1, 15),
    )
    cb2 = COSTS.round_trip(
        instrument=_fut_instr(lot=50),
        side=Side.BUY,
        quantity=1,
        entry_price=22_000.0,
        exit_price=22_100.0,
        on=date(2025, 1, 15),
    )
    # STT, txn, sebi, stamp, ipft all scale 2×; brokerage is already capped at 40
    assert cb2.stt          == pytest.approx(2 * cb1.stt,          abs=ABS_TOL)
    assert cb2.exchange_txn == pytest.approx(2 * cb1.exchange_txn, abs=ABS_TOL)
    assert cb2.sebi         == pytest.approx(2 * cb1.sebi,         abs=ABS_TOL)
    assert cb2.stamp        == pytest.approx(2 * cb1.stamp,        abs=ABS_TOL)
    assert cb2.ipft         == pytest.approx(2 * cb1.ipft,         abs=ABS_TOL)
    # Brokerage is already capped (both lot=25 and lot=50 are above cap threshold)
    assert cb1.brokerage    == pytest.approx(40.0, abs=ABS_TOL)
    assert cb2.brokerage    == pytest.approx(40.0, abs=ABS_TOL)
