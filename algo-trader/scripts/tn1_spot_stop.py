"""TN-1 (Gen-8): SPOT-ANCHORED stop for the 0DTE short straddle.

NOT a new edge — a ROBUSTNESS play attacking the franchise's #1 risk: option
stop-fill sensitivity (PF 1.47->1.25->1.08 at 1x/2x/3x slippage). A |spot-spot0|
trigger is observed on the LIQUID NIFTY future (deterministic) and fires before
the option premium balloons on a fast move, so it should flatten the fill curve.

Variants (entry 09:20, flat 15:10, synthetic-BS marking, costs/slips identical
to gen6 for a clean A/B):
  S0 = 25%-premium basket stop                 (gen6 CONTROL; must reproduce gen6)
  S1 = spot-move stop: exit when |spot-spot0| >= X   X in {80,100,120,140} pts
  S2 = HYBRID first-to-fire: 25%-premium OR |spot|>=X (premium kept as backstop)
  S3 = portable: |spot-spot0| >= m * sigma_1d (sigma_1d = spot0*iv_atm/sqrt(252)),
       m in {0.4,0.5,0.6,0.7}   (DTE-invariant -> reusable for P3)

Decision axis = the FILL-SENSITIVITY CURVE (PF at 1x/2x/3x), not raw PF.
KEEP if (FENCED n=218): PF@1x >= 0.95*S0 AND PF@2x improved >=0.10 abs AND PF@3x
improved >=0.10 abs AND worst-trade no worse than S0 AND plateau across >=2
adjacent params. Holdout = informational paired-delta. KILL otherwise.

Run: cd /home/ubuntu/projects/algo-trader-strategies && \
     ./.venv/bin/python scripts/tn1_spot_stop.py
"""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from scripts.zerodte_straddle import (  # noqa: E402
    bs, straddle_val, load_leg, nifty_1m, iv_norm, expiry_days,
    FENCE_LAST_THURSDAY, COSTS, _OPT, YEAR_MIN, SQUARE_T,
    ENTRY_SLIP, STOP_SLIP, EXIT_SLIP, summarize,
)
from algotrader.data.instruments import _nifty_lot  # noqa: E402
from algotrader.core import Side  # noqa: E402

ENTRY_T, STOP_PCT = "09:20", 0.25


def day_struct(day: str) -> dict | None:
    ce, pe = load_leg(day, "CE"), load_leg(day, "PE")
    sp = nifty_1m(day)
    if ce is None or pe is None or sp.empty:
        return None
    e_ce = ce[ce["t"] == ENTRY_T]; e_pe = pe[pe["t"] == ENTRY_T]
    e_sp = sp[sp["t"] < ENTRY_T].tail(1)
    if e_ce.empty or e_pe.empty or e_sp.empty:
        return None
    prem_c, prem_p = float(e_ce.iloc[0]["close"]), float(e_pe.iloc[0]["close"])
    spot0 = float(e_sp.iloc[0]["close"])
    iv_c_s, _ = iv_norm(ce.set_index("t")["iv"]); iv_p_s, _ = iv_norm(pe.set_index("t")["iv"])
    iv_c0 = iv_c_s.get(ENTRY_T, np.nan); iv_p0 = iv_p_s.get(ENTRY_T, np.nan)
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
    t_entry = e_ce.iloc[0]["ts"]
    path = sp[(sp["t"] > ENTRY_T) & (sp["t"] <= SQUARE_T)]
    mins = []
    for row in path.itertuples():
        elapsed = (row.ts - t_entry).total_seconds() / 60.0
        tau = max(tau0 - elapsed / YEAR_MIN, 1e-7)
        iv_c = iv_c_s.get(row.t, np.nan); iv_c = float(iv_c) if iv_c == iv_c else iv_c0
        iv_p = iv_p_s.get(row.t, np.nan); iv_p = float(iv_p) if iv_p == iv_p else iv_p0
        v_s = straddle_val(row.close, k, tau, max(iv_c, .02), max(iv_p, .02))
        mins.append((row.close, v_s))
    if not mins:
        return None
    iv_atm = (iv_c0 + iv_p0) / 2
    return {"day": day, "spot0": spot0, "k": k, "es": es, "lot": _nifty_lot(date.fromisoformat(day)),
            "prem_c": prem_c, "prem_p": prem_p, "iv_atm": iv_atm,
            "sigma_1d": spot0 * iv_atm / math.sqrt(252), "mins": mins}


