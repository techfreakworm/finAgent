"""Fetch minute-level NIFTY option data for breadth-strategy signal days.

For each signal day (10:15 IST pct_above_vwap >= 0.72 LONG or <= 0.28 SHORT),
fetches ATM NIFTY weekly option (CE for LONG, PE for SHORT) at 1-minute granularity
using Dhan's rolling-option endpoint.

DATA ONLY — no order endpoints are called. Safe for paper/research use.

Output:
  data/cache/_OPTIONS/signal_days.json        list of {date, direction}
  data/cache/_OPTIONS/NIFTY/<date>_<CE|PE>.parquet  per-day bars
  data/cache/_OPTIONS/inventory.json          per-day status/metadata
"""
from __future__ import annotations

import json
import logging
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz
import requests

# ---------------------------------------------------------------------------
# Paths & logging
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from algotrader.data.token_manager import get_valid_token  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("fetch_options")

IST = pytz.timezone("Asia/Kolkata")
API_BASE = "https://api.dhan.co/v2"

# Dhan NIFTY underlying scrip / segment (confirmed from finAgent)
NIFTY_SCRIP_ID = "13"
NIFTY_EXCHANGE_SEGMENT = "NSE_FNO"

BREADTH_PARQUET = PROJECT_ROOT / "data" / "cache" / "_BREADTH" / "5m" / "breadth.parquet"
OPTIONS_DIR = PROJECT_ROOT / "data" / "cache" / "_OPTIONS"
NIFTY_DIR = OPTIONS_DIR / "NIFTY"
SIGNAL_DAYS_JSON = OPTIONS_DIR / "signal_days.json"
INVENTORY_JSON = OPTIONS_DIR / "inventory.json"

# Rate-limit: max 4 req/s → 0.25s between requests; we use 0.3s for safety
REQUEST_INTERVAL = 0.3
MAX_RETRIES = 5


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _build_headers(token: str, client_id: str) -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token,
        "client-id": client_id,
    }


def _load_client_id() -> str:
    env: dict[str, str] = {}
    for p in [
        PROJECT_ROOT / ".dhan-creds.env",
        Path("/etc/claude-soma/algo-trader-dhan.env"),
        PROJECT_ROOT / ".env",
        Path("/home/ubuntu/finAgent/.env"),
    ]:
        try:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if v and k not in env:
                    env[k] = v
        except OSError:
            pass
    return env.get("DHAN_CLIENT_ID", "")


# ---------------------------------------------------------------------------
# Step 1: Build signal-day list
# ---------------------------------------------------------------------------
def build_signal_days() -> list[dict]:
    """Load breadth parquet and extract signal days."""
    log.info("Loading breadth data from %s", BREADTH_PARQUET)
    df = pd.read_parquet(BREADTH_PARQUET)

    # Rows at 10:15 IST
    df_1015 = df[df["ts"].dt.time == pd.Timestamp("10:15").time()].copy()
    log.info("Total 10:15 rows: %d", len(df_1015))

    long_mask = (df_1015["pct_above_vwap"] >= 0.72) & (df_1015["n_stocks"] >= 30)
    short_mask = (df_1015["pct_above_vwap"] <= 0.28) & (df_1015["n_stocks"] >= 30)

    long_days = df_1015[long_mask]["ts"].dt.date.tolist()
    short_days = df_1015[short_mask]["ts"].dt.date.tolist()

    # Deduplicate (there shouldn't be any, but guard)
    long_set = {d for d in long_days}
    short_set = {d for d in short_days}
    overlap = long_set & short_set
    if overlap:
        log.warning("Removing %d days that appear in both LONG and SHORT: %s", len(overlap), overlap)
        long_set -= overlap
        short_set -= overlap

    signal_days = sorted(
        [{"date": str(d), "direction": "LONG"} for d in sorted(long_set)]
        + [{"date": str(d), "direction": "SHORT"} for d in sorted(short_set)],
        key=lambda x: x["date"],
    )

    log.info(
        "Signal days: %d total (%d LONG, %d SHORT)",
        len(signal_days), len(long_set), len(short_set),
    )
    return signal_days


