"""Max-Pain / PCR / OI-Wall range-favorability LOGGING HARNESS (pre-registration).

Authoritative contract: reports/maxpain_pcr_prereg.md. This harness ONLY LOGS
signals + outcomes for the pre-registered 0DTE-straddle range-favorability filter.
It makes NO trading decisions, changes NO strategy behaviour, and is read-only
over the collected data (paper-only project, live-order lockout intact).

Per SESSION (invoked post-close) it reads that day's forward option-chain snapshot
`data/cache/_OPTIONS/NIFTY_CHAIN_FWD/<date>.parquet` and appends rows to
`reports/maxpain_pcr_eval.jsonl` (idempotent: re-running a date replaces its rows).

All decision signals are computed from `previous_oi` (prior-session CLOSING OI per
strike) ONCE, pre-open-equivalent -> zero intraday-drift risk (the doc's forced
signal source; live `oi` is a feed-recalc artifact in the morning). Two blocks per
session:

  * expiry_0dte  -- the dte==0 expiring leg. Present ONLY on expiry-Tuesdays. This
                    is the PRIMARY block: it joins the live paper straddle trade and
                    carries the running-median-split trade/skip label. The 2 collected
                    Tuesdays (2026-06-23, 2026-06-30) are DESIGN-ONLY / BURNED
                    (`_designonly: true`) and can never enter an evaluation aggregate.
  * near_weekly  -- the nearest NON-expiring weekly leg (smallest dte > 0). Logged
                    EVERY session as the mechanism-test-ONLY ancillary sample. On an
                    expiry-Tuesday this leg is exactly dte==7 (the doc's "dte=7
                    near-weekly"); on other sessions it is the true near-weekly dte.

Usage:
    .venv/bin/python scripts/maxpain_pcr_log.py --date 2026-06-30   # one session
    .venv/bin/python scripts/maxpain_pcr_log.py                      # today (IST)
    .venv/bin/python scripts/maxpain_pcr_log.py --backfill           # all collected
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")

CHAIN_DIR = PROJECT / "data/cache/_OPTIONS/NIFTY_CHAIN_FWD"
NIFTY_1M_DIR = PROJECT / "data/cache/NIFTY/1m"
PAPER_DB = PROJECT / "data/paper/paper-0dte.db"
OUT_JSONL = PROJECT / "reports/maxpain_pcr_eval.jsonl"

SCHEMA_VERSION = 1
STRADDLE_STRATEGY_ID = "zerodte_straddle"
LOT_SIZE = 75  # NIFTY lot (paper net_pnl is already per-lot; used only for MAE in rupees)

# The frozen gen6 config flattens the straddle at 15:10; an exit strictly before
# that is a stop/target fire (the doc's "exit_reason + time-based" stop_fired rule).
FLAT_EXIT_TIME = "15:10"

# Design-only / burned expiry-Tuesdays (constraint (e)); never enter an aggregate.
DESIGNONLY_DATES = {"2026-06-23", "2026-06-30"}

# spot0 reference window (the straddle's entry-spot neighbourhood): median spot
# over 09:15:00-09:20:00 inclusive.
SPOT0_START = "09:15:00"
SPOT0_END = "09:20:00"

# Regular trading hours used to derive open/high/low/close from the chain spot
# column when the NIFTY 1m cache is absent for the date.
RTH_OPEN = "09:15:00"
RTH_CLOSE = "15:30:00"

# Bookkeeping-only: a running median needs a non-degenerate reference set before a
# FAVORABLE/UNFAVORABLE label is meaningful. This gates ONLY the per-row label; it
# does NOT affect any decision (verdicts happen downstream at the pre-committed
# looks). Set small so labels appear once real evaluation data accrues.
MIN_SPLIT_N = 5


# --------------------------------------------------------------------------- I/O

def _read_chain(date: str) -> pd.DataFrame | None:
    """Load a session's forward-chain parquet with a parsed snap_dt (tz-aware IST)."""
    path = CHAIN_DIR / f"{date}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["snap_dt"] = pd.to_datetime(df["snap_ts"])
    return df


def available_dates() -> list[str]:
    """All collected NIFTY_CHAIN_FWD session dates, ascending."""
    return sorted(p.stem for p in CHAIN_DIR.glob("*.parquet"))


# --------------------------------------------------------------- signal helpers

