"""One-off fix: SUNPHARMA/ITC cache held WRONG instruments (2026-07-02).

Root cause: the legacy finAgent scratchpad map (dhan_nifty50_mapping.json) had
SUNPHARMA -> 14788 (= SPARC, Sun Pharma Advanced Research Co) and
ITC -> 10453 (= SATIN, Satin Creditcare). Every backfill faithfully fetched the
impostors. Discovered by the momentum B-triage adversarial panel (price-scale
QC); confirmed by auditing all 53 map ids against the NSE-EQ scrip master
(the only 2 equity mismatches; TATAMOTORS/3456 is a benign post-demerger
rename to TMPV, same id).

This script:
  1. quarantines the impostor cache dirs under data/cache/_QUARANTINE/
  2. re-fetches 5y of 1-min for SUNPHARMA (3351) and ITC (1660), storing 1m+5m
  3. QCs the fresh series (yearly ranges, |daily ret|>15% events)

The corrected canonical map now lives at data/reference/nifty50_secid_map.json
(all consumers repointed off the finAgent scratchpad).

Usage: .venv/bin/python scripts/fix_mismapped_secids.py
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from algotrader.data.history import FetchSpec, HistoryFetcher, integrity_report  # noqa: E402
from scripts.backfill import backfill_one, TODAY, START  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
FIXES = {"SUNPHARMA": "3351", "ITC": "1660"}   # authoritative NSE-EQ ids
QUAR = PROJECT / "data/cache/_QUARANTINE"


def main() -> None:
    cmap = json.load(open(PROJECT / "data/reference/nifty50_secid_map.json"))
    for sym, sid in FIXES.items():
        assert str(cmap[sym]) == sid, f"repo map not corrected for {sym}"

    # 1. quarantine impostor data (SPARC-as-SUNPHARMA, SATIN-as-ITC)
    QUAR.mkdir(parents=True, exist_ok=True)
    for sym, wrong in (("SUNPHARMA", "SPARC_as_SUNPHARMA"), ("ITC", "SATIN_as_ITC")):
        src = PROJECT / "data/cache" / sym
        dst = QUAR / wrong
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))
            print(f"quarantined {src} -> {dst}")

    # 2. re-fetch with correct ids (same probe/offset pattern as backfill.py)
    fetcher = HistoryFetcher()
    probe = FetchSpec("RELIANCE", str(cmap["RELIANCE"]), "NSE_EQ", "EQUITY")
    offset = fetcher.detect_stamp_mode(probe, TODAY - timedelta(days=7))
    print(f"stamp offset: {offset} min")
    for sym, sid in FIXES.items():
        spec = FetchSpec(sym, sid, "NSE_EQ", "EQUITY")
        r = backfill_one(fetcher, spec, offset, START, TODAY)
        print(f"{sym}: {r['bars']:,} bars fetched")

    # 3. QC
    for sym in FIXES:
        rep = integrity_report(sym, "1m")
        df = pd.concat([pd.read_parquet(f) for f in
                        sorted((PROJECT / "data/cache" / sym / "1m").glob("*.parquet"))])
        df["ts"] = pd.to_datetime(df["ts"])
        df["y"] = df["ts"].dt.year
        print(f"\n=== {sym} QC ===")
        print(df.groupby("y")["close"].agg(["min", "max"]))
        daily = df.set_index("ts").resample("1D")["close"].last().dropna()
        big = daily.pct_change().abs()
        big = big[big > 0.15]
        print(f"|daily ret|>15% events: {len(big)}")
        for ts, v in big.items():
            print(f"  {ts.date()}: {v:+.1%}")
        print(f"integrity: {rep}")


if __name__ == "__main__":
    main()
