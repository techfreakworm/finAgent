"""Build causal market-internals (breadth) dataset from the 1-min bar store.

Output: data/cache/_BREADTH/5m/breadth.parquet

Columns:
  ts             — 5-min decision boundary (IST, tz-aware); values 09:20..15:25
  pct_above_vwap — fraction of stocks whose last 1-min close before ts >= session VWAP
  adv_frac       — fraction of stocks whose last 1-min close before ts > session-open
  net_breadth    — 2*adv_frac - 1  (advances minus declines, normalised)
  n_stocks       — number of symbols contributing data at this ts

Causality: every value at ts uses only 1-min bars where ts_open < ts.
No same-bar peeking; bars are bar-START stamped so ts_open < decision_ts
means the 1-min bar has fully closed before the decision point.

Run: .venv/bin/python scripts/build_breadth.py
"""
from __future__ import annotations

import json
import random
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

IST = ZoneInfo("Asia/Kolkata")
CACHE = PROJECT / "data" / "cache"
OUT_DIR = CACHE / "_BREADTH" / "5m"

SESSION_START_MIN: int = 9 * 60 + 15  # 09:15, first bar start

# Decision times: 09:20, 09:25, …, 15:25 (close-time of each 5-min bar starting at 09:15)
DECISION_MINS: np.ndarray = np.arange(9 * 60 + 20, 15 * 60 + 26, 5, dtype=np.int32)
N_DEC: int = len(DECISION_MINS)  # 74

NIFTY50_MAP_PATH = Path(
    "/home/ubuntu/finAgent/research_scratchpad/dhan_nifty50_mapping.json"
)
EXCLUDE: frozenset[str] = frozenset({"NIFTY_50", "INDIA_VIX", "NIFTYBEES"})


# ── symbol helpers ────────────────────────────────────────────────────────

def equity_symbols() -> list[str]:
    """NIFTY-50 equity symbols, non-index, sorted."""
    mapping: dict[str, int] = json.loads(NIFTY50_MAP_PATH.read_text())
    return sorted(s for s in mapping if s not in EXCLUDE)


def available_months(symbol: str) -> list[Path]:
    """Sorted 1m parquet files for *symbol*; empty if no data dir."""
    p = CACHE / symbol / "1m"
    return sorted(p.glob("*.parquet")) if p.exists() else []


def all_ym_keys(syms: list[str]) -> list[str]:
    """Sorted YYYY-MM keys present across any equity symbol."""
    keys: set[str] = set()
    for sym in syms:
        for p in available_months(sym):
            keys.add(p.stem)
    return sorted(keys)


# ── data loading ──────────────────────────────────────────────────────────

def load_month(symbol: str, ym: str) -> pd.DataFrame | None:
    """Load one symbol-month parquet; return None if missing/unreadable."""
    p = CACHE / symbol / "1m" / f"{ym}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p, columns=["ts", "open", "close", "volume"])
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] {symbol}/{ym}: {exc}", flush=True)
        return None
    # Ensure IST-aware ts
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize(IST)
    else:
        df["ts"] = df["ts"].dt.tz_convert(IST)
    return df


def load_month_all_symbols(ym: str, syms: list[str]) -> pd.DataFrame:
    """Concatenate *ym* 1-min data for all available symbols.

    Returns a single DataFrame with an extra 'symbol' column, or an
    empty DataFrame if nothing loads.
    """
    frames: list[pd.DataFrame] = []
    for sym in syms:
        df = load_month(sym, ym)
        if df is not None and not df.empty:
            df["symbol"] = sym
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ── breadth computation ───────────────────────────────────────────────────

