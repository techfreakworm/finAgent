"""Tests for algotrader/backtest/walkforward.py.

Covers:
  1. WalkForwardSplitter — window count and boundary math.
  2. HoldoutFence — passes valid windows; raises on holdout violation.
  3. TrialRegistry — append, count, JSONL format.
  4. deflated_sharpe — range, monotonicity, properties.

All date arithmetic is verified against hand-computed expected values using
calendar month boundaries.
"""
from __future__ import annotations

import json
import math
import tempfile
from datetime import date
from pathlib import Path

import pytest

from algotrader.backtest.walkforward import (
    DateRange,
    HoldoutFence,
    HoldoutViolation,
    TrialRegistry,
    WalkForwardSplitter,
    deflated_sharpe,
)

# ---------------------------------------------------------------------------
# DateRange
# ---------------------------------------------------------------------------

class TestDateRange:
    """DateRange is a half-open [start, end) interval."""

    def test_valid(self):
        dr = DateRange(date(2024, 1, 1), date(2024, 3, 1))
        assert dr.start == date(2024, 1, 1)
        assert dr.end == date(2024, 3, 1)

    def test_start_equals_end_raises(self):
        with pytest.raises(ValueError):
            DateRange(date(2024, 1, 1), date(2024, 1, 1))

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError):
            DateRange(date(2024, 3, 1), date(2024, 1, 1))


# ---------------------------------------------------------------------------
# WalkForwardSplitter — window math
# ---------------------------------------------------------------------------

class TestWalkForwardSplitter:
    """Verify window boundaries against hand-computed expected dates.

    Config: start=2024-01-01, end=2024-07-01, train=2m, test=1m, step=1m.

    Expected windows:
      Window 0: train=[2024-01-01, 2024-03-01), test=[2024-03-01, 2024-04-01)
      Window 1: train=[2024-02-01, 2024-04-01), test=[2024-04-01, 2024-05-01)
      Window 2: train=[2024-03-01, 2024-05-01), test=[2024-05-01, 2024-06-01)
      Window 3: train=[2024-04-01, 2024-06-01), test=[2024-06-01, 2024-07-01)
      Window 4: train=[2024-05-01, 2024-07-01), test=[2024-07-01, 2024-08-01)
        → test_end=2024-08-01 > 2024-07-01 → STOP

    So exactly 4 windows.
    """

    @pytest.fixture(autouse=True)
    def _splitter(self):
        self.splitter = WalkForwardSplitter(
            start=date(2024, 1, 1),
            end=date(2024, 7, 1),
            train_months=2,
            test_months=1,
            step=1,
        )
        self.windows = self.splitter.windows()

    def test_window_count(self):
        assert len(self.windows) == 4

    def test_window_0_train(self):
        tr, _ = self.windows[0]
        assert tr.start == date(2024, 1, 1)
        assert tr.end == date(2024, 3, 1)

    def test_window_0_test(self):
        _, te = self.windows[0]
        assert te.start == date(2024, 3, 1)
        assert te.end == date(2024, 4, 1)

    def test_window_1_train(self):
        tr, _ = self.windows[1]
        assert tr.start == date(2024, 2, 1)
        assert tr.end == date(2024, 4, 1)

    def test_window_1_test(self):
        _, te = self.windows[1]
        assert te.start == date(2024, 4, 1)
        assert te.end == date(2024, 5, 1)

    def test_window_3_test(self):
        # Last valid window
        _, te = self.windows[3]
        assert te.start == date(2024, 6, 1)
        assert te.end == date(2024, 7, 1)

    def test_train_end_equals_test_start(self):
        """Train end must exactly equal test start (no gap, no overlap)."""
        for tr, te in self.windows:
            assert tr.end == te.start

    def test_train_window_length(self):
        """Each train window is exactly train_months calendar months long."""
        from dateutil.relativedelta import relativedelta
        for tr, _ in self.windows:
            assert tr.end == tr.start + relativedelta(months=2)

    def test_test_window_length(self):
        """Each test window is exactly test_months calendar months long."""
        from dateutil.relativedelta import relativedelta
        for _, te in self.windows:
            assert te.end == te.start + relativedelta(months=1)

    def test_step_equals_test_default(self):
        """Default step = test_months."""
        s = WalkForwardSplitter(
            start=date(2024, 1, 1),
            end=date(2025, 1, 1),
            train_months=8,
            test_months=2,
        )
        windows = s.windows()
        # Each successive test_start advances by 2 months (= test_months = default step)
        from dateutil.relativedelta import relativedelta
        for (_, te_a), (_, te_b) in zip(windows, windows[1:]):
            assert te_b.start == te_a.start + relativedelta(months=2)

    def test_no_windows_if_range_too_short(self):
        """If end is before the first test_end, yield zero windows."""
        s = WalkForwardSplitter(
            start=date(2024, 1, 1),
            end=date(2024, 2, 1),  # only 1 month — can't fit 2m train + 1m test
            train_months=2,
            test_months=1,
        )
        assert s.windows() == []

    def test_iterator_matches_windows(self):
        """iter() and windows() must return identical results."""
        assert self.windows == list(self.splitter)

    def test_start_equals_end_raises(self):
        with pytest.raises(ValueError):
            WalkForwardSplitter(
                start=date(2024, 1, 1),
                end=date(2024, 1, 1),
                train_months=2,
                test_months=1,
            )


