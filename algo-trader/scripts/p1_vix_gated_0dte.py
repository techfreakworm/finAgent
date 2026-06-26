"""P1 (Gen-8): VIX-gate the proven 0DTE ATM short straddle.

PRE-REGISTERED RULE (fixed BEFORE looking at the holdout):
  - VIX_open = INDIAVIX 09:15 close on the expiry day (known at the 09:20 entry).
  - trailing percentile = % of the prior 252 trading-day VIX *closes* (strictly
    before the expiry day) that are < VIX_open.  Causal, no look-ahead.
  - GATE: SKIP the day if percentile < 33.333 (bottom trailing tercile).
    TRADE otherwise.  Window 252d PRIMARY (126d only as a robustness check).
  - Days lacking a full 252d VIX history CANNOT be gated -> traded + flagged.
  - Frozen config from gen-6 (NOT re-optimized): entry 09:20, stop 25%, no take.

SUCCESS CRITERIA (judged on BOTH eras, esp. the Tue-era HOLDOUT):
  gated PF >= ungated PF  AND  gated n < ungated n  AND
  the SKIPPED days' aggregate net <= 0  (i.e. we removed losers, not winners).

This is a Sharpe/draw-down improvement on a known earner, NOT a new-edge claim.
Run:  cd /home/ubuntu/projects/algo-trader-strategies && \
      ./.venv/bin/python scripts/p1_vix_gated_0dte.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from scripts.zerodte_straddle import (  # noqa: E402
    run_day, summarize, expiry_days, FENCE_LAST_THURSDAY, IST,
)

WINDOW = 252            # trailing trading days (primary)
TERCILE = 100.0 / 3.0   # bottom-tercile cutoff (percentile units)
ENTRY_T, STOP_PCT, TAKE_PCT = "09:20", 0.25, None  # frozen gen-6 config


def vix_series() -> tuple[pd.Series, pd.Series]:
    """Return (daily_close, open_0915) VIX series indexed by 'YYYY-MM-DD' str."""
    df = pd.concat([pd.read_parquet(f) for f in
                    sorted((PROJECT / "data/cache/INDIAVIX/1m").glob("*.parquet"))])
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
    df["d"] = df["ts"].dt.date.astype(str)
    df["t"] = df["ts"].dt.strftime("%H:%M")
    daily_close = df.sort_values("ts").groupby("d")["close"].last()
    op = df[df["t"] == "09:15"].set_index("d")["close"]
    return daily_close, op


def trailing_pct(daily_close: pd.Series, day: str, vix_open: float) -> float | None:
    prior = daily_close[daily_close.index < day]
    if len(prior) < WINDOW:
        return None
    w = prior.iloc[-WINDOW:]
    return float((w < vix_open).mean() * 100.0)


def main() -> None:
    daily_close, vix_open = vix_series()
    days = expiry_days()
    fence = FENCE_LAST_THURSDAY

    rows = []
    for d in days:
        r = run_day(d, ENTRY_T, STOP_PCT, TAKE_PCT)
        if r is None:
            continue
        vo = vix_open.get(d)
        pct = trailing_pct(daily_close, d, float(vo)) if vo is not None else None
        gated_in = (pct is None) or (pct >= TERCILE)   # None => can't gate => trade
        rows.append({**r, "vix_open": None if vo is None else round(float(vo), 2),
                     "vix_pct": None if pct is None else round(pct, 1),
                     "ungateable": pct is None, "gated_in": gated_in,
                     "era": "fenced" if d <= fence else "holdout"})

    def report(era: str) -> None:
        ev = [r for r in rows if r["era"] == era]
        ungated = ev
        gated = [r for r in ev if r["gated_in"]]
        skipped = [r for r in ev if not r["gated_in"]]
        ung = summarize(ungated, f"{era}-ungated")
        gat = summarize(gated, f"{era}-gated")
        skip_net = round(sum(r["net"] for r in skipped))
        n_ungateable = sum(1 for r in ev if r["ungateable"])
        print(f"\n===== {era.upper()} ({len(ev)} tradeable expiry days; "
              f"{n_ungateable} ungateable/no-252d-history) =====")
        for s in (ung, gat):
            if s["n"]:
                print(f"  {s['label']:<18} n={s['n']:>3} net=Rs{s['net']:>9,} "
                      f"PF={s['pf']} win={s['win_rate']:.0%} "
                      f"posW={s['pos_window_frac']:.0%} DD=Rs{s['max_dd']:>9,} "
                      f"worstTrade=Rs{s['worst_trade']:>8,} avg=Rs{s['avg_per_trade']:>6,}")
        print(f"  SKIPPED (bottom-VIX-tercile): n={len(skipped)} "
              f"aggregate net=Rs{skip_net:,}  "
              f"[gate justified iff this is <= 0]")
        # pre-registered verdict (skip if either side empty)
        if ung["n"] and gat["n"]:
            ok = (gat["pf"] is not None and ung["pf"] is not None
                  and gat["pf"] >= ung["pf"] and gat["n"] < ung["n"]
                  and skip_net <= 0)
            print(f"  PRE-REG VERDICT [{era}]: {'PASS' if ok else 'FAIL/NEUTRAL'} "
                  f"(PF {gat['pf']} vs {ung['pf']}; n {gat['n']} vs {ung['n']}; "
                  f"skipped-net {skip_net:,})")

    print(f"Total tradeable expiry days: {len(rows)} "
          f"(fence={fence}; window={WINDOW}d; bottom-tercile cutoff={TERCILE:.1f}pct)")
    for era in ("fenced", "holdout"):
        report(era)


if __name__ == "__main__":
    main()