def _spot0(leg: pd.DataFrame) -> float | None:
    """Reference spot: median spot over 09:15-09:20 (the straddle's entry spot).

    Falls back to the earliest snapshot's spot if the window is empty (documented
    resolution: the doc's prose says "first clean spot print in 09:15-09:20"; the
    build spec says "median spot" over that window -- median is used, being robust
    to a single noisy print, and it reproduces the pre-reg's design-only values).
    Returns None if there is no usable spot at all.
    """
    t = leg["snap_dt"].dt.strftime("%H:%M:%S")
    win = leg[(t >= SPOT0_START) & (t <= SPOT0_END)]
    if len(win):
        return float(win["spot"].median())
    if len(leg):
        first = leg.sort_values("snap_dt").iloc[0]
        val = float(first["spot"])
        return val if val > 0 else None
    return None


def _earliest_prevoi(leg: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Per-strike prior-session CLOSING OI (CE, PE) from the earliest snapshot.

    `previous_oi` is constant across the whole day per (strike, opt_type) -- verified
    against the collected data -- so the first occurrence is taken. Series are indexed
    by strike over the UNION of strikes (missing side filled 0).
    """
    first_ts = leg["snap_dt"].min()
    first = leg[leg["snap_dt"] == first_ts]
    ce = first[first["opt_type"] == "CE"].groupby("strike")["previous_oi"].first()
    pe = first[first["opt_type"] == "PE"].groupby("strike")["previous_oi"].first()
    strikes = sorted(set(ce.index) | set(pe.index))
    ce = ce.reindex(strikes).fillna(0).astype(float)
    pe = pe.reindex(strikes).fillna(0).astype(float)
    return ce, pe


def _prev_volume(leg: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Per-strike prior-session volume (CE, PE) from the earliest snapshot (diagnostic)."""
    first_ts = leg["snap_dt"].min()
    first = leg[leg["snap_dt"] == first_ts]
    ce = first[first["opt_type"] == "CE"].groupby("strike")["previous_volume"].first()
    pe = first[first["opt_type"] == "PE"].groupby("strike")["previous_volume"].first()
    strikes = sorted(set(ce.index) | set(pe.index))
    ce = ce.reindex(strikes).fillna(0).astype(float)
    pe = pe.reindex(strikes).fillna(0).astype(float)
    return ce, pe


def max_pain_strike(ce_oi: pd.Series, pe_oi: pd.Series) -> float:
    """Settle strike minimising total intrinsic paid to option HOLDERS by writers.

    MP = argmin_K [ sum_s max(K-s,0)*Oce(s) + sum_s max(s-K,0)*Ope(s) ]
    Candidate settle K ranges over the strike grid; the sums run over all strikes.
    On ties, the lowest strike (first argmin) is returned.
    """
    strikes = ce_oi.index.to_numpy(dtype=float)
    ce = ce_oi.to_numpy(dtype=float)
    pe = pe_oi.to_numpy(dtype=float)
    pain = np.array([
        float(np.clip(K - strikes, 0, None) @ ce + np.clip(strikes - K, 0, None) @ pe)
        for K in strikes
    ])
    return float(strikes[int(np.argmin(pain))])


def signal_block(leg: pd.DataFrame) -> dict:
    """Compute the full pre-open signal block for one expiry leg from `previous_oi`.

    Returns None-valued/empty dict only if the leg is unusable (no spot0).
    """
    spot0 = _spot0(leg)
    ce_oi, pe_oi = _earliest_prevoi(leg)
    if spot0 is None or spot0 <= 0 or ce_oi.empty:
        return {}

    strikes = ce_oi.index.to_numpy(dtype=float)
    mp = max_pain_strike(ce_oi, pe_oi)
    pin_gap_pct = abs(spot0 - mp) / spot0 * 100.0

    ce_tot = float(ce_oi.sum())
    pe_tot = float(pe_oi.sum())
    pcr = pe_tot / ce_tot if ce_tot > 0 else float("nan")
    abs_ln_pcr = abs(math.log(pcr)) if pcr and pcr > 0 else float("nan")

    # PCR volume variant (causal: prior-session volume) -- DIAGNOSTIC only.
    ce_vol, pe_vol = _prev_volume(leg)
    cv, pv = float(ce_vol.sum()), float(pe_vol.sum())
    pcr_volume = pv / cv if cv > 0 else float("nan")

    call_wall = float(strikes[int(np.argmax(ce_oi.to_numpy()))])
    put_wall = float(strikes[int(np.argmax(pe_oi.to_numpy()))])
    wall_bracket_flag = bool(put_wall <= spot0 <= call_wall)
    call_conc = float(ce_oi.max() / ce_tot) if ce_tot > 0 else float("nan")
    put_conc = float(pe_oi.max() / pe_tot) if pe_tot > 0 else float("nan")

    return {
        "spot0": round(spot0, 2),
        "n_strikes": int(len(strikes)),
        "prev_oi_ce": {str(int(k)): int(v) for k, v in ce_oi.items()},
        "prev_oi_pe": {str(int(k)): int(v) for k, v in pe_oi.items()},
        "max_pain": mp,
        "pin_gap_pct": round(pin_gap_pct, 4),
        "pcr": round(pcr, 4) if pcr == pcr else None,          # OI variant (PRIMARY-adjacent, diagnostic)
        "pcr_volume": round(pcr_volume, 4) if pcr_volume == pcr_volume else None,  # diagnostic
        "abs_ln_pcr": round(abs_ln_pcr, 4) if abs_ln_pcr == abs_ln_pcr else None,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "wall_bracket_flag": wall_bracket_flag,
        "call_wall_concentration": round(call_conc, 4) if call_conc == call_conc else None,
        "put_wall_concentration": round(put_conc, 4) if put_conc == put_conc else None,
    }


# ------------------------------------------------------------- outcome helpers

def _load_nifty_1m(date: str) -> pd.DataFrame | None:
    """NIFTY spot 1m bars for a date from the monthly-partitioned cache, or None."""
    month = date[:7]
    path = NIFTY_1M_DIR / f"{month}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"])
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize(IST)
    else:
        df["ts"] = df["ts"].dt.tz_convert(IST)
    day = df[df["ts"].dt.strftime("%Y-%m-%d") == date].sort_values("ts")
    return day if len(day) else None


