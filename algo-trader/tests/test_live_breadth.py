"""Tests for algotrader/data/live_breadth.py — LiveBreadth class.

Tests
-----
1. GOLD TEST: feed a stored real day's 1-min bars through on_bar and assert
   that every 5-min snapshot MATCHES the pre-computed
   data/cache/_BREADTH/5m/breadth.parquet row (tolerance 1e-6).

2. RESET ON NEW SESSION: verify that state clears cleanly when bars from a
   second date arrive, and that the first session's snapshots are gone.

3. N_STOCKS COUNTING: with missing symbols (only a subset feeds bars), assert
   n_stocks equals the actual contributing count, not the universe size.

4. SEED FROM HISTORY: verify that seed_from_history produces the same snapshots
   as feeding bars manually through on_bar.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

PROJECT = Path(__file__).resolve().parent.parent
CACHE = PROJECT / "data" / "cache"
BREADTH_PARQUET = CACHE / "_BREADTH" / "5m" / "breadth.parquet"
NIFTY50_MAP = Path("/home/ubuntu/projects/algo-trader/data/reference/nifty50_secid_map.json")

IST = ZoneInfo("Asia/Kolkata")

# The decision times that LiveBreadth computes (09:20 … 15:25, 5-min step)
DECISION_MINS = tuple(range(9 * 60 + 20, 15 * 60 + 26, 5))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXCLUDE = frozenset({"NIFTY_50", "INDIA_VIX", "NIFTYBEES"})


def _equity_symbols() -> list[str]:
    """NIFTY-50 equity symbols from the mapping file; sorted."""
    if not NIFTY50_MAP.exists():
        pytest.skip("NIFTY-50 mapping file not found — test requires finAgent data")
    mapping: dict[str, int] = json.loads(NIFTY50_MAP.read_text())
    return sorted(s for s in mapping if s not in _EXCLUDE)


def _load_1m_for_date(symbols: list[str], target_date: date) -> pd.DataFrame:
    """Load and concatenate 1-min bars for *symbols* on *target_date*."""
    ym = target_date.strftime("%Y-%m")
    frames: list[pd.DataFrame] = []
    for sym in symbols:
        p = CACHE / sym / "1m" / f"{ym}.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p, columns=["ts", "open", "close", "volume"])
        except Exception:
            continue
        if df["ts"].dt.tz is None:
            df["ts"] = df["ts"].dt.tz_localize(IST)
        else:
            df["ts"] = df["ts"].dt.tz_convert(IST)
        day_mask = df["ts"].dt.date == target_date
        df = df.loc[day_mask].copy()
        if df.empty:
            continue
        df["symbol"] = sym
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["ts", "open", "close", "volume", "symbol"])
    big = pd.concat(frames, ignore_index=True)
    return big.sort_values(["ts", "symbol"]).reset_index(drop=True)


def _make_stub_bar(symbol: str, ts_open: datetime, open_px: float,
                   close_px: float, volume: int) -> "Bar":
    """Create a minimal core.Bar for feeding into LiveBreadth.on_bar."""
    from algotrader.core import Bar, Instrument, Segment
    instr = Instrument(
        symbol=symbol,
        security_id="0",
        segment=Segment.NSE_EQ,
        tick_size=0.05,
    )
    return Bar(
        instrument=instr,
        ts_open=ts_open,
        interval_min=1,
        open=open_px,
        high=max(open_px, close_px),
        low=min(open_px, close_px),
        close=close_px,
        volume=volume,
        complete=True,
    )


def _feed_bars(lb: "LiveBreadth", df: pd.DataFrame) -> None:
    """Feed every row of *df* into *lb* via on_bar in ts order."""
    from algotrader.data.live_breadth import LiveBreadth  # noqa: F401 (type check)

    for row in df.itertuples(index=False):
        bar = _make_stub_bar(
            symbol=row.symbol,
            ts_open=row.ts,
            open_px=float(row.open),
            close_px=float(row.close),
            volume=int(row.volume),
        )
        lb.on_bar(row.symbol, bar)


def _epoch_for(d: date, t_min: int) -> int:
    """Epoch-second for (d, t_min) in IST."""
    dt = datetime(d.year, d.month, d.day, t_min // 60, t_min % 60, tzinfo=IST)
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# Test 1: Gold test — match breadth.parquet exactly
# ---------------------------------------------------------------------------

class TestGoldMatch:
    """Feed real 1-min bars through LiveBreadth and assert they match
    the pre-computed breadth.parquet rows (tolerance 1e-6)."""

    @pytest.fixture(scope="class")
    def gold_breadth(self) -> pd.DataFrame:
        if not BREADTH_PARQUET.exists():
            pytest.skip("breadth.parquet not found — run scripts/build_breadth.py first")
        df = pd.read_parquet(BREADTH_PARQUET)
        if df["ts"].dt.tz is None:
            df["ts"] = df["ts"].dt.tz_localize(IST)
        else:
            df["ts"] = df["ts"].dt.tz_convert(IST)
        return df

    @pytest.fixture(scope="class")
    def equity_symbols(self) -> list[str]:
        return _equity_symbols()

    # Use 2021-06-28 — 49 symbols with data, 74 gold rows
    TEST_DATE = date(2021, 6, 28)

    @pytest.fixture(scope="class")
    def bars_df(self, equity_symbols: list[str]) -> pd.DataFrame:
        df = _load_1m_for_date(equity_symbols, self.TEST_DATE)
        if df.empty:
            pytest.skip(f"No 1-min bars found for {self.TEST_DATE}")
        return df

    @pytest.fixture(scope="class")
    def live_breadth(self, equity_symbols: list[str], bars_df: pd.DataFrame):
        from algotrader.data.live_breadth import LiveBreadth
        lb = LiveBreadth(equity_symbols)
        _feed_bars(lb, bars_df)
        return lb

    @pytest.fixture(scope="class")
    def gold_day_rows(self, gold_breadth: pd.DataFrame) -> pd.DataFrame:
        mask = gold_breadth["ts"].dt.date == self.TEST_DATE
        return gold_breadth.loc[mask].reset_index(drop=True)

    def test_row_count_matches(
        self, live_breadth, gold_day_rows: pd.DataFrame
    ) -> None:
        """Every gold row must have a corresponding .at() result."""
        missing = []
        for row in gold_day_rows.itertuples(index=False):
            epoch_s = int(row.ts.timestamp())
            if live_breadth.at(epoch_s) is None:
                missing.append(str(row.ts))
        assert not missing, f"Missing snapshots for: {missing}"

    def test_pct_above_vwap_matches_gold(
        self, live_breadth, gold_day_rows: pd.DataFrame
    ) -> None:
        """pct_above_vwap must match to within 1e-6."""
        for row in gold_day_rows.itertuples(index=False):
            epoch_s = int(row.ts.timestamp())
            result = live_breadth.at(epoch_s)
            assert result is not None, f"No snapshot at {row.ts}"
            computed, _, _ = result
            gold_val = float(row.pct_above_vwap)
            assert abs(computed - gold_val) < 1e-6, (
                f"pct_above_vwap mismatch at {row.ts}: "
                f"computed={computed:.8f} gold={gold_val:.8f}"
            )

    def test_net_breadth_matches_gold(
        self, live_breadth, gold_day_rows: pd.DataFrame
    ) -> None:
        """net_breadth must match to within 1e-6."""
        for row in gold_day_rows.itertuples(index=False):
            epoch_s = int(row.ts.timestamp())
            result = live_breadth.at(epoch_s)
            assert result is not None
            _, _, net = result
            gold_val = float(row.net_breadth)
            assert abs(net - gold_val) < 1e-6, (
                f"net_breadth mismatch at {row.ts}: "
                f"computed={net:.8f} gold={gold_val:.8f}"
            )

    def test_n_stocks_matches_gold(
        self, live_breadth, gold_day_rows: pd.DataFrame
    ) -> None:
        """n_stocks must be exactly equal to the gold value."""
        for row in gold_day_rows.itertuples(index=False):
            epoch_s = int(row.ts.timestamp())
            result = live_breadth.at(epoch_s)
            assert result is not None
            _, n, _ = result
            gold_n = int(row.n_stocks)
            assert n == gold_n, (
                f"n_stocks mismatch at {row.ts}: computed={n} gold={gold_n}"
            )

    def test_net_breadth_formula_holds(
        self, live_breadth, gold_day_rows: pd.DataFrame
    ) -> None:
        """net_breadth == 2*adv_frac - 1 — verify via stored adv_frac column."""
        for row in gold_day_rows.itertuples(index=False):
            epoch_s = int(row.ts.timestamp())
            result = live_breadth.at(epoch_s)
            assert result is not None
            pct, _, net = result
            # Compare net to 2*(gold adv_frac)-1 as an independent cross-check
            expected_net = float(2.0 * row.adv_frac - 1.0)
            assert abs(net - expected_net) < 1e-5, (
                f"net_breadth formula mismatch at {row.ts}: "
                f"net={net:.8f} 2*adv-1={expected_net:.8f}"
            )

    def test_all_snapshot_values_in_unit_interval(
        self, live_breadth, gold_day_rows: pd.DataFrame
    ) -> None:
        """pct_above_vwap must be in [0, 1]; net_breadth in [-1, 1]."""
        for row in gold_day_rows.itertuples(index=False):
            epoch_s = int(row.ts.timestamp())
            result = live_breadth.at(epoch_s)
            assert result is not None
            pct, n, net = result
            assert 0.0 <= pct <= 1.0, f"pct_above_vwap out of range at {row.ts}: {pct}"
            assert -1.0 <= net <= 1.0, f"net_breadth out of range at {row.ts}: {net}"
            assert n > 0, f"n_stocks must be positive at {row.ts}"


# ---------------------------------------------------------------------------
# Test 2: Reset on new session
# ---------------------------------------------------------------------------

class TestResetOnNewSession:
    """Verify that state clears correctly when a new session date is encountered."""

    def _make_one_decision_day(
        self,
        target_date: date,
        symbols: list[str],
        n_bars: int = 7,
        base_price: float = 100.0,
        base_vol: int = 1000,
    ) -> list[tuple[str, "Bar"]]:
        """Create (symbol, bar) tuples for one synthetic session.

        Generates bars from 09:15 through 09:15+n_bars-1, which includes the
        09:20 decision boundary when n_bars >= 6 (09:15..09:20 inclusive).
        """
        bars: list[tuple[str, "Bar"]] = []
        start = datetime(
            target_date.year, target_date.month, target_date.day, 9, 15, tzinfo=IST
        )
        for sym in symbols:
            for i in range(n_bars):
                ts = start + timedelta(minutes=i)
                price = base_price + i * 0.1
                bar = _make_stub_bar(sym, ts, base_price, price, base_vol)
                bars.append((sym, bar))
        # Sort by ts then symbol (consistent with on_bar expectations)
        bars.sort(key=lambda t: (t[1].ts_open, t[0]))
        return bars

    def test_session_resets_on_new_date(self) -> None:
        """After feeding day-1 bars, day-2 bars must produce clean state."""
        from algotrader.data.live_breadth import LiveBreadth

        syms = ["AAAA", "BBBB", "CCCC"]
        day1 = date(2024, 1, 15)
        day2 = date(2024, 1, 16)

        lb = LiveBreadth(syms)

        # Feed day 1: enough bars to trigger the 09:20 decision
        for sym, bar in self._make_one_decision_day(day1, syms):
            lb.on_bar(sym, bar)

        epoch_day1 = _epoch_for(day1, 9 * 60 + 20)
        assert lb.at(epoch_day1) is not None, "Day 1 snapshot should exist"
        assert lb._session_date == day1

        # Feed day 2: the first bar must trigger a session reset
        day2_bars = self._make_one_decision_day(day2, syms)
        # Feed just the first bar to trigger reset
        first_sym, first_bar = day2_bars[0]
        lb.on_bar(first_sym, first_bar)

        assert lb._session_date == day2, "Session date should have updated to day 2"

        # Day 1 snapshot must be gone
        assert lb.at(epoch_day1) is None, (
            "Day 1 snapshot must be cleared after session reset"
        )

    def test_first_open_resets_correctly(self) -> None:
        """first_open for each symbol must be the day's first bar's open, not day-1's."""
        from algotrader.data.live_breadth import LiveBreadth

        syms = ["SYM_X"]
        day1 = date(2024, 2, 1)
        day2 = date(2024, 2, 2)
        lb = LiveBreadth(syms)

        # Day 1: open = 200
        bar_d1 = _make_stub_bar(
            "SYM_X",
            datetime(2024, 2, 1, 9, 15, tzinfo=IST),
            open_px=200.0, close_px=201.0, volume=500,
        )
        lb.on_bar("SYM_X", bar_d1)
        assert lb._first_open.get("SYM_X") == 200.0

        # Day 2: open = 150 — should replace 200 after reset
        bar_d2 = _make_stub_bar(
            "SYM_X",
            datetime(2024, 2, 2, 9, 15, tzinfo=IST),
            open_px=150.0, close_px=151.0, volume=500,
        )
        lb.on_bar("SYM_X", bar_d2)
        assert lb._first_open.get("SYM_X") == 150.0, (
            "first_open must be reset to day-2 value"
        )

    def test_snapshots_cleared_on_new_session(self) -> None:
        """_snapshots dict must be empty immediately after a session reset."""
        from algotrader.data.live_breadth import LiveBreadth

        syms = ["AA"]
        day1 = date(2024, 3, 1)
        day2 = date(2024, 3, 4)
        lb = LiveBreadth(syms)

        # Populate some snapshots on day 1
        for sym, bar in self._make_one_decision_day(day1, syms):
            lb.on_bar(sym, bar)
        assert lb._snapshots, "Snapshots should have been computed"

        # Trigger reset via day-2 bar
        bar_d2 = _make_stub_bar("AA", datetime(2024, 3, 4, 9, 15, tzinfo=IST),
                                 100.0, 100.5, 1000)
        lb.on_bar("AA", bar_d2)

        assert lb._snapshots == {}, "Snapshots must be cleared on session reset"
        assert lb._computed_decisions == set(), "Computed decisions must be cleared"


