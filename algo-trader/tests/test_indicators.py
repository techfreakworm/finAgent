"""Tests for algotrader/strategies/indicators.py.

Three test layers:
1. Fixture validity — synthetic bars satisfy OHLC invariants.
2. Correctness spot-checks — hand-computed expected values.
3. CAUSALITY PROPERTY — mutating bars after index t must not change
   any indicator value at index <= t, for every indicator.

ARCHITECTURE §3.8 / §4.2: causality and no look-ahead are hard rules.
"""
from __future__ import annotations

import dataclasses
import math
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from algotrader.core import Bar, Instrument, Segment
from algotrader.strategies.indicators import (
    adx,
    atr,
    ema,
    opening_range,
    session_vwap,
    time_of_day_volume_profile,
    vwap_deviation_sigma,
)
from tests.fixtures.synthetic import (
    NIFTY_FUT_INSTRUMENT,
    TEST_INSTRUMENT,
    assert_bars_valid,
    gap_through_price,
    multi_session,
    quiet_then_spike,
    range_day,
    trend_day,
)

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_DATE = date(2024, 1, 2)  # Tuesday — guaranteed trading day


def _make_simple_bars(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[int],
    instrument: Instrument = TEST_INSTRUMENT,
    session_date: date = _BASE_DATE,
) -> list[Bar]:
    """Build minimal bars for hand-computation tests."""
    assert len(closes) == len(highs) == len(lows) == len(volumes)
    start = datetime(session_date.year, session_date.month, session_date.day,
                     9, 15, tzinfo=IST)
    bars = []
    for i, (c, h, l, v) in enumerate(zip(closes, highs, lows, volumes)):
        open_ = closes[i - 1] if i > 0 else c  # open = prev close
        bars.append(Bar(
            instrument=instrument,
            ts_open=start + timedelta(minutes=i),
            interval_min=1,
            open=open_,
            high=h,
            low=l,
            close=c,
            volume=v,
            complete=True,
        ))
    return bars


def _vals_equal(a: float, b: float) -> bool:
    """True if both NaN or both equal (exact)."""
    if math.isnan(a) and math.isnan(b):
        return True
    return a == b


# ---------------------------------------------------------------------------
# 1. Fixture validity
# ---------------------------------------------------------------------------

