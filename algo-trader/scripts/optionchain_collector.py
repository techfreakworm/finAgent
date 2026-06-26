"""Forward option-chain collector — Dhan REST /v2/optionchain.

Captures, each session, the REAL per-strike option market for the 2 nearest NIFTY
weeklies: last / bid / ask (+qty) / IV / greeks / OI / volume / security_id, by
NAMED expiry (no rollingoption re-anchoring). This is the real-data store that
unblocks clean P2 (defined-risk wings), P3 (non-expiry both-leg straddles), and a
MEASURED slippage model (real half-spreads vs our modeled 1%/2.5%).

PAPER / DATA ONLY. Read-only market data; imports/calls NO order path. The
structural live-order lockout is unaffected.

Store:
  data/cache/_OPTIONS/NIFTY_CHAIN_FWD/{YYYY-MM-DD}.parquet  (long format)
  data/cache/_OPTIONS/inventory_chain_fwd.json              (per-day manifest)

Run (systemd, market hours):  .venv/bin/python scripts/optionchain_collector.py
Smoke test (off-hours, N cycles, ignores market gate):
  .venv/bin/python scripts/optionchain_collector.py --smoke 3
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time as _time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from algotrader.data.token_manager import get_valid_token, _load_env  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
API = "https://api.dhan.co/v2"
NIFTY_SCRIP, SEG = 13, "IDX_I"
STORE = PROJECT / "data/cache/_OPTIONS/NIFTY_CHAIN_FWD"
INV = PROJECT / "data/cache/_OPTIONS/inventory_chain_fwd.json"

BAND_PTS = 600          # capture ATM +/- this many points
STEP = 50               # NIFTY strike step
N_EXPIRIES = 2          # 2 nearest weeklies (front + next)
SESSION_START = dtime(9, 14)
SESSION_END = dtime(15, 16)
BURST_WINDOWS = [(dtime(9, 14), dtime(9, 26)), (dtime(15, 4), dtime(15, 15))]
BURST_CADENCE = 5       # seconds
BASE_CADENCE = 60       # seconds
FLUSH_EVERY = 20        # write parquet every N snapshots
REQ_SPACING = 1.6       # seconds between the two per-expiry calls (distinct keys)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s optionchain_collector: %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger(__name__)

_GREEKS = ("delta", "gamma", "theta", "vega")
_NODE = ("last_price", "top_bid_price", "top_bid_quantity", "top_ask_price",
         "top_ask_quantity", "implied_volatility", "oi", "volume",
         "previous_close_price", "previous_oi", "previous_volume",
         "average_price", "security_id")


def _headers() -> dict:
    tok = get_valid_token()
    cid = _load_env().get("DHAN_CLIENT_ID", "")
    if not tok or not cid:
        raise RuntimeError("no token/client_id")
    return {"access-token": tok, "client-id": cid, "Content-Type": "application/json"}


def _post(path: str, body: dict, headers: dict) -> dict:
    r = requests.post(f"{API}{path}", json=body, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def expirylist(headers: dict) -> list[str]:
    j = _post("/optionchain/expirylist",
              {"UnderlyingScrip": NIFTY_SCRIP, "UnderlyingSeg": SEG}, headers)
    d = j.get("data", j)
    return d if isinstance(d, list) else []


def _in_burst(t: dtime) -> bool:
    return any(a <= t <= b for a, b in BURST_WINDOWS)


def snapshot(expiry: str, headers: dict, snap_ts: datetime) -> list[dict]:
    """One /optionchain snapshot for one expiry → rows for the ATM±BAND band."""
    j = _post("/optionchain",
              {"UnderlyingScrip": NIFTY_SCRIP, "UnderlyingSeg": SEG, "Expiry": expiry},
              headers)
    data = j.get("data", {})
    spot = data.get("last_price")
    oc = data.get("oc", {}) or {}
    if not oc or spot is None:
        return []
    atm = round(float(spot) / STEP) * STEP
    lo, hi = atm - BAND_PTS, atm + BAND_PTS
    dte = (datetime.fromisoformat(expiry).date() - snap_ts.date()).days
    rows = []
    for k, node in oc.items():
        try:
            strike = float(k)
        except ValueError:
            continue
        if not (lo <= strike <= hi):
            continue
        for ot in ("ce", "pe"):
            n = node.get(ot) or {}
            if not n:
                continue
            g = n.get("greeks", {}) or {}
            row = {"snap_ts": snap_ts.isoformat(), "expiry": expiry, "dte": dte,
                   "spot": float(spot), "strike": strike, "opt_type": ot.upper()}
            for f in _NODE:
                row[f] = n.get(f)
            for f in _GREEKS:
                row[f] = g.get(f)
            rows.append(row)
    return rows


def _flush(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def _qc_inventory(rows: list[dict], date_str: str, n_snaps: int, expiries: list[str]) -> dict:
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    inv = {"date": date_str, "n_snaps": n_snaps, "n_rows": len(df),
           "expiries": expiries, "status": "ok" if len(df) else "empty"}
    if len(df):
        df["bid"] = pd.to_numeric(df["top_bid_price"], errors="coerce")
        df["ask"] = pd.to_numeric(df["top_ask_price"], errors="coerce")
        df["iv"] = pd.to_numeric(df["implied_volatility"], errors="coerce")
        df["spread"] = df["ask"] - df["bid"]
        atm_mask = (df["strike"] - df["spot"]).abs() <= STEP
        inv["strikes_min"] = float(df["strike"].min())
        inv["strikes_max"] = float(df["strike"].max())
        inv["first_ts"] = df["snap_ts"].min()
        inv["last_ts"] = df["snap_ts"].max()
        inv["median_atm_spread"] = round(float(df.loc[atm_mask, "spread"].median()), 4)
        inv["median_iv"] = round(float(df["iv"].median()), 3)
        # illiquid flag: rows with no bid or no ask
        inv["illiquid_rows"] = int(((df["bid"] <= 0) | (df["ask"] <= 0)).sum())
        # IV sanity (ATM IV in a plausible band)
        atm_iv = df.loc[atm_mask, "iv"].median()
        inv["atm_iv_ok"] = bool(3 <= (atm_iv if atm_iv == atm_iv else -1) <= 80)
    return inv


def _write_inventory(entry: dict) -> None:
    allinv = {}
    if INV.exists():
        try:
            allinv = json.loads(INV.read_text())
        except ValueError:
            allinv = {}
    allinv[entry["date"]] = entry
    INV.parent.mkdir(parents=True, exist_ok=True)
    INV.write_text(json.dumps(allinv, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=0,
                    help="run N cycles ignoring the market-hours gate (testing)")
    args = ap.parse_args()

    headers = _headers()
    exps = expirylist(headers)[:N_EXPIRIES]
    if not exps:
        log.error("no expiries returned; aborting")
        return 1
    log.info("collecting %d expiries: %s", len(exps), exps)

    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))

    # pre-market wait (skipped in smoke mode)
    while not args.smoke and not stop["flag"]:
        now = datetime.now(IST)
        if now.time() >= SESSION_START:
            break
        log.info("pre-market (%s < %s) — waiting", now.time().strftime("%H:%M:%S"), SESSION_START)
        _time.sleep(20)

    date_str = datetime.now(IST).date().isoformat()
    out = STORE / f"{date_str}.parquet"
    rows: list[dict] = []
    n_snaps = 0
    cycles = 0

    while not stop["flag"]:
        now = datetime.now(IST)
        if not args.smoke and now.time() >= SESSION_END:
            break
        # one snapshot cycle across both expiries (distinct rate-limit keys)
        got = 0
        for i, exp in enumerate(exps):
            try:
                r = snapshot(exp, headers, datetime.now(IST))
                rows.extend(r); got += len(r)
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else "?"
                log.warning("optionchain HTTP %s for %s (refreshing headers next cycle)", code, exp)
                if code in (401, 403):
                    try:
                        headers = _headers()
                    except Exception:
                        pass
            except Exception as e:
                log.warning("snapshot error %s for %s: %s", type(e).__name__, exp, e)
            if i + 1 < len(exps):
                _time.sleep(REQ_SPACING)
        if got:
            n_snaps += 1
        if n_snaps and n_snaps % FLUSH_EVERY == 0:
            _flush(rows, out)
            log.info("flushed %d rows after %d snaps", len(rows), n_snaps)

        cycles += 1
        if args.smoke and cycles >= args.smoke:
            break
        cadence = BURST_CADENCE if _in_burst(datetime.now(IST).time()) else BASE_CADENCE
        # responsive sleep so SIGTERM is honoured promptly
        slept = 0.0
        while slept < cadence and not stop["flag"]:
            _time.sleep(min(1.0, cadence - slept)); slept += 1.0

    _flush(rows, out)
    inv = _qc_inventory(rows, date_str, n_snaps, exps)
    _write_inventory(inv)
    log.info("DONE: %d snaps, %d rows -> %s | QC: %s", n_snaps, len(rows), out,
             {k: inv.get(k) for k in ("median_atm_spread", "median_iv", "illiquid_rows", "atm_iv_ok", "status")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