# ---------------------------------------------------------------------------
# Test 3: n_stocks counting with missing symbols
# ---------------------------------------------------------------------------

class TestNStocksCounting:
    """n_stocks must reflect the number of symbols that contributed bars,
    not the total universe size."""

    def test_partial_symbol_coverage(self) -> None:
        """Feed bars for only a subset and assert n_stocks equals the subset size."""
        from algotrader.data.live_breadth import LiveBreadth

        # Universe = 50 symbols, but only 20 will send bars
        universe = [f"SYM_{i:02d}" for i in range(50)]
        active = universe[:20]

        lb = LiveBreadth(universe)

        sess_date = date(2024, 4, 1)
        start = datetime(2024, 4, 1, 9, 15, tzinfo=IST)

        # Build all (sym, bar) pairs sorted by (ts, sym) so bars arrive in
        # chronological order — all symbols' 09:15 bars before any 09:20 bar.
        all_bars: list[tuple[datetime, str, "Bar"]] = []
        for sym in active:
            for i in range(6):
                ts = start + timedelta(minutes=i)
                price = 100.0 + i
                bar = _make_stub_bar(sym, ts, 100.0, price, 1000)
                all_bars.append((ts, sym, bar))
        all_bars.sort(key=lambda t: (t[0], t[1]))

        for _ts, sym, bar in all_bars:
            lb.on_bar(sym, bar)

        epoch_s = _epoch_for(sess_date, 9 * 60 + 20)
        result = lb.at(epoch_s)
        assert result is not None, "Snapshot must exist at 09:20"
        _, n_stocks, _ = result
        assert n_stocks == len(active), (
            f"n_stocks should be {len(active)} (active), got {n_stocks}"
        )

    def test_single_symbol_n_stocks(self) -> None:
        """With only one symbol feeding bars, n_stocks == 1."""
        from algotrader.data.live_breadth import LiveBreadth

        universe = ["A", "B", "C", "D", "E"]
        lb = LiveBreadth(universe)

        sess_date = date(2024, 4, 2)
        start = datetime(2024, 4, 2, 9, 15, tzinfo=IST)

        # Only "A" sends bars — all 6 arrive in order (no interleaving needed for 1 sym)
        for i in range(6):
            ts = start + timedelta(minutes=i)
            bar = _make_stub_bar("A", ts, 50.0, 50.0 + i, 100)
            lb.on_bar("A", bar)

        epoch_s = _epoch_for(sess_date, 9 * 60 + 20)
        result = lb.at(epoch_s)
        assert result is not None
        _, n, _ = result
        assert n == 1, f"Expected n_stocks=1, got {n}"

    def test_ignored_symbol_not_counted(self) -> None:
        """Bars for symbols not in the universe are ignored."""
        from algotrader.data.live_breadth import LiveBreadth

        universe = ["A", "B"]
        lb = LiveBreadth(universe)

        sess_date = date(2024, 4, 3)
        start = datetime(2024, 4, 3, 9, 15, tzinfo=IST)

        # Feed universe symbols + one outsider; interleave in ts order
        for i in range(6):
            ts = start + timedelta(minutes=i)
            # All three arrive at the same ts — order within ts is deterministic
            lb.on_bar("A",       _make_stub_bar("A",       ts, 100.0, 100.0 + i, 500))
            lb.on_bar("B",       _make_stub_bar("B",       ts, 200.0, 200.0 + i, 500))
            lb.on_bar("OUTSIDER",_make_stub_bar("OUTSIDER",ts, 300.0, 300.0 + i, 500))

        epoch_s = _epoch_for(sess_date, 9 * 60 + 20)
        result = lb.at(epoch_s)
        assert result is not None
        _, n, _ = result
        assert n == 2, f"OUTSIDER must not be counted; expected n=2, got {n}"


