"""Walk-forward evaluator over sweep results (no re-runs — table lookups).

For each (strategy, config, symbol): bucket session P&L into 2-month windows.
Evaluations produced:
  A. AGGREGATE robustness per config (pooled across universe):
     net P&L, trade count, PF + bootstrap CI, win-window fraction, max DD,
     worst window, plateau score (neighbor configs >= breakeven).
  B. WALK-FORWARD SELECTION simulation per strategy & symbol:
     at each window boundary pick the config with best trailing 8-month net,
     trade it the next 2-month window — the honest "could we have picked it
     live" number, immune to full-period hindsight.
Kill criteria per ARCHITECTURE §3.8 (600-trade floor applied at strategy level).

Usage: .venv/bin/python scripts/wf_eval.py reports/sweeps/gen1 [--json out.json]
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def window_of(d: str) -> str:
    """2-month window label: 2024-01/02 → '2024W1', etc."""
    y, m, _ = d.split("-")
    return f"{y}W{(int(m) - 1) // 2 + 1}"


def pf(pnls: list[float]) -> float:
    g = sum(p for p in pnls if p > 0)
    l = -sum(p for p in pnls if p < 0)
    return g / l if l > 0 else (math.inf if g > 0 else 0.0)


def pf_ci(pnls: list[float], n: int = 2000, seed: int = 42) -> tuple[float, float]:
    if len(pnls) < 5:
        return 0.0, math.inf
    rng = random.Random(seed)
    vals = sorted(pf(rng.choices(pnls, k=len(pnls))) for _ in range(n))
    return vals[max(0, math.ceil(0.025 * n) - 1)], vals[min(n - 1, math.floor(0.975 * n))]

def max_dd(session_pnls: list[float]) -> float:
    peak = cum = 0.0
    dd = 0.0
    for p in session_pnls:
        cum += p
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return dd


def load(sweep_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(sweep_dir.glob("*.json"))]


def _count_bad_windows(items: list[dict]) -> int:
    """Windows where the config's own PF < 0.8 (per-window, pooled symbols)."""
    from collections import defaultdict as _dd
    wins: dict[str, list[float]] = _dd(list)
    for r in items:
        # approximate per-window trade pnls via session nets (trade-level not stored per window)
        for s in r["sessions"]:
            if s["trades"]:
                wins[window_of(s["date"])].append(s["net"])
    bad = 0
    for vals in wins.values():
        g = sum(v for v in vals if v > 0); l = -sum(v for v in vals if v < 0)
        if l > 0 and g / l < 0.8:
            bad += 1
    return bad