def compute_breadth(big: pd.DataFrame) -> pd.DataFrame:
    """Compute causal breadth rows from concatenated 1-min bars.

    *big* must have columns: ts (IST tz-aware), open, close, volume, symbol.
    Returns rows with: date, t_min (int, minutes since midnight),
    pct_above_vwap, adv_frac, net_breadth, n_stocks.

    CAUSALITY: for each decision minute D, only bars with t_min < D are
    used (bar-START < D means the 1-min bar closed before D).
    """
    if big.empty:
        return pd.DataFrame()

    big = big.copy()
    big["date"] = big["ts"].dt.date
    big["t_min"] = (big["ts"].dt.hour * 60 + big["ts"].dt.minute).astype(np.int32)

    # Drop pre-session bars (defensive)
    big = big[big["t_min"] >= SESSION_START_MIN]
    if big.empty:
        return pd.DataFrame()

    big["cv"] = big["close"] * big["volume"]
    big.sort_values(["symbol", "date", "t_min"], inplace=True)
    big.reset_index(drop=True, inplace=True)

    # Cumulative VWAP numerator/denominator within each (symbol, session)
    grp_key = ["symbol", "date"]
    big["cv_cum"] = big.groupby(grp_key, sort=False)["cv"].cumsum()
    big["v_cum"] = big.groupby(grp_key, sort=False)["volume"].cumsum()
    big["first_open"] = big.groupby(grp_key, sort=False)["open"].transform("first")

    # ── per-(symbol, date) group: fill decision-slot matrix ──────────────
    groups = list(big.groupby(grp_key))
    n_groups = len(groups)

    above_vwap_mat = np.zeros((n_groups, N_DEC), dtype=np.int8)
    advance_mat = np.zeros((n_groups, N_DEC), dtype=np.int8)
    has_data_mat = np.zeros((n_groups, N_DEC), dtype=np.int8)
    group_dates: list[date] = []

    for gi, ((sym, d), grp) in enumerate(groups):
        t_arr = grp["t_min"].values  # int32, sorted ascending
        close_arr = grp["close"].values
        cv_cum_arr = grp["cv_cum"].values
        v_cum_arr = grp["v_cum"].values
        first_open = float(grp["first_open"].values[0])

        # For each decision minute: index of last bar with t_min < decision
        # searchsorted(..., side='left') returns first position >= value
        # subtract 1 to get last bar strictly before decision
        idxs = np.searchsorted(t_arr, DECISION_MINS, side="left") - 1
        valid = idxs >= 0  # bool array of shape (N_DEC,)

        valid_j = np.where(valid)[0]
        if valid_j.size == 0:
            group_dates.append(d)
            continue

        j_idxs = idxs[valid_j]  # bar indices for valid decision slots

        lc = close_arr[j_idxs]
        vv = v_cum_arr[j_idxs]
        cv = cv_cum_arr[j_idxs]
        vwap = np.where(vv > 0, cv / vv, np.nan)

        has_data_mat[gi, valid_j] = 1
        above_vwap_mat[gi, valid_j] = (lc >= vwap).astype(np.int8)
        advance_mat[gi, valid_j] = (lc > first_open).astype(np.int8)
        group_dates.append(d)

    # ── aggregate by (date, decision_slot) ───────────────────────────────
    date_arr = np.array(group_dates, dtype=object)
    unique_dates = sorted(set(group_dates))

    out_rows: list[dict] = []
    for d in unique_dates:
        mask = date_arr == d
        hd = has_data_mat[mask]   # (n_syms, N_DEC)
        ab = above_vwap_mat[mask]
        adv = advance_mat[mask]

        n_per_slot = hd.sum(axis=0)          # (N_DEC,)
        nonzero = n_per_slot > 0

        for j in np.where(nonzero)[0]:
            n = int(n_per_slot[j])
            pct_vwap = float(ab[:, j].sum()) / n
            adv_frac = float(adv[:, j].sum()) / n
            out_rows.append(
                {
                    "date": d,
                    "t_min": int(DECISION_MINS[j]),
                    "pct_above_vwap": pct_vwap,
                    "adv_frac": adv_frac,
                    "net_breadth": 2.0 * adv_frac - 1.0,
                    "n_stocks": n,
                }
            )

    if not out_rows:
        return pd.DataFrame()

    result = pd.DataFrame(out_rows)
    return result


def rows_to_breadth_df(df: pd.DataFrame) -> pd.DataFrame:
    """Convert (date, t_min) rows to a ts-indexed breadth DataFrame."""
    if df.empty:
        return pd.DataFrame(
            columns=["ts", "pct_above_vwap", "adv_frac", "net_breadth", "n_stocks"]
        )
    # Reconstruct tz-aware ts from date + t_min
    ts_vals = pd.to_datetime(
        df["date"].astype(str)
    ) + pd.to_timedelta(df["t_min"].astype(int), unit="min")
    df = df.copy()
    df["ts"] = ts_vals.dt.tz_localize(IST)
    return df.drop(columns=["date", "t_min"])[
        ["ts", "pct_above_vwap", "adv_frac", "net_breadth", "n_stocks"]
    ]


# ── causality verification ────────────────────────────────────────────────

