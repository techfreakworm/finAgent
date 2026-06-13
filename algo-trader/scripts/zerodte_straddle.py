"""Gen-6: 0DTE expiry-day ATM short straddle on NIFTY (pre-registered grid).

Value-path machinery mirrors options_expression_v2 (Gen-5b): real entry
premiums (both legs ATM at entry), fixed strike K thereafter, synthetic
BS value per leg on the real 1-min spot path using each leg's series IV,
tau implied once at entry from the real straddle. Stops fill at the stop
level with widened slippage (expiry tails gap).

Usage: .venv/bin/python scripts/zerodte_straddle.py
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
from scipy.optimize import brentq
from scipy.stats import norm

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from algotrader.data.instruments import _nifty_lot, FnoInstrument  # noqa: E402
from algotrader.backtest.costs import DhanCosts  # noqa: E402
from algotrader.core import Side, Segment  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
DTE_DIR = PROJECT / "data/cache/_OPTIONS/NIFTY_0DTE"
CAL = json.loads((PROJECT / "data/cache/_OPTIONS/expiry_calendar.json").read_text())
FENCE_LAST_THURSDAY = "2025-08-28"
CAPITAL = 500_000.0
ENTRY_SLIP = 0.01          # 1% per leg at entry (selling at bid-ish)
STOP_SLIP = 0.025          # 2.5% per leg on stop buyback (tails gap)
EXIT_SLIP = 0.01
SQUARE_T = "15:10"
R = 0.065
YEAR_MIN = 365.0 * 24 * 60

COSTS = DhanCosts()
_OPT = FnoInstrument(symbol="NIFTY-0DTE", security_id="0", segment=Segment.NSE_FNO,
                     tick_size=0.05, is_derivative=True, underlying="NIFTY")

_NIFTY: pd.DataFrame | None = None


def nifty_1m(day: str) -> pd.DataFrame:
    global _NIFTY
    if _NIFTY is None:
        df = pd.concat([pd.read_parquet(f) for f in
                        sorted((PROJECT / "data/cache/NIFTY/1m").glob("*.parquet"))])
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
        df["d"] = df["ts"].dt.date.astype(str)
        df["t"] = df["ts"].dt.strftime("%H:%M")
        _NIFTY = df
    return _NIFTY[_NIFTY["d"] == day]


def bs(spot, k, tau, iv, is_call):
    if tau <= 0 or iv <= 0:
        return max(spot - k, 0.0) if is_call else max(k - spot, 0.0)
    sq = iv * math.sqrt(tau)
    d1 = (math.log(spot / k) + (R + iv * iv / 2) * tau) / sq
    d2 = d1 - sq
    if is_call:
        return spot * norm.cdf(d1) - k * math.exp(-R * tau) * norm.cdf(d2)
    return k * math.exp(-R * tau) * norm.cdf(-d2) - spot * norm.cdf(-d1)


def straddle_val(spot, k, tau, iv_c, iv_p):
    return bs(spot, k, tau, iv_c, True) + bs(spot, k, tau, iv_p, False)


def expiry_days() -> list[str]:
    days = []
    for wk in CAL.values():
        d = wk.get("expiry_date") if isinstance(wk, dict) else None
        if d:
            days.append(d)
    return sorted(set(days))


def load_leg(day: str, leg: str) -> pd.DataFrame | None:
    f = DTE_DIR / f"{day}_{leg}.parquet"
    if not f.exists():
        return None
    df = pd.read_parquet(f)
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
    df["t"] = df["ts"].dt.strftime("%H:%M")
    return df


def iv_norm(series: pd.Series) -> tuple[pd.Series, float]:
    s = series.astype(float)
    scale = 100.0 if s.dropna().median() > 3 else 1.0
    return s / scale, scale


def run_day(day: str, entry_t: str, stop_pct: float, take_pct: float | None) -> dict | None:
    ce, pe = load_leg(day, "CE"), load_leg(day, "PE")
    sp = nifty_1m(day)
    if ce is None or pe is None or sp.empty:
        return None
    e_ce = ce[ce["t"] == entry_t]; e_pe = pe[pe["t"] == entry_t]
    e_sp = sp[sp["t"] < entry_t].tail(1)        # last closed spot bar before entry
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

    # implied tau from the REAL straddle (expiry day: bounded 10 min .. 1.2 days)
    f = lambda t: straddle_val(spot0, k, t, max(iv_c0, .02), max(iv_p0, .02)) - entry_straddle
    lo, hi = 10 / (365 * 24 * 60), 1.2 / 365
    try:
        if f(lo) * f(hi) > 0:
            return None
        tau0 = brentq(f, lo, hi, xtol=1e-9)
    except Exception:
        return None

    credit = entry_straddle * (1 - ENTRY_SLIP)            # received per unit
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

    # costs: 2 sell legs at entry + 2 buy legs at exit (approximate via two round trips)
    cb1 = COSTS.round_trip(_OPT, Side.SELL, 1, prem_c, exit_val / 2, date.fromisoformat(day))
    cb2 = COSTS.round_trip(_OPT, Side.SELL, 1, prem_p, exit_val / 2, date.fromisoformat(day))
    costs = cb1.total + cb2.total
    net = (credit - exit_val) * lot - costs
    return {"date": day, "entry_straddle": round(entry_straddle, 2), "k": k,
            "exit_val": round(exit_val, 2), "net": round(net, 2),
            "reason": exit_reason, "exit_t": exit_t,
            "tau0_min": round(tau0 * YEAR_MIN, 1), "lot": lot}


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
            "max_dd": round(dd),
            "pos_window_frac": round(sum(1 for v in wv if v > 0) / len(wv), 3),
            "worst_window": round(min(wv)), "best_window": round(max(wv)),
            "avg_per_trade": round(sum(pnls) / len(pnls)),
            "worst_trade": round(min(pnls)), "n_windows": len(wv)}


def main() -> None:
    spec = json.loads((PROJECT / "grids/gen6_0dte.json").read_text())
    g = spec["grid"]
    days = expiry_days()
    fenced_days = [d for d in days if d <= FENCE_LAST_THURSDAY]
    print(f"expiry days: {len(days)} total, {len(fenced_days)} fenced (Thursday era)")
    rows = []
    for et, sp_, tp in itertools.product(g["entry_time"], g["stop_pct"], g["take_pct"]):
        trades = [r for d in fenced_days if (r := run_day(d, et, sp_, tp))]
        s = summarize(trades, "fenced")
        rows.append({"config": {"entry_time": et, "stop_pct": sp_, "take_pct": tp},
                     "fenced": s,
                     "exit_mix": {k: sum(1 for t in trades if t["reason"] == k)
                                  for k in ("stop", "take", "square_off")}})
        print(f"entry={et} stop={sp_} take={tp}  n={s['n']} net=₹{s['net']:,} PF={s['pf']} "
              f"win={s['win_rate']:.0%} posW={s['pos_window_frac']:.0%} "
              f"worstW=₹{s['worst_window']:,} worstTrade=₹{s['worst_trade']:,} "
              f"DD=₹{s['max_dd']:,} avg=₹{s['avg_per_trade']:,}")
        print(f"   exits={rows[-1]['exit_mix']}")
    rows.sort(key=lambda r: r["fenced"].get("net", -9e9), reverse=True)
    (PROJECT / "reports/sweeps/gen6_0dte_eval.json").write_text(json.dumps(rows, indent=1))
    print("\nsaved: reports/sweeps/gen6_0dte_eval.json")


if __name__ == "__main__":
    main()
