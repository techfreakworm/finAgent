"""Gen-5: backtest the frozen breadth signal expressed as ATM weekly option buys.

Bespoke evaluator over data/cache/_OPTIONS (the option's own minute bars carry
all the dynamics — theta, IV crush, gamma — because they are real traded
prices). Signal dates/directions are FROZEN inputs from signal_days.json.

Usage: .venv/bin/python scripts/options_expression.py
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from collections import defaultdict
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from algotrader.data.instruments import _nifty_lot  # point-in-time lot  # noqa: E402
from algotrader.backtest.costs import DhanCosts  # noqa: E402
from algotrader.core import Side, Segment, Instrument  # noqa: E402
from algotrader.data.instruments import FnoInstrument  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
OPT_DIR = PROJECT / "data" / "cache" / "_OPTIONS" / "NIFTY"
SIGNALS = json.loads((PROJECT / "data/cache/_OPTIONS/signal_days.json").read_text())
FENCE = date(2025, 9, 1)
CAPITAL = 500_000.0
ENTRY_T, EXIT_T = time(10, 15), time(15, 15)
SLIP_PCT = 0.01                      # 1% of premium each way (ATM weekly is liquid)
MAX_RISK_FRAC = 0.012                # skip days where stop-risk > 1.2% capital

# Option-leg instrument for the cost model (is_derivative, NIFTY lots)
def _opt_instrument(sym: str) -> Instrument:
    return FnoInstrument(symbol=sym, security_id="0", segment=Segment.NSE_FNO,
                         tick_size=0.05, is_derivative=True, underlying="NIFTY")

COSTS = DhanCosts()


def run_config(stop_pct: float, trail_trigger: float | None,
               trail_giveback: float) -> dict:
    trades, skipped = [], 0
    for sig in SIGNALS:
        d = date.fromisoformat(sig["date"])
        opt_type = "CE" if sig["direction"] == "LONG" else "PE"
        f = OPT_DIR / f"{sig['date']}_{opt_type}.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f)
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
        df = df.sort_values("ts").reset_index(drop=True)
        t = df["ts"].dt.time

        entry_rows = df[t == ENTRY_T]
        if entry_rows.empty:
            continue
        e_idx = entry_rows.index[0]
        entry_px = float(entry_rows.iloc[0]["close"]) * (1 + SLIP_PCT)
        lot = _nifty_lot(d)
        risk = stop_pct * entry_px * lot
        if risk > MAX_RISK_FRAC * CAPITAL or entry_px <= 0:
            skipped += 1
            continue

        stop_level = entry_px * (1 - stop_pct)
        run_max = entry_px
        exit_px, exit_reason = None, None
        for row in df.iloc[e_idx + 1:].itertuples():
            bt = row.ts.time()
            if bt > EXIT_T:
                break
            # stop first (pessimistic): bar low through stop
            if row.low <= stop_level:
                exit_px = max(row.open if row.open < stop_level else stop_level, 0.05)
                exit_px *= (1 - SLIP_PCT)
                exit_reason = "stop"
                break
            run_max = max(run_max, row.high)
            if trail_trigger is not None and run_max >= entry_px * (1 + trail_trigger):
                trail_level = run_max * (1 - trail_giveback)
                if row.low <= trail_level and trail_level > stop_level:
                    exit_px = trail_level * (1 - SLIP_PCT)
                    exit_reason = "trail"
                    break
        if exit_px is None:
            last = df[t <= EXIT_T].iloc[-1]
            exit_px = float(last["close"]) * (1 - SLIP_PCT)
            exit_reason = "square_off"

        cb = COSTS.round_trip(_opt_instrument(f"NIFTY-ATM-{opt_type}"), Side.BUY,
                              1, entry_px, exit_px, d)
        net = (exit_px - entry_px) * lot - cb.total
        trades.append({"date": sig["date"], "dir": sig["direction"],
                       "entry": round(entry_px, 2), "exit": round(exit_px, 2),
                       "lot": lot, "net": round(net, 2), "reason": exit_reason,
                       "costs": round(cb.total, 2)})
    return {"trades": trades, "skipped": skipped}


def summarize(trades: list[dict], label: str) -> dict:
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
            "max_dd": round(dd), "n_windows": len(wv),
            "pos_window_frac": round(sum(1 for v in wv if v > 0) / len(wv), 3),
            "worst_window": round(min(wv)), "avg_per_trade": round(sum(pnls)/len(pnls))}


def main() -> None:
    spec = json.loads((PROJECT / "grids/gen5_options.json").read_text())
    g = spec["grid"]
    results = []
    for sp, tt in itertools.product(g["stop_pct"], g["trail_trigger"]):
        r = run_config(sp, tt, g["trail_giveback"][0])
        fenced = [t for t in r["trades"] if t["date"] < str(FENCE)]
        reused = [t for t in r["trades"] if t["date"] >= str(FENCE)]
        results.append({
            "config": {"stop_pct": sp, "trail_trigger": tt,
                       "trail_giveback": g["trail_giveback"][0]},
            "skipped_oversize": r["skipped"],
            "fenced": summarize(fenced, "fenced"),
            "reused_holdout": summarize(reused, "reused_holdout"),
            "exit_mix": {k: sum(1 for t in fenced if t["reason"] == k)
                         for k in ("stop", "trail", "square_off")},
        })
    results.sort(key=lambda r: r["fenced"].get("net", -9e9), reverse=True)
    out = PROJECT / "reports/sweeps/gen5_options_eval.json"
    out.write_text(json.dumps(results, indent=1))
    for r in results:
        f, h = r["fenced"], r["reused_holdout"]
        c = r["config"]
        print(f"stop={c['stop_pct']} trail={c['trail_trigger']}  "
              f"FENCED: n={f['n']} net=₹{f['net']:,} PF={f['pf']} win={f['win_rate']:.0%} "
              f"posW={f['pos_window_frac']:.0%} worstW=₹{f['worst_window']:,} DD=₹{f['max_dd']:,} | "
              f"REUSED-HOLDOUT: n={h.get('n',0)} net=₹{h.get('net',0):,} PF={h.get('pf')}")
        print(f"   exits={r['exit_mix']} skipped_oversize={r['skipped_oversize']} avg/trade=₹{f['avg_per_trade']:,}")
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