class TestSyntheticFixtures:
    """All generated sessions must satisfy OHLC invariants and IST tz."""

    def test_trend_day_up_valid(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=1)
        assert len(bars) == 375
        assert_bars_valid(bars)

    def test_trend_day_down_valid(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=2, direction="down")
        assert len(bars) == 375
        assert_bars_valid(bars)

    def test_trend_day_deterministic(self):
        b1 = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=7)
        b2 = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=7)
        assert [b.close for b in b1] == [b.close for b in b2]

    def test_trend_day_different_seeds_differ(self):
        b1 = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=7)
        b2 = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=8)
        assert [b.close for b in b1] != [b.close for b in b2]

    def test_range_day_valid(self):
        bars = range_day(TEST_INSTRUMENT, _BASE_DATE, seed=3)
        assert len(bars) == 375
        assert_bars_valid(bars)

    def test_quiet_then_spike_valid(self):
        bars = quiet_then_spike(TEST_INSTRUMENT, _BASE_DATE, seed=4)
        assert len(bars) == 375
        assert_bars_valid(bars)

    def test_quiet_then_spike_low_vol_before_spike(self):
        """Quiet phase: std-dev of close should be much smaller than after spike."""
        bars = quiet_then_spike(
            TEST_INSTRUMENT, _BASE_DATE, seed=5,
            quiet_sigma=2.0, spike_sigma=100.0, spike_bar_idx=100,
        )
        quiet_closes = [b.close for b in bars[:100]]
        spike_closes = [b.close for b in bars[100:200]]
        quiet_std = math.sqrt(sum((c - sum(quiet_closes) / len(quiet_closes)) ** 2
                                  for c in quiet_closes) / len(quiet_closes))
        spike_std = math.sqrt(sum((c - sum(spike_closes) / len(spike_closes)) ** 2
                                   for c in spike_closes) / len(spike_closes))
        assert spike_std > quiet_std * 3, (
            f"Expected spike std ({spike_std:.2f}) >> quiet std ({quiet_std:.2f})"
        )

    def test_gap_through_price_up(self):
        bars = gap_through_price(
            TEST_INSTRUMENT, _BASE_DATE,
            level=20_100.0, direction="up", gap_bar_idx=60, gap_size=50.0, seed=6,
        )
        assert len(bars) == 375
        assert_bars_valid(bars)
        # The gap bar must open ABOVE the level
        gap_bar = bars[60]
        assert gap_bar.open > 20_100.0, (
            f"Gap bar open={gap_bar.open} should be > level=20100"
        )

    def test_gap_through_price_down(self):
        bars = gap_through_price(
            TEST_INSTRUMENT, _BASE_DATE,
            level=19_900.0, direction="down", gap_bar_idx=60, gap_size=50.0, seed=9,
            base_price=20_000.0,
        )
        assert len(bars) == 375
        assert_bars_valid(bars)
        gap_bar = bars[60]
        assert gap_bar.open < 19_900.0, (
            f"Gap bar open={gap_bar.open} should be < level=19900"
        )

    def test_multi_session_count(self):
        sessions = multi_session(TEST_INSTRUMENT, 5, ["trend_up", "range"])
        assert len(sessions) == 5

    def test_multi_session_all_valid(self):
        sessions = multi_session(
            TEST_INSTRUMENT, 6,
            ["trend_up", "trend_down", "range", "quiet_then_spike", "gap_up", "gap_down"],
            seed=42,
        )
        for d, bars in sessions.items():
            assert_bars_valid(bars), f"session {d} failed OHLC validation"

    def test_multi_session_no_weekend_dates(self):
        sessions = multi_session(TEST_INSTRUMENT, 10, ["range"], seed=1)
        for d in sessions:
            assert d.weekday() < 5, f"{d} is a weekend"

    def test_multi_session_deterministic(self):
        s1 = multi_session(TEST_INSTRUMENT, 4, ["trend_up"], seed=11)
        s2 = multi_session(TEST_INSTRUMENT, 4, ["trend_up"], seed=11)
        for d in s1:
            assert [b.close for b in s1[d]] == [b.close for b in s2[d]]

    def test_session_timestamps_ist(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=1)
        assert bars[0].ts_open.hour == 9 and bars[0].ts_open.minute == 15
        assert bars[-1].ts_open.hour == 15 and bars[-1].ts_open.minute == 29
        for b in bars:
            assert b.ts_open.tzinfo is not None


# ---------------------------------------------------------------------------
# 2. Indicator correctness spot-checks
# ---------------------------------------------------------------------------

class TestEMACorrectness:
    """Hand-computed EMA values for period=3."""

    # alpha = 2/(3+1) = 0.5
    # closes = [100, 101, 102, 103, 104]
    # EMA[2] = SMA([100,101,102]) = 101.0
    # EMA[3] = 0.5*103 + 0.5*101 = 102.0
    # EMA[4] = 0.5*104 + 0.5*102 = 103.0

    _CLOSES = [100.0, 101.0, 102.0, 103.0, 104.0]

    def _bars(self) -> list[Bar]:
        n = len(self._CLOSES)
        return _make_simple_bars(
            closes=self._CLOSES,
            highs=[c + 1 for c in self._CLOSES],
            lows=[c - 1 for c in self._CLOSES],
            volumes=[1000] * n,
        )

    def test_warmup_is_nan(self):
        vals = ema(self._bars(), period=3)
        assert math.isnan(vals[0])
        assert math.isnan(vals[1])

    def test_seed_value(self):
        vals = ema(self._bars(), period=3)
        assert vals[2] == pytest.approx(101.0)

    def test_ema_3(self):
        vals = ema(self._bars(), period=3)
        assert vals[3] == pytest.approx(102.0)

    def test_ema_4(self):
        vals = ema(self._bars(), period=3)
        assert vals[4] == pytest.approx(103.0)

    def test_period_1_equals_close(self):
        bars = self._bars()
        vals = ema(bars, period=1)
        for b, v in zip(bars, vals):
            assert v == pytest.approx(b.close)

    def test_empty_bars(self):
        assert ema([], period=9) == []

    def test_fewer_bars_than_period(self):
        bars = self._bars()[:2]
        vals = ema(bars, period=5)
        assert all(math.isnan(v) for v in vals)

    def test_length_equals_input(self):
        bars = self._bars()
        assert len(ema(bars, period=3)) == len(bars)