def outcome_block(date: str, leg: pd.DataFrame) -> dict:
    """EOD outcome: open/high/low/close, realized range %, |close-open| %.

    Prefers the NIFTY 1m spot cache; falls back to the chain's spot column
    (restricted to regular trading hours). `spot_source` documents which was used.
    The realized range is the SAME session's spot range for both blocks (the signal,
    from either leg, is tested against that day's underlying move).

    `outcome_reliable` guards the mechanism test: it is False when the chain-fallback
    spot column is frozen for the whole session (a stale-feed artifact -- e.g.
    2026-06-26 -- that reports a spurious 0% range). Downstream ρ(pin-gap, range)
    must exclude unreliable outcomes. The 1m-cache source is always reliable.
    """
    bars = _load_nifty_1m(date)
    if bars is not None:
        o = float(bars.iloc[0]["open"])
        hi = float(bars["high"].max())
        lo = float(bars["low"].min())
        cl = float(bars.iloc[-1]["close"])
        src = "nifty_1m"
        reliable = True
    else:
        t = leg["snap_dt"].dt.strftime("%H:%M:%S")
        rth = leg[(t >= RTH_OPEN) & (t <= RTH_CLOSE)].sort_values("snap_dt")
        if rth.empty:
            rth = leg.sort_values("snap_dt")
        # one spot value per snapshot (identical across strikes within a snap)
        per_snap = rth.groupby("snap_dt")["spot"].first()
        o = float(per_snap.iloc[0])
        hi = float(per_snap.max())
        lo = float(per_snap.min())
        cl = float(per_snap.iloc[-1])
        src = "chain"
        # a frozen spot column (single distinct value across a full RTH session) is a
        # feed artifact, not a real 0-range day.
        reliable = bool(per_snap.nunique() > 1)

    range_pct = (hi - lo) / o * 100.0 if o else None
    close_open_abs_pct = abs(cl - o) / o * 100.0 if o else None
    return {
        "spot_source": src,
        "outcome_reliable": reliable,
        "spot_open": round(o, 2),
        "spot_high": round(hi, 2),
        "spot_low": round(lo, 2),
        "spot_close": round(cl, 2),
        "realized_range_pct": round(range_pct, 4) if range_pct is not None else None,
        "close_open_abs_pct": round(close_open_abs_pct, 4) if close_open_abs_pct is not None else None,
    }


