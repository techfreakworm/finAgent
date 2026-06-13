"""ORB v2 — selectivity-first Opening Range Breakout (Gen-2).

Gen-1 evidence (reports/sweeps/gen1): ORB v1 had the least-bad PF (0.85–0.89
gross of friction) across 13k+ trades and the only WF-positive cells
(TATAMOTORS/TCS/LT) — a tiny edge ground down by trade frequency. v2 attacks
frequency and exit quality, not entry cleverness:

  SELECTIVITY (fewer, better trades):
  - compression filter: opening range width ≤ range_max_atr_frac × prior
    20-day average daily range (narrow range ⇒ energy stored; breakouts from
    wide ranges are exhaustion-prone)
  - gap alignment: break direction must agree with the overnight gap (or gap
    must be negligible) — fading the gap intraday showed nothing in Gen-1
  - entry window: first break before entry_cutoff only (11:30 default);
    late breaks are chop
  - ONE trade per session total (v1 allowed one per direction)
  - volume confirmation kept (1.5× same-time-of-day profile)

  EXITS (let trend days pay):
  - no fixed target: stop → breakeven at +1R, then ATR(14)×trail_atr_mult
    trailing stop via manage(); square-off rules unchanged (engine-owned)
"""
from __future__ import annotations

from datetime import date, time
from typing import Optional

from algotrader.core import (
    Bar, OrderIntent, Position, SessionContext, Side, Strategy,
)
from algotrader.strategies.indicators import (
    atr, opening_range, time_of_day_volume_profile,
)


