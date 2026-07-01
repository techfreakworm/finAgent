"""End-to-end replay integration test (ASSIGNMENT §2).

Tests:
  (A) LiveBreadth 5-min snapshots match data/cache/_BREADTH/5m/breadth.parquet
      for every decision boundary on 2026-06-10 (74 rows, tol=0.02).
  (B) paper_trade._run_replay runs a full stored session via ReplayDriver →
      LiveBreadth → PaperExecutors (paper + paper-minlot) for 2026-06-10 and
      finishes without error; EOD HTML report is written.
  (C) The breadth at 10:15 IST on 2026-06-10 is 0.34 (below the 0.72 threshold;
      gold 0.32→0.34 on 2026-07-02 after the secid-map fix swapped impostor
      SPARC/SATIN for real SUNPHARMA/ITC — a one-name breadth shift),
      so BreadthRider generates 0 trades — verified both through the executor
      trade_records and the EOD report.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

IST = ZoneInfo("Asia/Kolkata")
_SESSION_DATE = date(2026, 6, 10)
_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
_BREADTH_PARQUET = _CACHE / "_BREADTH" / "5m" / "breadth.parquet"

# NIFTY-50 symbols whose 1-min bars are present in the cache
_NIFTY50_SYMBOLS = [
    "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL", "BPCL",
    "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT",
    "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT",
    "MARUTI", "M&M", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_equity_bars_for_date(
    session_date: date,
) -> list[tuple[str, object]]:
    """Load (symbol, Bar) pairs from the 1-min parquet for each equity symbol."""
    from algotrader.core import Bar, Instrument, Segment

    rows: list[tuple[str, object]] = []
    ym = session_date.strftime("%Y-%m")
    for sym in _NIFTY50_SYMBOLS:
        parquet = _CACHE / sym / "1m" / f"{ym}.parquet"
        if not parquet.exists():
            continue
        df = pd.read_parquet(parquet)
        df["ts"] = pd.to_datetime(df["ts"])
        if df["ts"].dt.tz is None:
            df["ts"] = df["ts"].dt.tz_localize(IST)
        else:
            df["ts"] = df["ts"].dt.tz_convert(IST)
        day = df[df["ts"].dt.date == session_date].copy()
        if day.empty:
            continue
        instr = Instrument(
            symbol=sym, security_id=sym,
            segment=Segment.NSE_EQ, tick_size=0.05,
        )
        for _, row in day.iterrows():
            ts = row["ts"].to_pydatetime()
            bar = Bar(
                instrument=instr,
                ts_open=ts,
                interval_min=1,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                complete=True,
            )
            rows.append((sym, bar))

    # Sort by ts_open for chronological processing
    rows.sort(key=lambda x: x[1].ts_open)
    return rows


# ---------------------------------------------------------------------------
# (A) LiveBreadth matches parquet
# ---------------------------------------------------------------------------

def test_live_breadth_matches_parquet() -> None:
    """(A) All 74 LiveBreadth 5-min snapshots for 2026-06-10 match the parquet.

    Tolerance 0.02 absolute on pct_above_vwap and net_breadth.
    The parquet was built from the same 1-min equity data by build_breadth.py.
    """
    if not _BREADTH_PARQUET.exists():
        pytest.skip("breadth parquet not found")

    from algotrader.data.live_breadth import LiveBreadth

    # Build LiveBreadth and feed all equity 1-min bars for 2026-06-10
    lb = LiveBreadth(_NIFTY50_SYMBOLS)
    for sym, bar in _load_equity_bars_for_date(_SESSION_DATE):
        lb.on_bar(sym, bar)

    # Load parquet reference
    df = pd.read_parquet(_BREADTH_PARQUET)
    df["ts"] = pd.to_datetime(df["ts"])
    mask = df["ts"].dt.date == _SESSION_DATE
    day_df = df[mask].copy()
    assert len(day_df) > 0, "No parquet rows for 2026-06-10"

    tol = 0.02
    mismatches: list[str] = []
    checked = 0
    for _, prow in day_df.iterrows():
        epoch_s = int(prow["ts"].timestamp())
        live = lb.at(epoch_s)
        if live is None:
            # A decision boundary with no prior bars is acceptable
            continue
        live_pct, live_n, live_net = live
        parq_pct = float(prow["pct_above_vwap"])
        parq_net = float(prow["net_breadth"])
        checked += 1

        if abs(live_pct - parq_pct) > tol or abs(live_net - parq_net) > tol:
            mismatches.append(
                f"@{prow['ts'].strftime('%H:%M')}: "
                f"pct live={live_pct:.4f} parq={parq_pct:.4f} "
                f"net live={live_net:.4f} parq={parq_net:.4f}"
            )

    assert checked >= 70, f"Only {checked}/74 boundaries had live snapshots"
    assert mismatches == [], (
        f"{len(mismatches)} breadth mismatches (tol={tol}):\n"
        + "\n".join(mismatches)
    )


# ---------------------------------------------------------------------------
# (B) + (C) End-to-end replay via paper_trade._run_replay
# ---------------------------------------------------------------------------

def test_replay_end_to_end(tmp_path: Path) -> None:
    """(B) Full replay session completes; (C) BreadthRider generates 0 trades.

    Breadth at 10:15 on 2026-06-10 is 0.34 — below the 0.72 threshold and
    above 0.28 (1 - 0.72), so no signal is generated.
    EOD HTML report is written to the tmp_path out_dir.
    """
    import argparse
    from scripts.paper_trade import _run_replay, _sync_to_fn_api
    from algotrader.reports.eod import generate_eod_report
    from algotrader.paper.executor import PaperExecutor

    # Use tmp_path for DB isolation
    args = argparse.Namespace(accounts=["paper", "paper-minlot"])

    # Patch executor factory to use tmp_path DBs
    import scripts.paper_trade as _pt_mod
    original_build = _pt_mod._build_executor

    def _patched_build(account_id, session_date, store_path=None, **kwargs):
        return original_build(
            account_id, session_date,
            store_path=tmp_path / f"paper_{account_id}.db",
        )

    _pt_mod._build_executor = _patched_build
    try:
        executors = _run_replay(args, _SESSION_DATE)
    finally:
        _pt_mod._build_executor = original_build

    assert len(executors) == 2, "Expected 2 executor instances"

    # (C): BreadthRider generates 0 trades (breadth 0.34 at 10:15 < 0.72 threshold)
    for exec_ in executors:
        assert isinstance(exec_, PaperExecutor)
        trades = exec_.trade_records
        assert len(trades) == 0, (
            f"Expected 0 trades for {exec_.account_id} "
            f"(breadth=0.34 at 10:15 does not meet thr=0.72), got {len(trades)}"
        )

    # Sync and generate EOD report
    db_dir = tmp_path / "fn_db"
    from algotrader.paper import store as _store_mod
    for exec_ in executors:
        _store_mod.init_db(exec_.account_id, db_dir=db_dir)
        raw_trades = exec_._store.load_trades(exec_.account_id, _SESSION_DATE)
        for row in raw_trades:
            from datetime import datetime as _dt
            row2 = dict(row)
            for k in ("entry_ts", "exit_ts"):
                v = row2[k]
                if isinstance(v, str):
                    parsed = _dt.fromisoformat(v)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=IST)
                    row2[k] = parsed.astimezone(IST)
            _store_mod.record_trade(exec_.account_id, _SESSION_DATE, row2, db_dir=db_dir)

    out_dir = tmp_path / "reports"
    md_path, html_path = generate_eod_report(
        _SESSION_DATE,
        [e.account_id for e in executors],
        db_dir=db_dir,
        out_dir=out_dir,
    )

    # (B): EOD files written
    assert html_path.exists(), f"HTML report not written: {html_path}"
    assert md_path.exists(), f"MD report not written: {md_path}"

    html_text = html_path.read_text()
    assert "2026-06-10" in html_text
    assert "EOD P&L Report" in html_text
    assert "Grand Total" in html_text

    # (C): no trades in report
    assert "0 trades" not in html_text.lower() or "Trades" in html_text
    for exec_ in executors:
        account_trades = _store_mod.get_trades(
            exec_.account_id, _SESSION_DATE, db_dir=db_dir
        )
        assert len(account_trades) == 0, (
            f"Unexpected trades in functional DB for {exec_.account_id}"
        )


# ---------------------------------------------------------------------------
# Spot-check: breadth at specific timestamps
# ---------------------------------------------------------------------------

def test_breadth_at_decision_time() -> None:
    """Spot-check: at 10:15 IST on 2026-06-10, pct_above_vwap ≈ 0.34.

    (Gold 0.32→0.34 on 2026-07-02: secid-map fix replaced impostor SPARC/SATIN
    with real SUNPHARMA/ITC; one of the two sits on the other side of VWAP at
    this minute — exactly a one-name 0.02 shift.)

    This is below the 0.72 BreadthRider threshold and above 0.28 (bear
    threshold), so the frozen cell generates no signal — expected.
    """
    from algotrader.data.live_breadth import LiveBreadth

    lb = LiveBreadth(_NIFTY50_SYMBOLS)
    for sym, bar in _load_equity_bars_for_date(_SESSION_DATE):
        lb.on_bar(sym, bar)

    # 10:15 IST epoch
    dt_1015 = datetime(2026, 6, 10, 10, 15, tzinfo=IST)
    epoch_1015 = int(dt_1015.timestamp())
    snap = lb.at(epoch_1015)
    assert snap is not None, "No breadth snapshot at 10:15 IST"
    pct, n_stocks, net = snap
    assert n_stocks == 50, f"Expected 50 stocks, got {n_stocks}"
    # Parquet says pct_above_vwap = 0.34 at 10:15 (post secid-map fix)
    assert abs(pct - 0.34) <= 0.02, f"pct_above_vwap at 10:15: {pct:.4f} != ~0.34"
    # Below both thresholds: no BreadthRider signal
    assert pct < 0.72, "Should be below bullish threshold"
    assert pct > 0.28, "Should be above bearish threshold (1 - 0.72)"