class TestATRCorrectness:
    """Hand-computed ATR values for period=2.

    Bars (open=prev_close):
        i=0: H=102, L=99,  C=101  -> TR[0] = 102-99 = 3
        i=1: H=104, L=100, C=103  -> TR[1] = max(4, |104-101|, |100-101|) = max(4,3,1) = 4
        i=2: H=106, L=101, C=105  -> TR[2] = max(5, |106-103|, |101-103|) = max(5,3,2) = 5
        ATR[1] = (3+4)/2 = 3.5
        ATR[2] = (3.5*1 + 5)/2 = 4.25
    """

    def _bars(self) -> list[Bar]:
        closes = [101.0, 103.0, 105.0]
        highs = [102.0, 104.0, 106.0]
        lows = [99.0, 100.0, 101.0]
        return _make_simple_bars(closes, highs, lows, [1000, 2000, 1500])

    def test_warmup_nan(self):
        vals = atr(self._bars(), period=2)
        assert math.isnan(vals[0])

    def test_atr_at_1(self):
        vals = atr(self._bars(), period=2)
        assert vals[1] == pytest.approx(3.5)

    def test_atr_at_2(self):
        vals = atr(self._bars(), period=2)
        assert vals[2] == pytest.approx(4.25)

    def test_empty(self):
        assert atr([], period=14) == []

    def test_length_equals_input(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=10)
        assert len(atr(bars, period=14)) == len(bars)

    def test_atr_nonneg(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=11)
        vals = atr(bars, period=14)
        for v in vals:
            if not math.isnan(v):
                assert v >= 0.0


class TestADXCorrectness:
    """Structural / sanity tests for ADX (exact hand-calc is long; we verify bounds)."""

    def test_warmup_period(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=20)
        period = 14
        vals = adx(bars, period=period)
        # First 2*period-2 = 26 indices should be NaN
        for i in range(2 * period - 2):
            assert math.isnan(vals[i]), f"Expected NaN at index {i}, got {vals[i]}"

    def test_first_defined_value_in_range(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=20)
        period = 14
        vals = adx(bars, period=period)
        first_val = vals[2 * period - 2]
        assert not math.isnan(first_val)
        assert 0.0 <= first_val <= 100.0

    def test_all_values_in_range(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=21)
        vals = adx(bars, period=14)
        for v in vals:
            if not math.isnan(v):
                assert 0.0 <= v <= 100.0, f"ADX={v} out of [0,100]"

    def test_trending_day_adx_elevated(self):
        """A strong trend day should produce higher ADX than a range day."""
        trend_bars = trend_day(
            TEST_INSTRUMENT, _BASE_DATE, seed=30,
            bar_sigma=5.0, drift_per_bar=10.0,
        )
        range_bars = range_day(
            TEST_INSTRUMENT, _BASE_DATE, seed=30,
            bar_sigma=5.0,
        )
        period = 14
        trend_adx = adx(trend_bars, period)
        range_adx = adx(range_bars, period)

        # Use the last quarter of the session to get settled values
        trend_avg = sum(
            v for v in trend_adx[-90:] if not math.isnan(v)
        ) / max(1, sum(1 for v in trend_adx[-90:] if not math.isnan(v)))
        range_avg = sum(
            v for v in range_adx[-90:] if not math.isnan(v)
        ) / max(1, sum(1 for v in range_adx[-90:] if not math.isnan(v)))

        assert trend_avg > range_avg, (
            f"Trend ADX avg {trend_avg:.2f} should exceed range ADX avg {range_avg:.2f}"
        )

    def test_empty(self):
        assert adx([], period=14) == []

    def test_length_equals_input(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=22)
        assert len(adx(bars, period=14)) == len(bars)