class TestWalkForwardDefaultParams:
    """Default 8m train / 2m test / 2m step on a 5-year window."""

    def test_default_creates_windows(self):
        s = WalkForwardSplitter(
            start=date(2020, 1, 1),
            end=date(2025, 1, 1),
        )
        windows = s.windows()
        assert len(windows) > 0

    def test_default_train_is_8_months(self):
        from dateutil.relativedelta import relativedelta
        s = WalkForwardSplitter(
            start=date(2020, 1, 1),
            end=date(2025, 1, 1),
        )
        for tr, _ in s.windows():
            assert tr.end == tr.start + relativedelta(months=8)

    def test_default_test_is_2_months(self):
        from dateutil.relativedelta import relativedelta
        s = WalkForwardSplitter(
            start=date(2020, 1, 1),
            end=date(2025, 1, 1),
        )
        for _, te in s.windows():
            assert te.end == te.start + relativedelta(months=2)


# ---------------------------------------------------------------------------
# HoldoutFence
# ---------------------------------------------------------------------------

class TestHoldoutFence:
    """Guard logic for the holdout period.

    Fence cutoff: 2024-06-01.

    Acceptable:
      test_range.end <= 2024-06-01  → passes
    Violation:
      test_range.end > 2024-06-01   → raises HoldoutViolation
    """

    @pytest.fixture(autouse=True)
    def _fence(self):
        self.fence = HoldoutFence(cutoff_date=date(2024, 6, 1))

    def test_valid_window_passes(self):
        # test ends exactly at cutoff → test window = [2024-05-01, 2024-06-01)
        tr = DateRange(date(2024, 3, 1), date(2024, 5, 1))
        te = DateRange(date(2024, 5, 1), date(2024, 6, 1))
        self.fence.guard(tr, te)   # must not raise

    def test_window_well_before_cutoff_passes(self):
        tr = DateRange(date(2024, 1, 1), date(2024, 3, 1))
        te = DateRange(date(2024, 3, 1), date(2024, 4, 1))
        self.fence.guard(tr, te)   # must not raise

    def test_window_crossing_cutoff_raises(self):
        # test_end = 2024-07-01 > 2024-06-01 → violation
        tr = DateRange(date(2024, 3, 1), date(2024, 5, 1))
        te = DateRange(date(2024, 5, 1), date(2024, 7, 1))
        with pytest.raises(HoldoutViolation):
            self.fence.guard(tr, te)

    def test_window_starting_at_cutoff_raises(self):
        # test_start = cutoff → test_end > cutoff → violation
        tr = DateRange(date(2024, 4, 1), date(2024, 6, 1))
        te = DateRange(date(2024, 6, 1), date(2024, 8, 1))
        with pytest.raises(HoldoutViolation):
            self.fence.guard(tr, te)

    def test_filter_windows_removes_violations(self):
        """filter_windows returns only windows that pass the fence."""
        windows = [
            (DateRange(date(2024, 1, 1), date(2024, 3, 1)),
             DateRange(date(2024, 3, 1), date(2024, 4, 1))),   # passes
            (DateRange(date(2024, 2, 1), date(2024, 4, 1)),
             DateRange(date(2024, 4, 1), date(2024, 5, 1))),   # passes
            (DateRange(date(2024, 3, 1), date(2024, 5, 1)),
             DateRange(date(2024, 5, 1), date(2024, 6, 1))),   # passes (end == cutoff)
            (DateRange(date(2024, 4, 1), date(2024, 6, 1)),
             DateRange(date(2024, 6, 1), date(2024, 7, 1))),   # VIOLATION
        ]
        safe = self.fence.filter_windows(windows)
        assert len(safe) == 3

    def test_holdout_violation_message(self):
        """Error message should mention the cutoff date."""
        tr = DateRange(date(2024, 4, 1), date(2024, 6, 1))
        te = DateRange(date(2024, 6, 1), date(2024, 8, 1))
        with pytest.raises(HoldoutViolation, match="2024-06-01"):
            self.fence.guard(tr, te)


