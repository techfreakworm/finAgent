"""Trend-continuation strategy.

ARCHITECTURE §4.3: EMA(9/21) alignment + ADX floor + pullback-to-EMA9
entry; ATR trail via manage(); max ONE re-entry per direction per day.

Designed for 5-min bars (ARCHITECTURE §4.3) but indicator logic is
interval-agnostic; interval is encoded in Bar.interval_min.

Entry criteria (long):
    ema9 > ema21        — bull alignment
    adx >= adx_floor    — trend regime confirmed
    |close - ema9| / ema9 <= pullback_pct  — price pulled back to EMA9

Stop: initial stop = close − atr_trail_mult * ATR(14), giving risk budget.
manage(): trail stop upward on each subsequent bar (BUY) or downward (SELL).

Per-session state resets on date change (ARCHITECTURE §4 contract:
"stateless across sessions").
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

from algotrader.core import (
    Bar,
    OrderIntent,
    Position,
    SessionContext,
    Side,
    Strategy,
)
from algotrader.strategies.indicators import adx, atr, ema

_EMA_FAST = 9
_EMA_SLOW = 21
_ADX_PERIOD = 14
_ATR_PERIOD = 14

# ADX warmup: 2*period - 2; add a few bars of buffer
_MIN_BARS = 2 * _ADX_PERIOD + _EMA_SLOW


class TrendContinuation(Strategy):
    """EMA alignment + ADX + pullback-to-EMA9 trend-continuation strategy.

    Args:
        adx_floor: Minimum ADX(14) required to confirm a trend regime.
        pullback_pct: Entry fires when |close - ema9| / ema9 <= pullback_pct.
            e.g. 0.002 means within 0.2% of EMA9.
        atr_trail_mult: ATR(14) multiplier for both the initial stop distance
            and the trailing stop step in manage().
    """

    strategy_id: str = "trend_cont_v1"
    warmup_bars: int = _MIN_BARS + 5
    warmup_days: int = 5

    def __init__(
        self,
        adx_floor: float = 20.0,
        pullback_pct: float = 0.002,
        atr_trail_mult: float = 2.0,
    ) -> None:
        self.adx_floor = adx_floor
        self.pullback_pct = pullback_pct
        self.atr_trail_mult = atr_trail_mult

        # per-session state (reset when date changes)
        self._session_date: Optional[date] = None
        self._long_count: int = 0
        self._short_count: int = 0

    # ------------------------------------------------------------------

    def _reset_session(self, session_date: date) -> None:
        self._session_date = session_date
        self._long_count = 0
        self._short_count = 0

    def on_bar(self, ctx: SessionContext, bar: Bar) -> list[OrderIntent]:
        """Emit an entry intent on EMA-alignment + ADX + pullback."""
        session_date = bar.ts_open.date()
        if session_date != self._session_date:
            self._reset_session(session_date)

        if not ctx.clock.can_enter:
            return []

        instrument = bar.instrument
        bars = list(ctx.bars(instrument, _MIN_BARS + 10))

        if len(bars) < _MIN_BARS:
            return []

        # Indicator values (all causal — computed on bars[0..t])
        ema9_vals = ema(bars, period=_EMA_FAST)
        ema21_vals = ema(bars, period=_EMA_SLOW)
        adx_vals = adx(bars, period=_ADX_PERIOD)
        atr_vals = atr(bars, period=_ATR_PERIOD)

        e9 = ema9_vals[-1]
        e21 = ema21_vals[-1]
        adx_val = adx_vals[-1]
        atr_val = atr_vals[-1]

        # Require all indicators to be defined (past warmup)
        if any(math.isnan(v) for v in (e9, e21, adx_val, atr_val)):
            return []
        if atr_val <= 0:
            return []

        close = bar.close
        tick = instrument.tick_size

        # pullback proximity to EMA9
        near_ema9 = abs(close - e9) / e9 <= self.pullback_pct

        if not near_ema9:
            return []

        intents: list[OrderIntent] = []
        trail_dist = self.atr_trail_mult * atr_val

        # --- Long: bull alignment, ADX confirmed, pullback to EMA9 ---
        if self._long_count == 0 and e9 > e21 and adx_val >= self.adx_floor:
            stop_px = close - trail_dist
            # Ensure stop is strictly below close
            if stop_px >= close:
                stop_px = close - tick
            intents.append(OrderIntent(
                strategy_id=self.strategy_id,
                instrument=instrument,
                side=Side.BUY,
                ref_price=close,
                stop_price=stop_px,
                trail_atr_mult=self.atr_trail_mult,
                reason=(
                    f"trend_long e9={e9:.1f} e21={e21:.1f} "
                    f"adx={adx_val:.1f} atr={atr_val:.2f}"
                ),
            ))
            self._long_count += 1

        # --- Short: bear alignment, ADX confirmed, pullback to EMA9 ---
        if self._short_count == 0 and e9 < e21 and adx_val >= self.adx_floor:
            stop_px = close + trail_dist
            if stop_px <= close:
                stop_px = close + tick
            intents.append(OrderIntent(
                strategy_id=self.strategy_id,
                instrument=instrument,
                side=Side.SELL,
                ref_price=close,
                stop_price=stop_px,
                trail_atr_mult=self.atr_trail_mult,
                reason=(
                    f"trend_short e9={e9:.1f} e21={e21:.1f} "
                    f"adx={adx_val:.1f} atr={atr_val:.2f}"
                ),
            ))
            self._short_count += 1

        return intents

    # ------------------------------------------------------------------

    def manage(
        self,
        ctx: SessionContext,
        bar: Bar,
        position: Position,
    ) -> tuple[float | None, bool]:
        """Trail stop by ATR(14) * atr_trail_mult."""
        bars = list(ctx.bars(position.instrument, _ATR_PERIOD + 5))
        if len(bars) < _ATR_PERIOD:
            return None, False

        atr_vals = atr(bars, period=_ATR_PERIOD)
        current_atr = atr_vals[-1]
        if math.isnan(current_atr) or current_atr <= 0:
            return None, False

        trail_dist = self.atr_trail_mult * current_atr

        if position.side is Side.BUY:
            candidate = bar.close - trail_dist
            if candidate > position.stop_price:
                return candidate, False
        else:  # SELL
            candidate = bar.close + trail_dist
            if candidate < position.stop_price:
                return candidate, False

        return None, False