# ---------------------------------------------------------------------------
# Test 4: seed_from_history reproduces on_bar results
# ---------------------------------------------------------------------------

class TestSeedFromHistory:
    """seed_from_history must produce identical snapshots to direct on_bar feeding."""

    TEST_DATE = date(2021, 6, 28)

    @pytest.fixture(scope="class")
    def equity_symbols(self) -> list[str]:
        return _equity_symbols()

    def test_seed_matches_on_bar(self, equity_symbols: list[str]) -> None:
        """Snapshots from seed_from_history and on_bar must agree (tol 1e-8)."""
        from algotrader.data.live_breadth import LiveBreadth

        df = _load_1m_for_date(equity_symbols, self.TEST_DATE)
        if df.empty:
            pytest.skip(f"No data for {self.TEST_DATE}")

        # --- method A: on_bar ---
        lb_live = LiveBreadth(equity_symbols)
        _feed_bars(lb_live, df)

        # --- method B: seed_from_history (uses parquet directly) ---
        # Use 'until' far in the future to load the full day
        lb_seed = LiveBreadth(equity_symbols)
        far_future = datetime(2099, 1, 1, tzinfo=IST)
        lb_seed.seed_from_history(self.TEST_DATE, until=far_future)

        # Compare every decision boundary that was computed by method A
        mismatches: list[str] = []
        for t_min in range(9 * 60 + 20, 15 * 60 + 26, 5):
            epoch_s = _epoch_for(self.TEST_DATE, t_min)
            live_result = lb_live.at(epoch_s)
            seed_result = lb_seed.at(epoch_s)

            if live_result is None and seed_result is None:
                continue
            if live_result is None or seed_result is None:
                mismatches.append(
                    f"t={t_min:04d}: live={live_result} seed={seed_result}"
                )
                continue

            pct_l, n_l, net_l = live_result
            pct_s, n_s, net_s = seed_result

            if n_l != n_s or abs(pct_l - pct_s) > 1e-8 or abs(net_l - net_s) > 1e-8:
                mismatches.append(
                    f"t={t_min:04d} pct={pct_l:.8f}vs{pct_s:.8f} "
                    f"n={n_l}vs{n_s} net={net_l:.8f}vs{net_s:.8f}"
                )

        assert not mismatches, "seed_from_history differs from on_bar:\n" + "\n".join(mismatches)

    def test_seed_stops_at_cutoff(self, equity_symbols: list[str]) -> None:
        """seed_from_history with until=10:00 must not have snapshots after 10:00."""
        from algotrader.data.live_breadth import LiveBreadth

        lb = LiveBreadth(equity_symbols)
        cutoff = datetime(self.TEST_DATE.year, self.TEST_DATE.month,
                          self.TEST_DATE.day, 10, 0, tzinfo=IST)
        lb.seed_from_history(self.TEST_DATE, until=cutoff)

        # Snapshot at 10:00 itself uses bars with ts_open < 10:00, so it can exist.
        # Snapshot at 10:05 would need bars at 10:00..10:04, which are excluded.
        epoch_10_05 = _epoch_for(self.TEST_DATE, 10 * 60 + 5)
        result_10_05 = lb.at(epoch_10_05)
        assert result_10_05 is None, (
            f"10:05 snapshot must be absent when cutoff=10:00, got {result_10_05}"
        )


