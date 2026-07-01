"""FG-3 addendum — block-bootstrap CI on the measured-fill PF.

Quantifies sampling uncertainty of the Track-2 headline PFs (spread-only and
central-stress) over the 218 fenced / 40 holdout days: stationary circular
block bootstrap (block ~10 trades, 10k resamples) on the per-trade net series,
reporting the PF percentile CI. Complements (does not change) the pre-registered
PF>=1.10 gate — a CI-lo above ~1.0 says the measured-fill edge is unlikely to
be small-sample luck under that fill assumption.

Usage: .venv/bin/python scripts/fg3_bootstrap_ci.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
import scripts.zerodte_straddle as z  # noqa: E402
from scripts.zerodte_straddle import expiry_days, FENCE_LAST_THURSDAY  # noqa: E402

ENTRY_T, STOP_PCT, TAKE_PCT = "09:20", 0.25, None
CONFIGS = {
    "spread_only": (0.00128, 0.00336, 0.00138 + 0.00),
    "central_stress_5pct": (0.00128, 0.00336, 0.00138 + 0.05),
}
N_BOOT, BLOCK, SEED = 10_000, 10, 20260702


def pf(pnls: np.ndarray) -> float:
    g = pnls[pnls > 0].sum()
    l = -pnls[pnls < 0].sum()
    return float(g / l) if l > 0 else float("inf")


def block_bootstrap_ci(pnls: np.ndarray, n_boot: int, block: int, rng) -> dict:
    n = len(pnls)
    stats = np.empty(n_boot)
    for b in range(n_boot):
        idx = []
        while len(idx) < n:
            start = rng.integers(0, n)
            idx.extend((start + k) % n for k in range(block))  # circular block
        stats[b] = pf(pnls[np.asarray(idx[:n])])
    return {"pf_ci_2.5": round(float(np.percentile(stats, 2.5)), 3),
            "pf_ci_5": round(float(np.percentile(stats, 5)), 3),
            "pf_median": round(float(np.percentile(stats, 50)), 3),
            "pf_ci_95": round(float(np.percentile(stats, 95)), 3),
            "pf_ci_97.5": round(float(np.percentile(stats, 97.5)), 3)}


def main() -> None:
    rng = np.random.default_rng(SEED)
    days = expiry_days()
    orig = (z.ENTRY_SLIP, z.STOP_SLIP, z.EXIT_SLIP)
    out = {"n_boot": N_BOOT, "block": BLOCK, "seed": SEED, "configs": {}}
    for name, (e, x, s) in CONFIGS.items():
        z.ENTRY_SLIP, z.EXIT_SLIP, z.STOP_SLIP = e, x, s
        rows = [r for d in days if (r := z.run_day(d, ENTRY_T, STOP_PCT, TAKE_PCT))]
        res = {}
        for scope, sel in (("fenced", lambda r: r["date"] <= FENCE_LAST_THURSDAY),
                           ("holdout", lambda r: r["date"] > FENCE_LAST_THURSDAY)):
            pnls = np.array([r["net"] for r in rows if sel(r)], float)
            res[scope] = {"n": int(len(pnls)), "pf_point": round(pf(pnls), 3),
                          **block_bootstrap_ci(pnls, N_BOOT, BLOCK, rng)}
        out["configs"][name] = {"entry_slip": e, "squareoff_slip": x, "stop_slip": round(s, 5),
                                **res}
        print(f"{name}: fenced PF {res['fenced']['pf_point']} "
              f"CI95 [{res['fenced']['pf_ci_2.5']}, {res['fenced']['pf_ci_97.5']}] "
              f"CI-lo5 {res['fenced']['pf_ci_5']} | holdout PF {res['holdout']['pf_point']} "
              f"CI95 [{res['holdout']['pf_ci_2.5']}, {res['holdout']['pf_ci_97.5']}]")
    z.ENTRY_SLIP, z.STOP_SLIP, z.EXIT_SLIP = orig
    (PROJECT / "reports/fg3/bootstrap_ci.json").write_text(json.dumps(out, indent=1))
    print("saved: reports/fg3/bootstrap_ci.json")


if __name__ == "__main__":
    main()
