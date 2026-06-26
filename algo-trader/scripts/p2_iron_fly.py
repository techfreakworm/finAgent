"""P2 (Gen-8): does adding defined-risk WINGS to the 0DTE short straddle improve
the TAIL (ruin-control), or does the existing 25% stop already do the job?

Framed as ruin-control, NOT an edge hunt. Three variants on identical gen6 days
(synthetic-BS value path; wings priced SYNTHETICALLY at ATM IV x lambda since the
cache has NO real wing quotes — favorable to wings, so a KILL is robust):
  V0 = naked straddle + 25% basket stop          (gen6 CONTROL; must reproduce gen6)
  V1 = iron fly + 25% basket stop                (wings ADDED, stop rule identical)
  V2 = iron fly, NO stop (ride to 15:10)         (wings as SOLE tail mechanism)

Decision rests on the PAIRED DELTA (Vk minus V0 on the same days) — overfit-robust.
Holdout already consumed (gen6/P1) -> absolute PF on holdout is INFORMATIONAL; the
paired delta is the real signal. True validation = FORWARD real wing quotes.

KEEP a fly variant iff (vs V0, at 2x slippage): maxDD AND worst_trade both better by
>=25%, PF>=1.10, survives lambda=1.30, plateau across >=2 W. Else KILL.

Run: cd /home/ubuntu/projects/algo-trader-strategies && \
     ./.venv/bin/python scripts/p2_iron_fly.py
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
    FENCE_LAST_THURSDAY, COSTS, _OPT, YEAR_MIN, SQUARE_T,
    ENTRY_SLIP, STOP_SLIP, EXIT_SLIP, summarize,
)
from scripts.p1_vix_gated_0dte import vix_series, trailing_pct  # noqa: E402
from algotrader.data.instruments import _nifty_lot  # noqa: E402
from algotrader.core import Side  # noqa: E402

ENTRY_T, STOP_PCT = "09:20", 0.25


def day_struct(day: str) -> dict | None:
    """Entry state + per-minute short-straddle value path (gen6 machinery)."""
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
        mins.append((row.close, tau, v_s))
    if not mins:
        return None
    return {"day": day, "spot0": spot0, "k": k, "es": es, "tau0": tau0,
            "iv_atm": (iv_c0 + iv_p0) / 2, "lot": _nifty_lot(date.fromisoformat(day)),
            "prem_c": prem_c, "prem_p": prem_p, "mins": mins}


def outcome(d: dict, variant: str, W: float, lam: float, slip: float) -> dict:
    es, k, lot, iva, tau0, spot0 = d["es"], d["k"], d["lot"], d["iv_atm"], d["tau0"], d["spot0"]
    e_sl, s_sl, x_sl = ENTRY_SLIP * slip, STOP_SLIP * slip, EXIT_SLIP * slip
    stop_level = es * (1 + STOP_PCT)
    credit = es * (1 - e_sl)
    wings = variant in ("V1", "V2")
    use_stop = variant in ("V0", "V1")
    if wings:
        wc0 = bs(spot0, k + W, tau0, max(iva * lam, .02), True)
        wp0 = bs(spot0, k - W, tau0, max(iva * lam, .02), False)
        wing_cost = (wc0 + wp0) * (1 + e_sl)
    else:
        wc0 = wp0 = wing_cost = 0.0

    exit_vs = exit_spot = exit_tau = None
    reason = None
    for (spot, tau, v_s) in d["mins"]:
        if use_stop and v_s >= stop_level:
            exit_vs, exit_spot, exit_tau, reason = stop_level * (1 + s_sl), spot, tau, "stop"
            break
    if exit_vs is None:
        spot, tau, v_s = d["mins"][-1]
        exit_vs, exit_spot, exit_tau, reason = v_s * (1 + x_sl), spot, tau, "square"

    if wings:
        wcx = bs(exit_spot, k + W, exit_tau, max(iva * lam, .02), True)
        wpx = bs(exit_spot, k - W, exit_tau, max(iva * lam, .02), False)
        wing_exit = (wcx + wpx) * (1 - x_sl)
    else:
        wcx = wpx = wing_exit = 0.0

    gross = (credit - wing_cost) - (exit_vs - wing_exit)
    dd = date.fromisoformat(d["day"])
    costs = (COSTS.round_trip(_OPT, Side.SELL, 1, d["prem_c"], exit_vs / 2, dd).total +
             COSTS.round_trip(_OPT, Side.SELL, 1, d["prem_p"], exit_vs / 2, dd).total)
    if wings:
        costs += (COSTS.round_trip(_OPT, Side.BUY, 1, wc0, wcx, dd).total +
                  COSTS.round_trip(_OPT, Side.BUY, 1, wp0, wpx, dd).total)
    return {"date": d["day"], "net": round(gross * lot - costs, 2), "reason": reason}


def line(label, s):
    if not s["n"]:
        print(f"  {label:<22} (no trades)"); return
    print(f"  {label:<22} n={s['n']:>3} net=Rs{s['net']:>8,} PF={s['pf']} "
          f"maxDD=Rs{s['max_dd']:>8,} worstTr=Rs{s['worst_trade']:>7,} "
          f"avg=Rs{s['avg_per_trade']:>6,}")


def main():
    daily_close, vix_open = vix_series()
    days = expiry_days()
    structs = {d: s for d in days if (s := day_struct(d))}
    print(f"built {len(structs)} day structs")

    # classify each day: era + high-VIX tercile (rolling-252d, causal)
    era, hivix = {}, {}
    for d in structs:
        era[d] = "fenced" if d <= FENCE_LAST_THURSDAY else "holdout"
        vo = vix_open.get(d)
        pct = trailing_pct(daily_close, d, float(vo)) if vo is not None else None
        hivix[d] = (pct is not None and pct >= 200.0 / 3.0)

    def run(variant, W, lam, slip, day_filter):
        trades = [outcome(structs[d], variant, W, lam, slip)
                  for d in structs if day_filter(d)]
        return summarize(trades, f"{variant}")

    SLIPS = {"1x": 1.0, "2x": 2.0}
    for scope, filt in (("HOLDOUT (Tue era)", lambda d: era[d] == "holdout"),
                        ("HOLDOUT high-VIX top-tercile", lambda d: era[d] == "holdout" and hivix[d]),
                        ("FENCED (Thu era) [V0 sanity vs gen6]", lambda d: era[d] == "fenced")):
        print(f"\n================ {scope} ================")
        for sl_label, sl in SLIPS.items():
            print(f"-- slippage {sl_label} --")
            v0 = run("V0", 0, 1.0, sl, filt)
            line("V0 naked+stop", v0)
            for W in (150, 200, 250):
                for lam in (1.0, 1.30):
                    v1 = run("V1", W, lam, sl, filt)
                    v2 = run("V2", W, lam, sl, filt)
                    def deltas(s):
                        if not (s["n"] and v0["n"] and v0["max_dd"] and v0["worst_trade"]):
                            return ""
                        dd_imp = (1 - s["max_dd"] / v0["max_dd"]) * 100  # +ve = smaller DD
                        wt_imp = (1 - s["worst_trade"] / v0["worst_trade"]) * 100
                        return f" | dDD={dd_imp:+.0f}% dWorst={wt_imp:+.0f}%"
                    line(f"V1 W{W} lam{lam}", v1); print(f"      {deltas(v1)}")
                    line(f"V2 W{W} lam{lam} NOstop", v2); print(f"      {deltas(v2)}")


if __name__ == "__main__":
    main()
