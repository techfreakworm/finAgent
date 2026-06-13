"""Gen-5b: frozen breadth signal expressed as FIXED-strike ATM weekly option buys,
priced synthetically (Black-Scholes on the real 1-min NIFTY spot path with the
option series' own 1-min IV).

WHY SYNTHETIC: Dhan's /charts/rollingoption re-anchors the strike as spot moves
(proved empirically: corr(spot move, "ATM CE" premium move) = 0.08; -14% premium
on +0.8..2.7% spot days) — it cannot represent a HELD contract, and expired
fixed-strike contracts are not addressable on the API. Model risk is disclosed
and anchored:
  - entry premium is REAL (rolling==fixed at the entry instant);
  - tau is IMPLIED from the real entry premium given IV@entry (absorbs expiry
    calendar, rates, smile level into the anchor);
  - exit premium = BS(spot_t, K, iv_t, tau_entry - elapsed). iv_t is the rolling
    series' IV (ATM IV; our strike drifts ITM/OTM intra-day — flat-smile
    approximation, the residual model risk).
Final arbiter for this expression remains forward paper trading of real contracts.

Usage: .venv/bin/python scripts/options_expression_v2.py
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.optimize import brentq

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from algotrader.data.instruments import _nifty_lot  # noqa: E402
from algotrader.backtest.costs import DhanCosts  # noqa: E402
from algotrader.core import Side, Segment  # noqa: E402
from algotrader.data.instruments import FnoInstrument  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
OPT_DIR = PROJECT / "data/cache/_OPTIONS/NIFTY"
SIGNALS = json.loads((PROJECT / "data/cache/_OPTIONS/signal_days.json").read_text())
FENCE = "2025-09-01"
CAPITAL = 500_000.0
SLIP_PCT = 0.01
MAX_RISK_FRAC = 0.012
R = 0.065                                  # annual risk-free (anchored away by implied tau)
YEAR_MIN = 365.0 * 24 * 60

COSTS = DhanCosts()
_OPT_INST = FnoInstrument(symbol="NIFTY-OPT", security_id="0", segment=Segment.NSE_FNO,
                          tick_size=0.05, is_derivative=True, underlying="NIFTY")

_NIFTY_1M: pd.DataFrame | None = None


def nifty_1m(day: str) -> pd.DataFrame:
    global _NIFTY_1M
    if _NIFTY_1M is None:
        df = pd.concat([pd.read_parquet(f) for f in
                        sorted((PROJECT / "data/cache/NIFTY/1m").glob("*.parquet"))])
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
        df["d"] = df["ts"].dt.date.astype(str)
        _NIFTY_1M = df
    return _NIFTY_1M[_NIFTY_1M["d"] == day]


def bs_price(spot, k, tau, iv, is_call):
    if tau <= 0 or iv <= 0:
        intr = max(spot - k, 0.0) if is_call else max(k - spot, 0.0)
        return intr
    d1 = (math.log(spot / k) + (R + iv * iv / 2) * tau) / (iv * math.sqrt(tau))
    d2 = d1 - iv * math.sqrt(tau)
    if is_call:
        return spot * norm.cdf(d1) - k * math.exp(-R * tau) * norm.cdf(d2)
    return k * math.exp(-R * tau) * norm.cdf(-d2) - spot * norm.cdf(-d1)


def implied_tau(premium, spot, k, iv, is_call):
    """Solve BS for tau in [0.25d, 10d]; None if unsolvable (deep mispricing)."""
    lo, hi = 0.25 / 365, 10.0 / 365
    f = lambda t: bs_price(spot, k, t, iv, is_call) - premium
    try:
        if f(lo) * f(hi) > 0:
            return None
        return brentq(f, lo, hi, xtol=1e-7)
    except Exception:
        return None


def run_day(sig: dict, stop_pct: float, trail_trigger: float | None,
            trail_giveback: float) -> dict | None:
    day = sig["date"]
    is_call = sig["direction"] == "LONG"
    opt_f = OPT_DIR / f"{day}_{'CE' if is_call else 'PE'}.parquet"
    if not opt_f.exists():
        return None
    opt = pd.read_parquet(opt_f)
    opt["ts"] = pd.to_datetime(opt["ts"]).dt.tz_convert(IST)
    opt["t"] = opt["ts"].dt.strftime("%H:%M")
    spot_df = nifty_1m(day)
    if spot_df.empty:
        return None
    sp = spot_df.copy()
    sp["t"] = sp["ts"].dt.strftime("%H:%M")

    e_opt = opt[opt["t"] == "10:15"]
    e_spot = sp[sp["t"] == "10:14"]          # last closed 1-min spot before entry
    if e_opt.empty or e_spot.empty:
        return None
    prem0 = float(e_opt.iloc[0]["close"])
    iv0 = float(e_opt.iloc[0].get("iv") or 0) / (100.0 if float(e_opt.iloc[0].get("iv") or 0) > 3 else 1.0)
    spot0 = float(e_spot.iloc[0]["close"])
    if prem0 <= 0 or iv0 <= 0.02:
        return None
    k = round(spot0 / 50.0) * 50.0
    tau0 = implied_tau(prem0, spot0, k, iv0, is_call)
    if tau0 is None:
        return None

    entry_px = prem0 * (1 + SLIP_PCT)
    lot = _nifty_lot(date.fromisoformat(day))
    risk = stop_pct * entry_px * lot
    if risk > MAX_RISK_FRAC * CAPITAL:
        return {"skipped": True}

    # modeled premium path on 1-min spot closes, IV interpolated from option series
    iv_series = opt.set_index("t")["iv"].astype(float)
    iv_scale = 100.0 if iv_series.dropna().median() > 3 else 1.0
    path = sp[(sp["t"] > "10:15") & (sp["t"] <= "15:15")]
    stop_level = entry_px * (1 - stop_pct)
    run_max, exit_px, exit_reason, exit_t = prem0, None, None, None
    t_entry = e_opt.iloc[0]["ts"]
    for row in path.itertuples():
        elapsed_min = (row.ts - t_entry).total_seconds() / 60.0
        tau = max(tau0 - elapsed_min / YEAR_MIN, 1e-6)
        iv_t = iv_series.get(row.t, np.nan)
        iv_t = (float(iv_t) / iv_scale) if iv_t == iv_t else iv0
        prem_t = bs_price(row.close, k, tau, max(iv_t, 0.02), is_call)
        if prem_t <= stop_level:
            exit_px = max(prem_t, 0.05) * (1 - SLIP_PCT)
            exit_reason, exit_t = "stop", row.t
            break
        run_max = max(run_max, prem_t)
        if trail_trigger is not None and run_max >= prem0 * (1 + trail_trigger):
            trail_level = run_max * (1 - trail_giveback)
            if prem_t <= trail_level and trail_level > stop_level:
                exit_px = prem_t * (1 - SLIP_PCT)
                exit_reason, exit_t = "trail", row.t
                break
    if exit_px is None:
        last = path.iloc[-1]
        tau = max(tau0 - ((last.ts - t_entry).total_seconds() / 60.0) / YEAR_MIN, 1e-6)
        iv_t = iv_series.get(last.t, np.nan)
        iv_t = (float(iv_t) / iv_scale) if iv_t == iv_t else iv0
        exit_px = bs_price(last.close, k, tau, max(iv_t, 0.02), is_call) * (1 - SLIP_PCT)
        exit_reason, exit_t = "square_off", last.t

    cb = COSTS.round_trip(_OPT_INST, Side.BUY, 1, entry_px, exit_px,
                          date.fromisoformat(day))
    net = (exit_px - entry_px) * lot - cb.total
    return {"date": day, "dir": sig["direction"], "entry": round(entry_px, 2),
            "exit": round(exit_px, 2), "k": k, "tau0_days": round(tau0 * 365, 2),
            "lot": lot, "net": round(net, 2), "reason": exit_reason, "exit_t": exit_t}


def summarize(trades, label):
    if not trades:
        return {"label": label, "n": 0}
    pnls = [t["net"] for t in trades]
    g = sum(p for p in pnls if p > 0); l = -sum(p for p in pnls if p < 0)
    cum = peak = dd = 0.0
    for p in pnls:
        cum += p; peak = max(peak, cum); dd = min(dd, cum - peak)
    win = defaultdict(float)
    for t in trades:
        y, m, _ = t["date"].split("-")
        win[f"{y}W{(int(m)-1)//2+1}"] += t["net"]
    wv = list(win.values())
    return {"label": label, "n": len(trades), "net": round(sum(pnls)),
            "pf": round(g / l, 3) if l else None,
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
            "max_dd": round(dd), "pos_window_frac": round(sum(1 for v in wv if v > 0) / len(wv), 3),
            "n_windows": len(wv), "worst_window": round(min(wv)),
            "avg_per_trade": round(sum(pnls) / len(pnls))}


def main() -> None:
    spec = json.loads((PROJECT / "grids/gen5_options.json").read_text())
    g = spec["grid"]
    # model-anchor calibration: entry BS(iv0, implied tau) == real premium by
    # construction; report implied-tau distribution as the sanity signal
    out_rows = []
    for sp_, tt in itertools.product(g["stop_pct"], g["trail_trigger"]):
        trades, skipped, taus = [], 0, []
        for sig in SIGNALS:
            r = run_day(sig, sp_, tt, g["trail_giveback"][0])
            if r is None:
                continue
            if r.get("skipped"):
                skipped += 1
                continue
            trades.append(r)
            taus.append(r["tau0_days"])
        fenced = [t for t in trades if t["date"] < FENCE]
        reused = [t for t in trades if t["date"] >= FENCE]
        row = {"config": {"stop_pct": sp_, "trail_trigger": tt,
                          "trail_giveback": g["trail_giveback"][0]},
               "skipped_oversize": skipped,
               "implied_tau_days_median": round(float(np.median(taus)), 2) if taus else None,
               "fenced": summarize(fenced, "fenced"),
               "reused_holdout": summarize(reused, "reused_holdout"),
               "exit_mix_fenced": {k: sum(1 for t in fenced if t["reason"] == k)
                                   for k in ("stop", "trail", "square_off")}}
        out_rows.append(row)
        f, h = row["fenced"], row["reused_holdout"]
        print(f"stop={sp_} trail={tt}  FENCED n={f['n']} net=₹{f['net']:,} PF={f['pf']} "
              f"win={f['win_rate']:.0%} posW={f['pos_window_frac']:.0%} "
              f"worstW=₹{f['worst_window']:,} DD=₹{f['max_dd']:,} avg=₹{f['avg_per_trade']:,} | "
              f"REUSED n={h.get('n',0)} net=₹{h.get('net',0):,} PF={h.get('pf')}")
        print(f"   exits={row['exit_mix_fenced']} skipped={skipped} "
              f"tau~{row['implied_tau_days_median']}d")
    out_rows.sort(key=lambda r: r["fenced"].get("net", -9e9), reverse=True)
    (PROJECT / "reports/sweeps/gen5b_options_eval.json").write_text(json.dumps(out_rows, indent=1))
    print("\nsaved: reports/sweeps/gen5b_options_eval.json")


if __name__ == "__main__":
    main()