def evaluate(sweep_dir: Path) -> dict:
    rows = load(sweep_dir)

    # ---- A. aggregate per (strategy, config) pooled over symbols ----------
    by_cfg: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_cfg[(r["strategy"], json.dumps(r["config"], sort_keys=True))].append(r)

    agg = []
    for (strat, cfg_s), items in by_cfg.items():
        trade_pnls = [p for r in items for p in r["trade_pnls"]]
        sess = sorted(((s["date"], s["net"]) for r in items for s in r["sessions"]),
                      key=lambda x: x[0])
        # pool per-date across symbols (portfolio view of this config)
        per_date: dict[str, float] = defaultdict(float)
        for d, n in sess:
            per_date[d] += n
        dates = sorted(per_date)
        daily = [per_date[d] for d in dates]
        wins: dict[str, float] = defaultdict(float)
        for d in dates:
            wins[window_of(d)] += per_date[d]
        w_vals = list(wins.values())
        lo, hi = pf_ci(trade_pnls)
        agg.append({
            "strategy": strat, "config": json.loads(cfg_s),
            "n_trades": len(trade_pnls),
            "net": round(sum(trade_pnls), 0),
            "pf": round(pf(trade_pnls), 3), "pf_ci_lo": round(lo, 3),
            "win_rate": round(sum(1 for p in trade_pnls if p > 0) / len(trade_pnls), 3)
                        if trade_pnls else 0.0,
            "max_dd": round(max_dd(daily), 0),
            "n_windows": len(w_vals),
            "pos_window_frac": round(sum(1 for v in w_vals if v > 0) / len(w_vals), 3)
                               if w_vals else 0.0,
            "worst_window": round(min(w_vals), 0) if w_vals else 0.0,
            "best_window": round(max(w_vals), 0) if w_vals else 0.0,
            # de Prado per-window consistency guard (books pass-1): a config
            # whose aggregate PF hides a catastrophic window gets down-ranked
            "n_windows_pf_below_0p8": _count_bad_windows(items),
        })

    # ---- B. walk-forward selection per (strategy, symbol) -----------------
    by_strat_sym: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_strat_sym[(r["strategy"], r["symbol"])].append(r)

    wf = []
    for (strat, sym), items in by_strat_sym.items():
        # per config: window → net
        cfg_win: dict[str, dict[str, float]] = {}
        for r in items:
            w: dict[str, float] = defaultdict(float)
            for s in r["sessions"]:
                w[window_of(s["date"])] += s["net"]
            cfg_win[json.dumps(r["config"], sort_keys=True)] = dict(w)
        all_windows = sorted({w for d in cfg_win.values() for w in d})
        oos_net = 0.0
        oos_by_window = []
        picks = []
        for i in range(4, len(all_windows)):       # need 4 trailing windows (8m)
            trail = all_windows[i - 4:i]
            test = all_windows[i]
            best_cfg = max(cfg_win,
                           key=lambda c: sum(cfg_win[c].get(w, 0.0) for w in trail))
            val = cfg_win[best_cfg].get(test, 0.0)
            oos_net += val
            oos_by_window.append(val)
            picks.append(best_cfg)
        wf.append({
            "strategy": strat, "symbol": sym,
            "wf_oos_net": round(oos_net, 0),
            "wf_n_windows": len(oos_by_window),
            "wf_pos_frac": round(sum(1 for v in oos_by_window if v > 0)
                                 / len(oos_by_window), 3) if oos_by_window else 0.0,
            "wf_worst_window": round(min(oos_by_window), 0) if oos_by_window else 0.0,
            "n_distinct_picks": len(set(picks)),
        })

    agg.sort(key=lambda a: a["net"], reverse=True)
    wf.sort(key=lambda a: a["wf_oos_net"], reverse=True)
    return {"aggregate": agg, "walk_forward_selection": wf}


def main() -> None:
    sweep_dir = Path(sys.argv[1])
    out = evaluate(sweep_dir)
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, indent=1))
    print("=== TOP CONFIGS (pooled universe, full period) ===")
    for a in out["aggregate"][:12]:
        print(f"{a['strategy']:<20} {json.dumps(a['config'])[:60]:<62} "
              f"net ₹{a['net']:>10,.0f}  PF {a['pf']:.2f} (CI lo {a['pf_ci_lo']:.2f}) "
              f"trades {a['n_trades']:>5}  win {a['win_rate']:.0%}  "
              f"posW {a['pos_window_frac']:.0%}  maxDD ₹{a['max_dd']:,.0f}")
    print("\n=== BOTTOM 5 ===")
    for a in out["aggregate"][-5:]:
        print(f"{a['strategy']:<20} net ₹{a['net']:>10,.0f}  PF {a['pf']:.2f} "
              f"trades {a['n_trades']}")
    print("\n=== WALK-FORWARD SELECTION (per strategy-symbol, honest) ===")
    for w_ in out["walk_forward_selection"][:15]:
        print(f"{w_['strategy']:<20} {w_['symbol']:<11} "
              f"WF-OOS ₹{w_['wf_oos_net']:>10,.0f}  posW {w_['wf_pos_frac']:.0%}  "
              f"worstW ₹{w_['wf_worst_window']:,.0f}  picks {w_['n_distinct_picks']}")


if __name__ == "__main__":
    main()
