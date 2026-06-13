"""Opening Range Breakout strategy.

ARCHITECTURE §4.1: range from first N minutes (15 or 30); enter on close
beyond range high/low with volume confirmation defined against prior-days'
same-time-of-day volume profile; ONE breakout per direction per day; honors
ctx.clock.can_enter; optional ATR trail in manage().

No look-ahead: all indicators use bars <= t (opening_range is locked once
formed; vol_profile uses prior sessions only; ATR uses bars strictly ≤ t).
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
from algotrader.strategies.indicators import (
    atr,
    opening_range,
    time_of_day_volume_profile,
)

_STOP_MODES = ("range_mid", "range_opposite")


class OpeningRangeBreakout(Strategy):
    """Opening Range Breakout.

    Args:
        range_minutes: Width of the opening range (15 or 30 bars of 1-min data).
        vol_confirm_mult: Bar volume must be >= this multiple of the
            prior-days' same-time-of-day average to confirm a breakout.
        target_r_mult: Target distance = (entry - stop) * target_r_mult.
        stop: 'range_mid' places stop at range midpoint; 'range_opposite'
            places stop at the opposite range edge.
        atr_trail_mult: If set, manage() trails the stop by this multiple
            of ATR(14).  None disables trailing.
    """

    strategy_id: str = "orb_v1"
    warmup_bars: int = 30
    warmup_days: int = 5

    def __init__(
        self,
        range_minutes: int = 15,
        vol_confirm_mult: float = 1.5,
        target_r_mult: float = 2.0,
        stop: str = "range_mid",
        atr_trail_mult: Optional[float] = None,
    ) -> None:
        if range_minutes not in (15, 30):
            raise ValueError(f"range_minutes must be 15 or 30, got {range_minutes}")
        if stop not in _STOP_MODES:
            raise ValueError(f"stop must be one of {_STOP_MODES}, got {stop!r}")
        self.range_minutes = range_minutes
        self.vol_confirm_mult = vol_confirm_mult
        self.target_r_mult = target_r_mult
        self.stop_mode = stop
        self.atr_trail_mult = atr_trail_mult

        # per-session state (reset when session date changes)
        self._session_date: Optional[date] = None
        self._long_done: bool = False
        self._short_done: bool = False

    # ------------------------------------------------------------------

    def _reset_session(self, session_date: date) -> None:
        self._session_date = session_date
        self._long_done = False
        self._short_done = False

    def _bar_offset(self, bar: Bar) -> int:
        """Minute offset from session open (09:15 = 0)."""
        t = bar.ts_open
        return (t.hour - 9) * 60 + (t.minute - 15)

    def on_bar(self, ctx: SessionContext, bar: Bar) -> list[OrderIntent]:
        """Emit at most one OrderIntent per direction per session."""
        session_date = bar.ts_open.date()
        if session_date != self._session_date:
            self._reset_session(session_date)

        # both directions already traded
        if self._long_done and self._short_done:
            return []

        if not ctx.clock.can_enter:
            return []

        instrument = bar.instrument
        # All bars in today's session up to and including this bar
        today_bars = list(ctx.bars(instrument, 400))

        # Opening range spans range_minutes of WALL-CLOCK time; the signal bar
        # must be AFTER the range-forming window (strictly beyond). Bar count
        # is interval-aware so 5-min sweeps and 1-min validation agree.
        n_range_bars = self.range_minutes // bar.interval_min
        if n_range_bars <= 0 or len(today_bars) <= n_range_bars:
            return []

        rng = opening_range(today_bars, self.range_minutes)
        if rng is None:
            return []
        range_high, range_low = rng

        # Volume confirmation via prior-days' same-time-of-day profile
        prior = ctx.prior_sessions(instrument, self.warmup_days)
        if not prior:
            return []  # no prior data — cannot confirm volume
        vol_profile = time_of_day_volume_profile(prior)
        offset = self._bar_offset(bar)
        profile_vol = vol_profile.get(offset, 0.0)

        vol_ok = (profile_vol <= 0) or (bar.volume >= self.vol_confirm_mult * profile_vol)
        if not vol_ok:
            return []

        intents: list[OrderIntent] = []
        close = bar.close

        # --- Long breakout ---
        if not self._long_done and close > range_high:
            stop_px = self._calc_stop(range_high, range_low, Side.BUY, close)
            if stop_px < close:  # safety guard (should always hold)
                risk = close - stop_px
                target_px = close + risk * self.target_r_mult
                intents.append(OrderIntent(
                    strategy_id=self.strategy_id,
                    instrument=instrument,
                    side=Side.BUY,
                    ref_price=close,
                    stop_price=stop_px,
                    target_price=target_px,
                    trail_atr_mult=self.atr_trail_mult,
                    reason=f"orb_long range={range_low:.1f}-{range_high:.1f}",
                ))
                self._long_done = True

        # --- Short breakout ---
        if not self._short_done and close < range_low:
            stop_px = self._calc_stop(range_high, range_low, Side.SELL, close)
            if stop_px > close:  # safety guard
                risk = stop_px - close
                target_px = close - risk * self.target_r_mult
                intents.append(OrderIntent(
                    strategy_id=self.strategy_id,
                    instrument=instrument,
                    side=Side.SELL,
                    ref_price=close,
                    stop_price=stop_px,
                    target_price=target_px,
                    trail_atr_mult=self.atr_trail_mult,
                    reason=f"orb_short range={range_low:.1f}-{range_high:.1f}",
                ))
                self._short_done = True

        return intents

    def _calc_stop(
        self,
        range_high: float,
        range_low: float,
        side: Side,
        close: float,
    ) -> float:
        """Compute stop price for a given breakout direction."""
        range_mid = (range_high + range_low) / 2.0
        if self.stop_mode == "range_mid":
            return range_mid
        # range_opposite
        if side is Side.BUY:
            return range_low
        return range_high

    # ------------------------------------------------------------------

    def manage(
        self,
        ctx: SessionContext,
        bar: Bar,
        position: Position,
    ) -> tuple[float | None, bool]:
        """Trail stop by ATR(14) if atr_trail_mult is configured."""
        if self.atr_trail_mult is None:
            return None, False

        bars = list(ctx.bars(position.instrument, 20))
        if len(bars) < 14:
            return None, False

        atr_vals = atr(bars, period=14)
        current_atr = atr_vals[-1]
        if math.isnan(current_atr) or current_atr <= 0:
            return None, False

        trail_dist = self.atr_trail_mult * current_atr
        if position.side is Side.BUY:
            candidate = bar.close - trail_dist
            if candidate > position.stop_price:
                return candidate, False
        else:
            candidate = bar.close + trail_dist
            if candidate < position.stop_price:
                return candidate, False

        return None, False