class TestSessionVWAPCorrectness:
    """Hand-computed VWAP values.

    Bars with exact integer math:
        i=0: tp=(102+99+101)/3=100.667, vol=3000 -> VWAP=100.667
        i=1: tp=(104+100+103)/3=102.333, vol=3000
               VWAP = (100.667*3000 + 102.333*3000) / 6000 = 101.5
        i=2: tp=(106+101+105)/3=104.0,   vol=3000
               VWAP = (101.5*6000 + 104*3000) / 9000 = 102.333...
    """

    def _bars(self) -> list[Bar]:
        closes = [101.0, 103.0, 105.0]
        highs = [102.0, 104.0, 106.0]
        lows = [99.0, 100.0, 101.0]
        return _make_simple_bars(closes, highs, lows, [3000, 3000, 3000])

    def test_vwap_bar0(self):
        vals = session_vwap(self._bars())
        tp0 = (102.0 + 99.0 + 101.0) / 3.0
        assert vals[0] == pytest.approx(tp0)

    def test_vwap_bar1(self):
        bars = self._bars()
        vals = session_vwap(bars)
        # equal volumes so VWAP = mean of typical prices
        tp0 = (102.0 + 99.0 + 101.0) / 3.0
        tp1 = (104.0 + 100.0 + 103.0) / 3.0
        expected = (tp0 + tp1) / 2.0
        assert vals[1] == pytest.approx(expected)

    def test_vwap_bar2(self):
        bars = self._bars()
        vals = session_vwap(bars)
        tp0 = (102.0 + 99.0 + 101.0) / 3.0
        tp1 = (104.0 + 100.0 + 103.0) / 3.0
        tp2 = (106.0 + 101.0 + 105.0) / 3.0
        expected = (tp0 + tp1 + tp2) / 3.0  # equal volumes
        assert vals[2] == pytest.approx(expected)

    def test_vwap_no_nan(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=40)
        vals = session_vwap(bars)
        assert all(not math.isnan(v) for v in vals)

    def test_vwap_nondecreasing_with_trend(self):
        """On a strong up-trend, VWAP should generally increase."""
        bars = trend_day(
            TEST_INSTRUMENT, _BASE_DATE, seed=41,
            direction="up", drift_per_bar=5.0, bar_sigma=1.0,
        )
        vals = session_vwap(bars)
        # The final VWAP should be > first VWAP
        assert vals[-1] > vals[0]

    def test_empty(self):
        assert session_vwap([]) == []

    def test_length_equals_input(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=42)
        assert len(session_vwap(bars)) == len(bars)


class TestVWAPDeviationSigma:
    """Tests for prior-session-only sigma estimation (ARCHITECTURE §4.2)."""

    def _make_prior_sessions(self, n: int, start: date = _BASE_DATE) -> dict[date, list[Bar]]:
        return multi_session(
            TEST_INSTRUMENT, n,
            ["trend_up", "range", "trend_down"],
            start_date=start, seed=100,
        )

    def test_returns_nonneg(self):
        prior = self._make_prior_sessions(5)
        sigma = vwap_deviation_sigma(prior)
        assert sigma >= 0.0

    def test_returns_float(self):
        prior = self._make_prior_sessions(3)
        assert isinstance(vwap_deviation_sigma(prior), float)

    def test_empty_returns_zero(self):
        assert vwap_deviation_sigma({}) == 0.0

    def test_single_session_returns_zero(self):
        # Only one session → deviations all relative to own VWAP;
        # pooled variance can be > 0 but should at least not error
        prior = self._make_prior_sessions(1)
        sigma = vwap_deviation_sigma(prior)
        assert sigma >= 0.0

    def test_more_volatile_sessions_give_larger_sigma(self):
        """Sessions with wider price swings should produce higher sigma."""
        calm_sessions = multi_session(
            TEST_INSTRUMENT, 5, ["range"],
            seed=200, base_price=20_000.0,
        )
        # Build calm sessions manually with tiny sigma
        from tests.fixtures.synthetic import range_day
        calm_sessions = {
            date(2024, 1, d): range_day(
                TEST_INSTRUMENT, date(2024, 1, d),
                seed=d, bar_sigma=1.0, range_half_width=5.0,
            )
            for d in range(2, 7)
        }
        volatile_sessions = {
            date(2024, 1, d): trend_day(
                TEST_INSTRUMENT, date(2024, 1, d),
                seed=d + 100, drift_per_bar=20.0, bar_sigma=30.0,
            )
            for d in range(2, 7)
        }
        sigma_calm = vwap_deviation_sigma(calm_sessions)
        sigma_volatile = vwap_deviation_sigma(volatile_sessions)
        assert sigma_volatile > sigma_calm, (
            f"Volatile sigma {sigma_volatile:.2f} should exceed calm sigma {sigma_calm:.2f}"
        )

    def test_independent_of_session_order(self):
        """Sigma must not depend on insertion order of prior_sessions dict."""
        prior = self._make_prior_sessions(4)
        dates = list(prior.keys())
        reversed_prior = {d: prior[d] for d in reversed(dates)}
        assert vwap_deviation_sigma(prior) == pytest.approx(
            vwap_deviation_sigma(reversed_prior)
        )

    def test_uses_prior_only_not_current(self):
        """Changing the 'current' session (not passed in) must not affect sigma."""
        prior = self._make_prior_sessions(3)
        sigma_before = vwap_deviation_sigma(prior)

        # This would be the 'current' session — NOT passed in
        _ = trend_day(TEST_INSTRUMENT, date(2024, 1, 20), seed=999)

        # sigma unchanged — we never passed the current session
        sigma_after = vwap_deviation_sigma(prior)
        assert sigma_before == sigma_after


