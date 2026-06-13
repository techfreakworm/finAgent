"""Date-banded Dhan cost model (ARCHITECTURE §3.3).

Implements core.CostModel for NSE intraday trading via the Dhan broker.
All rates carry dated source comments; each component has a corresponding
unit test in tests/test_costs.py asserting the value per date band.

Rate sources verified against dhan.co/pricing 2026-06-11 unless marked TODO.
"""
from __future__ import annotations

from datetime import date

from algotrader.core import (
    CostBreakdown,
    Instrument,
    ProductType,
    Segment,
    Side,
)

# ---------------------------------------------------------------------------
# STT date-band boundaries (ARCHITECTURE §3.3 + finagent-assessment.md
# "Post-review corrections" — Budget 2024-25 interim and Budget 2026).
# ---------------------------------------------------------------------------
_FUT_STT_BAND2_START = date(2024, 10, 1)   # futures STT: 0.0125% → 0.02%
_FUT_STT_BAND3_START = date(2026, 4, 1)    # futures STT: 0.02%  → 0.05%
_OPT_STT_BAND2_START = date(2026, 4, 1)    # options STT: 0.10%  → 0.15%

# ---------------------------------------------------------------------------
# Common per-rupee-of-turnover rates
# ---------------------------------------------------------------------------
# SEBI charge: ₹10 per crore = 10 / 10,000,000 (dhan.co/pricing 2026-06-11)
_SEBI_PER_RUPEE = 10.0 / 10_000_000.0          # 1e-6

# IPFT (Investor Protection Fund Trust) — NSE charge, both legs, all segments
# 0.0001% = 0.000001 per rupee (finagent-assessment.md "Post-review corrections")
_IPFT_PER_RUPEE = 0.0001 / 100.0               # 1e-6

# GST on (brokerage + exchange txn + SEBI + IPFT)
_GST_RATE = 0.18

# ---------------------------------------------------------------------------
# Equity-specific rates (NSE_EQ, INTRADAY)
# Source: dhan.co/pricing, verified 2026-06-11
# ---------------------------------------------------------------------------
_EQ_STT_RATE       = 0.025 / 100      # 0.025% sell-side
_EQ_TXN_RATE       = 0.0030699 / 100  # 0.0030699% both legs (finagent-assessment)
_EQ_STAMP_RATE     = 0.003 / 100      # 0.003% buy-side
_EQ_BROKERAGE_PCT  = 0.03 / 100       # 0.03% per leg; capped at ₹20/order
_EQ_BROKERAGE_CAP  = 20.0             # ₹20 per executed order

# ---------------------------------------------------------------------------
# Index-futures-specific rates (NSE_FNO, is_derivative, NOT option)
# ---------------------------------------------------------------------------
# Brokerage same as equity: min(₹20, 0.03%) per order
_FUT_BROKERAGE_PCT = 0.03 / 100
_FUT_BROKERAGE_CAP = 20.0

# STT sell-side, date-banded:
#   <2024-10-01   → 0.0125% (ARCHITECTURE §3.3)
#   2024-10-01..  → 0.02%   (Budget 2024-25 interim)
#   2026-04-01..  → 0.05%   (Budget 2026)
_FUT_STT_BAND1 = 0.0125 / 100   # pre-2024-10-01
_FUT_STT_BAND2 = 0.02   / 100   # 2024-10-01 .. 2026-03-31
_FUT_STT_BAND3 = 0.05   / 100   # 2026-04-01+

# NSE txn charge (futures), both legs
# TODO: verify exact rate on dhan.co/pricing for futures segment;
#       0.00173% used per ARCHITECTURE §3.3 cross-reference
_FUT_TXN_RATE  = 0.00173 / 100   # 0.00173% both legs

_FUT_STAMP_RATE = 0.002 / 100    # 0.002% buy-side (dhan.co/pricing 2026-06-11)

# ---------------------------------------------------------------------------
# Index-options-specific rates (NSE_FNO, is_derivative, symbol ends CE/PE)
# All turnover computed on PREMIUM (not notional) per NSE rules
# ---------------------------------------------------------------------------
# Brokerage: flat ₹20 per order (no percentage for options — dhan.co/pricing)
_OPT_BROKERAGE_FLAT = 20.0

# STT sell-side on PREMIUM, date-banded:
#   <2026-04-01  → 0.10% (ARCHITECTURE §3.3)
#   2026-04-01+  → 0.15% (Budget 2026 — finagent-assessment.md)
_OPT_STT_BAND1 = 0.10 / 100    # pre-2026-04-01
_OPT_STT_BAND2 = 0.15 / 100    # 2026-04-01+

