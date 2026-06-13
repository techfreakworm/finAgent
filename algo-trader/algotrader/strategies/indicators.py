"""Causal incremental indicators for intraday strategies.

All functions are PURE: given a prefix of bars bars[0..i], the value
at index i is determined solely by those bars (no look-ahead).

Causality invariant (ARCHITECTURE §3.8, §4.2):
    indicator(bars)[i] == indicator(bars[:k] + anything)[i]
    for every i <= k.

This is enforced by design: every indicator iterates forward through
the bar sequence and each step depends only on previously accumulated
state — never on future elements.

NaN sentinel: float('nan') is returned for indices inside the warmup
window where the indicator is not yet defined.

Usage pattern (from a strategy's on_bar):
    vals = ema(ctx.bars(instrument, n=50), period=9)
    current_ema = vals[-1]   # the bar just closed
"""
from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Mapping, Sequence

from algotrader.core import Bar

_NAN = float("nan")


# ---------------------------------------------------------------------------
# EMA — Exponential Moving Average
# ---------------------------------------------------------------------------

def ema(bars: Sequence[Bar], period: int) -> list[float]:
    """Causal EMA over bar closes.

    First value defined at index period-1 (seeded with SMA of first
    `period` bars); subsequent values use alpha = 2/(period+1).

    Returns:
        List of length len(bars); NaN for indices < period-1.
    """
    n = len(bars)
    result: list[float] = [_NAN] * n
    if n == 0 or period <= 0:
        return result

    if period == 1:
        for i in range(n):
            result[i] = bars[i].close
        return result

    if n < period:
        return result

    alpha = 2.0 / (period + 1)

    # Seed: simple average of first `period` closes
    seed_sum = sum(bars[j].close for j in range(period))
    result[period - 1] = seed_sum / period

    for i in range(period, n):
        result[i] = alpha * bars[i].close + (1.0 - alpha) * result[i - 1]

    return result


# ---------------------------------------------------------------------------
# ATR — Average True Range (Wilder's smoothing)
# ---------------------------------------------------------------------------

def atr(bars: Sequence[Bar], period: int) -> list[float]:
    """Causal ATR using Wilder's smoothing.

    True Range[i] = max(high-low, |high-prev_close|, |low-prev_close|).
    For bar 0 (no previous close): TR = high - low.

    First ATR defined at index period-1 (SMA of first period TRs);
    subsequent values use Wilder's RMA: atr[i] = (atr[i-1]*(p-1)+tr[i])/p.

    Returns:
        List of length len(bars); NaN for indices < period-1.
    """
    n = len(bars)
    result: list[float] = [_NAN] * n
    if n == 0 or period <= 0:
        return result

    # --- True Range pass (causal by construction) ---
    tr: list[float] = [0.0] * n
    tr[0] = bars[0].high - bars[0].low
    for i in range(1, n):
        prev_close = bars[i - 1].close
        tr[i] = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_close),
            abs(bars[i].low - prev_close),
        )

    if n < period:
        return result

    # Seed with SMA
    result[period - 1] = sum(tr[:period]) / period

    for i in range(period, n):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period

    return result


# ---------------------------------------------------------------------------
# ADX — Average Directional Index (Wilder's DM system)
# ---------------------------------------------------------------------------

def adx(bars: Sequence[Bar], period: int) -> list[float]:
    """Causal ADX (Wilder's 14-period directional movement system).

    Computes smoothed +DM, -DM, and TR using Wilder's RMA (same as ATR).
    Then:
        +DI = 100 * smooth_DM+ / smooth_TR
        -DI = 100 * smooth_DM- / smooth_TR
        DX  = 100 * |+DI - -DI| / (+DI + -DI)
        ADX = Wilder RMA of DX over `period` bars

    Warmup: first ADX value is defined at index 2*period - 1 (period
    bars to build DI, then period bars to smooth DX into ADX).

    Returns:
        List of length len(bars); NaN during warmup.
    """
    n = len(bars)
    result: list[float] = [_NAN] * n
    if n == 0 or period <= 0:
        return result

    # --- Directional movement and True Range per bar (i >= 1) ---
    # Index 0 is undefined (no previous bar); treat as 0.
    dm_plus: list[float] = [0.0] * n
    dm_minus: list[float] = [0.0] * n
    tr: list[float] = [0.0] * n
    tr[0] = bars[0].high - bars[0].low

    for i in range(1, n):
        up_move = bars[i].high - bars[i - 1].high
        down_move = bars[i - 1].low - bars[i].low
        prev_close = bars[i - 1].close

        if up_move > down_move and up_move > 0:
            dm_plus[i] = up_move
        elif down_move > up_move and down_move > 0:
            dm_minus[i] = down_move
        # else: both 0 (equal moves or both non-positive)

        tr[i] = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_close),
            abs(bars[i].low - prev_close),
        )

    if n < period:
        return result

    # --- First smoothed values: SMA of first `period` values (indices 0..period-1) ---
    s_tr = sum(tr[:period])
    s_dp = sum(dm_plus[:period])
    s_dm = sum(dm_minus[:period])

    # Compute DX series starting at index period-1
    dx: list[float] = [_NAN] * n

    def _dx_at(s_dp_: float, s_dm_: float, s_tr_: float) -> float:
        if s_tr_ == 0:
            return _NAN
        di_plus = 100.0 * s_dp_ / s_tr_
        di_minus = 100.0 * s_dm_ / s_tr_
        total = di_plus + di_minus
        if total == 0:
            return 0.0
        return 100.0 * abs(di_plus - di_minus) / total

    dx[period - 1] = _dx_at(s_dp, s_dm, s_tr)

    for i in range(period, n):
        # Wilder's RMA update
        s_tr = (s_tr * (period - 1) + tr[i]) / period
        s_dp = (s_dp * (period - 1) + dm_plus[i]) / period
        s_dm = (s_dm * (period - 1) + dm_minus[i]) / period
        dx[i] = _dx_at(s_dp, s_dm, s_tr)

    # --- Smooth DX into ADX: need `period` valid DX values ---
    # DX is defined from index period-1; first ADX at index 2*period-2
    adx_start = 2 * period - 2
    if n <= adx_start:
        return result

    # Seed ADX: SMA of dx[period-1 .. 2*period-2]
    dx_window = [dx[j] for j in range(period - 1, 2 * period - 1) if not math.isnan(dx[j])]
    if len(dx_window) < period:
        return result

    adx_val = sum(dx_window) / period
    result[adx_start] = adx_val

    for i in range(adx_start + 1, n):
        dx_i = dx[i]
        if math.isnan(dx_i):
            result[i] = adx_val  # carry last if DX undefined (shouldn't happen)
        else:
            adx_val = (adx_val * (period - 1) + dx_i) / period
            result[i] = adx_val

    return result


