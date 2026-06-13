"""VWAP mean-reversion strategy.

ARCHITECTURE §4.2: fade extensions >= k*sigma from session VWAP where
sigma is estimated from a rolling window of PRIOR days' VWAP-deviation
dispersion (never the current day's future bars — unit-tested causal
invariant).  Regime gate: ADX(14) < adx_max.  Target = session VWAP.
Stop beyond the extension extreme.  time_stop_min via OrderIntent.

Look-ahead rule (enforced by design):
    sigma = vwap_deviation_sigma(ctx.prior_sessions(...))
The SessionContext.prior_sessions() protocol guarantees it never exposes
today's bars.  The unit test in test_strategies.py verifies the indicator
itself is stable w.r.t. mutations of today's data.
"""
from __future__ import annotations

import math

from algotrader.core import (
    Bar,
    OrderIntent,
    Position,
    SessionContext,
    Side,
    Strategy,
)
from algotrader.strategies.indicators import (
    adx,
    session_vwap,
    vwap_deviation_sigma,
)

_ADX_PERIOD = 14
_ADX_WARMUP_BARS = 2 * _ADX_PERIOD  # first ADX defined at 2*period-2


class VwapReversion(Strategy):
    """VWAP mean-reversion strategy.

    Args:
        k_sigma: Enter when |close - session_vwap| >= k_sigma * sigma.
            Higher k_sigma = tighter (rarer) entries.
        adx_max: Skip entry if ADX(14) >= adx_max (trending regime).
        time_stop_min: Minutes after entry to exit if target not hit.
            Passed through to OrderIntent.time_stop_min.
    """

    strategy_id: str = "vwap_rev_v1"
    warmup_bars: int = _ADX_WARMUP_BARS + 2   # 30 bars covers ADX warmup
    warmup_days: int = 10

    def __init__(
        self,
        k_sigma: float = 2.0,
        adx_max: float = 25.0,
        time_stop_min: int = 60,
    ) -> None:
        self.k_sigma = k_sigma
        self.adx_max = adx_max
        self.time_stop_min = time_stop_min

    # ------------------------------------------------------------------

    def on_bar(self, ctx: SessionContext, bar: Bar) -> list[OrderIntent]:
        """Emit a BUY or SELL intent when price extends >= k_sigma from VWAP."""
        if not ctx.clock.can_enter:
            return []

        instrument = bar.instrument

        # sigma from PRIOR sessions only (causal contract — ARCHITECTURE §4.2)
        prior = ctx.prior_sessions(instrument, self.warmup_days)
        if len(prior) < 2:
            return []  # insufficient history to estimate sigma
        sigma = vwap_deviation_sigma(prior)
        if sigma <= 0:
            return []

        # ADX regime gate: reject trending conditions
        bars = list(ctx.bars(instrument, max(50, _ADX_WARMUP_BARS + 5)))
        if len(bars) < _ADX_WARMUP_BARS:
            return []

        adx_vals = adx(bars, period=_ADX_PERIOD)
        current_adx = adx_vals[-1]
        if math.isnan(current_adx):
            return []
        if current_adx >= self.adx_max:
            return []

        # Session VWAP up to and including this bar (causal)
        vwap_vals = session_vwap(bars)
        current_vwap = vwap_vals[-1]
        if math.isnan(current_vwap):
            return []

        close = bar.close
        deviation = close - current_vwap
        threshold = self.k_sigma * sigma

        intents: list[OrderIntent] = []

        if deviation <= -threshold:
            # Price is k*sigma BELOW VWAP → BUY (fade the downward extension)
            # Stop: beyond the bar's low (the extreme of the extension)
            tick = instrument.tick_size
            stop_px = bar.low if bar.low < close else close - tick
            # Guard: stop must be strictly below close
            if stop_px >= close:
                stop_px = close - tick
            intents.append(OrderIntent(
                strategy_id=self.strategy_id,
                instrument=instrument,
                side=Side.BUY,
                ref_price=close,
                stop_price=stop_px,
                target_price=current_vwap,
                time_stop_min=self.time_stop_min,
                reason=(
                    f"vwap_buy dev={deviation:.2f} thr={threshold:.2f} "
                    f"vwap={current_vwap:.2f}"
                ),
            ))

        elif deviation >= threshold:
            # Price is k*sigma ABOVE VWAP → SELL (fade the upward extension)
            # Stop: beyond the bar's high (the extreme of the extension)
            tick = instrument.tick_size
            stop_px = bar.high if bar.high > close else close + tick
            if stop_px <= close:
                stop_px = close + tick
            intents.append(OrderIntent(
                strategy_id=self.strategy_id,
                instrument=instrument,
                side=Side.SELL,
                ref_price=close,
                stop_price=stop_px,
                target_price=current_vwap,
                time_stop_min=self.time_stop_min,
                reason=(
                    f"vwap_sell dev={deviation:.2f} thr={threshold:.2f} "
                    f"vwap={current_vwap:.2f}"
                ),
            ))

        return intents

    # ------------------------------------------------------------------

    def manage(
        self,
        ctx: SessionContext,
        bar: Bar,
        position: Position,
    ) -> tuple[float | None, bool]:
        """No active management; stop/target/time_stop handled by engine."""
        return None, False