# NSE txn charge (options), on premium, both legs
# TODO: verify exact rate on dhan.co/pricing for options segment;
#       0.03503% used per ARCHITECTURE §3.3 cross-reference
_OPT_TXN_RATE = 0.03503 / 100   # 0.03503% both legs on premium

_OPT_STAMP_RATE = 0.003 / 100   # 0.003% buy-side on premium (dhan.co/pricing)


def _is_option(instrument: Instrument) -> bool:
    """Return True if the instrument is an index option.

    Options use NSE standard naming: symbol ends with 'CE' or 'PE'
    (e.g. 'NIFTY25500CE', 'BANKNIFTY25JAN30000PE').
    """
    sym = instrument.symbol.upper()
    return sym.endswith("CE") or sym.endswith("PE")


def _fut_stt_rate(on: date) -> float:
    """Date-banded futures STT rate (sell-side, on notional)."""
    if on >= _FUT_STT_BAND3_START:
        return _FUT_STT_BAND3
    if on >= _FUT_STT_BAND2_START:
        return _FUT_STT_BAND2
    return _FUT_STT_BAND1


def _opt_stt_rate(on: date) -> float:
    """Date-banded options STT rate (sell-side, on premium)."""
    if on >= _OPT_STT_BAND2_START:
        return _OPT_STT_BAND2
    return _OPT_STT_BAND1


