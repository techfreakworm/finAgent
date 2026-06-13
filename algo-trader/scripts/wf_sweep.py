"""Walk-forward sweep driver: pre-registered grid × universe, full fenced
period per task, session-level P&L series persisted for post-hoc window
evaluation (WF selection happens in wf_eval.py — table lookups, no re-runs).

Usage: .venv/bin/python scripts/wf_sweep.py grids/gen1.json [--procs 3]
Output: reports/sweeps/gen{N}/{strategy}__{cfg_id}__{symbol}.json (one per task)
Each task also appends to the trial registry (multiple-testing accounting).
"""
from __future__ import annotations

import itertools
import json
import sys
import traceback
from datetime import date, datetime
from importlib import import_module
from multiprocessing import Pool
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

IST = ZoneInfo("Asia/Kolkata")


def _load_class(path: str):
    mod, _, name = path.rpartition(".")
    return getattr(import_module(mod), name)


def _configs(grid: dict) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]


def cfg_id(cfg: dict) -> str:
    return "_".join(f"{k}-{v}" for k, v in sorted(cfg.items())).replace(".", "p")


def run_task(args: tuple) -> dict:
    (strategy_name, class_path, cfg, symbol, frm_s, to_s, interval, out_dir_s, universe) = args
    # imports inside the worker (multiprocessing)
    from algotrader.backtest.engine import BacktestEngine, DefaultSlippage
    from algotrader.backtest.costs import DhanCosts
    from algotrader.backtest.data_source import ParquetBarSource, universe_for
    from algotrader.risk.engine import IntradayRiskEngine, RiskParams

    out = Path(out_dir_s) / f"{strategy_name}__{cfg_id(cfg)}__{symbol}.json"
    if out.exists():                      # resumable
        return {"task": out.stem, "status": "cached"}
    try:
        inst, data_symbol = next((i, d) for i, d in universe_for(universe)
                                 if (d == symbol))
        strat = _load_class(class_path)(**cfg)
        risk = IntradayRiskEngine(RiskParams(
            capital=500_000, hard_floor=400_000, max_daily_loss_pct=0.02,
            per_trade_risk_pct=0.0075, max_open_positions=3, max_per_symbol=1))
        engine = BacktestEngine(strategies=[strat], risk_engine=risk,
                                cost_model=DhanCosts(), slippage_model=DefaultSlippage())
        src = ParquetBarSource(inst, date.fromisoformat(frm_s),
                               date.fromisoformat(to_s), interval, data_symbol)
        result = engine.run(src)

        sessions = [{
            "date": str(s.session_date), "net": round(s.net_pnl, 2),
            "gross": round(s.gross_pnl, 2), "costs": round(s.cost_total, 2),
            "trades": len(s.trades), "risk_events": s.risk_events,
        } for s in result.sessions]
        trades = result.trades
        rejections: dict[str, int] = {}
        for s in result.sessions:
            for r in s.rejections:
                rejections[r.rule] = rejections.get(r.rule, 0) + 1
        payload = {
            "strategy": strategy_name, "config": cfg, "symbol": symbol,
            "interval": interval, "period": [frm_s, to_s],
            "n_sessions": len(sessions), "n_trades": len(trades),
            "net_pnl": round(sum(t.net_pnl for t in trades), 2),
            "slippage": round(sum(t.slippage_paid for t in trades), 2),
            "rejections": rejections,
            "exit_reasons": _count(t.position.exit_reason.value for t in trades),
            "sessions": sessions,
            "trade_pnls": [round(t.net_pnl, 2) for t in trades],
        }
        out.write_text(json.dumps(payload))
        return {"task": out.stem, "status": "ok", "trades": len(trades),
                "net": payload["net_pnl"]}
    except Exception:
        return {"task": out.stem, "status": "error",
                "trace": traceback.format_exc()[-600:]}


def _count(it) -> dict:
    d: dict = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return d


def main() -> None:
    grid_path = Path(sys.argv[1])
    procs = int(sys.argv[sys.argv.index("--procs") + 1]) if "--procs" in sys.argv else 3
    spec = json.loads(grid_path.read_text())
    gen = spec["generation"]
    out_dir = PROJECT / "reports" / "sweeps" / f"gen{gen}"
    out_dir.mkdir(parents=True, exist_ok=True)

    from algotrader.backtest.data_source import universe_for
    universe = spec.get("universe", "sweep12")
    symbols = [d for _, d in universe_for(universe)]

    tasks = []
    for sname, sdef in spec["strategies"].items():
        for cfg in _configs(sdef["grid"]):
            for sym in symbols:
                tasks.append((sname, sdef["class"], cfg, sym,
                              spec["period"]["from"], spec["period"]["to"],
                              spec["interval"], str(out_dir), universe))

    # trial registry: every config-instrument evaluated this generation
    reg = PROJECT / "reports" / "trial_registry.jsonl"
    with reg.open("a") as f:
        for t in tasks:
            f.write(json.dumps({"ts": datetime.now(IST).isoformat(timespec='seconds'),
                                "gen": gen, "strategy": t[0], "config": t[2],
                                "symbol": t[3], "interval": t[6]}) + "\n")

    print(f"gen{gen}: {len(tasks)} tasks ({len(symbols)} symbols), {procs} procs",
          flush=True)
    done = ok = err = 0
    t0 = datetime.now()
    with Pool(procs) as pool:
        for res in pool.imap_unordered(run_task, tasks, chunksize=1):
            done += 1
            if res["status"] == "ok":
                ok += 1
            elif res["status"] == "error":
                err += 1
                print(f"ERROR {res['task']}: {res['trace'][-200:]}", flush=True)
            if done % 25 == 0 or done == len(tasks):
                el = (datetime.now() - t0).total_seconds()
                print(f"[{el:6.0f}s] {done}/{len(tasks)} done ({ok} ok, {err} err)",
                      flush=True)
    print("SWEEP COMPLETE", flush=True)


if __name__ == "__main__":
    main()
