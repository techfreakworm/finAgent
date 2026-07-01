"""P1 VIX-gated 0DTE - 2x slippage robustness check.

Same logic as p1_vix_gated_0dte.py but with ENTRY_SLIP=0.02, STOP_SLIP=0.05, EXIT_SLIP=0.02.
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import date
from zoneinfo import ZoneInfo
import math
import json
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from scripts.zerodte_straddle import (  # noqa: E402
    summarize, expiry_days, FENCE_LAST_THURSDAY, IST,
    nifty_1m, load_leg, iv_norm, straddle_val, SQUARE_T, R, YEAR_MIN,
    _nifty_lot, _OPT, COSTS,
)
from algotrader.core import Side  # noqa: E402

# 2x slippage
ENTRY_SLIP = 0.02
STOP_SLIP  = 0.05
EXIT_SLIP  = 0.02

WINDOW  = 252
TERCILE = 100.0 / 3.0
ENTRY_T, STOP_PCT, TAKE_PCT = "09:20", 0.25, None


def run_day_2x(day: str, entry_t: str, stop_pct: float, take_pct):
    ce, pe = load_leg(day, "CE"), load_leg(day, "PE")
    sp = nifty_1m(day)
    if ce is None or pe is None or sp.empty:
        return None
    e_ce = ce[ce["t"] == entry_t]; e_pe = pe[pe["t"] == entry_t]
    e_sp = sp[sp["t"] < entry_t].tail(1)
    if e_ce.empty or e_pe.empty or e_sp.empty:
        return None
    prem_c, prem_p = float(e_ce.iloc[0]["close"]), float(e_pe.iloc[0]["close"])
    spot0 = float(e_sp.iloc[0]["close"])
    iv_c_series, _ = iv_norm(ce.set_index("t")["iv"])
    iv_p_series, _ = iv_norm(pe.set_index("t")["iv"])
    iv_c0 = iv_c_series.get(entry_t, np.nan)
    iv_p0 = iv_p_series.get(entry_t, np.nan)
    if not (prem_c > 0 and prem_p > 0) or iv_c0 != iv_c0 or iv_p0 != iv_p0:
        return None
    k = round(spot0 / 50.0) * 50.0
    entry_straddle = prem_c + prem_p

    f = lambda t: straddle_val(spot0, k, t, max(iv_c0, .02), max(iv_p0, .02)) - entry_straddle
    lo, hi = 10 / (365 * 24 * 60), 1.2 / 365
    try:
        if f(lo) * f(hi) > 0:
            return None
        tau0 = brentq(f, lo, hi, xtol=1e-9)
    except Exception:
        return None

    credit = entry_straddle * (1 - ENTRY_SLIP)
    stop_level = entry_straddle * (1 + stop_pct)
    take_level = entry_straddle * (1 - take_pct) if take_pct else None
    lot = _nifty_lot(date.fromisoformat(day))

    t_entry = e_ce.iloc[0]["ts"]
    path = sp[(sp["t"] > entry_t) & (sp["t"] <= SQUARE_T)]
    exit_val, exit_reason, exit_t = None, None, None
    for row in path.itertuples():
        elapsed = (row.ts - t_entry).total_seconds() / 60.0
        tau = max(tau0 - elapsed / YEAR_MIN, 1e-7)
        iv_c = iv_c_series.get(row.t, np.nan); iv_c = float(iv_c) if iv_c == iv_c else iv_c0
        iv_p = iv_p_series.get(row.t, np.nan); iv_p = float(iv_p) if iv_p == iv_p else iv_p0
        v = straddle_val(row.close, k, tau, max(iv_c, .02), max(iv_p, .02))
        if v >= stop_level:
            exit_val = stop_level * (1 + STOP_SLIP)
            exit_reason, exit_t = "stop", row.t
            break
        if take_level and v <= take_level:
            exit_val = v * (1 + EXIT_SLIP)
            exit_reason, exit_t = "take", row.t
            break
    if exit_val is None:
        last = path.iloc[-1]
        elapsed = (last.ts - t_entry).total_seconds() / 60.0
        tau = max(tau0 - elapsed / YEAR_MIN, 1e-7)
        iv_c = iv_c_series.get(last.t, np.nan); iv_c = float(iv_c) if iv_c == iv_c else iv_c0
        iv_p = iv_p_series.get(last.t, np.nan); iv_p = float(iv_p) if iv_p == iv_p else iv_p0
        exit_val = straddle_val(last.close, k, tau, max(iv_c, .02), max(iv_p, .02)) * (1 + EXIT_SLIP)
        exit_reason, exit_t = "square_off", last.t

    cb1 = COSTS.round_trip(_OPT, Side.SELL, 1, prem_c, exit_val / 2, date.fromisoformat(day))
    cb2 = COSTS.round_trip(_OPT, Side.SELL, 1, prem_p, exit_val / 2, date.fromisoformat(day))
    costs = cb1.total + cb2.total
    net = (credit - exit_val) * lot - costs
    return {"date": day, "entry_straddle": round(entry_straddle, 2), "k": k,
            "exit_val": round(exit_val, 2), "net": round(net, 2),
            "reason": exit_reason, "exit_t": exit_t,
            "tau0_min": round(tau0 * YEAR_MIN, 1), "lot": lot}


def vix_series():
    df = pd.concat([pd.read_parquet(f) for f in
                    sorted((PROJECT / "data/cache/INDIAVIX/1m").glob("*.parquet"))])
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
    df["d"] = df["ts"].dt.date.astype(str)
    df["t"] = df["ts"].dt.strftime("%H:%M")
    daily_close = df.sort_values("ts").groupby("d")["close"].last()
    op = df[df["t"] == "09:15"].set_index("d")["close"]
    return daily_close, op


def trailing_pct(daily_close, day, vix_open):
    prior = daily_close[daily_close.index < day]
    if len(prior) < WINDOW:
        return None
    w = prior.iloc[-WINDOW:]
    return float((w < vix_open).mean() * 100.0)


def main():
    daily_close, vix_open = vix_series()
    days = expiry_days()
    fence = FENCE_LAST_THURSDAY

    rows = []
    for d in days:
        r = run_day_2x(d, ENTRY_T, STOP_PCT, TAKE_PCT)
        if r is None:
            continue
        vo = vix_open.get(d)
        pct = trailing_pct(daily_close, d, float(vo)) if vo is not None else None
        gated_in = (pct is None) or (pct >= TERCILE)
        rows.append({**r, "vix_open": None if vo is None else round(float(vo), 2),
                     "vix_pct": None if pct is None else round(pct, 1),
                     "ungateable": pct is None, "gated_in": gated_in,
                     "era": "fenced" if d <= fence else "holdout"})

    def report(era):
        ev = [r for r in rows if r["era"] == era]
        ungated = ev
        gated = [r for r in ev if r["gated_in"]]
        skipped = [r for r in ev if not r["gated_in"]]
        ung = summarize(ungated, f"{era}-ungated")
        gat = summarize(gated, f"{era}-gated")
        skip_net = round(sum(r["net"] for r in skipped))
        n_ungateable = sum(1 for r in ev if r["ungateable"])
        print(f"\n===== {era.upper()} ({len(ev)} tradeable expiry days; "
              f"{n_ungateable} ungateable/no-252d-history) =====")
        for s in (ung, gat):
            if s["n"]:
                print(f"  {s['label']:<18} n={s['n']:>3} net=Rs{s['net']:>9,} "
                      f"PF={s['pf']} win={s['win_rate']:.0%} "
                      f"posW={s['pos_window_frac']:.0%} DD=Rs{s['max_dd']:>9,} "
                      f"worstTrade=Rs{s['worst_trade']:>8,} avg=Rs{s['avg_per_trade']:>6,}")
        print(f"  SKIPPED (bottom-VIX-tercile): n={len(skipped)} "
              f"aggregate net=Rs{skip_net:,}  "
              f"[gate justified iff this is <= 0]")
        if ung["n"] and gat["n"]:
            ok = (gat["pf"] is not None and ung["pf"] is not None
                  and gat["pf"] >= ung["pf"] and gat["n"] < ung["n"]
                  and skip_net <= 0)
            print(f"  PRE-REG VERDICT [{era}]: {'PASS' if ok else 'FAIL/NEUTRAL'} "
                  f"(PF {gat['pf']} vs {ung['pf']}; n {gat['n']} vs {ung['n']}; "
                  f"skipped-net {skip_net:,})")

    print(f"2x SLIPPAGE RUN: ENTRY_SLIP={ENTRY_SLIP} STOP_SLIP={STOP_SLIP} EXIT_SLIP={EXIT_SLIP}")
    print(f"Total tradeable expiry days: {len(rows)} "
          f"(fence={fence}; window={WINDOW}d; bottom-tercile cutoff={TERCILE:.1f}pct)")
    for era in ("fenced", "holdout"):
        report(era)


if __name__ == "__main__":
    main()