def verify_causality(breadth_df: pd.DataFrame) -> bool:
    """Recompute one random session's 11:00 row with future bars deleted.

    Prints PASS/FAIL and returns the boolean.
    """
    CHECK_HOUR, CHECK_MIN = 11, 0
    CHECK_TMIN = CHECK_HOUR * 60 + CHECK_MIN  # 660

    unique_dates = sorted({ts.date() for ts in breadth_df["ts"]})
    if not unique_dates:
        print("[causality-check] SKIP — no data", flush=True)
        return True

    rng = random.Random(42)
    check_date = rng.choice(unique_dates[-60:])
    check_ts = pd.Timestamp(
        datetime(check_date.year, check_date.month, check_date.day,
                 CHECK_HOUR, CHECK_MIN, tzinfo=IST)
    )

    row = breadth_df[breadth_df["ts"] == check_ts]
    if row.empty:
        print(f"[causality-check] SKIP — no row for {check_date} 11:00", flush=True)
        return True

    orig = row.iloc[0]

    # Load original month data for that session
    ym = check_date.strftime("%Y-%m")
    syms = equity_symbols()
    big = load_month_all_symbols(ym, syms)
    if big.empty:
        print("[causality-check] SKIP — no month data", flush=True)
        return True

    # Truncate: delete all bars at/after check_ts (i.e., keep only ts < check_ts)
    # This simulates computing from a dataset where bars after 11:00 do not exist
    big_trunc = big[
        (big["ts"].dt.date == check_date) & (big["ts"] < check_ts)
    ].copy()

    # Compute
    raw = compute_breadth(big_trunc)
    if raw.empty:
        print("[causality-check] SKIP — recheck produced empty result", flush=True)
        return True

    recheck_df = rows_to_breadth_df(raw)
    recheck_row = recheck_df[recheck_df["ts"] == check_ts]

    if recheck_row.empty:
        print(f"[causality-check] SKIP — no recheck row for {check_date} 11:00",
              flush=True)
        return True

    rc = recheck_row.iloc[0]

    # Compare at float32 precision: the stored file is float32-cast, so normalise
    # both sides to float32 before diffing to avoid false failures from float64 vs
    # float32 representation differences.
    def _f32(v: float) -> float:
        return float(np.float32(v))

    match_vwap = abs(_f32(orig["pct_above_vwap"]) - _f32(rc["pct_above_vwap"])) < 1e-9
    match_adv = abs(_f32(orig["adv_frac"]) - _f32(rc["adv_frac"])) < 1e-9
    match_n = int(orig["n_stocks"]) == int(rc["n_stocks"])
    passed = match_vwap and match_adv and match_n

    status = "PASS" if passed else "FAIL"
    print(
        f"[causality-check] {status} — session {check_date} @11:00  "
        f"orig=(vwap={orig['pct_above_vwap']:.4f}, adv={orig['adv_frac']:.4f}, "
        f"n={orig['n_stocks']})  "
        f"recheck=(vwap={rc['pct_above_vwap']:.4f}, adv={rc['adv_frac']:.4f}, "
        f"n={rc['n_stocks']})",
        flush=True,
    )
    return passed


# ── main ─────────────────────────────────────────────────────────────────

def main() -> None:
    t_start = time.monotonic()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    syms = equity_symbols()
    print(f"Universe: {len(syms)} equity symbols", flush=True)

    ym_keys = all_ym_keys(syms)
    print(
        f"Month-keys to process: {len(ym_keys)}  ({ym_keys[0]} .. {ym_keys[-1]})",
        flush=True,
    )

    all_chunks: list[pd.DataFrame] = []

    for i, ym in enumerate(ym_keys, 1):
        t0 = time.monotonic()
        big = load_month_all_symbols(ym, syms)
        raw = compute_breadth(big)
        chunk = rows_to_breadth_df(raw)
        if not chunk.empty:
            all_chunks.append(chunk)
        elapsed = time.monotonic() - t0
        n_syms = big["symbol"].nunique() if not big.empty else 0
        print(
            f"  [{i:3d}/{len(ym_keys)}] {ym}: "
            f"{n_syms} syms, {len(chunk)} rows  ({elapsed:.1f}s)",
            flush=True,
        )

    if not all_chunks:
        print("ERROR: no breadth rows produced", flush=True)
        sys.exit(1)

    breadth = pd.concat(all_chunks, ignore_index=True)
    breadth = breadth.sort_values("ts").reset_index(drop=True)

    # Downcast for compact storage
    breadth["pct_above_vwap"] = breadth["pct_above_vwap"].astype(np.float32)
    breadth["adv_frac"] = breadth["adv_frac"].astype(np.float32)
    breadth["net_breadth"] = breadth["net_breadth"].astype(np.float32)
    breadth["n_stocks"] = breadth["n_stocks"].astype(np.int16)

    out_path = OUT_DIR / "breadth.parquet"
    breadth.to_parquet(out_path, index=False)

    total_elapsed = time.monotonic() - t_start
    print(
        f"\nWrote {out_path}  ({len(breadth):,} rows, "
        f"{out_path.stat().st_size / 1024:.0f} KB)  "
        f"total runtime: {total_elapsed:.1f}s",
        flush=True,
    )

    # Causality verification
    verify_causality(breadth)


if __name__ == "__main__":
    main()
