"""TN-2 (Gen-8): entry/exit-TIME robustness of the 0DTE short straddle.

NOT an edge hunt — an OPERATIONAL risk question we should answer because we trade
this every Tuesday: is the live 09:16 drift safe? Is 09:20 entry / 15:10 flat a
robust PLATEAU or a knife-edge? Pre-registered interpretation (so it can't become
a fishing trip):
  PASS/reassurance: 09:20/15:10 sits on a plateau (neighbors within ~0.10 PF@2x)
    AND the ranking is consistent across BOTH eras -> keep 09:20/15:10; 09:16 is
    immaterial (live drift safe).
  GENUINE FINDING (rare): a different cell is materially better (>=0.15 PF@2x) AND
    both-era-consistent AND not a lone spike -> only then consider a change.
  Do NOT pick the single best Tue-era cell (the n=40 small-sample trap).

Frozen otherwise to gen6: 25% premium basket stop, no take, 1 lot, synthetic-BS.
Run: cd /home/ubuntu/projects/algo-trader-strategies && \
     ./.venv/bin/python scripts/tn2_time_robustness.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from scripts.zerodte_straddle import (  # noqa: E402
    bs, straddle_val, load_leg, nifty_1m, iv_norm, expiry_days,
    FENCE_LAST_THURSDAY, COSTS, _OPT, YEAR_MIN,
    ENTRY_SLIP, STOP_SLIP, EXIT_SLIP, summarize,
)
from algotrader.data.instruments import _nifty_lot  # noqa: E402
from algotrader.core import Side  # noqa: E402

ENTRIES = ["09:16", "09:20", "09:30", "09:45"]
FLATS = ["15:00", "15:10", "15:15"]
STOP_PCT = 0.25


def day_cache(day: str) -> dict | None:
    ce, pe = load_leg(day, "CE"), load_leg(day, "PE")
    sp = nifty_1m(day)
    if ce is None or pe is None or sp.empty:
        return None
    return {"ce": ce, "pe": pe, "sp": sp,
            "ivc": iv_norm(ce.set_index("t")["iv"])[0],
            "ivp": iv_norm(pe.set_index("t")["iv"])[0],
            "lot": _nifty_lot(date.fromisoformat(day)), "day": day}


def run_cell(c: dict, entry_t: str, flat_t: str, slip: float) -> dict | None:
    ce, pe, sp = c["ce"], c["pe"], c["sp"]
    e_ce = ce[ce["t"] == entry_t]; e_pe = pe[pe["t"] == entry_t]
    e_sp = sp[sp["t"] < entry_t].tail(1)
    if e_ce.empty or e_pe.empty or e_sp.empty:
        return None
    prem_c, prem_p = float(e_ce.iloc[0]["close"]), float(e_pe.iloc[0]["close"])
    spot0 = float(e_sp.iloc[0]["close"])
    iv_c0 = c["ivc"].get(entry_t, np.nan); iv_p0 = c["ivp"].get(entry_t, np.nan)
    if not (prem_c > 0 and prem_p > 0) or iv_c0 != iv_c0 or iv_p0 != iv_p0:
        return None
    k = round(spot0 / 50.0) * 50.0
    es = prem_c + prem_p
    f = lambda t: straddle_val(spot0, k, t, max(iv_c0, .02), max(iv_p0, .02)) - es
    lo, hi = 10 / (365 * 24 * 60), 1.2 / 365
    try:
        if f(lo) * f(hi) > 0:
            return None
        tau0 = brentq(f, lo, hi, xtol=1e-9)
    except Exception:
        return None
    e_sl, s_sl, x_sl = ENTRY_SLIP * slip, STOP_SLIP * slip, EXIT_SLIP * slip
    credit = es * (1 - e_sl); stop_level = es * (1 + STOP_PCT)
    t_entry = e_ce.iloc[0]["ts"]
    path = sp[(sp["t"] > entry_t) & (sp["t"] <= flat_t)]
    if path.empty:
        return None
    exit_val = None
    for row in path.itertuples():
        elapsed = (row.ts - t_entry).total_seconds() / 60.0
        tau = max(tau0 - elapsed / YEAR_MIN, 1e-7)
        iv_c = c["ivc"].get(row.t, np.nan); iv_c = float(iv_c) if iv_c == iv_c else iv_c0
        iv_p = c["ivp"].get(row.t, np.nan); iv_p = float(iv_p) if iv_p == iv_p else iv_p0
        v_s = straddle_val(row.close, k, tau, max(iv_c, .02), max(iv_p, .02))
        if v_s >= stop_level:
            exit_val = stop_level * (1 + s_sl); break
    if exit_val is None:
        last = path.iloc[-1]
        elapsed = (last["ts"] - t_entry).total_seconds() / 60.0
        tau = max(tau0 - elapsed / YEAR_MIN, 1e-7)
        iv_c = c["ivc"].get(last["t"], np.nan); iv_c = float(iv_c) if iv_c == iv_c else iv_c0
        iv_p = c["ivp"].get(last["t"], np.nan); iv_p = float(iv_p) if iv_p == iv_p else iv_p0
        exit_val = straddle_val(last["close"], k, tau, max(iv_c, .02), max(iv_p, .02)) * (1 + x_sl)
    dd = date.fromisoformat(c["day"])
    costs = (COSTS.round_trip(_OPT, Side.SELL, 1, prem_c, exit_val / 2, dd).total +
             COSTS.round_trip(_OPT, Side.SELL, 1, prem_p, exit_val / 2, dd).total)
    return {"date": c["day"], "net": round((credit - exit_val) * c["lot"] - costs, 2)}


def main():
    days = expiry_days()
    cache = {d: c for d in days if (c := day_cache(d))}
    fenced = [d for d in cache if d <= FENCE_LAST_THURSDAY]
    holdout = [d for d in cache if d > FENCE_LAST_THURSDAY]
    print(f"built {len(cache)} day caches (fenced {len(fenced)}, holdout {len(holdout)})")

    for scope, ds in (("FENCED (Thu era, n=%d)" % len(fenced), fenced),
                      ("HOLDOUT (Tue era, n=%d)" % len(holdout), holdout)):
        print(f"\n================ {scope} ================")
        print(f"  {'entry':>6} {'flat':>6} {'PF@1x':>6} {'PF@2x':>6} {'n':>4} "
              f"{'avg':>6} {'worstTr':>8} {'posW':>5}")
        for entry in ENTRIES:
            for flat in FLATS:
                t1 = [r for d in ds if (r := run_cell(cache[d], entry, flat, 1.0))]
                t2 = [r for d in ds if (r := run_cell(cache[d], entry, flat, 2.0))]
                s1 = summarize(t1, "1x"); s2 = summarize(t2, "2x")
                star = " <-" if (entry == "09:20" and flat == "15:10") else ""
                print(f"  {entry:>6} {flat:>6} {str(s1.get('pf')):>6} {str(s2.get('pf')):>6} "
                      f"{s1.get('n',0):>4} {str(s1.get('avg_per_trade')):>6} "
                      f"{str(s2.get('worst_trade')):>8} {str(s1.get('pos_window_frac')):>5}{star}")


if __name__ == "__main__":
    main()