class OrbV2(Strategy):
    strategy_id = "orb_v2"
    warmup_bars = 0
    warmup_days = 20          # needs 20 prior sessions for the range filter

    def __init__(
        self,
        range_minutes: int = 30,
        range_max_atr_frac: float = 0.35,
        gap_align: bool = True,
        gap_negligible_pct: float = 0.001,
        vol_confirm_mult: float = 1.5,
        trail_atr_mult: float = 2.5,
        be_at_r: float = 1.0,
        entry_cutoff: time = time(11, 30),
        breadth_min_abs: float = 0.0,   # >0 enables 50-stock breadth alignment gate
    ) -> None:
        if range_minutes not in (15, 30):
            raise ValueError(f"range_minutes must be 15 or 30, got {range_minutes}")
        self.range_minutes = range_minutes
        self.range_max_atr_frac = range_max_atr_frac
        self.gap_align = gap_align
        self.gap_negligible_pct = gap_negligible_pct
        self.vol_confirm_mult = vol_confirm_mult
        self.trail_atr_mult = trail_atr_mult
        self.be_at_r = be_at_r
        self.entry_cutoff = entry_cutoff
        self.breadth_min_abs = breadth_min_abs

        # per-session state
        self._session_date: Optional[date] = None
        self._traded: bool = False
        # per-position management state: pid -> (initial_risk, be_done)
        self._pos_state: dict[str, tuple[float, bool]] = {}

    # ------------------------------------------------------------------

    def _reset_session(self, session_date: date) -> None:
        self._session_date = session_date
        self._traded = False
        self._pos_state.clear()

    @staticmethod
    def _bar_offset(bar: Bar) -> int:
        t = bar.ts_open
        return (t.hour - 9) * 60 + (t.minute - 15)

    # ------------------------------------------------------------------

    def on_bar(self, ctx: SessionContext, bar: Bar) -> list[OrderIntent]:
        session_date = bar.ts_open.date()
        if session_date != self._session_date:
            self._reset_session(session_date)

        if self._traded or not ctx.clock.can_enter:
            return []
        if bar.ts_open.timetz().replace(tzinfo=None) >= self.entry_cutoff:
            return []

        instrument = bar.instrument
        today_bars = list(ctx.bars(instrument, 400))
        n_range_bars = self.range_minutes // bar.interval_min
        if n_range_bars <= 0 or len(today_bars) <= n_range_bars:
            return []

        rng = opening_range(today_bars, self.range_minutes)
        if rng is None:
            return []
        range_high, range_low = rng
        range_width = range_high - range_low

        prior = ctx.prior_sessions(instrument, self.warmup_days)
        if len(prior) < self.warmup_days:
            return []

        # --- compression filter: narrow opening range vs avg daily range ---
        day_ranges = []
        for bars_ in prior.values():
            hi = max(b.high for b in bars_)
            lo = min(b.low for b in bars_)
            day_ranges.append(hi - lo)
        avg_day_range = sum(day_ranges) / len(day_ranges)
        if avg_day_range <= 0 or range_width > self.range_max_atr_frac * avg_day_range:
            return []

        # --- overnight gap (today's first open vs yesterday's last close) ---
        latest_prior_day = max(prior)
        prev_close = prior[latest_prior_day][-1].close
        today_open = today_bars[0].open
        gap = today_open - prev_close
        gap_small = abs(gap) <= self.gap_negligible_pct * prev_close

        # --- volume confirmation ---
        vol_profile = time_of_day_volume_profile(prior)
        profile_vol = vol_profile.get(self._bar_offset(bar), 0.0)
        if profile_vol > 0 and bar.volume < self.vol_confirm_mult * profile_vol:
            return []

        close = bar.close
        intent: Optional[OrderIntent] = None

        # optional gate: market internals must agree with break direction
        breadth_long_ok = breadth_short_ok = True
        if self.breadth_min_abs > 0:
            from algotrader.strategies.breadth_rider import breadth_at
            row = breadth_at(int(bar.ts_close.timestamp()))
            if row is None:
                return []
            _, _, net_b = row
            breadth_long_ok = net_b >= self.breadth_min_abs
            breadth_short_ok = net_b <= -self.breadth_min_abs

        if breadth_long_ok and close > range_high and (not self.gap_align or gap_small or gap > 0):
            stop_px = range_low + range_width / 2
            if stop_px < close:
                intent = OrderIntent(
                    strategy_id=self.strategy_id, instrument=instrument,
                    side=Side.BUY, ref_price=close, stop_price=stop_px,
                    target_price=None,                     # trail, not target
                    trail_atr_mult=self.trail_atr_mult,
                    reason=f"orbv2_long w={range_width:.1f} adr={avg_day_range:.1f} gap={gap:+.1f}",
                )
        elif breadth_short_ok and close < range_low and (not self.gap_align or gap_small or gap < 0):
            stop_px = range_low + range_width / 2
            if stop_px > close:
                intent = OrderIntent(
                    strategy_id=self.strategy_id, instrument=instrument,
                    side=Side.SELL, ref_price=close, stop_price=stop_px,
                    target_price=None,
                    trail_atr_mult=self.trail_atr_mult,
                    reason=f"orbv2_short w={range_width:.1f} adr={avg_day_range:.1f} gap={gap:+.1f}",
                )

        if intent is None:
            return []
        self._traded = True          # one shot per session, hit or miss
        return [intent]

    # ------------------------------------------------------------------

    def manage(self, ctx: SessionContext, bar: Bar,
               position: Position) -> tuple[float | None, bool]:
        """Breakeven at +be_at_r, then ATR trail. Never loosens the stop."""
        if bar.instrument is not position.instrument and \
                bar.instrument.symbol != position.instrument.symbol:
            return None, False

        pid = position.position_id
        if pid not in self._pos_state:
            init_risk = abs(position.entry_price - position.stop_price)
            self._pos_state[pid] = (init_risk, False)
        init_risk, be_done = self._pos_state[pid]
        if init_risk <= 0:
            return None, False

        sgn = 1.0 if position.side is Side.BUY else -1.0
        progress_r = sgn * (bar.close - position.entry_price) / init_risk

        new_stop: float | None = None
        if not be_done and progress_r >= self.be_at_r:
            new_stop = position.entry_price
            self._pos_state[pid] = (init_risk, True)
            be_done = True

        if be_done:
            today_bars = list(ctx.bars(position.instrument, 400))
            series = atr(today_bars, period=14) if len(today_bars) >= 14 else []
            a = series[-1] if series else 0.0
            if a and a == a and a > 0:  # defined, non-NaN, positive
                trail = bar.close - sgn * self.trail_atr_mult * a
                candidate = max(new_stop or position.stop_price, trail) \
                    if position.side is Side.BUY else \
                    min(new_stop or position.stop_price, trail)
                # only ever tighten
                if (position.side is Side.BUY and candidate > position.stop_price) or \
                   (position.side is Side.SELL and candidate < position.stop_price):
                    new_stop = candidate

        return new_stop, False