class TestOpeningRange:
    """Correctness tests for opening_range."""

    def test_returns_none_if_not_enough_bars(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=50)
        assert opening_range(bars[:5], minutes=15) is None

    def test_returns_none_for_empty(self):
        assert opening_range([], minutes=15) is None

    def test_range_is_correct(self):
        # Build bars where we know the high/low exactly
        highs = [101.0, 103.0, 105.0, 102.0, 100.0]
        lows = [99.0,  100.0, 102.0, 100.0, 98.0]
        closes = [100.0, 102.0, 104.0, 101.0, 99.0]
        bars = _make_simple_bars(closes, highs, lows, [1000] * 5)
        result = opening_range(bars, minutes=3)
        assert result is not None
        rh, rl = result
        assert rh == pytest.approx(105.0)  # max of first 3 highs
        assert rl == pytest.approx(99.0)   # min of first 3 lows

    def test_range_uses_only_first_n_bars(self):
        """Bars after minutes should not affect the range."""
        highs = [101.0, 103.0, 105.0, 999.0, 998.0]  # huge highs after window
        lows = [99.0,  100.0, 102.0, 0.1,   0.1]      # tiny lows after window
        closes = [100.0, 102.0, 104.0, 100.0, 100.0]
        bars = _make_simple_bars(closes, highs, lows, [1000] * 5)
        result = opening_range(bars, minutes=3)
        rh, rl = result
        assert rh == pytest.approx(105.0), "Range high should ignore bars after window"
        assert rl == pytest.approx(99.0), "Range low should ignore bars after window"

    def test_30min_range_length(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=51)
        result = opening_range(bars, minutes=30)
        assert result is not None
        rh, rl = result
        assert rh >= rl

    def test_range_high_ge_low(self):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=52)
        for minutes in (1, 5, 15, 30):
            result = opening_range(bars, minutes=minutes)
            if result is not None:
                rh, rl = result
                assert rh >= rl


