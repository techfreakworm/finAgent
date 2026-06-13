"""Performance metrics over TradeRecord and SessionResult collections (ARCHITECTURE §3.7).

All functions are pure and stateless: same inputs → same outputs. No I/O.
Bootstrap CI uses seeded resampling (>=2000 draws) so results are reproducible.

Sharpe choice (§3.7): when exposure_days_only=True, Sharpe is computed on sessions
that contain at least one trade, omitting zero-exposure days from both the numerator
and denominator. This is the "trading-days-only" definition, which avoids deflating
Sharpe by idle periods when the strategy is intentionally flat. Zero-exposure sessions
are counted separately by the caller if needed.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime
from typing import Sequence

from algotrader.core import SessionResult, TradeRecord

# ---------------------------------------------------------------------------
# Basic aggregates
# ---------------------------------------------------------------------------


def net_pnl(trades: list[TradeRecord]) -> float:
    """Sum of net P&L across all trades (after costs and slippage).

    See TradeRecord.net_pnl (core.py) for the per-trade formula.
    """
    return sum(t.net_pnl for t in trades)


def win_rate(trades: list[TradeRecord]) -> float:
    """Fraction of trades with positive net P&L.

    Returns 0.0 for empty input.
    """
    if not trades:
        return 0.0
    winners = sum(1 for t in trades if t.net_pnl > 0.0)
    return winners / len(trades)


def avg_win(trades: list[TradeRecord]) -> float:
    """Mean net P&L of winning trades (positive net_pnl).

    Returns 0.0 if there are no winners.
    """
    wins = [t.net_pnl for t in trades if t.net_pnl > 0.0]
    if not wins:
        return 0.0
    return sum(wins) / len(wins)


def avg_loss(trades: list[TradeRecord]) -> float:
    """Mean net P&L of losing trades (negative net_pnl); value is negative.

    Returns 0.0 if there are no losses.
    """
    losses = [t.net_pnl for t in trades if t.net_pnl < 0.0]
    if not losses:
        return 0.0
    return sum(losses) / len(losses)


# ---------------------------------------------------------------------------
# Profit factor with bootstrap CI
# ---------------------------------------------------------------------------

def _pf_from_pnls(pnls: list[float]) -> float:
    """Compute profit factor from a list of per-trade net PnL values.

    Returns math.inf if there are no losing trades (all winners or empty winners).
    Returns 0.0 if there are no winning trades.
    """
    gross_wins = sum(p for p in pnls if p > 0.0)
    gross_loss = sum(p for p in pnls if p < 0.0)   # negative number
    if gross_loss == 0.0:
        return math.inf if gross_wins > 0.0 else 0.0
    return gross_wins / abs(gross_loss)


def profit_factor(
    trades: list[TradeRecord],
    seed: int = 42,
    n_resamples: int = 2000,
) -> tuple[float, tuple[float, float]]:
    """Profit factor with 95% bootstrap confidence interval.

    Resamples trade-level net P&L with replacement (seeded for reproducibility).
    Requires n_resamples >= 2000 per ARCHITECTURE §3.7.

    Returns:
        (point_estimate, (ci_lower, ci_upper)) where the CI is the 2.5th–97.5th
        percentile of the bootstrap distribution of PF.

    Edge cases:
        - Empty trades: returns (0.0, (0.0, 0.0)).
        - All winners: point_estimate = math.inf; CI bounds reflect bootstrap
          distribution (typically all inf → (inf, inf)).
    """
    if not trades:
        return 0.0, (0.0, 0.0)

    pnls = [t.net_pnl for t in trades]
    point_est = _pf_from_pnls(pnls)

    rng = random.Random(seed)
    n = len(pnls)
    bootstrap_pfs: list[float] = []
    for _ in range(n_resamples):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        bootstrap_pfs.append(_pf_from_pnls(sample))

    bootstrap_pfs.sort()
    lo_idx = int(math.ceil(0.025 * n_resamples)) - 1  # nearest-rank 2.5th percentile
    hi_idx = int(math.ceil(0.975 * n_resamples)) - 1
    hi_idx = min(hi_idx, n_resamples - 1)
    ci_lower = bootstrap_pfs[lo_idx]
    ci_upper = bootstrap_pfs[hi_idx]
    return point_est, (ci_lower, ci_upper)


# ---------------------------------------------------------------------------
# Drawdown on session-level cumulative P&L
# ---------------------------------------------------------------------------

def max_drawdown(sessions: list[SessionResult]) -> float:
    """Maximum peak-to-trough drawdown on session-level cumulative net P&L (₹).

    Uses session.net_pnl which must be pre-computed by the backtest engine.
    Returns a non-negative number (magnitude of the worst drawdown).
    Returns 0.0 if sessions is empty or cumulative P&L never drops below a peak.
    """
    if not sessions:
        return 0.0

    peak = 0.0
    cum = 0.0
    worst_dd = 0.0

    for s in sessions:
        cum += s.net_pnl
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > worst_dd:
            worst_dd = dd

    return worst_dd


# ---------------------------------------------------------------------------
# Sharpe ratio (daily)
# ---------------------------------------------------------------------------

def sharpe_daily(
    sessions: list[SessionResult],
    exposure_days_only: bool,
    rf: float = 0.0,
) -> float:
    """Annualised Sharpe ratio computed from session-level net P&L.

    Annualisation factor: sqrt(252) applied to the daily mean/std ratio.
    rf: annualised risk-free rate (e.g. 0.065 for 6.5%); converted to daily by /252.

    exposure_days_only (ARCHITECTURE §3.7):
        True  → compute on sessions with at least one trade; zero-trade sessions
                are excluded from mean and std. Avoids diluting Sharpe with idle
                days when the strategy is intentionally flat.
        False → include all sessions; idle sessions contribute 0 net P&L.

    Returns math.nan if fewer than 2 data points remain after filtering.
    """
    if exposure_days_only:
        daily = [s.net_pnl for s in sessions if s.trades]
    else:
        daily = [s.net_pnl for s in sessions]

    if len(daily) < 2:
        return math.nan

    rf_daily = rf / 252.0
    excess = [d - rf_daily for d in daily]
    n = len(excess)
    mean_e = sum(excess) / n
    variance = sum((x - mean_e) ** 2 for x in excess) / (n - 1)  # sample variance
    if variance <= 0.0:
        return math.nan
    return math.sqrt(252.0) * mean_e / math.sqrt(variance)


# ---------------------------------------------------------------------------
# Gini coefficient — daily P&L edge concentration
# ---------------------------------------------------------------------------

def daily_pnl_gini(sessions: list[SessionResult]) -> float:
    """Gini coefficient of session net P&L clipped at zero.

    Measures edge concentration: a high Gini means most profits come from a
    few sessions (ARCHITECTURE §3.8 kill criterion: Gini below threshold).

    Each session's contribution is max(0, net_pnl); loss sessions count as 0
    because they contribute nothing to the positive edge being measured.
    Returns 0.0 if total positive P&L is zero or sessions is empty.
    """
    if not sessions:
        return 0.0

    values = sorted(max(0.0, s.net_pnl) for s in sessions)
    n = len(values)
    total = sum(values)
    if total == 0.0:
        return 0.0

    # Standard Gini: G = (2 * sum(i * x_i)) / (n * total) - (n + 1) / n
    # where x_i are sorted in ascending order and i is 1-based rank.
    weighted = sum((i + 1) * x for i, x in enumerate(values))  # 1-based: rank = i+1
    return (2.0 * weighted / (n * total)) - (n + 1.0) / n


# ---------------------------------------------------------------------------
# Time-of-day P&L buckets
# ---------------------------------------------------------------------------

# Bucket boundaries: label → (start_hour_minute, end_hour_minute) — half-open [start, end)
_BUCKET_BOUNDARIES: list[tuple[str, int, int, int, int]] = [
    ("09:15-10:00", 9, 15, 10, 0),
    ("10:00-11:00", 10, 0, 11, 0),
    ("11:00-12:00", 11, 0, 12, 0),
    ("12:00-13:00", 12, 0, 13, 0),
    ("13:00-14:00", 13, 0, 14, 0),
    ("14:00-15:30", 14, 0, 15, 30),
]


def _bucket_label(ts: datetime) -> str:
    """Return the bucket label for an IST entry timestamp."""
    hm = ts.hour * 60 + ts.minute
    for label, sh, sm, eh, em in _BUCKET_BOUNDARIES:
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        if start_min <= hm < end_min:
            return label
    return "other"


def time_of_day_pnl_buckets(trades: list[TradeRecord]) -> dict[str, float]:
    """Aggregate net P&L by time-of-day bucket of entry.

    Buckets are half-open [start, end) in IST wall-clock minutes:
      09:15-10:00, 10:00-11:00, 11:00-12:00, 12:00-13:00, 13:00-14:00, 14:00-15:30

    Returns a dict with only the buckets that contain at least one trade.
    Empty input returns {}.
    """
    buckets: dict[str, float] = defaultdict(float)
    for t in trades:
        label = _bucket_label(t.position.entry_ts)
        buckets[label] += t.net_pnl
    return dict(buckets)


# ---------------------------------------------------------------------------
# Cost share of gross P&L
# ---------------------------------------------------------------------------

def cost_share_of_gross(trades: list[TradeRecord]) -> float:
    """Total friction (brokerage+taxes+slippage) as a fraction of total gross P&L.

    Returns math.inf if total gross P&L <= 0 (strategy has negative or zero gross edge).
    Returns 0.0 for empty input.

    A healthy value is typically < 0.30 (costs below 30% of gross edge).
    """
    if not trades:
        return 0.0

    total_costs = sum(t.costs.total + t.slippage_paid for t in trades)
    total_gross = sum(t.position.gross_pnl() for t in trades)

    if total_gross <= 0.0:
        return math.inf
    return total_costs / total_gross