def _straddle_mae(leg: pd.DataFrame, strike: float, expiry: str, entry_premium: float | None) -> float | None:
    """Best-effort short-straddle max adverse excursion in rupees, from the chain.

    Tracks the CE+PE basket premium (last_price) at the entry strike across the day;
    MAE = LOT_SIZE * max_t (basket_premium_t - entry_premium). For a short straddle
    the adverse direction is a RISE in basket premium. Returns None if the strike/
    expiry legs or the entry premium are unavailable. Diagnostic outcome only.
    """
    if entry_premium is None:
        return None
    sub = leg[(leg["strike"] == strike) & (leg["expiry"] == expiry)]
    if sub.empty:
        return None
    basket = sub.groupby("snap_dt").apply(
        lambda g: float(g.loc[g["opt_type"] == "CE", "last_price"].sum()
                        + g.loc[g["opt_type"] == "PE", "last_price"].sum()),
        include_groups=False,
    )
    if basket.empty:
        return None
    return round(LOT_SIZE * float((basket.max() - entry_premium)), 2)


def paper_trade_block(date: str, expiry_leg: pd.DataFrame) -> dict:
    """Join the live 0DTE paper straddle for an expiry-Tue: per-leg fills, net, stop.

    stop_fired (doc's "exit_reason + time-based"): True if any leg's exit_reason names
    a stop, OR the latest leg exit is strictly before the 15:10 flat time (an early
    exit = a basket-stop/target fire). Straddle MAE is a best-effort chain estimate.
    Returns {"paper_trade": None, ...} if no trade is stored for the date.
    """
    if not PAPER_DB.exists():
        return {"paper_trade": None, "paper_note": "paper-0dte.db absent"}
    con = sqlite3.connect(f"file:{PAPER_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT symbol, side, quantity, entry_price, entry_ts, exit_price, exit_ts, "
            "exit_reason, gross_pnl, net_pnl FROM fn_trades "
            "WHERE session_date = ? AND strategy_id = ? ORDER BY id",
            (date, STRADDLE_STRATEGY_ID),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return {"paper_trade": None, "paper_note": "no straddle trade for date"}

    cols = ["symbol", "side", "quantity", "entry_price", "entry_ts", "exit_price",
            "exit_ts", "exit_reason", "gross_pnl", "net_pnl"]
    legs = [dict(zip(cols, r)) for r in rows]

    exit_times = [str(l["exit_ts"]) for l in legs if l["exit_ts"]]
    reasons = [str(l["exit_reason"] or "") for l in legs]

    def _hhmm(ts: str) -> str:
        try:
            return datetime.fromisoformat(ts).astimezone(IST).strftime("%H:%M")
        except Exception:
            return "99:99"

    reason_stop = any("stop" in r.lower() for r in reasons)
    time_stop = any(_hhmm(t) < FLAT_EXIT_TIME for t in exit_times) if exit_times else False
    stop_fired = bool(reason_stop or time_stop)

    straddle_net = round(float(sum(l["net_pnl"] for l in legs)), 2)
    entry_ts = min((l["entry_ts"] for l in legs if l["entry_ts"]), default=None)
    exit_ts = max((l["exit_ts"] for l in legs if l["exit_ts"]), default=None)

    # entry strike (from the CE/PE symbols) for the MAE estimate
    strike = None
    try:
        strike = float(legs[0]["symbol"].split("-")[-2])
    except Exception:
        pass
    entry_premium = float(sum(l["entry_price"] for l in legs)) if legs else None
    expiry = str(expiry_leg["expiry"].iloc[0]) if len(expiry_leg) else None
    mae = _straddle_mae(expiry_leg, strike, expiry, entry_premium) if strike and expiry else None

    return {
        "paper_trade": {
            "legs": [
                {
                    "symbol": l["symbol"], "side": l["side"], "quantity": l["quantity"],
                    "entry_price": l["entry_price"], "exit_price": l["exit_price"],
                    "entry_ts": l["entry_ts"], "exit_ts": l["exit_ts"],
                    "exit_reason": l["exit_reason"],
                    "gross_pnl": round(float(l["gross_pnl"]), 2),
                    "net_pnl": round(float(l["net_pnl"]), 2),
                }
                for l in legs
            ],
            "straddle_net": straddle_net,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "stop_fired": stop_fired,
            "straddle_mae": mae,
        }
    }


# ----------------------------------------------------------- row construction

def _select_blocks(df: pd.DataFrame) -> list[tuple[str, int]]:
    """Return the (block_name, dte) pairs to log for a session.

    * expiry_0dte -> dte==0, if present (expiry-Tuesday only).
    * near_weekly -> the smallest dte > 0 (the nearest non-expiring weekly). On an
      expiry-Tuesday this is exactly 7; on other sessions it is the true near-weekly
      dte. This is the doc's mechanism-ancillary "dte=7 near-weekly" logged EVERY
      session (documented generalisation -- literal dte==7 would emit ancillary rows
      only on Tuesdays, defeating the "more days" purpose).
    """
    dtes = sorted(int(x) for x in df["dte"].dropna().unique())
    blocks: list[tuple[str, int]] = []
    if 0 in dtes:
        blocks.append(("expiry_0dte", 0))
    positive = [d for d in dtes if d > 0]
    if positive:
        blocks.append(("near_weekly", positive[0]))
    return blocks


def build_rows(date: str, df: pd.DataFrame) -> tuple[list[dict], str | None]:
    """Build the eval rows for one session. Returns (rows, skip_reason).

    skip_reason is set (and rows empty) for a session that cannot be logged
    (e.g. 2026-06-21: a weekend pre-collection with a single 09:14 snapshot and no
    09:15-09:20 window).
    """
    n_snaps = df["snap_dt"].nunique()
    weekday = pd.Timestamp(date).day_name()

    # A loggable session must have a real market-open neighbourhood: at least one
    # snapshot in 09:15-09:20 (spot0's window). This excludes off-hours collections
    # such as 2026-06-21 (2 snaps at ~22:41, no RTH) -- skipped with a reason rather
    # than logging a poisoned 0-range row.
    tt = df["snap_dt"].dt.strftime("%H:%M:%S")
    if not ((tt >= SPOT0_START) & (tt <= SPOT0_END)).any():
        return [], f"no 09:15-09:20 window (snaps={n_snaps}); off-hours/partial collection"

    blocks = _select_blocks(df)
    if not blocks:
        return [], "no dte blocks in chain"

    rows: list[dict] = []
    for block_name, dte in blocks:
        leg = df[df["dte"] == dte].copy()
        sig = signal_block(leg)
        if not sig:
            return [], f"unusable leg (dte={dte}, snaps={n_snaps}); no spot0/OI"
        out = outcome_block(date, leg)
        is_expiry = block_name == "expiry_0dte"
        row = {
            "schema_version": SCHEMA_VERSION,
            "date": date,
            "weekday": weekday,
            "block": block_name,
            "dte": dte,
            "expiry": str(leg["expiry"].iloc[0]),
            "n_snaps": int(n_snaps),
            "ancillary": (not is_expiry),
            "_designonly": bool(is_expiry and date in DESIGNONLY_DATES),
            **sig,
            **out,
            "split_label": None,           # filled by the running-median pass
            "split_median_pin_gap_pct": None,
        }
        if is_expiry:
            row.update(paper_trade_block(date, leg))
        rows.append(row)
    return rows, None


# ------------------------------------------------------- median-split labelling

def apply_split_labels(rows: list[dict]) -> list[dict]:
    """Assign the running-median PIN-GAP trade/skip label across all rows in place.

    The median-split is a Stage-2 0DTE-P&L construct, so it is computed over the
    accumulated NON-designonly expiry_0dte rows ONLY, in date order (leakage-free:
    each row's label uses the running median of pin-gaps up to and including itself).
    Small pin-gap => spot already near the pin => FAVORABLE (trade); large => skip.
    Rows are labelled:
      * "designonly" for burned expiry_0dte rows (never enter an aggregate);
      * "ancillary"  for near_weekly rows (mechanism-only; not a 0DTE-P&L population);
      * "insufficient_history" for eval rows before MIN_SPLIT_N have accrued;
      * "favorable" / "unfavorable" thereafter.
    """
    ordered = sorted(rows, key=lambda r: (r["date"], r["block"]))
    eval_gaps: list[float] = []
    for r in ordered:
        if r["block"] != "expiry_0dte":
            r["split_label"] = "ancillary"
            r["split_median_pin_gap_pct"] = None
            continue
        if r["_designonly"]:
            r["split_label"] = "designonly"
            r["split_median_pin_gap_pct"] = None
            continue
        eval_gaps.append(float(r["pin_gap_pct"]))
        if len(eval_gaps) < MIN_SPLIT_N:
            r["split_label"] = "insufficient_history"
            r["split_median_pin_gap_pct"] = None
        else:
            med = float(np.median(eval_gaps))
            r["split_median_pin_gap_pct"] = round(med, 4)
            r["split_label"] = "favorable" if r["pin_gap_pct"] <= med else "unfavorable"
    return ordered


# --------------------------------------------------------------- persistence

def _read_existing() -> list[dict]:
    if not OUT_JSONL.exists():
        return []
    out = []
    for line in OUT_JSONL.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _write(rows: list[dict]) -> None:
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda r: (r["date"], r["block"]))
    OUT_JSONL.write_text("".join(json.dumps(r) + "\n" for r in ordered))


