"""Walk-forward splitting, holdout fence, trial registry, and Deflated Sharpe Ratio.

Implements ARCHITECTURE §3.6 anti-snooping protocol:
  - Rolling 8m/2m train/test windows with step = test_months.
  - HoldoutFence: structurally prevents evaluating holdout data before the
    designated final evaluation.
  - TrialRegistry: durable JSONL log of every (strategy, params, OOS window,
    metrics) combination; trial counts drive DSR reporting.
  - deflated_sharpe: Bailey & López de Prado (2014) DSR — adjusts observed
    Sharpe for the number of strategies/parameter sets tested.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from dateutil.relativedelta import relativedelta
from scipy.stats import norm  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Default registry path (relative to project root; operator can override)
# ---------------------------------------------------------------------------
_DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "reports" / "trial_registry.jsonl"


# ---------------------------------------------------------------------------
# Walk-forward splitter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DateRange:
    """A half-open date interval [start, end) — start inclusive, end exclusive."""
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(f"DateRange: start {self.start} must be before end {self.end}")

    def __repr__(self) -> str:
        return f"DateRange({self.start}, {self.end})"


class WalkForwardSplitter:
    """Rolling walk-forward window generator (ARCHITECTURE §3.6).

    Produces (train_range, test_range) pairs with:
      - Fixed train window of `train_months` months.
      - Fixed test (OOS) window of `test_months` months.
      - Windows advance by `step` months each iteration (default = test_months
        so windows tile without overlap in the OOS dimension).

    The `end` date is the exclusive upper bound of the available data; windows
    are generated while test_range.end <= end.

    Args:
        start:        First date of the in-sample data (inclusive).
        end:          Last exclusive date; no test window extends past here.
        train_months: Length of each training window in calendar months (default 8).
        test_months:  Length of each OOS test window in calendar months (default 2).
        step:         Advance each window by this many months (default = test_months).
    """

    def __init__(
        self,
        start: date,
        end: date,
        train_months: int = 8,
        test_months: int = 2,
        step: int | None = None,
    ) -> None:
        if start >= end:
            raise ValueError(f"start {start} must be before end {end}")
        if train_months < 1:
            raise ValueError(f"train_months must be >= 1, got {train_months}")
        if test_months < 1:
            raise ValueError(f"test_months must be >= 1, got {test_months}")
        self.start = start
        self.end = end
        self.train_months = train_months
        self.test_months = test_months
        self.step = test_months if step is None else step
        if self.step < 1:
            raise ValueError(f"step must be >= 1, got {self.step}")

    def __iter__(self) -> Iterator[tuple[DateRange, DateRange]]:
        """Yield (train_range, test_range) for each rolling window."""
        i = 0
        while True:
            train_start = self.start + relativedelta(months=i * self.step)
            train_end = train_start + relativedelta(months=self.train_months)
            test_start = train_end
            test_end = test_start + relativedelta(months=self.test_months)

            if test_end > self.end:
                break

            yield (
                DateRange(train_start, train_end),
                DateRange(test_start, test_end),
            )
            i += 1

    def windows(self) -> list[tuple[DateRange, DateRange]]:
        """Return all windows as a list (for inspection / logging)."""
        return list(self)


# ---------------------------------------------------------------------------
# Holdout fence
# ---------------------------------------------------------------------------

class HoldoutViolation(Exception):
    """Raised when a window crosses into the protected holdout period."""


class HoldoutFence:
    """Guards the frozen holdout period (ARCHITECTURE §3.6).

    The holdout is the period [cutoff_date, +inf).  Any window whose test
    range extends into the holdout raises HoldoutViolation.  The holdout is
    touched exactly once per strategy — after candidate freeze.

    Args:
        cutoff_date: First date of the holdout. Any test_range.end > cutoff_date
                     is a violation.
    """

    def __init__(self, cutoff_date: date) -> None:
        self.cutoff_date = cutoff_date

    def guard(self, train_range: DateRange, test_range: DateRange) -> None:
        """Raise HoldoutViolation if test_range extends past the cutoff.

        Args:
            train_range: The training DateRange (checked for documentation;
                         violation is only raised on the test window).
            test_range:  The OOS DateRange whose end must not exceed the cutoff.

        Raises:
            HoldoutViolation: if test_range.end > self.cutoff_date.
        """
        if test_range.end > self.cutoff_date:
            raise HoldoutViolation(
                f"Test window [{test_range.start}, {test_range.end}) "
                f"extends into the holdout period starting {self.cutoff_date}. "
                "Touch holdout exactly once, after candidate parameter freeze."
            )

    def filter_windows(
        self,
        windows: list[tuple[DateRange, DateRange]],
    ) -> list[tuple[DateRange, DateRange]]:
        """Return only windows that do not cross the holdout boundary."""
        return [(tr, te) for tr, te in windows if te.end <= self.cutoff_date]


# ---------------------------------------------------------------------------
# Trial registry
# ---------------------------------------------------------------------------

def _params_hash(params: dict) -> str:
    """Deterministic SHA-256 of the sorted JSON serialisation of params."""
    serialised = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()[:16]


@dataclass
class TrialEntry:
    """A single registry entry for one (strategy, params, OOS eval) trial."""
    ts: str                        # ISO-8601 UTC timestamp
    strategy_id: str
    params_hash: str               # first 16 hex chars of SHA-256(params)
    params: dict
    windows: list[dict]            # [{"train": [str, str], "test": [str, str]}, ...]
    oos_metrics: dict              # arbitrary metrics from the OOS evaluation


class TrialRegistry:
    """Durable JSONL appender for trial tracking (ARCHITECTURE §3.6).

    Each call to `record()` appends one JSON line to the registry file.
    The trial count drives DSR context reporting: a strategy with many
    trials warrants heavier deflation of its observed OOS Sharpe.

    DSR N-COUNT (de Prado audit, books pass-1 2026-06-13): parameter variants
    within one strategy FAMILY are correlated, not independent trials. For
    deflated_sharpe(), N = number of distinct families (count_families()),
    NOT raw grid points — raw counts over-deflate; but selection ACROSS
    families must use the family count. Tag every record with family=<id>
    (e.g. 'orb', 'vwap_rev', 'trend_cont', 'breadth_rider', 'zerodte_straddle').

    Args:
        path: Path to the JSONL file. Defaults to
              reports/trial_registry.jsonl inside the project root.
              The parent directory is created on first write.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else _DEFAULT_REGISTRY_PATH

    def record(
        self,
        strategy_id: str,
        params: dict,
        windows: list[tuple[DateRange, DateRange]],
        oos_metrics: dict,
    ) -> TrialEntry:
        """Append one trial to the registry and return the TrialEntry.

        Args:
            strategy_id: Unique identifier for the strategy variant.
            params:       The parameter dict for this trial.
            windows:      The (train_range, test_range) pairs evaluated.
            oos_metrics:  Any metrics from the OOS evaluation (e.g. net_pnl,
                          profit_factor, sharpe, n_trades).

        Returns:
            The TrialEntry written to disk.
        """
        entry = TrialEntry(
            ts=datetime.now(tz=timezone.utc).isoformat(),
            strategy_id=strategy_id,
            params_hash=_params_hash(params),
            params=params,
            windows=[
                {
                    "train": [str(tr.start), str(tr.end)],
                    "test": [str(te.start), str(te.end)],
                }
                for tr, te in windows
            ],
            oos_metrics=oos_metrics,
        )
        self._append(entry)
        return entry

    def count(self, strategy_id: str) -> int:
        """Count the number of trials recorded for `strategy_id`.

        Reads the entire JSONL file each call; suitable for reporting
        (not called in tight loops).  Returns 0 if the file does not exist.
        """
        if not self.path.exists():
            return 0
        total = 0
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("strategy_id") == strategy_id:
                    total += 1
        return total

    def _append(self, entry: TrialEntry) -> None:
        """Write one JSON line to the registry file (atomic per-line append)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": entry.ts,
            "strategy_id": entry.strategy_id,
            "params_hash": entry.params_hash,
            "params": entry.params,
            "windows": entry.windows,
            "oos_metrics": entry.oos_metrics,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio (Bailey & López de Prado, 2014)
# ---------------------------------------------------------------------------

def count_families(registry_path=None) -> int:
    """Distinct strategy families in the trial registry (DSR N per de Prado).
    Falls back to the 'strategy' field for legacy lines without 'family'."""
    import json as _json
    from pathlib import Path as _P
    path = _P(registry_path) if registry_path else (
        _P(__file__).resolve().parent.parent.parent / "reports" / "trial_registry.jsonl")
    fams = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                d = _json.loads(line)
            except ValueError:
                continue
            fams.add(d.get("family") or d.get("strategy") or "?")
    fams.discard("?")
    return max(len(fams), 1)


def deflated_sharpe(
    observed_sharpe: float,
    n_trials: int,
    n_days: int,
    skew: float,
    kurt: float,
) -> float:
    """Deflated Sharpe Ratio (DSR) per Bailey & López de Prado (2014).

    Adjusts the observed in-sample Sharpe for the number of trials tested,
    the non-normality of returns, and the sample size.  DSR = P(SR > SR*)
    where SR* is the expected maximum Sharpe across `n_trials` IID strategies
    under the null hypothesis of zero edge.

    Args:
        observed_sharpe: Per-period (daily) Sharpe ratio of the best strategy,
                         un-annualised (i.e. mean_pnl / std_pnl per day).
        n_trials:        Total number of (strategy, parameter) combinations
                         evaluated against any OOS data.
        n_days:          Number of daily observations (T in the paper).
        skew:            Skewness (third standardised moment) of the daily P&L
                         distribution.
        kurt:            Raw kurtosis (fourth standardised moment; normal = 3).
                         NOT excess kurtosis.

    Returns:
        DSR in [0, 1]: probability that the strategy's Sharpe exceeds SR* under
        repeated testing.  Values < 0.95 suggest the result is not statistically
        significant after correcting for multiple trials.

    Formula (Bailey-LdP §4):
        sigma_SR = sqrt((1 - skew*SR + (kurt-1)/4 * SR^2) / (T-1))
        SR*      = Phi^{-1}(1 - 1/n_trials) * sigma_SR
        DSR      = Phi[(SR - SR*) / sigma_SR]

    Notes:
        - For n_trials == 1, Phi^{-1}(0) = -inf → SR* = -inf → DSR approaches
          the one-sided PSR(0), i.e. P(SR > 0).
        - The formula uses raw kurtosis (normal dist has kurt=3); pass
          (excess_kurt + 3) if your stat library returns excess kurtosis.
    """
    if n_days < 2:
        return math.nan

    sr = observed_sharpe
    t = n_days

    # Variance of the SR estimator (eq. 3 in Bailey-LdP 2014)
    inner = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    if inner <= 0.0:
        # Degenerate: returns are too skewed/peaked for the formula to apply
        return math.nan

    sigma_sr = math.sqrt(inner / (t - 1))

    # Expected maximum SR under the null across n_trials trials
    # Approximation: E[max_n N(0,1)] ≈ Phi^{-1}(1 - 1/n_trials)
    if n_trials < 1:
        return math.nan
    sr_star = norm.ppf(1.0 - 1.0 / n_trials) * sigma_sr

    # DSR = PSR(SR*) = Phi[(SR - SR*) / sigma_SR]
    if sigma_sr == 0.0:
        return math.nan
    return float(norm.cdf((sr - sr_star) / sigma_sr))