# ---------------------------------------------------------------------------
# Step 2: Fetch one day's option bars
# ---------------------------------------------------------------------------
def _post_with_retry(
    session: requests.Session,
    endpoint: str,
    payload: dict,
    headers: dict,
) -> dict | None:
    """POST with exponential backoff on 429/5xx."""
    url = f"{API_BASE}{endpoint}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(url, data=json.dumps(payload), headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = 5 * attempt
                log.warning("Rate-limited on attempt %d; sleeping %ds", attempt, wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2 ** attempt
                log.warning("Server error %d on attempt %d; sleeping %ds",
                             resp.status_code, attempt, wait)
                time.sleep(wait)
                continue
            # Client error (4xx other than 429) — non-retryable
            log.error("POST %s → %d: %s", endpoint, resp.status_code, resp.text[:300])
            return None
        except requests.RequestException as exc:
            wait = 2 ** attempt
            log.warning("Request exception on attempt %d: %s; retrying in %ds", attempt, exc, wait)
            time.sleep(wait)
    log.error("Exhausted %d retries for %s", MAX_RETRIES, endpoint)
    return None


def fetch_day_bars(
    session: requests.Session,
    headers: dict,
    date_str: str,
    opt_type: str,  # "CALL" or "PUT"
) -> tuple[pd.DataFrame | None, dict]:
    """Fetch 1-min bars for one signal day. Returns (df, meta)."""
    payload = {
        "exchangeSegment": NIFTY_EXCHANGE_SEGMENT,
        "interval": "1",
        "securityId": NIFTY_SCRIP_ID,
        "instrument": "OPTIDX",
        "expiryFlag": "WEEK",
        "expiryCode": 1,
        "strike": "ATM",
        "drvOptionType": opt_type,
        "requiredData": ["open", "high", "low", "close", "volume", "oi", "iv", "strike"],
        "fromDate": date_str,
        "toDate": date_str,
    }

    result = _post_with_retry(session, "/charts/rollingoption", payload, headers)
    time.sleep(REQUEST_INTERVAL)

    if result is None:
        return None, {"error": "API returned None"}

    key = "ce" if opt_type == "CALL" else "pe"
    raw = result.get("data", {}).get(key, {})

    timestamps = raw.get("timestamp", [])
    if not timestamps:
        msg = f"empty timestamps; raw keys={list(raw.keys())}"
        log.warning("Day %s %s: %s", date_str, opt_type, msg)
        return None, {"error": msg}

    # Convert Unix epoch seconds to IST
    ts_ist = [
        datetime.fromtimestamp(t, tz=pytz.utc).astimezone(IST)
        for t in timestamps
    ]

    df = pd.DataFrame({
        "ts": ts_ist,
        "open": raw.get("open", [None] * len(timestamps)),
        "high": raw.get("high", [None] * len(timestamps)),
        "low": raw.get("low", [None] * len(timestamps)),
        "close": raw.get("close", [None] * len(timestamps)),
        "volume": raw.get("volume", [None] * len(timestamps)),
        "oi": raw.get("oi", [None] * len(timestamps)),
        "iv": raw.get("iv", [None] * len(timestamps)),
        "strike": raw.get("strike", [None] * len(timestamps)),
    })

    # Filter to trading session 09:15–15:30 IST (should already be clean)
    def in_session(t: datetime) -> bool:
        mins = t.hour * 60 + t.minute
        return 9 * 60 + 15 <= mins <= 15 * 60 + 30

    df = df[df["ts"].apply(in_session)].copy()
    df["strike_offset"] = "ATM"
    df["expiry_code"] = "WEEK_1"

    meta = {
        "n_bars": len(df),
        "first_ts": str(df["ts"].iloc[0]) if len(df) > 0 else None,
        "last_ts": str(df["ts"].iloc[-1]) if len(df) > 0 else None,
        "opt_type": opt_type,
        "close_at_1015": None,
    }
    # Capture close at 10:15 for plausibility check
    row_1015 = df[df["ts"].apply(lambda t: t.hour == 10 and t.minute == 15)]
    if len(row_1015) > 0:
        meta["close_at_1015"] = float(row_1015.iloc[0]["close"])

    return df, meta


# ---------------------------------------------------------------------------
# Step 3: Run and persist
# ---------------------------------------------------------------------------
def run() -> None:
    OPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    NIFTY_DIR.mkdir(parents=True, exist_ok=True)

    # --- Auth ---
    token = get_valid_token(verbose=False)
    if not token:
        log.error("Cannot get valid Dhan token — aborting")
        sys.exit(1)
    client_id = _load_client_id()
    headers = _build_headers(token, client_id)
    session = requests.Session()

    # --- Signal days ---
    signal_days = build_signal_days()
    SIGNAL_DAYS_JSON.write_text(json.dumps(signal_days, indent=2))
    log.info("Wrote %d signal days to %s", len(signal_days), SIGNAL_DAYS_JSON)

    # --- Load existing inventory (resume support) ---
    inventory: dict[str, dict] = {}
    if INVENTORY_JSON.exists():
        try:
            inventory = json.loads(INVENTORY_JSON.read_text())
            log.info("Loaded existing inventory with %d entries", len(inventory))
        except Exception:
            inventory = {}

    # --- Fetch loop ---
    ok_count = 0
    fail_count = 0
    total_bars = 0

    for idx, entry in enumerate(signal_days):
        date_str = entry["date"]
        direction = entry["direction"]
        opt_type = "CALL" if direction == "LONG" else "PUT"
        suffix = "CE" if direction == "LONG" else "PE"
        key = f"{date_str}_{suffix}"
        out_path = NIFTY_DIR / f"{key}.parquet"

        # Skip if already fetched successfully
        if out_path.exists() and key in inventory and inventory[key].get("n_bars", 0) > 0:
            log.info("[%d/%d] SKIP  %s (cached %d bars)",
                     idx + 1, len(signal_days), key, inventory[key]["n_bars"])
            ok_count += 1
            total_bars += inventory[key]["n_bars"]
            continue

        log.info("[%d/%d] FETCH %s  direction=%s", idx + 1, len(signal_days), date_str, direction)

        df, meta = fetch_day_bars(session, headers, date_str, opt_type)

        if df is not None and len(df) > 0:
            df.to_parquet(out_path, index=False)
            meta["status"] = "ok"
            meta["path"] = str(out_path.relative_to(PROJECT_ROOT))
            inventory[key] = meta
            ok_count += 1
            total_bars += meta["n_bars"]
            log.info("  -> OK: %d bars, close@10:15=%.2f",
                     meta["n_bars"], meta.get("close_at_1015") or 0)
        else:
            meta["status"] = "failed"
            inventory[key] = meta
            fail_count += 1
            log.warning("  -> FAILED: %s", meta.get("error", "unknown"))

        # Persist inventory after every day (crash-safe)
        INVENTORY_JSON.write_text(json.dumps(inventory, indent=2))

    # --- Summary ---
    log.info("=" * 60)
    log.info("DONE: %d OK, %d failed, %d total bars", ok_count, fail_count, total_bars)

    # --- Spot-check 3 random successful days ---
    ok_keys = [k for k, v in inventory.items() if v.get("status") == "ok" and v.get("n_bars", 0) > 0]
    if len(ok_keys) >= 3:
        samples = random.sample(ok_keys, 3)
        log.info("\n--- SPOT CHECK (3 random days) ---")
        for skey in sorted(samples):
            fpath = NIFTY_DIR / f"{skey}.parquet"
            df = pd.read_parquet(fpath)
            first_ts = df["ts"].iloc[0]
            last_ts = df["ts"].iloc[-1]
            close_range = (float(df["close"].min()), float(df["close"].max()))
            n_bars = len(df)
            row_1015 = df[df["ts"].apply(lambda t: t.hour == 10 and t.minute == 15)]
            close_1015 = float(row_1015.iloc[0]["close"]) if len(row_1015) > 0 else None
            print(f"\n  {skey}:")
            print(f"    bars={n_bars}, first={first_ts}, last={last_ts}")
            print(f"    close range=[{close_range[0]:.2f}, {close_range[1]:.2f}]")
            print(f"    close@10:15={close_1015}")
            plausible = close_1015 is not None and 10 <= close_1015 <= 2000
            print(f"    plausibility (10-2000): {'PASS' if plausible else 'WARN'}")
            in_session_all = all(
                9 * 60 + 15 <= (t.hour * 60 + t.minute) <= 15 * 60 + 30
                for t in df["ts"]
            )
            print(f"    all bars in 09:15-15:30: {'PASS' if in_session_all else 'FAIL'}")
    else:
        log.warning("Not enough successful days for spot-check (%d ok)", len(ok_keys))

    # Final stats
    long_days_cnt = sum(1 for s in signal_days if s["direction"] == "LONG")
    short_days_cnt = sum(1 for s in signal_days if s["direction"] == "SHORT")
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print(f"  Signal days total: {len(signal_days)}  (LONG={long_days_cnt}, SHORT={short_days_cnt})")
    print(f"  Days fetched OK  : {ok_count}")
    print(f"  Days failed      : {fail_count}")
    print(f"  Total bars       : {total_bars}")
    print(f"  Inventory        : {INVENTORY_JSON}")
    print(f"  Parquet dir      : {NIFTY_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    run()