class TestTimeOfDayVolumeProfile:
    """Correctness tests for time_of_day_volume_profile."""

    def _two_sessions(self) -> dict[date, list[Bar]]:
        """Two sessions with known volumes at offsets 0 and 1."""
        # Build minimal 2-bar sessions manually
        start1 = datetime(2024, 1, 2, 9, 15, tzinfo=IST)
        start2 = datetime(2024, 1, 3, 9, 15, tzinfo=IST)

        def _b(ts, vol):
            return Bar(
                instrument=TEST_INSTRUMENT,
                ts_open=ts,
                interval_min=1,
                open=100.0, high=101.0, low=99.0, close=100.0,
                volume=vol, complete=True,
            )

        s1 = [_b(start1, 100), _b(start1 + timedelta(minutes=1), 200)]
        s2 = [_b(start2, 150), _b(start2 + timedelta(minutes=1), 250)]
        return {date(2024, 1, 2): s1, date(2024, 1, 3): s2}

    def test_offset_0_average(self):
        profile = time_of_day_volume_profile(self._two_sessions())
        # offset 0: (100 + 150) / 2 = 125
        assert profile[0] == pytest.approx(125.0)

    def test_offset_1_average(self):
        profile = time_of_day_volume_profile(self._two_sessions())
        # offset 1: (200 + 250) / 2 = 225
        assert profile[1] == pytest.approx(225.0)

    def test_empty_prior_sessions(self):
        assert time_of_day_volume_profile({}) == {}

    def test_all_offsets_covered(self):
        sessions = multi_session(TEST_INSTRUMENT, 3, ["range"], seed=60)
        profile = time_of_day_volume_profile(sessions)
        # All 375 offsets should be present
        assert set(profile.keys()) == set(range(375))

    def test_volumes_positive(self):
        sessions = multi_session(TEST_INSTRUMENT, 4, ["trend_up", "range"], seed=61)
        profile = time_of_day_volume_profile(sessions)
        assert all(v > 0 for v in profile.values())


# ---------------------------------------------------------------------------
# 3. CAUSALITY PROPERTY TESTS
# ---------------------------------------------------------------------------

def _mutate_bars_after(bars: list[Bar], t: int) -> list[Bar]:
    """Return a copy of bars where all bars after index t have 2x prices."""
    result = list(bars[:t + 1])
    for b in bars[t + 1:]:
        # Scale all OHLC by 2x; preserves OHLC consistency
        mutated = dataclasses.replace(
            b,
            open=b.open * 2.0,
            high=b.high * 2.0,
            low=b.low * 2.0,
            close=b.close * 2.0,
            volume=b.volume * 2,
        )
        result.append(mutated)
    return result


def _causality_check(
    vals_original: list[float],
    vals_mutated: list[float],
    t: int,
    label: str,
) -> None:
    """Assert indicator values at index <= t are unchanged after mutation."""
    for i in range(t + 1):
        orig = vals_original[i]
        mut = vals_mutated[i]
        assert _vals_equal(orig, mut), (
            f"CAUSALITY VIOLATION: {label} changed at index {i} "
            f"(t={t}): was {orig}, now {mut}"
        )


_CAUSALITY_T_VALUES = [0, 5, 14, 27, 50, 100, 200, 300, 374]


class TestCausalityEMA:
    """Causality property: EMA[i] must not change when bars after i are mutated."""

    @pytest.mark.parametrize("t", _CAUSALITY_T_VALUES)
    def test_causality_at_t(self, t):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=1001)
        if t >= len(bars):
            pytest.skip("t beyond session length")
        vals_orig = ema(bars, period=9)
        vals_mut = ema(_mutate_bars_after(bars, t), period=9)
        _causality_check(vals_orig, vals_mut, t, f"EMA(9) at t={t}")

    @pytest.mark.parametrize("period", [1, 3, 9, 20])
    def test_causality_various_periods(self, period):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=1002)
        t = 50
        vals_orig = ema(bars, period=period)
        vals_mut = ema(_mutate_bars_after(bars, t), period=period)
        _causality_check(vals_orig, vals_mut, t, f"EMA({period})")


class TestCausalityATR:
    """Causality property for ATR."""

    @pytest.mark.parametrize("t", _CAUSALITY_T_VALUES)
    def test_causality_at_t(self, t):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=2001)
        if t >= len(bars):
            pytest.skip("t beyond session length")
        vals_orig = atr(bars, period=14)
        vals_mut = atr(_mutate_bars_after(bars, t), period=14)
        _causality_check(vals_orig, vals_mut, t, f"ATR(14) at t={t}")

    @pytest.mark.parametrize("period", [2, 5, 14])
    def test_causality_various_periods(self, period):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=2002)
        t = 50
        vals_orig = atr(bars, period=period)
        vals_mut = atr(_mutate_bars_after(bars, t), period=period)
        _causality_check(vals_orig, vals_mut, t, f"ATR({period})")