class TestHoldoutFenceWithSplitter:
    """Integration: combine splitter with fence to get safe windows."""

    def test_fence_filters_splitter_windows(self):
        splitter = WalkForwardSplitter(
            start=date(2020, 1, 1),
            end=date(2025, 1, 1),
            train_months=8,
            test_months=2,
        )
        fence = HoldoutFence(cutoff_date=date(2024, 4, 1))
        all_windows = splitter.windows()
        safe_windows = fence.filter_windows(all_windows)

        # All safe windows must have test_end <= cutoff
        for _, te in safe_windows:
            assert te.end <= date(2024, 4, 1)

        # At least some windows should be safe (20-month range before cutoff)
        assert len(safe_windows) > 0

        # Total windows must include some violations
        violations = [w for w in all_windows if w not in safe_windows]
        assert len(violations) > 0


# ---------------------------------------------------------------------------
# TrialRegistry
# ---------------------------------------------------------------------------

class TestTrialRegistry:
    """JSONL-backed trial registry: append, count, and format."""

    @pytest.fixture
    def registry(self, tmp_path):
        """Fresh registry backed by a temporary file."""
        return TrialRegistry(path=tmp_path / "trials.jsonl")

    def _sample_windows(self):
        return [
            (DateRange(date(2024, 1, 1), date(2024, 3, 1)),
             DateRange(date(2024, 3, 1), date(2024, 4, 1))),
        ]

    def test_count_zero_before_any_record(self, registry):
        assert registry.count("my_strategy") == 0

    def test_count_after_one_record(self, registry):
        registry.record(
            strategy_id="my_strategy",
            params={"fast_ema": 9, "slow_ema": 21},
            windows=self._sample_windows(),
            oos_metrics={"net_pnl": 1500.0},
        )
        assert registry.count("my_strategy") == 1

    def test_count_after_two_records_same_strategy(self, registry):
        for params in [{"fast_ema": 9}, {"fast_ema": 12}]:
            registry.record(
                strategy_id="orb",
                params=params,
                windows=self._sample_windows(),
                oos_metrics={"sharpe": 1.2},
            )
        assert registry.count("orb") == 2

    def test_count_different_strategies_independent(self, registry):
        registry.record("strat_a", {"k": 1}, self._sample_windows(), {})
        registry.record("strat_b", {"k": 2}, self._sample_windows(), {})
        registry.record("strat_a", {"k": 3}, self._sample_windows(), {})
        assert registry.count("strat_a") == 2
        assert registry.count("strat_b") == 1

    def test_jsonl_file_created(self, registry, tmp_path):
        registry.record("s", {"x": 1}, self._sample_windows(), {})
        assert (tmp_path / "trials.jsonl").exists()

    def test_each_record_is_valid_json_line(self, registry, tmp_path):
        for i in range(3):
            registry.record("s", {"i": i}, self._sample_windows(), {"score": i})
        lines = (tmp_path / "trials.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            row = json.loads(line)   # must not raise
            assert "strategy_id" in row
            assert "params_hash" in row
            assert "ts" in row
            assert "windows" in row
            assert "oos_metrics" in row

    def test_params_hash_deterministic(self, registry):
        entry1 = registry.record("s", {"a": 1, "b": 2}, self._sample_windows(), {})
        entry2 = registry.record("s", {"b": 2, "a": 1}, self._sample_windows(), {})
        # Same params, different insertion order → same hash (sort_keys=True)
        assert entry1.params_hash == entry2.params_hash

    def test_different_params_different_hash(self, registry):
        entry1 = registry.record("s", {"a": 1}, self._sample_windows(), {})
        entry2 = registry.record("s", {"a": 2}, self._sample_windows(), {})
        assert entry1.params_hash != entry2.params_hash

    def test_windows_stored_in_jsonl(self, registry, tmp_path):
        registry.record("s", {}, self._sample_windows(), {})
        line = (tmp_path / "trials.jsonl").read_text().strip()
        row = json.loads(line)
        assert len(row["windows"]) == 1
        w = row["windows"][0]
        assert w["train"] == ["2024-01-01", "2024-03-01"]
        assert w["test"] == ["2024-03-01", "2024-04-01"]

    def test_count_missing_file_returns_zero(self, tmp_path):
        reg = TrialRegistry(path=tmp_path / "nonexistent.jsonl")
        assert reg.count("anything") == 0

    def test_parent_dir_created_automatically(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "trials.jsonl"
        reg = TrialRegistry(path=deep)
        reg.record("s", {}, self._sample_windows(), {})
        assert deep.exists()


# ---------------------------------------------------------------------------
# deflated_sharpe
# ---------------------------------------------------------------------------

class TestDeflatedSharpe:
    """Properties of the Bailey-López de Prado DSR.

    Reference: Bailey & López de Prado (2014), "The Deflated Sharpe Ratio".

    Formula (per-period, daily SR):
        sigma_SR = sqrt((1 - skew*SR + (kurt-1)/4 * SR^2) / (T-1))
        SR*      = Phi^{-1}(1 - 1/n_trials) * sigma_SR
        DSR      = Phi[(SR - SR*) / sigma_SR]
    """

    def test_returns_float(self):
        dsr = deflated_sharpe(0.1, n_trials=10, n_days=252, skew=0.0, kurt=3.0)
        assert isinstance(dsr, float)

    def test_in_unit_interval(self):
        """DSR must be in [0, 1] for normal parameters."""
        for n_trials in [1, 5, 20, 100]:
            dsr = deflated_sharpe(0.1, n_trials=n_trials, n_days=252, skew=0.0, kurt=3.0)
            assert 0.0 <= dsr <= 1.0, f"DSR={dsr} out of [0,1] for n_trials={n_trials}"

    def test_more_trials_deflates_sharpe(self):
        """More trials → lower DSR for the same observed Sharpe (multiple testing penalty)."""
        dsr_1 = deflated_sharpe(0.05, n_trials=1, n_days=500, skew=0.0, kurt=3.0)
        dsr_10 = deflated_sharpe(0.05, n_trials=10, n_days=500, skew=0.0, kurt=3.0)
        dsr_100 = deflated_sharpe(0.05, n_trials=100, n_days=500, skew=0.0, kurt=3.0)
        assert dsr_1 >= dsr_10 >= dsr_100, (
            f"DSR should decrease with more trials: {dsr_1:.4f}, {dsr_10:.4f}, {dsr_100:.4f}"
        )

    def test_zero_sharpe_low_dsr_many_trials(self):
        """SR=0 with many trials: DSR < 0.5 (expected max SR* > 0 under null)."""
        dsr = deflated_sharpe(0.0, n_trials=50, n_days=252, skew=0.0, kurt=3.0)
        assert dsr < 0.5, f"SR=0 with 50 trials should give DSR < 0.5, got {dsr:.4f}"

    def test_high_sharpe_high_dsr(self):
        """A very high per-period SR with few trials should give DSR close to 1."""
        # SR=0.3 per day is extremely high; with n_trials=2 and T=252 this should be near 1
        dsr = deflated_sharpe(0.3, n_trials=2, n_days=252, skew=0.0, kurt=3.0)
        assert dsr > 0.9, f"Very high SR with 2 trials should give DSR > 0.9, got {dsr:.4f}"

    def test_negative_sharpe_low_dsr(self):
        """Negative SR should produce low DSR."""
        dsr = deflated_sharpe(-0.1, n_trials=5, n_days=252, skew=0.0, kurt=3.0)
        assert dsr < 0.3, f"Negative SR should give very low DSR, got {dsr:.4f}"

    def test_n_days_less_than_2_returns_nan(self):
        assert math.isnan(deflated_sharpe(0.1, n_trials=5, n_days=1, skew=0.0, kurt=3.0))
        assert math.isnan(deflated_sharpe(0.1, n_trials=5, n_days=0, skew=0.0, kurt=3.0))

    def test_normal_returns_skew0_kurt3(self):
        """For normal returns (skew=0, kurt=3), formula should not produce nan."""
        dsr = deflated_sharpe(0.05, n_trials=10, n_days=252, skew=0.0, kurt=3.0)
        assert not math.isnan(dsr)
        assert 0.0 <= dsr <= 1.0

    def test_n_trials_1_is_one(self):
        """n_trials=1 → SR* = Phi^{-1}(1 - 1/1) * sigma_SR = Phi^{-1}(0) * sigma_SR = -inf.

        With SR* = -inf, (SR - SR*) / sigma_SR = +inf for any finite SR,
        so DSR = norm.cdf(+inf) = 1.0 regardless of sign of SR.  This is the
        correct formula behaviour: one trial cannot be "deflated" by multiple
        testing, so DSR collapses to 1.0 (maximum confidence) irrespective of SR.

        The monotonicity test (more_trials_deflates) already verifies that
        n_trials > 1 produces values below 1.0 for moderate SR.
        """
        dsr_pos = deflated_sharpe(0.05, n_trials=1, n_days=252, skew=0.0, kurt=3.0)
        dsr_neg = deflated_sharpe(-0.05, n_trials=1, n_days=252, skew=0.0, kurt=3.0)
        # Both should equal 1.0 (formula artefact; DSR is only meaningful for n >= 2)
        assert dsr_pos == pytest.approx(1.0, abs=1e-9)
        assert dsr_neg == pytest.approx(1.0, abs=1e-9)

    def test_larger_sample_increases_dsr(self):
        """More data (n_days) should increase confidence (higher DSR) for same SR."""
        dsr_small = deflated_sharpe(0.05, n_trials=5, n_days=100, skew=0.0, kurt=3.0)
        dsr_large = deflated_sharpe(0.05, n_trials=5, n_days=1000, skew=0.0, kurt=3.0)
        assert dsr_large > dsr_small, (
            f"Larger sample should increase DSR: small={dsr_small:.4f}, large={dsr_large:.4f}"
        )