def outcome(d: dict, variant: str, param: float, slip: float) -> dict:
    es, lot, spot0 = d["es"], d["lot"], d["spot0"]
    e_sl, s_sl, x_sl = ENTRY_SLIP * slip, STOP_SLIP * slip, EXIT_SLIP * slip
    stop_level = es * (1 + STOP_PCT)
    credit = es * (1 - e_sl)
    thr = param * d["sigma_1d"] if variant == "S3" else param  # spot-move threshold (pts)

    exit_val = reason = None
    for (spot, v_s) in d["mins"]:
        move = abs(spot - spot0)
        prem_fire = v_s >= stop_level
        spot_fire = move >= thr
        if variant == "S0":
            if prem_fire:
                exit_val, reason = stop_level * (1 + s_sl), "prem"; break
        elif variant == "S1":
            if spot_fire:
                exit_val, reason = v_s * (1 + s_sl), "spot"; break
        elif variant == "S2":
            if prem_fire:
                exit_val, reason = stop_level * (1 + s_sl), "prem"; break
            if spot_fire:
                exit_val, reason = v_s * (1 + s_sl), "spot"; break
        elif variant == "S3":
            if spot_fire:
                exit_val, reason = v_s * (1 + s_sl), "spot"; break
    if exit_val is None:
        exit_val, reason = d["mins"][-1][1] * (1 + x_sl), "square"

    dd = date.fromisoformat(d["day"])
    costs = (COSTS.round_trip(_OPT, Side.SELL, 1, d["prem_c"], exit_val / 2, dd).total +
             COSTS.round_trip(_OPT, Side.SELL, 1, d["prem_p"], exit_val / 2, dd).total)
    return {"date": d["day"], "net": round((credit - exit_val) * lot - costs, 2),
            "reason": reason, "exit_prem": round(exit_val, 2)}


def main():
    days = expiry_days()
    structs = {d: s for d in days if (s := day_struct(d))}
    fenced = [d for d in structs if d <= FENCE_LAST_THURSDAY]
    holdout = [d for d in structs if d > FENCE_LAST_THURSDAY]
    print(f"built {len(structs)} day structs (fenced {len(fenced)}, holdout {len(holdout)})")

    GRID = {"S0": [0], "S1": [80, 100, 120, 140], "S2": [80, 100, 120, 140],
            "S3": [0.4, 0.5, 0.6, 0.7]}

    def curve(variant, param, dayset):
        out = {}
        for sl in (1.0, 2.0, 3.0):
            s = summarize([outcome(structs[d], variant, param, sl) for d in dayset], variant)
            out[sl] = s
        return out

    # baseline S0 for delta reference
    base = {scope: curve("S0", 0, ds) for scope, ds in (("fenced", fenced), ("holdout", holdout))}

    for scope, ds in (("FENCED (Thu era, n=%d)" % len(fenced), fenced),
                      ("HOLDOUT (Tue era, n=%d) [informational]" % len(holdout), holdout)):
        b = base["fenced" if scope.startswith("FENCED") else "holdout"]
        print(f"\n================ {scope} ================")
        print(f"  {'variant':<14} {'PF@1x':>6} {'PF@2x':>6} {'PF@3x':>6} {'worstTr@2x':>11} "
              f"{'dPF2x':>6} {'dPF3x':>6}")
        for variant in ("S0", "S1", "S2", "S3"):
            for p in GRID[variant]:
                c = curve(variant, p, ds)
                pf = {sl: (c[sl]["pf"] if c[sl]["n"] else None) for sl in (1.0, 2.0, 3.0)}
                wt2 = c[2.0]["worst_trade"] if c[2.0]["n"] else None
                d2 = (pf[2.0] - b[2.0]["pf"]) if (pf[2.0] and b[2.0]["pf"]) else None
                d3 = (pf[3.0] - b[3.0]["pf"]) if (pf[3.0] and b[3.0]["pf"]) else None
                label = variant if variant == "S0" else f"{variant} {p}"
                print(f"  {label:<14} {str(pf[1.0]):>6} {str(pf[2.0]):>6} {str(pf[3.0]):>6} "
                      f"{str(wt2):>11} {('%+.3f'%d2) if d2 is not None else '   -':>6} "
                      f"{('%+.3f'%d3) if d3 is not None else '   -':>6}")

    # --- direct thesis diagnostic: on FENCED stop-days, does the spot trigger exit CHEAPER?
    print("\n---- THESIS DIAGNOSTIC (fenced): exit premium on stop-fire days, spot vs premium ----")
    for variant, p in (("S1", 120), ("S2", 120)):
        s0_stop, var_stop = [], []
        for d in fenced:
            o0 = outcome(structs[d], "S0", 0, 1.0)
            ov = outcome(structs[d], variant, p, 1.0)
            if o0["reason"] == "prem" and ov["reason"] in ("spot", "prem"):
                s0_stop.append(o0["exit_prem"]); var_stop.append(ov["exit_prem"])
        if s0_stop:
            import statistics as st
            print(f"  {variant}@{p}: matched stop-days n={len(s0_stop)} | "
                  f"mean exit prem  S0={st.mean(s0_stop):.1f}  {variant}={st.mean(var_stop):.1f}  "
                  f"(lower = cheaper buyback = thesis holds)")


if __name__ == "__main__":
    main()
