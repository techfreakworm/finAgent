"""Tests for scripts/build_breadth.py — breadth dataset correctness.

Tests:
1. CAUSALITY PROPERTY — truncating future bars must not change earlier rows.
   Uses a 3-symbol synthetic fixture constructed inline.
2. SANITY on the real output file — existence, ts monotone-per-day,
   pct_above_vwap in [0, 1], n_stocks >= 40 for recent sessions.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from scripts.build_breadth import (  # noqa: E402
    DECISION_MINS,
    compute_breadth,
    rows_to_breadth_df,
)

IST = ZoneInfo("Asia/Kolkata")
BREADTH_PATH = PROJECT / "data" / "cache" / "_BREADTH" / "5m" / "breadth.parquet"


# ── synthetic fixture helpers ─────────────────────────────────────────────

def _make_session(
    symbol: str,
    session_date: date,
    n_bars: int = 374,        # 09:15..15:28 inclusive
    base_price: float = 100.0,
    base_vol: int = 1000,
) -> pd.DataFrame:
    """Minimal 1-min session for one symbol."""
    start = pd.Timestamp(
        datetime(session_date.year, session_date.month, session_date.day, 9, 15),
        tz=IST,
    )
    ts = [start + pd.Timedelta(minutes=i) for i in range(n_bars)]
    # Slightly trending price so VWAP and advance signals differ
    prices = base_price + np.linspace(0, 5, n_bars)
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": np.concatenate([[base_price], prices[:-1]]),
            "close": prices,
            "volume": np.full(n_bars, base_vol),
            "symbol": symbol,
        }
    )
    return df


def _make_fixture(session_date: date = date(2024, 3, 5)) -> pd.DataFrame:
    """Three fake symbols, one session."""
    sym_a = _make_session("SYM_A", session_date, base_price=100.0)
    sym_b = _make_session("SYM_B", session_date, base_price=200.0)
    sym_c = _make_session("SYM_C", session_date, base_price=300.0)
    return pd.concat([sym_a, sym_b, sym_c], ignore_index=True)


# ── Test 1: causality property ────────────────────────────────────────────

class TestCausalityProperty:
    """Truncating future bars must not alter already-computed breadth rows."""

    def test_truncate_future_bars_does_not_change_earlier_rows(self) -> None:
        sess_date = date(2024, 3, 5)
        full_df = _make_fixture(sess_date)

        # Compute breadth on the full dataset
        raw_full = compute_breadth(full_df)
        full_breadth = rows_to_breadth_df(raw_full)

        # Pick a cut-off time in the middle of the session: 11:30
        CUT_TMIN = 11 * 60 + 30
        cut_ts = pd.Timestamp(
            datetime(sess_date.year, sess_date.month, sess_date.day, 11, 30), tz=IST
        )

        # Truncate: remove all bars at/after cut_ts
        trunc_df = full_df[full_df["ts"] < cut_ts].copy()
        raw_trunc = compute_breadth(trunc_df)
        trunc_breadth = rows_to_breadth_df(raw_trunc)

        # All rows with ts <= cut_ts in the full result must match the truncated result
        # (The cut point 11:30 IS a decision time, so it should appear in both)
        early_full = full_breadth[full_breadth["ts"] <= cut_ts].sort_values("ts")
        early_trunc = trunc_breadth[trunc_breadth["ts"] <= cut_ts].sort_values("ts")

        assert len(early_full) > 0, "fixture should produce rows before cut"
        assert len(early_full) == len(early_trunc), (
            "same number of early rows expected"
        )

        for col in ("pct_above_vwap", "adv_frac", "net_breadth", "n_stocks"):
            full_vals = early_full[col].values
            trunc_vals = early_trunc[col].values
            assert np.allclose(full_vals, trunc_vals, atol=1e-9), (
                f"column {col!r} differs after truncating future bars:\n"
                f"full={full_vals}\ntrunc={trunc_vals}"
            )

    def test_no_row_before_first_bar_close(self) -> None:
        """Decision at 09:20 needs the 09:15 bar; earlier decisions should be empty."""
        sess_date = date(2024, 3, 6)
        df = _make_fixture(sess_date)

        raw = compute_breadth(df)
        breadth = rows_to_breadth_df(raw)

        ts_930 = pd.Timestamp(
            datetime(sess_date.year, sess_date.month, sess_date.day, 9, 30), tz=IST
        )
        # All ts must be >= 09:20 (no earlier decision points exist)
        assert (breadth["ts"].dt.time >= pd.Timestamp("09:20").time()).all()

        # At 09:30 each symbol has bars 09:15, 09:16, ..., 09:29 available (15 bars)
        row_930 = breadth[breadth["ts"] == ts_930]
        assert len(row_930) == 1
        assert row_930.iloc[0]["n_stocks"] == 3

    def test_values_are_in_unit_interval(self) -> None:
        sess_date = date(2024, 3, 7)
        df = _make_fixture(sess_date)
        raw = compute_breadth(df)
        breadth = rows_to_breadth_df(raw)

        assert ((breadth["pct_above_vwap"] >= 0) & (breadth["pct_above_vwap"] <= 1)).all()
        assert ((breadth["adv_frac"] >= 0) & (breadth["adv_frac"] <= 1)).all()
        assert ((breadth["net_breadth"] >= -1) & (breadth["net_breadth"] <= 1)).all()

    def test_net_breadth_formula(self) -> None:
        """net_breadth == 2*adv_frac - 1 everywhere."""
        sess_date = date(2024, 3, 8)
        df = _make_fixture(sess_date)
        raw = compute_breadth(df)
        breadth = rows_to_breadth_df(raw)

        expected = 2.0 * breadth["adv_frac"].values - 1.0
        assert np.allclose(breadth["net_breadth"].values, expected, atol=1e-6)


# ── Test 2: sanity on the real output file ────────────────────────────────

@pytest.fixture(scope="module")
def breadth_output() -> pd.DataFrame:
    """Load the real breadth.parquet; skip if not present."""
    if not BREADTH_PATH.exists():
        pytest.skip("breadth.parquet not yet built — run scripts/build_breadth.py first")
    df = pd.read_parquet(BREADTH_PATH)
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize(IST)
    else:
        df["ts"] = df["ts"].dt.tz_convert(IST)
    return df


class TestRealOutput:
    def test_file_exists(self) -> None:
        assert BREADTH_PATH.exists(), f"Output file missing: {BREADTH_PATH}"

    def test_ts_strictly_increasing_per_day(self, breadth_output: pd.DataFrame) -> None:
        """Within each session date ts must be strictly increasing."""
        df = breadth_output.copy()
        df["date"] = df["ts"].dt.date
        for d, grp in df.groupby("date"):
            ts_vals = grp["ts"].values
            diffs = np.diff(ts_vals.astype(np.int64))
            assert (diffs > 0).all(), f"Non-monotone ts on session {d}"

    def test_pct_above_vwap_in_unit_interval(self, breadth_output: pd.DataFrame) -> None:
        col = breadth_output["pct_above_vwap"]
        assert col.between(0.0, 1.0).all(), (
            f"pct_above_vwap out of [0,1]: min={col.min():.4f}, max={col.max():.4f}"
        )

    def test_adv_frac_in_unit_interval(self, breadth_output: pd.DataFrame) -> None:
        col = breadth_output["adv_frac"]
        assert col.between(0.0, 1.0).all(), (
            f"adv_frac out of [0,1]: min={col.min():.4f}, max={col.max():.4f}"
        )

    def test_n_stocks_gte_40_for_recent_sessions(
        self, breadth_output: pd.DataFrame
    ) -> None:
        """Most recent 30 unique session dates should have n_stocks >= 40."""
        df = breadth_output.copy()
        df["date"] = df["ts"].dt.date
        recent_dates = sorted(df["date"].unique())[-30:]
        recent = df[df["date"].isin(recent_dates)]
        # Allow a small fraction of rows to dip below 40 (e.g. early in session)
        frac_ok = (recent["n_stocks"] >= 40).mean()
        assert frac_ok >= 0.95, (
            f"Only {frac_ok:.1%} of recent rows have n_stocks>=40 (expected >=95%)"
        )

    def test_decision_times_within_session_bounds(
        self, breadth_output: pd.DataFrame
    ) -> None:
        """All ts values should be in [09:20, 15:25] with 5-min spacing."""
        times = breadth_output["ts"].dt.time.unique()
        minutes = sorted({t.hour * 60 + t.minute for t in times})
        expected = list(range(9 * 60 + 20, 15 * 60 + 26, 5))
        # All observed minutes must be valid decision minutes
        unexpected = set(minutes) - set(expected)
        assert not unexpected, f"Unexpected decision times: {unexpected}"

    def test_row_count_reasonable(self, breadth_output: pd.DataFrame) -> None:
        """Roughly 74 rows per session date; at least 50,000 rows total."""
        assert len(breadth_output) >= 50_000, (
            f"Too few rows: {len(breadth_output):,}"
        )

    def test_net_breadth_formula_holds(self, breadth_output: pd.DataFrame) -> None:
        expected = 2.0 * breadth_output["adv_frac"].values - 1.0
        actual = breadth_output["net_breadth"].values.astype(np.float64)
        assert np.allclose(actual, expected, atol=1e-4), (
            "net_breadth != 2*adv_frac - 1 in real output"
        )
