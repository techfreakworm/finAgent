"""5-year 1-min backfill: NIFTY-50 equities + NIFTY/BANKNIFTY/VIX indices
+ active index futures. Derives 5-min locally. Writes integrity report.

Run:  cd <project> && .venv/bin/python scripts/backfill.py
"""
from __future__ import annotations

import json
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from algotrader.data.history import FetchSpec, HistoryFetcher, integrity_report  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime.now(IST).date()
START = TODAY - timedelta(days=5 * 365)          # API depth limit: 5 years
WINDOW = timedelta(days=89)

NIFTY50_MAP = json.load(open("/home/ubuntu/finAgent/research_scratchpad/dhan_nifty50_mapping.json"))

INDICES = [
    FetchSpec("NIFTY", "13", "IDX_I", "INDEX"),
    FetchSpec("BANKNIFTY", "25", "IDX_I", "INDEX"),
    FetchSpec("INDIAVIX", "21", "IDX_I", "INDEX"),
]


def active_index_futures() -> list[FetchSpec]:
    """Active NIFTY/BANKNIFTY futures from the scrip master (expired ones
    are not addressable on this API — index bars carry the 5y history)."""
    df = pd.read_csv(PROJECT / "data/instruments/api-scrip-master.csv", low_memory=False)
    fut = df[(df.SEM_EXM_EXCH_ID == "NSE") & (df.SEM_INSTRUMENT_NAME == "FUTIDX")
             & (df.SEM_TRADING_SYMBOL.str.match(r"^(NIFTY|BANKNIFTY)-"))]
    return [FetchSpec(r.SEM_TRADING_SYMBOL, str(r.SEM_SMST_SECURITY_ID), "NSE_FNO", "FUTIDX")
            for r in fut.itertuples()]


def windows(frm: date, to: date):
    cur = frm
    while cur <= to:
        end = min(cur + WINDOW, to)
        yield cur, end
        cur = end + timedelta(days=1)


def backfill_one(fetcher: HistoryFetcher, spec: FetchSpec, offset_min: int,
                 frm: date, to: date) -> dict:
    total = 0
    for w_from, w_to in windows(frm, to):
        raw = fetcher.fetch_window(spec, w_from, w_to)
        df = fetcher.normalize(raw, offset_min)
        if len(df):
            fetcher.store(spec.symbol, df, "1m")
            fetcher.store(spec.symbol, fetcher.resample_5m(df), "5m")
            total += len(df)
        print(f"[{datetime.now(IST):%H:%M:%S}] {spec.symbol} {w_from}..{w_to}: "
              f"+{len(df)} bars (cum {total})", flush=True)
    return {"symbol": spec.symbol, "bars": total}


def main() -> None:
    fetcher = HistoryFetcher(rate_per_sec=4.0)

    # 1) stamp-convention probe on a known full session
    probe_spec = FetchSpec("RELIANCE", str(NIFTY50_MAP["RELIANCE"]), "NSE_EQ", "EQUITY")
    offset = fetcher.detect_stamp_mode(probe_spec, date(2026, 6, 10))
    print(f"stamp mode: subtract {offset} min (0=start-stamped)", flush=True)

    specs = (
        [FetchSpec(sym, str(sid), "NSE_EQ", "EQUITY") for sym, sid in NIFTY50_MAP.items()]
        + INDICES
    )
    futures_specs = active_index_futures()
    print(f"universe: {len(specs)} cash/index + {len(futures_specs)} active futures; "
          f"range {START}..{TODAY}", flush=True)

    results, errors = [], []
    lock = threading.Lock()

    def run(spec: FetchSpec, frm: date) -> None:
        try:
            r = backfill_one(fetcher, spec, offset, frm, TODAY)
            with lock:
                results.append(r)
        except Exception:
            with lock:
                errors.append({"symbol": spec.symbol, "trace": traceback.format_exc()[-800:]})

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(run, s, START) for s in specs]
        # futures contracts: only ~3 months of life — fetch their whole span cheaply
        futs += [ex.submit(run, s, TODAY - timedelta(days=120)) for s in futures_specs]
        for f in as_completed(futs):
            f.result()

    # integrity pass
    reports = [integrity_report(r["symbol"], "1m") for r in results]
    summary = {
        "completed_at": datetime.now(IST).isoformat(timespec="seconds"),
        "instruments": len(results),
        "errors": errors,
        "total_bars_1m": sum(r["bars"] for r in reports),
        "instruments_with_issues": [r for r in reports if r["issues"]],
        "reports": reports,
    }
    out = PROJECT / "reports" / "backfill_integrity.json"
    out.write_text(json.dumps(summary, indent=1))
    print(f"\nBACKFILL DONE: {summary['instruments']} instruments, "
          f"{summary['total_bars_1m']:,} 1-min bars, "
          f"{len(errors)} errors, issues on "
          f"{len(summary['instruments_with_issues'])} instruments. "
          f"Report: {out}", flush=True)


if __name__ == "__main__":
    main()