class DhanCosts:
    """Date-banded Dhan cost model for NSE intraday trading.

    Implements core.CostModel.  Computes a full round-trip CostBreakdown
    (entry fill + exit fill) for three instrument types:

    - Equity INTRADAY (Segment.NSE_EQ)
    - Index futures   (Segment.NSE_FNO, is_derivative=True, symbol !CE/PE)
    - Index options   (Segment.NSE_FNO, is_derivative=True, symbol ends CE/PE)

    STT rates for futures and options are date-banded; equity STT is fixed.
    See ARCHITECTURE §3.3 and finagent-assessment.md "Post-review corrections".

    All charges are in INR (₹).
    """

    def round_trip(
        self,
        instrument: Instrument,
        side: Side,
        quantity: int,
        entry_price: float,
        exit_price: float,
        on: date,
        product: ProductType = ProductType.INTRADAY,
    ) -> CostBreakdown:
        """Compute all statutory and brokerage charges for one round trip.

        Parameters
        ----------
        instrument:
            The traded instrument.  lot_size(on) is called to compute notional.
        side:
            Entry direction (BUY = long, SELL = short).  Determines which leg
            pays STT (sell-side) and which pays stamp duty (buy-side).
        quantity:
            Number of shares (equity) or contracts (F&O), already lot-rounded.
        entry_price, exit_price:
            Actual fill prices (post-slippage).
        on:
            Trade date — used for date-banded STT rates and lot size lookup.
        product:
            Only INTRADAY is implemented in v1; CNC raises NotImplementedError.
        """
        if product is not ProductType.INTRADAY:
            raise NotImplementedError(
                "CostModel: only INTRADAY product is implemented in v1; "
                "CNC delivery cost rates require a separate implementation."
            )

        seg = instrument.segment
        if seg is Segment.NSE_EQ or not instrument.is_derivative:
            return self._equity_intraday(
                instrument, side, quantity, entry_price, exit_price, on
            )
        # NSE_FNO derivative — distinguish futures vs options by symbol
        if _is_option(instrument):
            return self._index_options(
                instrument, side, quantity, entry_price, exit_price, on
            )
        return self._index_futures(
            instrument, side, quantity, entry_price, exit_price, on
        )

    # ------------------------------------------------------------------
    # Private per-segment implementations
    # ------------------------------------------------------------------

    def _equity_intraday(
        self,
        instrument: Instrument,
        side: Side,
        quantity: int,
        entry_price: float,
        exit_price: float,
        on: date,
    ) -> CostBreakdown:
        """Equity INTRADAY cost breakdown.

        Rates (dhan.co/pricing, verified 2026-06-11):
          brokerage  min(₹20, 0.03%) per executed order × 2 orders
          STT        0.025% sell-side on turnover
          txn        0.0030699% both legs on turnover
          SEBI       ₹10/crore both legs
          stamp      0.003% buy-side on turnover
          IPFT       0.0001% both legs on turnover (NSE charge)
          GST        18% on (brokerage + txn + SEBI + IPFT)
        """
        # lot_size for equity is always 1 (core.py default)
        ls = instrument.lot_size(on)
        entry_val = entry_price * quantity * ls
        exit_val  = exit_price  * quantity * ls

        if side is Side.BUY:
            buy_val, sell_val = entry_val, exit_val
        else:
            sell_val, buy_val = entry_val, exit_val

        # Brokerage: per-order, based on that order's leg value
        brokerage = (
            min(_EQ_BROKERAGE_CAP, _EQ_BROKERAGE_PCT * entry_val)
            + min(_EQ_BROKERAGE_CAP, _EQ_BROKERAGE_PCT * exit_val)
        )

        stt           = _EQ_STT_RATE * sell_val
        exchange_txn  = _EQ_TXN_RATE * (entry_val + exit_val)
        sebi          = _SEBI_PER_RUPEE * (entry_val + exit_val)
        stamp         = _EQ_STAMP_RATE * buy_val
        ipft          = _IPFT_PER_RUPEE * (entry_val + exit_val)
        gst           = _GST_RATE * (brokerage + exchange_txn + sebi + ipft)

        return CostBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_txn=exchange_txn,
            sebi=sebi,
            stamp=stamp,
            ipft=ipft,
            gst=gst,
        )

    def _index_futures(
        self,
        instrument: Instrument,
        side: Side,
        quantity: int,
        entry_price: float,
        exit_price: float,
        on: date,
    ) -> CostBreakdown:
        """Index futures INTRADAY cost breakdown.

        Rates (dhan.co/pricing + ARCHITECTURE §3.3, verified 2026-06-11):
          brokerage  min(₹20, 0.03%) per executed order × 2 (same as equity)
          STT        sell-side on notional — date-banded:
                       <2024-10-01   0.0125%
                       2024-10-01..  0.02%
                       2026-04-01..  0.05%
          txn        0.00173% both legs on notional  [TODO: re-verify on dhan.co]
          SEBI       ₹10/crore both legs
          stamp      0.002% buy-side on notional
          IPFT       0.0001% both legs on notional
          GST        18% on (brokerage + txn + SEBI + IPFT)
        """
        ls = instrument.lot_size(on)
        entry_val = entry_price * quantity * ls
        exit_val  = exit_price  * quantity * ls

        if side is Side.BUY:
            buy_val, sell_val = entry_val, exit_val
        else:
            sell_val, buy_val = entry_val, exit_val

        brokerage = (
            min(_FUT_BROKERAGE_CAP, _FUT_BROKERAGE_PCT * entry_val)
            + min(_FUT_BROKERAGE_CAP, _FUT_BROKERAGE_PCT * exit_val)
        )

        stt          = _fut_stt_rate(on) * sell_val
        exchange_txn = _FUT_TXN_RATE * (entry_val + exit_val)
        sebi         = _SEBI_PER_RUPEE * (entry_val + exit_val)
        stamp        = _FUT_STAMP_RATE * buy_val
        ipft         = _IPFT_PER_RUPEE * (entry_val + exit_val)
        gst          = _GST_RATE * (brokerage + exchange_txn + sebi + ipft)

        return CostBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_txn=exchange_txn,
            sebi=sebi,
            stamp=stamp,
            ipft=ipft,
            gst=gst,
        )

    def _index_options(
        self,
        instrument: Instrument,
        side: Side,
        quantity: int,
        entry_price: float,
        exit_price: float,
        on: date,
    ) -> CostBreakdown:
        """Index options INTRADAY cost breakdown.

        All turnover is computed on PREMIUM (price × qty × lot_size),
        not notional, per NSE options charge rules.

        Rates (dhan.co/pricing + ARCHITECTURE §3.3, verified 2026-06-11):
          brokerage  flat ₹20 per executed order × 2 = ₹40 (no percentage)
          STT        sell-side on premium — date-banded:
                       <2026-04-01   0.10%
                       2026-04-01..  0.15%
          txn        0.03503% both legs on premium  [TODO: re-verify on dhan.co]
          SEBI       ₹10/crore on premium, both legs
          stamp      0.003% buy-side on premium
          IPFT       0.0001% both legs on premium
          GST        18% on (brokerage + txn + SEBI + IPFT)
        """
        ls = instrument.lot_size(on)
        entry_premium = entry_price * quantity * ls
        exit_premium  = exit_price  * quantity * ls

        if side is Side.BUY:
            buy_premium, sell_premium = entry_premium, exit_premium
        else:
            sell_premium, buy_premium = entry_premium, exit_premium

        # Flat brokerage for options: ₹20/order regardless of premium value
        brokerage = _OPT_BROKERAGE_FLAT * 2

        stt          = _opt_stt_rate(on) * sell_premium
        exchange_txn = _OPT_TXN_RATE * (entry_premium + exit_premium)
        sebi         = _SEBI_PER_RUPEE * (entry_premium + exit_premium)
        stamp        = _OPT_STAMP_RATE * buy_premium
        ipft         = _IPFT_PER_RUPEE * (entry_premium + exit_premium)
        gst          = _GST_RATE * (brokerage + exchange_txn + sebi + ipft)

        return CostBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_txn=exchange_txn,
            sebi=sebi,
            stamp=stamp,
            ipft=ipft,
            gst=gst,
        )
