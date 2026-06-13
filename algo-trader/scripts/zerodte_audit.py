"""Gen-7 books pass-2 audit of the 0DTE straddle survivor (fenced data only).

Tests the THREE highest-conviction critiques from the pass-2 reading program
against our own fenced data (Thursday era <= 2025-08-28). NOT a re-tune of the
burned holdout — a robustness audit of the FROZEN strategy; any better variant
found here becomes a NEW pre-registered candidate needing its own future
holdout (Tuesday-era expiry days keep accumulating).

A) ADVERSARIAL FILL RE-AUDIT (Harris/O'Hara/Taleb/Natenberg): our backtest
   filled the straddle at synthetic BS mid with ~1%/leg slippage. A 1-lot
   retail seller pays half bid-ask per leg + 1.5-2x on stop-triggered exits
   (short-gamma crowd exiting together). Re-run at slippage multipliers
   1x/2x/3x and see whether PF survives.
B) STOP-DESIGN TEST (Sinclair direct contradiction): our 25% premium stop
   fires ~57% of days; Sinclair says arbitrary short-vol stops exit
   mean-reverting spikes. Compare no-stop / 25% / 40% / 60% under the
   adversarial (2x) fill model.
C) STOP-FIRE REGIME SPLIT: tag each fenced trade with India VIX at entry; do
   stop-fires cluster in high-VIX days? If so a VIX gate fixes it WITHOUT
   touching the stop.

Usage: .venv/bin/python scripts/zerodte_audit.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
import zerodte_straddle as Z  # noqa: E402

IST = Z.IST
NO_STOP = 9.99  # effectively disables the stop


def summarize(trades):
    pnls = [t["net"] for t in trades]
    if not pnls:
        return {"n": 0}
    g = sum(p for p in pnls if p > 0); l = -sum(p for p in pnls if p < 0)
    cum = peak = dd = 0.0
    for p in pnls:
        cum += p; peak = max(peak, cum); dd = min(dd, cum - peak)
    win = defaultdict(float)
    for t in trades:
        y, m, _ = t["date"].split("-")
        win[f"{y}W{(int(m)-1)//2+1}"] += t["net"]
    wv = list(win.values())
    fires = sum(1 for t in trades if t["reason"] == "stop")
    return {"n": len(pnls), "net": round(sum(pnls)), "pf": round(g/l, 3) if l else None,
            "win_rate": round(sum(1 for p in pnls if p > 0)/len(pnls), 3),
            "stop_fire_rate": round(fires/len(pnls), 3),
            "pos_window_frac": round(sum(1 for v in wv if v > 0)/len(wv), 3),
            "worst_window": round(min(wv)), "max_dd": round(dd),
            "avg_per_trade": round(sum(pnls)/len(pnls))}


def run_fenced(entry_t="09:20", stop_pct=0.25, take_pct=None):
    days = [d for d in Z.expiry_days() if d <= Z.FENCE_LAST_THURSDAY]
    return [r for d in days if (r := Z.run_day(d, entry_t, stop_pct, take_pct)) and not r.get("skipped")]


def set_slip(mult):
    Z.ENTRY_SLIP, Z.STOP_SLIP, Z.EXIT_SLIP = 0.01*mult, 0.025*mult, 0.01*mult


def vix_at_entry(day: str) -> float | None:
    files = sorted((PROJECT/"data/cache/INDIAVIX/1m").glob("*.parquet"))
    # cache per-process
    global _VIX
    try:
        _VIX
    except NameError:
        df = pd.concat([pd.read_parquet(f) for f in files])
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
        df["d"] = df["ts"].dt.date.astype(str)
        df["t"] = df["ts"].dt.strftime("%H:%M")
        _VIX = df
    day_df = _VIX[(_VIX["d"] == day) & (_VIX["t"] <= "09:20")]
    return float(day_df.iloc[-1]["close"]) if len(day_df) else None


def main():
    out = {}
    print("=== A) ADVERSARIAL FILL RE-AUDIT (frozen 25% stop, fenced) ===")
    a = {}
    for mult in (1, 2, 3):
        set_slip(mult)
        s = summarize(run_fenced(stop_pct=0.25))
        a[f"slip_{mult}x"] = s
        print(f"  slippage {mult}x: PF={s['pf']} net=Rs{s['net']:,} win={s['win_rate']:.0%} "
              f"stopfire={s['stop_fire_rate']:.0%} posW={s['pos_window_frac']:.0%} avg=Rs{s['avg_per_trade']:,}")
    out["adversarial_fill"] = a

    print("\n=== B) STOP-DESIGN TEST (Sinclair), under 2x adversarial fills ===")
    set_slip(2)
    b = {}
    for sp, label in [(NO_STOP, "no_stop"), (0.25, "25pct"), (0.40, "40pct"), (0.60, "60pct")]:
        s = summarize(run_fenced(stop_pct=sp))
        b[label] = s
        print(f"  stop={label:<8}: PF={s['pf']} net=Rs{s['net']:,} win={s['win_rate']:.0%} "
              f"stopfire={s['stop_fire_rate']:.0%} maxDD=Rs{s['max_dd']:,} worstW=Rs{s['worst_window']:,}")
    out["stop_design_2x"] = b

    print("\n=== C) STOP-FIRE vs INDIA-VIX REGIME (frozen 25% stop, 1x) ===")
    set_slip(1)
    trades = run_fenced(stop_pct=0.25)
    tagged = [(t, vix_at_entry(t["date"])) for t in trades]
    tagged = [(t, v) for t, v in tagged if v is not None]
    vixes = sorted(v for _, v in tagged)
    lo_t, hi_t = vixes[len(vixes)//3], vixes[2*len(vixes)//3]
    buckets = {"low": [], "mid": [], "high": []}
    for t, v in tagged:
        buckets["low" if v <= lo_t else "high" if v > hi_t else "mid"].append((t, v))
    c = {"vix_terciles": {"lo<=": round(lo_t, 2), "hi>": round(hi_t, 2)}}
    for name, ts in buckets.items():
        if not ts: continue
        fires = sum(1 for t, _ in ts if t["reason"] == "stop")
        net = sum(t["net"] for t, _ in ts)
        c[name] = {"n": len(ts), "stop_fire_rate": round(fires/len(ts), 3),
                   "net": round(net), "avg": round(net/len(ts))}
        print(f"  VIX {name:<4} (n={len(ts)}): stopfire={fires/len(ts):.0%} "
              f"net=Rs{net:,} avg/trade=Rs{net/len(ts):,.0f}")
    out["vix_regime"] = c

    set_slip(1)  # restore
    (PROJECT/"reports/gen7_0dte_audit.json").write_text(json.dumps(out, indent=1))
    print("\nsaved: reports/gen7_0dte_audit.json")


if __name__ == "__main__":
    main()