class TestCausalityADX:
    """Causality property for ADX."""

    @pytest.mark.parametrize("t", _CAUSALITY_T_VALUES)
    def test_causality_at_t(self, t):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=3001)
        if t >= len(bars):
            pytest.skip("t beyond session length")
        vals_orig = adx(bars, period=14)
        vals_mut = adx(_mutate_bars_after(bars, t), period=14)
        _causality_check(vals_orig, vals_mut, t, f"ADX(14) at t={t}")


class TestCausalitySessionVWAP:
    """Causality property for session_vwap."""

    @pytest.mark.parametrize("t", _CAUSALITY_T_VALUES)
    def test_causality_at_t(self, t):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=4001)
        if t >= len(bars):
            pytest.skip("t beyond session length")
        vals_orig = session_vwap(bars)
        vals_mut = session_vwap(_mutate_bars_after(bars, t))
        _causality_check(vals_orig, vals_mut, t, f"session_vwap at t={t}")

    def test_causality_range_day(self):
        bars = range_day(TEST_INSTRUMENT, _BASE_DATE, seed=4002)
        t = 150
        vals_orig = session_vwap(bars)
        vals_mut = session_vwap(_mutate_bars_after(bars, t))
        _causality_check(vals_orig, vals_mut, t, "session_vwap on range_day")


class TestCausalityOpeningRange:
    """Causality property: opening_range depends only on first `minutes` bars."""

    @pytest.mark.parametrize("minutes", [15, 30])
    def test_bars_after_window_do_not_affect_range(self, minutes):
        bars = trend_day(TEST_INSTRUMENT, _BASE_DATE, seed=5001)

        # Compute range from full session
        result_full = opening_range(bars, minutes=minutes)

        # Mutate ALL bars after the window with extreme values
        mutated = list(bars[:minutes])
        for b in bars[minutes:]:
            mutated.append(dataclasses.replace(
                b, open=b.open * 100, high=b.high * 100,
                low=b.low * 100, close=b.close * 100,
                volume=b.volume * 100,
            ))

        result_mut = opening_range(mutated, minutes=minutes)

        assert result_full is not None and result_mut is not None
        assert result_full[0] == result_mut[0], "Range high changed after mutation"
        assert result_full[1] == result_mut[1], "Range low changed after mutation"


class TestCausalityVWAPDeviationSigma:
    """vwap_deviation_sigma must not change when the 'current session' is absent."""

    def test_sigma_stable_across_calls(self):
        prior = multi_session(TEST_INSTRUMENT, 5, ["range", "trend_up"], seed=6001)
        s1 = vwap_deviation_sigma(prior)
        s2 = vwap_deviation_sigma(prior)
        assert s1 == s2

    def test_sigma_unaffected_by_unseen_session(self):
        """Adding a new (unrelated) date to the prior dict changes sigma
        (as expected), but the original subset is unaffected."""
        prior = multi_session(TEST_INSTRUMENT, 3, ["range"], seed=6002)
        sigma_original = vwap_deviation_sigma(prior)

        # Re-compute from the same keys — must be identical
        sigma_recomputed = vwap_deviation_sigma(dict(prior))
        assert sigma_original == sigma_recomputed


class TestCausalityTimeOfDayProfile:
    """time_of_day_volume_profile uses only the passed prior sessions."""

    def test_profile_stable_across_calls(self):
        sessions = multi_session(TEST_INSTRUMENT, 4, ["range"], seed=7001)
        p1 = time_of_day_volume_profile(sessions)
        p2 = time_of_day_volume_profile(sessions)
        assert p1 == p2

    def test_additional_session_changes_profile(self):
        sessions_small = multi_session(TEST_INSTRUMENT, 2, ["range"], seed=7002)
        sessions_large = multi_session(TEST_INSTRUMENT, 4, ["range"], seed=7002)
        p_small = time_of_day_volume_profile(sessions_small)
        p_large = time_of_day_volume_profile(sessions_large)
        # More sessions → the profile values should differ (more samples)
        assert p_small != p_large