# ---------------------------------------------------------------------------
# Test 5: Causality — future bars must not change earlier snapshots
# ---------------------------------------------------------------------------

class TestCausality:
    """Incremental computation must be causal: no later bar may affect
    a snapshot that was already computed."""

    def test_earlier_snapshot_unchanged_after_later_bars(self) -> None:
        """Snapshot at 09:20 must not change after 10:00 bars arrive."""
        from algotrader.data.live_breadth import LiveBreadth

        syms = ["X", "Y"]
        sess = date(2024, 5, 1)
        start = datetime(2024, 5, 1, 9, 15, tzinfo=IST)
        lb = LiveBreadth(syms)

        # Feed bars up to and including 09:20 (triggers decision at 09:20)
        for i in range(6):  # 09:15 … 09:20
            ts = start + timedelta(minutes=i)
            for sym in syms:
                bar = _make_stub_bar(sym, ts, 100.0, 100.0 + i, 1000)
                lb.on_bar(sym, bar)

        epoch_0920 = _epoch_for(sess, 9 * 60 + 20)
        snap_before = lb.at(epoch_0920)
        assert snap_before is not None

        # Feed many more bars (up to 10:00) — should not change the 09:20 snapshot
        for i in range(6, 46):  # 09:21 … 10:00
            ts = start + timedelta(minutes=i)
            for sym in syms:
                bar = _make_stub_bar(sym, ts, 100.0, 100.0 + i * 0.5, 1000)
                lb.on_bar(sym, bar)

        snap_after = lb.at(epoch_0920)
        assert snap_before == snap_after, (
            "Earlier snapshot must be immutable after later bars arrive"
        )