def upsert(new_rows: list[dict], replace_dates: set[str]) -> list[dict]:
    """Merge new rows into the JSONL, replacing all rows for `replace_dates`.

    Idempotent by (date, block): a re-run of a date drops its old rows first. After
    the merge the running-median split labels are recomputed across ALL rows (a
    re-inserted date shifts the running medians of later eval rows), then written.
    """
    kept = [r for r in _read_existing() if r["date"] not in replace_dates]
    merged = kept + new_rows
    labelled = apply_split_labels(merged)
    _write(labelled)
    return labelled


# ---------------------------------------------------------------------- CLI

def process_date(date: str, quiet: bool = False) -> tuple[list[dict], str | None]:
    df = _read_chain(date)
    if df is None:
        if not quiet:
            print(f"[skip] {date}: no chain parquet")
        return [], "no chain parquet"
    rows, skip = build_rows(date, df)
    if skip:
        if not quiet:
            print(f"[skip] {date}: {skip}")
        return [], skip
    return rows, None


def _sanity_print(rows: list[dict]) -> None:
    for r in sorted(rows, key=lambda r: (r["date"], r["block"])):
        tag = " [DESIGN-ONLY]" if r["_designonly"] else ""
        pcr = r.get("pcr")
        unrel = "" if r["outcome_reliable"] else " [OUTCOME-UNRELIABLE]"
        line = (f"  {r['date']} {r['weekday'][:3]} {r['block']:<11} dte={r['dte']:<2} "
                f"spot0={r['spot0']:<9} MP={r['max_pain']:<9} "
                f"pin_gap={r['pin_gap_pct']:.3f}%  PCR={pcr}  "
                f"walls[{r['put_wall']:.0f}<=s<={r['call_wall']:.0f}]={r['wall_bracket_flag']}  "
                f"range={r['realized_range_pct']}%({r['spot_source']})  "
                f"label={r['split_label']}{tag}{unrel}")
        print(line)
        pt = r.get("paper_trade")
        if pt:
            print(f"      paper: net=Rs{pt['straddle_net']:,.0f} stop_fired={pt['stop_fired']} "
                  f"exit={pt['exit_ts']} mae={pt['straddle_mae']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Max-pain/PCR/OI-wall logging harness (pre-reg).")
    ap.add_argument("--date", help="Session date YYYY-MM-DD (default: today, IST).")
    ap.add_argument("--backfill", action="store_true",
                    help="Process ALL collected NIFTY_CHAIN_FWD sessions.")
    args = ap.parse_args(argv)

    if args.backfill:
        dates = available_dates()
        all_new: list[dict] = []
        skipped: list[tuple[str, str]] = []
        for d in dates:
            rows, skip = process_date(d)
            if skip:
                skipped.append((d, skip))
            else:
                all_new.extend(rows)
        final = upsert(all_new, replace_dates={r["date"] for r in all_new})
        print(f"\n[backfill] wrote {len(all_new)} rows for "
              f"{len({r['date'] for r in all_new})} sessions -> {OUT_JSONL.relative_to(PROJECT)}")
        if skipped:
            print("[backfill] skipped sessions:")
            for d, why in skipped:
                print(f"  {d}: {why}")
        print("[backfill] signal blocks (design-only where flagged; look freely -- "
              "evaluation discipline is downstream):")
        _sanity_print(all_new)
        return 0

    date = args.date or datetime.now(IST).strftime("%Y-%m-%d")
    rows, skip = process_date(date)
    if skip:
        return 0
    final = upsert(rows, replace_dates={date})
    print(f"[maxpain] logged {len(rows)} row(s) for {date} -> {OUT_JSONL.relative_to(PROJECT)}")
    _sanity_print(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
