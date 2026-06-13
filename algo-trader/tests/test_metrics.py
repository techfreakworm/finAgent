"""Tests for algotrader/backtest/metrics.py.

All test values are hand-computed with inline documentation so they can be
independently verified.  TradeRecord objects are constructed directly from
synthetic Positions and CostBreakdowns — no backtest engine required.

ARCHITECTURE §3.7: metrics must be functions over lists of TradeRecord /
SessionResult with no look-ahead (they consume post-hoc closed trades only).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from algotrader.backtest.metrics import (
    avg_loss,
    avg_win,
    cost_share_of_gross,
    daily_pnl_gini,
    max_drawdown,
    net_pnl,
    profit_factor,
    sharpe_daily,
    time_of_day_pnl_buckets,
    win_rate,
)
from algotrader.core import (
    CostBreakdown,
    ExitReason,
    Instrument,
    Position,
    ProductType,
    Segment,
    SessionResult,
    Side,
    TradeRecord,
)

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Helpers: instrument and trade constructors
# ---------------------------------------------------------------------------

_INSTR = Instrument(
    symbol="TESTSYM",
    security_id="99901",
    segment=Segment.NSE_EQ,
    tick_size=0.05,
    is_derivative=False,
)

_BASE_DATE = date(2024, 1, 2)
_BASE_ENTRY_TS = datetime(2024, 1, 2, 9, 30, tzinfo=IST)
_BASE_EXIT_TS = datetime(2024, 1, 2, 10, 0, tzinfo=IST)

_ZERO_COSTS = CostBreakdown(
    brokerage=0.0, stt=0.0, exchange_txn=0.0,
    sebi=0.0, stamp=0.0, ipft=0.0, gst=0.0,
)


def _make_trade(
    gross_pnl: float,
    *,
    costs_total: float = 0.0,
    slippage: float = 0.0,
    side: Side = Side.BUY,
    entry_ts: datetime | None = None,
    session_date: date | None = None,
    entry_price: float = 500.0,
    quantity: int = 100,
) -> TradeRecord:
    """Build a TradeRecord with controlled net_pnl for metric tests.

    gross_pnl = sgn * (exit - entry) * qty.
    For BUY: exit = entry + gross_pnl / qty.
    """
    if entry_ts is None:
        entry_ts = _BASE_ENTRY_TS
    if session_date is None:
        session_date = _BASE_DATE

    sign = 1.0 if side is Side.BUY else -1.0
    exit_price = entry_price + sign * gross_pnl / quantity

    pos = Position(
        position_id="p_test",
        strategy_id="test_strategy",
        instrument=_INSTR,
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        entry_ts=entry_ts,
        stop_price=entry_price - 20.0 if side is Side.BUY else entry_price + 20.0,
        target_price=None,
        session_date=session_date,
        product=ProductType.INTRADAY,
        exit_price=exit_price,
        exit_ts=_BASE_EXIT_TS,
        exit_reason=ExitReason.TARGET,
    )

    # Distribute costs_total across brokerage (all other components zero)
    costs = CostBreakdown(
        brokerage=costs_total,
        stt=0.0, exchange_txn=0.0, sebi=0.0,
        stamp=0.0, ipft=0.0, gst=0.0,
    )
    return TradeRecord(position=pos, costs=costs, slippage_paid=slippage)


# ---------------------------------------------------------------------------
# net_pnl
# ---------------------------------------------------------------------------

class TestNetPnl:
    """net_pnl sums TradeRecord.net_pnl across all trades.

    TradeRecord.net_pnl = gross_pnl - costs.total (slippage is embedded
    in fill prices; slippage_paid is attribution metadata only — contract v1.1).
    """

    def test_empty(self):
        assert net_pnl([]) == 0.0

    def test_single_win(self):
        # gross=1000, costs=63.55 → net=936.45 (slip metadata only)
        t = _make_trade(1000.0, costs_total=63.55, slippage=5.0)
        assert net_pnl([t]) == pytest.approx(936.45, abs=1e-9)

    def test_single_loss(self):
        # gross=-500, costs=63.0 → net=-563.0 (slip metadata only)
        t = _make_trade(-500.0, costs_total=63.0, slippage=5.0)
        assert net_pnl([t]) == pytest.approx(-563.0, abs=1e-9)

    def test_multiple_trades(self):
        # win: net=936.45, loss: net=-563.0 → total=373.45
        t1 = _make_trade(1000.0, costs_total=63.55, slippage=5.0)
        t2 = _make_trade(-500.0, costs_total=63.0, slippage=5.0)
        assert net_pnl([t1, t2]) == pytest.approx(373.45, abs=1e-9)

    def test_zero_costs(self):
        # gross = net when costs+slip=0
        t = _make_trade(250.0)
        assert net_pnl([t]) == pytest.approx(250.0, abs=1e-9)

    def test_additivity(self):
        trades = [_make_trade(float(100 * (i + 1))) for i in range(5)]
        expected = sum(100.0 * (i + 1) for i in range(5))
        assert net_pnl(trades) == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# win_rate, avg_win, avg_loss
# ---------------------------------------------------------------------------

class TestWinRateAndAverages:
    """Hand-computed win/loss metrics.

    Trades (all zero costs, using gross = net):
      Trade 1: gross = +100  (win)
      Trade 2: gross = +200  (win)
      Trade 3: gross = +150  (win)
      Trade 4: gross = -80   (loss)
      Trade 5: gross = -50   (loss)

    win_rate     = 3/5 = 0.6
    avg_win      = (100+200+150)/3 = 150.0
    avg_loss     = (-80-50)/2 = -65.0
    """

    @pytest.fixture(autouse=True)
    def _trades(self):
        self.trades = [
            _make_trade(100.0),
            _make_trade(200.0),
            _make_trade(150.0),
            _make_trade(-80.0),
            _make_trade(-50.0),
        ]

    def test_win_rate(self):
        assert win_rate(self.trades) == pytest.approx(0.6, abs=1e-9)

    def test_avg_win(self):
        assert avg_win(self.trades) == pytest.approx(150.0, abs=1e-9)

    def test_avg_loss(self):
        assert avg_loss(self.trades) == pytest.approx(-65.0, abs=1e-9)

    def test_empty_win_rate(self):
        assert win_rate([]) == 0.0

    def test_empty_avg_win(self):
        assert avg_win([]) == 0.0

    def test_empty_avg_loss(self):
        assert avg_loss([]) == 0.0

    def test_all_wins_no_avg_loss(self):
        wins = [_make_trade(100.0), _make_trade(200.0)]
        assert win_rate(wins) == pytest.approx(1.0, abs=1e-9)
        assert avg_loss(wins) == 0.0

    def test_all_losses_no_avg_win(self):
        losses = [_make_trade(-100.0), _make_trade(-200.0)]
        assert win_rate(losses) == 0.0
        assert avg_win(losses) == 0.0

    def test_avg_loss_is_negative(self):
        losses = [_make_trade(-100.0), _make_trade(-200.0)]
        assert avg_loss(losses) < 0.0


# ---------------------------------------------------------------------------
# profit_factor with bootstrap CI
# ---------------------------------------------------------------------------

class TestProfitFactor:
    """Profit factor and 95% bootstrap CI.

    Setup (zero costs, gross = net):
      wins:   +100, +200, +150  → gross_wins = 450
      losses: -80, -50          → gross_losses = 130

    PF = 450 / 130 ≈ 3.4615...

    CI must contain the point estimate (sanity property, not statistical guarantee).
    """

    @pytest.fixture(autouse=True)
    def _trades(self):
        self.trades = [
            _make_trade(100.0),
            _make_trade(200.0),
            _make_trade(150.0),
            _make_trade(-80.0),
            _make_trade(-50.0),
        ]

    def test_point_estimate(self):
        pf, _ = profit_factor(self.trades)
        assert pf == pytest.approx(450.0 / 130.0, abs=1e-9)

    def test_ci_contains_point_estimate(self):
        pf, (lo, hi) = profit_factor(self.trades, seed=42)
        assert lo <= pf <= hi, (
            f"CI [{lo:.4f}, {hi:.4f}] does not contain PF {pf:.4f}"
        )

    def test_ci_lower_positive(self):
        _, (lo, _) = profit_factor(self.trades, seed=42)
        assert lo > 0.0

    def test_ci_bounds_ordered(self):
        _, (lo, hi) = profit_factor(self.trades, seed=42)
        assert lo <= hi

    def test_2000_resamples_minimum(self):
        """Bootstrap must use >= 2000 resamples (ARCHITECTURE §3.7).

        We verify the lower CI bound is stable between 2000 and 3000 resamples
        (within 5%).  The upper CI may be inf if some resamples contain only
        winners; we only check the finite lower bound here.
        """
        _, (lo_2k, hi_2k) = profit_factor(self.trades, seed=42, n_resamples=2000)
        _, (lo_3k, hi_3k) = profit_factor(self.trades, seed=42, n_resamples=3000)
        # Lower bound must be finite for this mixed win/loss trade set
        assert math.isfinite(lo_2k), f"CI lower should be finite, got {lo_2k}"
        assert math.isfinite(lo_3k), f"CI lower should be finite, got {lo_3k}"
        assert abs(lo_2k - lo_3k) < lo_2k * 0.05   # within 5% of each other

    def test_empty_trades(self):
        pf, (lo, hi) = profit_factor([])
        assert pf == 0.0
        assert lo == 0.0
        assert hi == 0.0

    def test_seeded_reproducible(self):
        r1 = profit_factor(self.trades, seed=99)
        r2 = profit_factor(self.trades, seed=99)
        assert r1 == r2

    def test_different_seeds_may_differ(self):
        _, ci_a = profit_factor(self.trades, seed=1)
        _, ci_b = profit_factor(self.trades, seed=2)
        # CIs may differ with different seeds (they share the same trades here,
        # so they can be close; we just verify both are valid)
        assert ci_a[0] > 0.0
        assert ci_b[0] > 0.0


class TestProfitFactorDegenerateAllWin:
    """Degenerate all-win case: PF = infinity.

    PF is infinity when all trades are winners (no losses).
    Bootstrap CI lower bound should be >= 1.0 (all resamples are all-win).
    """

    @pytest.fixture(autouse=True)
    def _trades(self):
        self.trades = [
            _make_trade(100.0),
            _make_trade(200.0),
            _make_trade(150.0),
        ]

    def test_pf_is_infinity(self):
        pf, _ = profit_factor(self.trades)
        assert math.isinf(pf)

    def test_ci_lower_ge_one(self):
        """With all winners, every bootstrap resample is also all-winner → CI lower ≥ 1."""
        _, (lo, _) = profit_factor(self.trades, seed=42)
        assert lo >= 1.0 or math.isinf(lo), (
            f"CI lower {lo} should be >= 1.0 for all-win case"
        )

    def test_ci_contains_or_matches_point(self):
        """If CI lower = inf = point, the CI 'contains' the point estimate."""
        pf, (lo, hi) = profit_factor(self.trades, seed=42)
        if math.isinf(pf):
            # both CI bounds should also be inf (or at least >= PF lower bound)
            assert math.isinf(lo) or lo >= 1.0


class TestProfitFactorAllLoss:
    """All-loss case: PF = 0."""

    def test_pf_all_loss(self):
        trades = [_make_trade(-100.0), _make_trade(-200.0)]
        pf, _ = profit_factor(trades)
        assert pf == 0.0


# ---------------------------------------------------------------------------
# max_drawdown
# ---------------------------------------------------------------------------

class TestMaxDrawdown:
    """Max drawdown on session-level cumulative net P&L.

    Session net_pnls: [100, 200, -50, -150, 50, 100]
    Cumulative:       [100, 300, 250, 100,  150, 250]
    Peak at 300 (after session 2), trough at 100 (after session 4).
    Max drawdown = 300 - 100 = 200.
    """

    def _make_session(self, net: float, d: date | None = None) -> SessionResult:
        if d is None:
            d = _BASE_DATE
        s = SessionResult(session_date=d, net_pnl=net)
        return s

    @pytest.fixture(autouse=True)
    def _sessions(self):
        dates = [
            date(2024, 1, d) for d in [2, 3, 4, 5, 8, 9]
        ]
        pnls = [100.0, 200.0, -50.0, -150.0, 50.0, 100.0]
        self.sessions = [self._make_session(p, d) for p, d in zip(pnls, dates)]

    def test_max_dd_value(self):
        # Cumulative: 100, 300, 250, 100, 150, 250
        # Peak=300, lowest after peak = 100 → dd = 200
        assert max_drawdown(self.sessions) == pytest.approx(200.0, abs=1e-9)

    def test_empty(self):
        assert max_drawdown([]) == 0.0

    def test_monotone_up(self):
        # No drawdown on an ever-increasing equity curve
        sessions = [self._make_session(100.0, date(2024, 1, d)) for d in range(2, 8)]
        assert max_drawdown(sessions) == 0.0

    def test_all_losses(self):
        # Cumulative: -100, -200, -300 → peak=0, worst dd=300
        sessions = [self._make_session(-100.0, date(2024, 1, d)) for d in range(2, 5)]
        assert max_drawdown(sessions) == pytest.approx(300.0, abs=1e-9)

    def test_single_session_positive(self):
        # No drawdown if single positive session
        sessions = [self._make_session(500.0)]
        assert max_drawdown(sessions) == 0.0

    def test_single_session_negative(self):
        # Starts at 0, drops to -200 → dd = 200
        sessions = [self._make_session(-200.0)]
        assert max_drawdown(sessions) == pytest.approx(200.0, abs=1e-9)

    def test_recovery_does_not_affect_dd(self):
        # Peak=300, falls to 50, recovers to 400. Worst dd = 300-50 = 250.
        pnls = [100.0, 200.0, -250.0, 350.0]
        sessions = [self._make_session(p, date(2024, 1, d)) for p, d in
                    zip(pnls, [2, 3, 4, 5])]
        # Cumulative: 100, 300, 50, 400
        assert max_drawdown(sessions) == pytest.approx(250.0, abs=1e-9)


# ---------------------------------------------------------------------------
# sharpe_daily
# ---------------------------------------------------------------------------

class TestSharpeDaily:
    """Annualised Sharpe ratio, exposure-days-only and all-days modes.

    Session net_pnls (exposure days): [100, -50, 200, -100, 150]
    With rf=0:
      mean = (100 - 50 + 200 - 100 + 150) / 5 = 300 / 5 = 60.0
      deviations: [40, -110, 140, -160, 90]
      sum_sq = 1600 + 12100 + 19600 + 25600 + 8100 = 67000
      sample_std = sqrt(67000 / 4) = sqrt(16750) ≈ 129.4224...
      daily_sharpe = 60.0 / 129.4224 ≈ 0.46356...
      annualised = daily_sharpe * sqrt(252) ≈ 7.354...
    """

    def _make_sessions(
        self,
        pnls: list[float],
        with_trades: list[bool] | None = None,
    ) -> list[SessionResult]:
        if with_trades is None:
            with_trades = [True] * len(pnls)
        sessions = []
        for i, (p, has_trade) in enumerate(zip(pnls, with_trades)):
            d = date(2024, 1, 2) + timedelta(days=i)
            s = SessionResult(session_date=d, net_pnl=p)
            if has_trade:
                # add a dummy trade so trades is non-empty
                s.trades = [_make_trade(p if p != 0 else 1.0)]
            sessions.append(s)
        return sessions

    def test_sharpe_value_exposure_only(self):
        pnls = [100.0, -50.0, 200.0, -100.0, 150.0]
        sessions = self._make_sessions(pnls)
        mean = 60.0
        sq_diffs = [(x - mean) ** 2 for x in pnls]
        sample_std = math.sqrt(sum(sq_diffs) / 4)
        expected = math.sqrt(252.0) * mean / sample_std
        result = sharpe_daily(sessions, exposure_days_only=True, rf=0.0)
        assert result == pytest.approx(expected, abs=1e-6)

    def test_exposure_days_only_excludes_idle(self):
        """Sessions with no trades are excluded when exposure_days_only=True.

        Idle sessions have zero net_pnl but do not belong in the Sharpe denominator.
        Adding idle sessions should not change the result.
        """
        pnls = [100.0, -50.0, 200.0]
        sessions_active = self._make_sessions(pnls, with_trades=[True, True, True])
        # Add 2 idle sessions (no trades, zero PnL)
        idle_pnls = [0.0, 0.0]
        idle_sessions = self._make_sessions(idle_pnls, with_trades=[False, False])
        sessions_with_idle = sessions_active + idle_sessions

        sharpe_active = sharpe_daily(sessions_active, exposure_days_only=True)
        sharpe_with_idle = sharpe_daily(sessions_with_idle, exposure_days_only=True)
        assert sharpe_active == pytest.approx(sharpe_with_idle, abs=1e-9)

    def test_all_days_includes_idle(self):
        """When exposure_days_only=False, idle sessions are included as zero PnL days."""
        pnls = [100.0, -50.0, 200.0]
        sessions_active = self._make_sessions(pnls, with_trades=[True, True, True])
        idle = self._make_sessions([0.0, 0.0], with_trades=[False, False])
        sessions_with_idle = sessions_active + idle

        sharpe_active = sharpe_daily(sessions_active, exposure_days_only=False)
        sharpe_full = sharpe_daily(sessions_with_idle, exposure_days_only=False)
        # With extra zero-PnL days, mean drops and std changes → different result
        assert sharpe_active != pytest.approx(sharpe_full, abs=0.001)

    def test_rf_adjustment(self):
        """Non-zero rf reduces mean excess return → lower Sharpe."""
        pnls = [100.0, 200.0, 150.0, 300.0, 250.0]
        sessions = self._make_sessions(pnls)
        s0 = sharpe_daily(sessions, exposure_days_only=True, rf=0.0)
        s_rf = sharpe_daily(sessions, exposure_days_only=True, rf=0.05)
        assert s0 > s_rf

    def test_fewer_than_2_returns_nan(self):
        sessions = self._make_sessions([100.0])
        assert math.isnan(sharpe_daily(sessions, exposure_days_only=True))

    def test_empty_returns_nan(self):
        assert math.isnan(sharpe_daily([], exposure_days_only=True))

    def test_constant_returns_nan(self):
        """Constant daily PnL → std=0 → Sharpe undefined (nan)."""
        sessions = self._make_sessions([100.0, 100.0, 100.0])
        assert math.isnan(sharpe_daily(sessions, exposure_days_only=True))


# ---------------------------------------------------------------------------
# daily_pnl_gini
# ---------------------------------------------------------------------------

class TestDailyPnlGini:
    """Gini coefficient of session net P&L (clipped at 0) for edge concentration.

    Standard Gini formula (ascending-sorted, 1-based rank):
        G = (2 * sum(i * x_i)) / (n * sum(x_i)) - (n+1)/n

    Setup: net_pnls = [100, -50, 200, 0, 150]
    Clipped: values = [100, 0, 200, 0, 150], sorted = [0, 0, 100, 150, 200]
    n=5, total=450
    weighted = 1*0 + 2*0 + 3*100 + 4*150 + 5*200 = 0 + 0 + 300 + 600 + 1000 = 1900
    G = (2*1900) / (5*450) - (5+1)/5 = 3800/2250 - 1.2 = 1.6889 - 1.2 = 0.4889
    """

    def _make_sessions(self, pnls: list[float]) -> list[SessionResult]:
        sessions = []
        for i, p in enumerate(pnls):
            d = date(2024, 1, 2) + timedelta(days=i)
            sessions.append(SessionResult(session_date=d, net_pnl=p))
        return sessions

    def test_gini_value(self):
        sessions = self._make_sessions([100.0, -50.0, 200.0, 0.0, 150.0])
        # Clipped: [100, 0, 200, 0, 150] → sorted [0, 0, 100, 150, 200]
        # total=450, weighted = 1*0+2*0+3*100+4*150+5*200 = 1900
        expected = (2 * 1900) / (5 * 450) - 6 / 5
        result = daily_pnl_gini(sessions)
        assert result == pytest.approx(expected, abs=1e-9)

    def test_equal_pnl_gini_zero(self):
        """All sessions with equal positive P&L → Gini = 0 (perfect equality)."""
        sessions = self._make_sessions([100.0, 100.0, 100.0, 100.0])
        # sorted: [100,100,100,100], n=4, total=400
        # weighted = 1*100+2*100+3*100+4*100 = 1000
        # G = (2*1000)/(4*400) - 5/4 = 2000/1600 - 1.25 = 1.25 - 1.25 = 0
        assert daily_pnl_gini(sessions) == pytest.approx(0.0, abs=1e-9)

    def test_concentrated_gini_high(self):
        """One big day, rest near-zero → high Gini (close to 0.8 for n=5)."""
        sessions = self._make_sessions([0.0, 0.0, 0.0, 0.0, 1000.0])
        g = daily_pnl_gini(sessions)
        assert g > 0.6, f"Expected high Gini for concentrated PnL, got {g:.4f}"

    def test_all_losses_gini_zero(self):
        """All negative P&L → clipped to 0 → total=0 → Gini=0."""
        sessions = self._make_sessions([-100.0, -200.0, -50.0])
        assert daily_pnl_gini(sessions) == 0.0

    def test_empty(self):
        assert daily_pnl_gini([]) == 0.0

    def test_single_session(self):
        """Single positive session → Gini = 0 (single element, no inequality)."""
        # sorted: [100], n=1, total=100
        # G = (2*1*100)/(1*100) - 2/1 = 2 - 2 = 0
        sessions = self._make_sessions([100.0])
        assert daily_pnl_gini(sessions) == pytest.approx(0.0, abs=1e-9)

    def test_gini_between_0_and_1(self):
        """Gini must always be in [0, 1]."""
        sessions = self._make_sessions([10.0, 100.0, 50.0, 200.0, 30.0])
        g = daily_pnl_gini(sessions)
        assert 0.0 <= g <= 1.0


# ---------------------------------------------------------------------------
# time_of_day_pnl_buckets
# ---------------------------------------------------------------------------

class TestTimeOfDayPnlBuckets:
    """Trades bucketed by entry time (IST wall clock).

    Bucket definitions (half-open [start, end)):
        "09:15-10:00": 09:15 ≤ t < 10:00
        "10:00-11:00": 10:00 ≤ t < 11:00
        "11:00-12:00": 11:00 ≤ t < 12:00
        ...
    """

    def _trade_at(self, ts: datetime, net: float) -> TradeRecord:
        return _make_trade(net, entry_ts=ts)

    def test_bucket_assignment(self):
        ts_930 = datetime(2024, 1, 2, 9, 30, tzinfo=IST)     # 09:15-10:00
        ts_1030 = datetime(2024, 1, 2, 10, 30, tzinfo=IST)   # 10:00-11:00
        ts_1130 = datetime(2024, 1, 2, 11, 30, tzinfo=IST)   # 11:00-12:00
        trades = [
            self._trade_at(ts_930, 100.0),
            self._trade_at(ts_930, 50.0),   # same bucket, +50
            self._trade_at(ts_1030, 200.0),
            self._trade_at(ts_1130, -30.0),
        ]
        buckets = time_of_day_pnl_buckets(trades)
        assert buckets["09:15-10:00"] == pytest.approx(150.0, abs=1e-9)
        assert buckets["10:00-11:00"] == pytest.approx(200.0, abs=1e-9)
        assert buckets["11:00-12:00"] == pytest.approx(-30.0, abs=1e-9)

    def test_empty_input(self):
        assert time_of_day_pnl_buckets([]) == {}

    def test_bucket_boundary_9h15(self):
        """09:15 is the first minute of the session → 09:15-10:00 bucket."""
        ts_915 = datetime(2024, 1, 2, 9, 15, tzinfo=IST)
        trades = [self._trade_at(ts_915, 75.0)]
        buckets = time_of_day_pnl_buckets(trades)
        assert "09:15-10:00" in buckets
        assert buckets["09:15-10:00"] == pytest.approx(75.0, abs=1e-9)

    def test_bucket_boundary_10h00(self):
        """10:00 is included in 10:00-11:00 (not 09:15-10:00)."""
        ts_1000 = datetime(2024, 1, 2, 10, 0, tzinfo=IST)
        trades = [self._trade_at(ts_1000, 40.0)]
        buckets = time_of_day_pnl_buckets(trades)
        assert "10:00-11:00" in buckets
        assert "09:15-10:00" not in buckets

    def test_all_buckets_separate(self):
        """One trade per bucket → 6 distinct entries."""
        test_times = [
            (9, 30, "09:15-10:00"),
            (10, 30, "10:00-11:00"),
            (11, 30, "11:00-12:00"),
            (12, 30, "12:00-13:00"),
            (13, 30, "13:00-14:00"),
            (14, 30, "14:00-15:30"),
        ]
        trades = [
            self._trade_at(datetime(2024, 1, 2, h, m, tzinfo=IST), float(i * 10))
            for i, (h, m, _) in enumerate(test_times)
        ]
        buckets = time_of_day_pnl_buckets(trades)
        for i, (_, _, label) in enumerate(test_times):
            assert label in buckets
            assert buckets[label] == pytest.approx(float(i * 10), abs=1e-9)


# ---------------------------------------------------------------------------
# cost_share_of_gross
# ---------------------------------------------------------------------------

class TestCostShareOfGross:
    """Cost share = (costs + slippage) / gross_pnl.

    Setup:
      Trade 1: gross=1000, costs=50, slip=10  → friction=60
      Trade 2: gross=500,  costs=25, slip=5   → friction=30
      Trade 3: gross=-200, costs=10, slip=2   → friction=12

    total_friction = 60+30+12 = 102
    total_gross    = 1000+500-200 = 1300
    cost_share     = 102/1300 ≈ 0.07846...
    """

    @pytest.fixture(autouse=True)
    def _trades(self):
        self.t1 = _make_trade(1000.0, costs_total=50.0, slippage=10.0)
        self.t2 = _make_trade(500.0, costs_total=25.0, slippage=5.0)
        self.t3 = _make_trade(-200.0, costs_total=10.0, slippage=2.0)

    def test_cost_share_value(self):
        result = cost_share_of_gross([self.t1, self.t2, self.t3])
        assert result == pytest.approx(102.0 / 1300.0, abs=1e-9)

    def test_empty(self):
        assert cost_share_of_gross([]) == 0.0

    def test_zero_gross_returns_inf(self):
        # Net gross = 0 → inf
        t_balanced = _make_trade(-1000.0)
        t_up = _make_trade(1000.0)
        # gross sum = 0 → inf
        # total costs > 0 → inf
        trades = [
            _make_trade(1000.0, costs_total=10.0),
            _make_trade(-1000.0, costs_total=10.0),
        ]
        result = cost_share_of_gross(trades)
        assert math.isinf(result)

    def test_negative_gross_returns_inf(self):
        trades = [_make_trade(-500.0, costs_total=10.0)]
        assert math.isinf(cost_share_of_gross(trades))

    def test_zero_costs_returns_zero(self):
        trades = [_make_trade(1000.0)]   # costs_total=0, slip=0
        assert cost_share_of_gross(trades) == pytest.approx(0.0, abs=1e-9)

    def test_positive_cost_share_is_in_range(self):
        result = cost_share_of_gross([self.t1, self.t2, self.t3])
        assert 0.0 < result < 1.0
