"""VWAP-of-premium adaptive EXIT for the 0DTE short straddle (TV-mining candidate).

Idea (algotest 'VWAP straddle'): exit the short straddle when its combined premium
CROSSES ABOVE its session VWAP — an adaptive trailing exit that may cut trend-day
losers earlier than our fixed 25% stop (which is what cost us −₹3,548 on 06-23).

HONEST CONSTRAINT: historical option volume is unavailable, so the VWAP is a
volume-free TWAP proxy (running mean of the straddle value since entry). A true
volume-weighted VWAP test is FORWARD-GATED on the collector's option volume.

Variants (entry 09:20, flat 15:10, synthetic-BS value path, costs/slips identical
to gen6 for clean A/B):
  V0  = 25% premium basket stop                         (gen6 CONTROL; reproduces gen6)
  VW  = exit on first post-warmup bar where V crosses UP through its TWAP; warmup
        W ∈ {5,10,15} min after entry
  VWH = HYBRID: 25% stop OR VW cross, first-to-fire (W=10)  — keeps the hard backstop

Decision axis = fill-sensitivity curve (PF @1x/2x/3x) + paired delta vs V0, on the
FENCED set; holdout = informational. Pre-reg KEEP: a variant improves PF@2x by
≥0.10 abs AND maxDD/worst-trade no worse AND plateau across ≥2 warmups AND survives
holdout paired-delta. Else KILL (the 25% stop stays). Given TN-1 showed the premium
stop is already strong, prior is uncertain-to-skeptical.

Run: cd /home/ubuntu/projects/algo-trader-strategies && \
     ./.venv/bin/python scripts/vwap_premium_exit.py
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
    straddle_val, load_leg, nifty_1m, iv_norm, expiry_days,
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
        mins.append((elapsed, v_s))
    if not mins:
        return None
    return {"day": day, "es": es, "lot": _nifty_lot(date.fromisoformat(day)),
            "prem_c": prem_c, "prem_p": prem_p, "mins": mins}


def outcome(d: dict, variant: str, warmup: float, slip: float) -> dict:
    es, lot = d["es"], d["lot"]
    e_sl, s_sl, x_sl = ENTRY_SLIP * slip, STOP_SLIP * slip, EXIT_SLIP * slip
    stop_level = es * (1 + STOP_PCT)
    credit = es * (1 - e_sl)
    use_stop = variant in ("V0", "VWH")
    use_vwap = variant in ("VW", "VWH")

    exit_val = reason = None
    cum, cnt, prev_below = 0.0, 0, True
    for (elapsed, v_s) in d["mins"]:
        cum += v_s; cnt += 1
        twap = cum / cnt
        if use_stop and v_s >= stop_level:
            exit_val, reason = stop_level * (1 + s_sl), "stop"; break
        if use_vwap and elapsed >= warmup:
            if prev_below and v_s > twap:                 # cross UP through TWAP
                exit_val, reason = v_s * (1 + s_sl), "vwap"; break
        prev_below = v_s <= twap
    if exit_val is None:
        exit_val, reason = d["mins"][-1][1] * (1 + x_sl), "square"

    dd = date.fromisoformat(d["day"])
    costs = (COSTS.round_trip(_OPT, Side.SELL, 1, d["prem_c"], exit_val / 2, dd).total +
             COSTS.round_trip(_OPT, Side.SELL, 1, d["prem_p"], exit_val / 2, dd).total)
    return {"date": d["day"], "net": round((credit - exit_val) * lot - costs, 2), "reason": reason}


def main():
    days = expiry_days()
    structs = {d: s for d in days if (s := day_struct(d))}
    fenced = [d for d in structs if d <= FENCE_LAST_THURSDAY]
    holdout = [d for d in structs if d > FENCE_LAST_THURSDAY]
    print(f"built {len(structs)} structs (fenced {len(fenced)}, holdout {len(holdout)})")

    GRID = [("V0", 0)] + [("VW", w) for w in (5, 10, 15)] + [("VWH", 10)]

    def curve(variant, w, ds):
        return {sl: summarize([outcome(structs[d], variant, w, sl) for d in ds], variant)
                for sl in (1.0, 2.0, 3.0)}

    def mix(variant, w, ds):
        rs = [outcome(structs[d], variant, w, 1.0)["reason"] for d in ds]
        return {k: rs.count(k) for k in ("stop", "vwap", "square")}

    base = {"fenced": curve("V0", 0, fenced), "holdout": curve("V0", 0, holdout)}
    for scope, ds, key in (("FENCED (Thu, n=%d)" % len(fenced), fenced, "fenced"),
                           ("HOLDOUT (Tue, n=%d) [informational]" % len(holdout), holdout, "holdout")):
        b = base[key]
        print(f"\n================ {scope} ================")
        print(f"  {'variant':<9} {'PF@1x':>6} {'PF@2x':>6} {'PF@3x':>6} {'worstTr@2x':>11} "
              f"{'avg@1x':>7} {'dPF2x':>6}  exitmix(stop/vwap/sq)")
        for variant, w in GRID:
            c = curve(variant, w, ds)
            pf = {s: (c[s]["pf"] if c[s]["n"] else None) for s in (1.0, 2.0, 3.0)}
            wt = c[2.0]["worst_trade"] if c[2.0]["n"] else None
            av = c[1.0]["avg_per_trade"] if c[1.0]["n"] else None
            d2 = (pf[2.0] - b[2.0]["pf"]) if (pf[2.0] and b[2.0]["pf"]) else None
            m = mix(variant, w, ds)
            label = variant if variant == "V0" else f"{variant}{w}"
            print(f"  {label:<9} {str(pf[1.0]):>6} {str(pf[2.0]):>6} {str(pf[3.0]):>6} "
                  f"{str(wt):>11} {str(av):>7} {('%+.3f'%d2) if d2 is not None else '   -':>6}"
                  f"  {m['stop']}/{m['vwap']}/{m['square']}")


if __name__ == "__main__":
    main()