# ---------------------------------------------------------------------------
# Session VWAP — cumulative within a single session
# ---------------------------------------------------------------------------

def session_vwap(bars: Sequence[Bar]) -> list[float]:
    """Causal cumulative VWAP for a single session.

    VWAP[i] = sum(tp[j]*vol[j] for j<=i) / sum(vol[j] for j<=i)
    where tp[j] = (high[j] + low[j] + close[j]) / 3.

    Never NaN (defined from bar 0).  Bars are assumed to belong to a
    single session; no cross-session reset is applied here.

    Returns:
        List of length len(bars).
    """
    n = len(bars)
    result: list[float] = [_NAN] * n
    if n == 0:
        return result

    cum_tp_vol = 0.0
    cum_vol = 0.0
    for i, b in enumerate(bars):
        tp = (b.high + b.low + b.close) / 3.0
        cum_tp_vol += tp * b.volume
        cum_vol += b.volume
        result[i] = cum_tp_vol / cum_vol if cum_vol > 0 else _NAN

    return result


# ---------------------------------------------------------------------------
# VWAP deviation sigma — estimated from PRIOR sessions only (§4.2)
# ---------------------------------------------------------------------------

def vwap_deviation_sigma(
    prior_sessions: Mapping[date, Sequence[Bar]],
) -> float:
    """Standard deviation of per-bar VWAP deviations across PRIOR sessions.

    For each prior session, the session VWAP is computed cumulatively and
    each bar's deviation is recorded as:
        dev[j] = typical_price[j] - session_vwap_at_j

    The returned sigma is the population std-dev of all collected deviations
    (pooled across all prior sessions).  Used by VWAP mean-reversion strategy
    to judge whether a deviation is significant (ARCHITECTURE §4.2).

    CAUSAL contract: receives only prior days' data; never called with
    any bar from the current session (the strategy is responsible for this).

    Returns:
        Population std-dev (float).  Returns 0.0 if fewer than 2 data points.
    """
    deviations: list[float] = []
    for session_bars in prior_sessions.values():
        vwaps = session_vwap(session_bars)
        for b, vwap_val in zip(session_bars, vwaps):
            if math.isnan(vwap_val):
                continue
            tp = (b.high + b.low + b.close) / 3.0
            deviations.append(tp - vwap_val)

    if len(deviations) < 2:
        return 0.0
    # population std-dev (not sample) — consistent with how it will be consumed
    mean = sum(deviations) / len(deviations)
    variance = sum((d - mean) ** 2 for d in deviations) / len(deviations)
    return math.sqrt(variance)


# ---------------------------------------------------------------------------
# Opening Range
# ---------------------------------------------------------------------------

def opening_range(
    bars: Sequence[Bar],
    minutes: int,
) -> tuple[float, float] | None:
    """High and low of the opening range (first `minutes` bars).

    Returns (range_high, range_low) once at least `minutes` bars are
    available.  Returns None if len(bars) < minutes.

    Causal: only inspects bars[0..minutes-1].  Ignores any bars after
    that window — the range is locked once formed.

    ARCHITECTURE §4.1 (ORB): typically minutes=15 or minutes=30.
    Interval-aware: bar count is derived from minutes // interval_min, so the
    same wall-clock range works on 1-min and 5-min bars (sweep fidelity rule).
    """
    if not bars or minutes <= 0:
        return None
    n = minutes // bars[0].interval_min
    if n <= 0 or len(bars) < n:
        return None

    range_bars = bars[:n]
    range_high = max(b.high for b in range_bars)
    range_low = min(b.low for b in range_bars)
    return range_high, range_low


# ---------------------------------------------------------------------------
# Time-of-day volume profile — from prior sessions only
# ---------------------------------------------------------------------------

def time_of_day_volume_profile(
    prior_sessions: Mapping[date, Sequence[Bar]],
) -> dict[int, float]:
    """Average volume by minute-offset from session open, from prior sessions.

    Minute offset 0 = 09:15 bar, offset 1 = 09:16, …, offset 374 = 15:29.
    The offset is derived from position in the bar sequence (not wall-clock)
    so the caller must pass sessions in canonical order (09:15 first).

    Used by ORB volume-confirmation logic (ARCHITECTURE §4.1):
    compare current bar's volume to profile[bar_offset].

    Returns:
        dict[int, float] mapping minute_offset -> mean volume.
        Only offsets present in at least one prior session are included.
    """
    accum: dict[int, list[int]] = {}
    for session_bars in prior_sessions.values():
        for offset, bar in enumerate(session_bars):
            if offset not in accum:
                accum[offset] = []
            accum[offset].append(bar.volume)

    return {
        offset: sum(vols) / len(vols)
        for offset, vols in accum.items()
        if vols
    }
