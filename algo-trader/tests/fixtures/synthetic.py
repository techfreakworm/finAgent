"""Deterministic synthetic NSE 1-min bar generators.

Provides seeded (reproducible) sessions of core.Bar for test and
back-test fixtures.  ALL functions are pure: same seed → identical bars.
No wall-clock calls, no random-without-seed.

Session layout: 09:15 – 15:29 IST (375 bars, 1-min each), all
Bar.complete=True, all OHLC-consistent (low<=open,close<=high).

ARCHITECTURE §3 note: these fixtures are used by strategy and
engine unit tests only; they are never written to disk or parquet.
"""
from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from algotrader.core import Bar, Instrument, Segment

# ---------------------------------------------------------------------------
# Shared test instruments
# ---------------------------------------------------------------------------

IST = ZoneInfo("Asia/Kolkata")

TEST_INSTRUMENT = Instrument(
    symbol="TESTSYM",
    security_id="99901",
    segment=Segment.NSE_EQ,
    tick_size=0.05,
    is_derivative=False,
    can_short_intraday=True,
)

NIFTY_FUT_INSTRUMENT = Instrument(
    symbol="NIFTY-FUT",
    security_id="13",
    segment=Segment.NSE_FNO,
    tick_size=0.05,
    is_derivative=True,
    underlying="NIFTY",
    can_short_intraday=True,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MARKET_OPEN = time(9, 15)
_BARS_PER_SESSION = 375  # 09:15 .. 15:29 inclusive


def _session_timestamps(session_date: date) -> list[datetime]:
    """Return list of 375 tz-aware IST bar-open timestamps for one session."""
    start = datetime(
        session_date.year, session_date.month, session_date.day,
        9, 15, tzinfo=IST,
    )
    return [start + timedelta(minutes=i) for i in range(_BARS_PER_SESSION)]


def _make_bar(
    instrument: Instrument,
    ts: datetime,
    open_: float,
    close: float,
    wick_hi: float,
    wick_lo: float,
    volume: int,
) -> Bar:
    """Build an OHLC-consistent 1-min Bar.

    Guarantees: high = max(open,close)+wick_hi, low = min(open,close)-wick_lo.
    Both wick values must be >= 0.
    """
    high = max(open_, close) + wick_hi
    low = min(open_, close) - wick_lo
    assert high >= low, "Internal bug: high < low"
    return Bar(
        instrument=instrument,
        ts_open=ts,
        interval_min=1,
        open=round(open_, 2),
        high=round(high, 2),
        low=round(low, 2),
        close=round(close, 2),
        volume=max(1, volume),
        complete=True,
    )


def _next_trading_day(d: date) -> date:
    """Advance by one calendar day, skipping Saturday and Sunday."""
    d = d + timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d = d + timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Scenario generators
# ---------------------------------------------------------------------------

def trend_day(
    instrument: Instrument,
    session_date: date,
    *,
    seed: int = 42,
    direction: str = "up",
    base_price: float = 20_000.0,
    bar_sigma: float = 15.0,
    drift_per_bar: float = 2.5,
    base_volume: int = 5_000,
) -> list[Bar]:
    """Full NSE session with a persistent price drift.

    direction: "up" or "down".  Price follows a geometric random walk
    with a non-zero drift so strategies can detect a trending day.

    Args:
        seed: RNG seed for reproducibility.
        direction: "up" for bullish drift, "down" for bearish.
        base_price: session opening price.
        bar_sigma: 1-sigma noise per bar (points).
        drift_per_bar: systematic drift per bar (points).
        base_volume: median volume per bar (jittered ±50%).
    """
    rng = random.Random(seed)
    sign = 1.0 if direction == "up" else -1.0
    timestamps = _session_timestamps(session_date)
    bars: list[Bar] = []

    price = base_price
    for ts in timestamps:
        noise = rng.gauss(0.0, bar_sigma)
        close = price + sign * drift_per_bar + noise
        close = max(close, 1.0)  # never negative
        wick_hi = abs(rng.gauss(0.0, bar_sigma * 0.4))
        wick_lo = abs(rng.gauss(0.0, bar_sigma * 0.4))
        vol = max(1, int(base_volume * rng.uniform(0.5, 1.5)))
        bars.append(_make_bar(instrument, ts, price, close, wick_hi, wick_lo, vol))
        price = close  # next bar opens at this close

    return bars


def range_day(
    instrument: Instrument,
    session_date: date,
    *,
    seed: int = 42,
    base_price: float = 20_000.0,
    range_half_width: float = 80.0,
    bar_sigma: float = 12.0,
    reversion_strength: float = 0.15,
    base_volume: int = 4_500,
) -> list[Bar]:
    """Full NSE session where price oscillates inside a band.

    Uses a mean-reverting (Ornstein-Uhlenbeck style) process so price
    gravitates back toward base_price with each bar.

    Args:
        range_half_width: upper/lower band boundary for clamping.
        reversion_strength: OU phi coefficient (0=random walk, 1=instant revert).
    """
    rng = random.Random(seed)
    timestamps = _session_timestamps(session_date)
    bars: list[Bar] = []

    price = base_price
    for ts in timestamps:
        # mean-reversion pull + noise
        pull = reversion_strength * (base_price - price)
        noise = rng.gauss(0.0, bar_sigma)
        close = price + pull + noise
        # soft-clamp within band
        close = max(base_price - range_half_width, min(base_price + range_half_width, close))
        close = max(close, 1.0)
        wick_hi = abs(rng.gauss(0.0, bar_sigma * 0.35))
        wick_lo = abs(rng.gauss(0.0, bar_sigma * 0.35))
        vol = max(1, int(base_volume * rng.uniform(0.5, 1.5)))
        bars.append(_make_bar(instrument, ts, price, close, wick_hi, wick_lo, vol))
        price = close

    return bars


def gap_through_price(
    instrument: Instrument,
    session_date: date,
    level: float,
    *,
    direction: str = "up",
    gap_bar_idx: int = 60,
    gap_size: float = 50.0,
    seed: int = 42,
    base_price: float | None = None,
    bar_sigma: float = 10.0,
    base_volume: int = 5_000,
) -> list[Bar]:
    """Session containing one bar that opens BEYOND `level`.

    The bar at index `gap_bar_idx` has its open forced to
    level + gap_size (direction="up") or level - gap_size (direction="down").
    Bars before the gap are generated so price approaches the level; bars
    after continue normally from the gap bar's close.

    Useful for testing stop/target gap-through engine semantics (§3.2).
    """
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    if gap_bar_idx < 0 or gap_bar_idx >= _BARS_PER_SESSION:
        raise ValueError(f"gap_bar_idx {gap_bar_idx} out of range [0, {_BARS_PER_SESSION})")
    if gap_size <= 0:
        raise ValueError(f"gap_size must be positive, got {gap_size}")

    rng = random.Random(seed)
    timestamps = _session_timestamps(session_date)

    # Determine session starting price so price naturally reaches level by gap_bar_idx
    if base_price is None:
        if direction == "up":
            # start slightly below level
            base_price = level - gap_size * 0.5
        else:
            base_price = level + gap_size * 0.5
    base_price = max(base_price, 1.0)

    bars: list[Bar] = []
    price = base_price

    for idx, ts in enumerate(timestamps):
        if idx < gap_bar_idx:
            # Normal pre-gap bars: drift toward level
            target = level if direction == "up" else level
            pull = 0.05 * (target - price)
            noise = rng.gauss(0.0, bar_sigma)
            close = price + pull + noise
            close = max(close, 1.0)
            wick_hi = abs(rng.gauss(0.0, bar_sigma * 0.3))
            wick_lo = abs(rng.gauss(0.0, bar_sigma * 0.3))
            vol = max(1, int(base_volume * rng.uniform(0.5, 1.5)))
            bars.append(_make_bar(instrument, ts, price, close, wick_hi, wick_lo, vol))
            price = close

        elif idx == gap_bar_idx:
            # GAP bar: open is forced beyond level
            gap_open = (level + gap_size) if direction == "up" else (level - gap_size)
            gap_open = max(gap_open, 1.0)
            noise = rng.gauss(0.0, bar_sigma)
            close = gap_open + noise
            close = max(close, 1.0)
            wick_hi = abs(rng.gauss(0.0, bar_sigma * 0.3))
            wick_lo = abs(rng.gauss(0.0, bar_sigma * 0.3))
            vol = max(1, int(base_volume * rng.uniform(1.0, 3.0)))  # higher volume on gap
            bars.append(_make_bar(instrument, ts, gap_open, close, wick_hi, wick_lo, vol))
            price = close

        else:
            # Post-gap bars: normal random walk from current price
            noise = rng.gauss(0.0, bar_sigma)
            close = price + noise
            close = max(close, 1.0)
            wick_hi = abs(rng.gauss(0.0, bar_sigma * 0.3))
            wick_lo = abs(rng.gauss(0.0, bar_sigma * 0.3))
            vol = max(1, int(base_volume * rng.uniform(0.5, 1.5)))
            bars.append(_make_bar(instrument, ts, price, close, wick_hi, wick_lo, vol))
            price = close

    assert len(bars) == _BARS_PER_SESSION
    return bars


def quiet_then_spike(
    instrument: Instrument,
    session_date: date,
    *,
    seed: int = 42,
    base_price: float = 20_000.0,
    quiet_sigma: float = 3.0,
    spike_sigma: float = 60.0,
    spike_bar_idx: int = 180,
    base_volume: int = 3_000,
    spike_volume_mult: float = 8.0,
) -> list[Bar]:
    """Session: very low volatility until `spike_bar_idx`, then a large move.

    Useful for testing volatility-adaptive indicators (ATR, ADX) and
    strategy behaviour around sudden regime changes.

    Args:
        quiet_sigma: per-bar noise during the quiet phase.
        spike_sigma: per-bar noise during and after the spike.
        spike_bar_idx: first bar of the high-volatility regime.
        spike_volume_mult: volume multiplier on the spike bar.
    """
    rng = random.Random(seed)
    timestamps = _session_timestamps(session_date)
    bars: list[Bar] = []

    price = base_price
    for idx, ts in enumerate(timestamps):
        if idx < spike_bar_idx:
            sigma = quiet_sigma
            vol_mult = rng.uniform(0.8, 1.2)
        elif idx == spike_bar_idx:
            sigma = spike_sigma
            vol_mult = spike_volume_mult * rng.uniform(0.8, 1.2)
        else:
            sigma = spike_sigma * 0.6  # remain elevated after spike
            vol_mult = rng.uniform(1.0, 2.5)

        noise = rng.gauss(0.0, sigma)
        close = price + noise
        close = max(close, 1.0)
        wick_hi = abs(rng.gauss(0.0, sigma * 0.4))
        wick_lo = abs(rng.gauss(0.0, sigma * 0.4))
        vol = max(1, int(base_volume * vol_mult))
        bars.append(_make_bar(instrument, ts, price, close, wick_hi, wick_lo, vol))
        price = close

    assert len(bars) == _BARS_PER_SESSION
    return bars


def multi_session(
    instrument: Instrument,
    n_days: int,
    scenario_seq: list[str],
    *,
    start_date: date = date(2024, 1, 2),
    seed: int = 42,
    base_price: float = 20_000.0,
) -> dict[date, list[Bar]]:
    """Generate `n_days` full sessions cycling through `scenario_seq`.

    Scenario names (case-insensitive):
        "trend_up", "trend_down", "range", "quiet_then_spike",
        "gap_up", "gap_down"

    Dates advance over calendar trading days (skips Sat/Sun).
    Each session uses a deterministic sub-seed derived from the master seed
    so results are stable regardless of n_days.

    Returns:
        OrderedDict[date, list[Bar]] sorted chronologically.
    """
    if not scenario_seq:
        raise ValueError("scenario_seq must not be empty")
    if n_days <= 0:
        raise ValueError("n_days must be positive")

    master_rng = random.Random(seed)
    sessions: dict[date, list[Bar]] = {}

    current_date = start_date
    # advance past any weekend
    while current_date.weekday() >= 5:
        current_date = _next_trading_day(current_date)

    # price carries across sessions for realism (close of last bar seeds next open)
    price = base_price

    for i in range(n_days):
        scenario = scenario_seq[i % len(scenario_seq)].lower()
        sub_seed = master_rng.randint(0, 2**31 - 1)

        # Price continuity: derive base_price for this session from previous close
        session_base = price

        if scenario == "trend_up":
            day_bars = trend_day(
                instrument, current_date,
                seed=sub_seed, direction="up",
                base_price=session_base,
            )
        elif scenario == "trend_down":
            day_bars = trend_day(
                instrument, current_date,
                seed=sub_seed, direction="down",
                base_price=session_base,
            )
        elif scenario == "range":
            day_bars = range_day(
                instrument, current_date,
                seed=sub_seed, base_price=session_base,
            )
        elif scenario == "quiet_then_spike":
            day_bars = quiet_then_spike(
                instrument, current_date,
                seed=sub_seed, base_price=session_base,
            )
        elif scenario == "gap_up":
            day_bars = gap_through_price(
                instrument, current_date,
                level=session_base + 40,
                direction="up",
                seed=sub_seed,
                base_price=session_base,
            )
        elif scenario == "gap_down":
            day_bars = gap_through_price(
                instrument, current_date,
                level=session_base - 40,
                direction="down",
                seed=sub_seed,
                base_price=session_base,
            )
        else:
            raise ValueError(
                f"Unknown scenario {scenario!r}. "
                "Valid: trend_up, trend_down, range, quiet_then_spike, gap_up, gap_down"
            )

        sessions[current_date] = day_bars
        price = day_bars[-1].close  # carry forward for price continuity
        current_date = _next_trading_day(current_date)

    return sessions


# ---------------------------------------------------------------------------
# Validation helper (used in tests only)
# ---------------------------------------------------------------------------

def assert_bars_valid(bars: list[Bar]) -> None:
    """Assert all OHLC invariants and IST timezone for every bar."""
    for i, b in enumerate(bars):
        assert b.low <= b.open, f"bar {i}: low={b.low} > open={b.open}"
        assert b.low <= b.close, f"bar {i}: low={b.low} > close={b.close}"
        assert b.high >= b.open, f"bar {i}: high={b.high} < open={b.open}"
        assert b.high >= b.close, f"bar {i}: high={b.high} < close={b.close}"
        assert b.high >= b.low, f"bar {i}: high={b.high} < low={b.low}"
        assert b.volume >= 1, f"bar {i}: volume={b.volume} < 1"
        assert b.complete, f"bar {i}: complete=False"
        assert b.ts_open.tzinfo is not None, f"bar {i}: naive datetime"
        assert b.interval_min == 1, f"bar {i}: interval_min={b.interval_min}"
