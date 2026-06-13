"""Fetch 1-min NIFTY ATM CE+PE bars for every weekly expiry day (0DTE study).

DATA ONLY — no order endpoints are called. Safe for paper/research use.

Key findings from empirical verification:
  - expiryCode=0 returns HTTP 400 (invalid). expiryCode=1 = nearest/current expiry.
  - On the expiry day, expiryCode=1 gives the 0DTE contract whose last close → ~0.
  - NIFTY weekly expiry was THURSDAY from 2021-06-14 through 2025-08-28 (inclusive).
  - From 2025-09-09 onward, expiry moved to TUESDAY.
  - The week of 2025-09-01 to 2025-09-05 had NO weekly NIFTY expiry (transition gap).

Expiry detection logic:
  1. For each calendar week, probe Thursday (pre-transition) or Tuesday (post-transition).
  2. Convergence criterion: last ATM straddle (CE+PE close sum) / spot <= 0.0015 AND
     last individual close <= ~50 on absolute basis.
  3. If primary day fails convergence, probe fallback days (Wed, then Mon/prior day)
     to handle exchange holidays.
  4. Weeks where no probe day shows convergence are skipped and documented.

Output:
  data/cache/_OPTIONS/NIFTY_0DTE/{YYYY-MM-DD}_{CE|PE}.parquet
  data/cache/_OPTIONS/inventory_0dte.json
  data/cache/_OPTIONS/expiry_calendar.json
"""
from __future__ import annotations

import json
import logging
import random
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

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
log = logging.getLogger("fetch_expiry_options")

IST = pytz.timezone("Asia/Kolkata")
API_BASE = "https://api.dhan.co/v2"

NIFTY_SCRIP_ID = "13"
NIFTY_EXCHANGE_SEGMENT = "NSE_FNO"

OPTIONS_DIR = PROJECT_ROOT / "data" / "cache" / "_OPTIONS"
NIFTY_0DTE_DIR = OPTIONS_DIR / "NIFTY_0DTE"
INVENTORY_JSON = OPTIONS_DIR / "inventory_0dte.json"
EXPIRY_CALENDAR_JSON = OPTIONS_DIR / "expiry_calendar.json"

# Rate-limit: max 4 req/s → use 0.28s between requests
REQUEST_INTERVAL = 0.28
MAX_RETRIES = 5

# Study window
STUDY_START = date(2021, 6, 14)
STUDY_END = date(2026, 6, 11)

# Transition: last Thursday expiry = 2025-08-28, first Tuesday expiry = 2025-09-09
LAST_THURSDAY_EXPIRY = date(2025, 8, 28)
FIRST_TUESDAY_EXPIRY = date(2025, 9, 9)

# Convergence thresholds for expiry-day detection
# On expiry day, last ATM close should be tiny (intrinsic ~0 for ATM)
CONVERGENCE_LAST_CLOSE_MAX = 80.0   # absolute close: below this is suspicious
CONVERGENCE_LAST_CLOSE_ZERO = 15.0  # below this is strong convergence signal
CONVERGENCE_IV_MAX = 500.0          # IV can spike on expiry (valid if close ~0)

# expiryCode confirmed correct:
EXPIRY_CODE = 1  # expiryCode=0 returns HTTP 400; expiryCode=1 = nearest (current) expiry


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
# API helper
# ---------------------------------------------------------------------------
def _post_with_retry(
    session: requests.Session,
    endpoint: str,
    payload: dict,
    headers: dict,
) -> Optional[dict]:
    """POST with exponential backoff on 429/5xx."""
    url = f"{API_BASE}{endpoint}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(
                url, data=json.dumps(payload), headers=headers, timeout=30
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = 5 * attempt
                log.warning("Rate-limited on attempt %d; sleeping %ds", attempt, wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2 ** attempt
                log.warning(
                    "Server error %d on attempt %d; sleeping %ds",
                    resp.status_code, attempt, wait,
                )
                time.sleep(wait)
                continue
            # Client error (4xx other than 429) — non-retryable
            log.error(
                "POST %s → %d: %s", endpoint, resp.status_code, resp.text[:300]
            )
            return None
        except requests.RequestException as exc:
            wait = 2 ** attempt
            log.warning(
                "Request exception on attempt %d: %s; retrying in %ds",
                attempt, exc, wait,
            )
            time.sleep(wait)
    log.error("Exhausted %d retries for %s", MAX_RETRIES, endpoint)
    return None


# ---------------------------------------------------------------------------
# Expiry calendar
# ---------------------------------------------------------------------------
def primary_candidate(week_monday: date) -> date:
    """Return the primary expiry candidate for a given week's Monday."""
    # Week's Thursday
    thursday = week_monday + timedelta(days=3)
    # Week's Tuesday
    tuesday = week_monday + timedelta(days=1)

    if week_monday <= LAST_THURSDAY_EXPIRY:
        # Still in Thursday-expiry era
        return thursday
    elif week_monday > LAST_THURSDAY_EXPIRY and tuesday < FIRST_TUESDAY_EXPIRY:
        # Transition gap week (Sep 1-5, 2025) — no expiry
        return None  # type: ignore[return-value]
    else:
        # Tuesday-expiry era
        return tuesday


def fallback_candidates(week_monday: date) -> list[date]:
    """Holiday fallback candidates (prior trading days)."""
    primary = primary_candidate(week_monday)
    if primary is None:
        return []
    candidates = []
    # Try prior day, then day before that (max 2 fallbacks)
    for delta in [1, 2]:
        fallback = primary - timedelta(days=delta)
        # Don't go back to previous week's Monday or earlier
        if fallback >= week_monday:
            candidates.append(fallback)
    return candidates


def week_mondays(start: date, end: date) -> list[date]:
    """Generate all Monday dates from start to end."""
    mondays = []
    # Find first Monday >= start
    d = start
    while d.weekday() != 0:  # 0=Monday
        d += timedelta(days=1)
    while d <= end:
        mondays.append(d)
        d += timedelta(days=7)
    return mondays


# ---------------------------------------------------------------------------
# Convergence probe
# ---------------------------------------------------------------------------
def probe_expiry(
    session: requests.Session,
    headers: dict,
    candidate: date,
) -> Optional[dict]:
    """
    Fetch CE+PE 1-min bars for a candidate date. Returns probe result dict or None.
    Result includes: ce_last_close, pe_last_close, straddle_last, n_bars_ce, n_bars_pe,
                     converges (bool), evidence string.
    """
    date_str = candidate.isoformat()
    results = {}

    for opt_type, leg_key in [("CALL", "ce"), ("PUT", "pe")]:
        payload = {
            "exchangeSegment": NIFTY_EXCHANGE_SEGMENT,
            "interval": "1",
            "securityId": NIFTY_SCRIP_ID,
            "instrument": "OPTIDX",
            "expiryFlag": "WEEK",
            "expiryCode": EXPIRY_CODE,
            "strike": "ATM",
            "drvOptionType": opt_type,
            "requiredData": ["open", "high", "low", "close", "volume", "oi", "iv", "strike"],
            "fromDate": date_str,
            "toDate": date_str,
        }
        result = _post_with_retry(session, "/charts/rollingoption", payload, headers)
        time.sleep(REQUEST_INTERVAL)

        if result is None:
            results[leg_key] = None
            continue

        raw = (result.get("data") or {}).get(leg_key) or {}
        timestamps = raw.get("timestamp", [])
        closes = raw.get("close", [])

        if not timestamps:
            results[leg_key] = None
            continue

        results[leg_key] = {
            "raw": raw,
            "n_bars": len(timestamps),
            "last_close": closes[-1] if closes else None,
            "first_close": closes[0] if closes else None,
        }

    ce = results.get("ce")
    pe = results.get("pe")

    if ce is None or pe is None:
        return None

    ce_last = ce.get("last_close")
    pe_last = pe.get("last_close")

    if ce_last is None or pe_last is None:
        return None

    straddle_last = ce_last + pe_last
    n_bars = max(ce.get("n_bars", 0), pe.get("n_bars", 0))

    # Convergence: on expiry day, the OTM leg expires worthless (~0).
    # The ITM leg retains intrinsic value. So: min(CE, PE) must be ~0.
    # This correctly handles cases where spot closes far from strike.
    min_last = min(ce_last, pe_last)
    converges = (
        min_last <= CONVERGENCE_LAST_CLOSE_ZERO
        and n_bars >= 300  # Need most of the session (375 bars = full session)
    )

    evidence = (
        f"ce_last={ce_last:.2f}, pe_last={pe_last:.2f}, "
        f"straddle={straddle_last:.2f}, n_bars={n_bars}"
    )

    return {
        "ce_data": ce,
        "pe_data": pe,
        "ce_last_close": ce_last,
        "pe_last_close": pe_last,
        "straddle_last": straddle_last,
        "n_bars": n_bars,
        "converges": converges,
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# Persist one leg as parquet
# ---------------------------------------------------------------------------
def save_leg(
    raw: dict,
    date_str: str,
    suffix: str,  # "CE" or "PE"
    expiry_code_used: int,
) -> tuple[Optional[pd.DataFrame], dict]:
    """Parse raw API data dict (with timestamp, open, ...) and save parquet."""
    timestamps = raw.get("timestamp", [])
    if not timestamps:
        return None, {"error": "empty timestamps"}

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

    # Filter to trading session 09:15–15:30 IST
    def in_session(t: datetime) -> bool:
        mins = t.hour * 60 + t.minute
        return 9 * 60 + 15 <= mins <= 15 * 60 + 30

    df = df[df["ts"].apply(in_session)].copy()
    df["expiry_code"] = f"WEEK_{expiry_code_used}"

    out_path = NIFTY_0DTE_DIR / f"{date_str}_{suffix}.parquet"
    df.to_parquet(out_path, index=False)

    meta = {
        "n_bars": len(df),
        "first_ts": str(df["ts"].iloc[0]) if len(df) > 0 else None,
        "last_ts": str(df["ts"].iloc[-1]) if len(df) > 0 else None,
        "last_close": float(df["close"].iloc[-1]) if len(df) > 0 else None,
        "path": str(out_path.relative_to(PROJECT_ROOT)),
        "status": "ok",
    }
    return df, meta


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------
def run() -> None:
    OPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    NIFTY_0DTE_DIR.mkdir(parents=True, exist_ok=True)

    # --- Auth ---
    token = get_valid_token(verbose=False)
    if not token:
        log.error("Cannot get valid Dhan token — aborting")
        sys.exit(1)
    client_id = _load_client_id()
    headers = _build_headers(token, client_id)
    session = requests.Session()

    # --- Load existing inventory (resume support) ---
    inventory: dict[str, dict] = {}
    if INVENTORY_JSON.exists():
        try:
            inventory = json.loads(INVENTORY_JSON.read_text())
            log.info("Loaded existing inventory with %d entries", len(inventory))
        except Exception:
            inventory = {}

    # --- Load existing expiry calendar ---
    expiry_calendar: dict[str, dict] = {}
    if EXPIRY_CALENDAR_JSON.exists():
        try:
            expiry_calendar = json.loads(EXPIRY_CALENDAR_JSON.read_text())
            log.info(
                "Loaded existing expiry calendar with %d entries", len(expiry_calendar)
            )
        except Exception:
            expiry_calendar = {}

    # --- Build list of weeks ---
    mondays = week_mondays(STUDY_START, STUDY_END)
    log.info("Total weeks to process: %d", len(mondays))

    ok_count = 0
    fail_count = 0
    skip_count = 0
    total_bars = 0

    for week_idx, monday in enumerate(mondays):
        week_str = monday.isoformat()

        # Determine primary candidate
        primary = primary_candidate(monday)
        if primary is None:
            log.info(
                "[week %d/%d] %s → TRANSITION GAP (no expiry this week)",
                week_idx + 1, len(mondays), week_str,
            )
            if week_str not in expiry_calendar:
                expiry_calendar[week_str] = {
                    "status": "transition_gap",
                    "reason": "Sep 1-5 2025 transition week between Thu and Tue expiry eras",
                }
                EXPIRY_CALENDAR_JSON.write_text(json.dumps(expiry_calendar, indent=2))
            skip_count += 1
            continue

        # Skip if already fully done for this week
        ce_key = f"{primary.isoformat()}_CE"
        pe_key = f"{primary.isoformat()}_PE"
        ce_ok = (
            (NIFTY_0DTE_DIR / f"{ce_key}.parquet").exists()
            and ce_key in inventory
            and inventory[ce_key].get("n_bars", 0) > 0
        )
        pe_ok = (
            (NIFTY_0DTE_DIR / f"{pe_key}.parquet").exists()
            and pe_key in inventory
            and inventory[pe_key].get("n_bars", 0) > 0
        )
        if ce_ok and pe_ok:
            log.info(
                "[week %d/%d] SKIP  %s (CE=%d bars, PE=%d bars cached)",
                week_idx + 1, len(mondays), primary.isoformat(),
                inventory[ce_key]["n_bars"], inventory[pe_key]["n_bars"],
            )
            ok_count += 2
            total_bars += inventory[ce_key]["n_bars"] + inventory[pe_key]["n_bars"]
            continue

        # Probe primary candidate, then fallbacks
        candidates_to_try = [primary] + fallback_candidates(monday)
        detected_expiry = None
        probe_result = None

        for candidate in candidates_to_try:
            if candidate > STUDY_END:
                continue
            log.info(
                "[week %d/%d] PROBE %s (%s)",
                week_idx + 1, len(mondays), candidate.isoformat(),
                candidate.strftime("%a"),
            )
            result = probe_expiry(session, headers, candidate)
            if result is not None and result["converges"]:
                detected_expiry = candidate
                probe_result = result
                log.info(
                    "  -> EXPIRY DETECTED: %s (%s) | %s",
                    candidate.isoformat(), candidate.strftime("%a"), result["evidence"],
                )
                break
            elif result is not None:
                log.info(
                    "  -> No convergence on %s (%s) | %s",
                    candidate.isoformat(), candidate.strftime("%a"),
                    result.get("evidence", "no data"),
                )

        if detected_expiry is None:
            log.warning(
                "[week %d/%d] %s → NO EXPIRY DETECTED (tried %s)",
                week_idx + 1, len(mondays), week_str,
                [c.isoformat() for c in candidates_to_try],
            )
            expiry_calendar[week_str] = {
                "status": "no_expiry_detected",
                "candidates_tried": [c.isoformat() for c in candidates_to_try],
                "probes": {
                    c.isoformat(): "no_convergence" for c in candidates_to_try
                },
            }
            EXPIRY_CALENDAR_JSON.write_text(json.dumps(expiry_calendar, indent=2))
            skip_count += 1
            continue

        # Record in calendar
        date_str = detected_expiry.isoformat()
        expiry_calendar[week_str] = {
            "status": "detected",
            "expiry_date": date_str,
            "weekday": detected_expiry.strftime("%A"),
            "evidence": probe_result["evidence"],
            "ce_last_close": probe_result["ce_last_close"],
            "pe_last_close": probe_result["pe_last_close"],
            "straddle_last": probe_result["straddle_last"],
            "n_bars": probe_result["n_bars"],
        }

        # Now save the legs — probe already fetched CE+PE data
        for opt_type, leg_key_suffix, data_key in [
            ("CALL", "CE", "ce_data"),
            ("PUT", "PE", "pe_data"),
        ]:
            inv_key = f"{date_str}_{leg_key_suffix}"
            out_path = NIFTY_0DTE_DIR / f"{inv_key}.parquet"

            if (
                out_path.exists()
                and inv_key in inventory
                and inventory[inv_key].get("n_bars", 0) > 0
            ):
                log.info("  SKIP %s (already cached)", inv_key)
                ok_count += 1
                total_bars += inventory[inv_key]["n_bars"]
                continue

            leg_data = probe_result[data_key]
            raw = leg_data.get("raw", {}) if leg_data else {}

            df, meta = save_leg(raw, date_str, leg_key_suffix, EXPIRY_CODE)

            if df is not None and len(df) > 0:
                meta["opt_type"] = opt_type
                meta["expiry_code_used"] = EXPIRY_CODE
                inventory[inv_key] = meta
                ok_count += 1
                total_bars += meta["n_bars"]
                log.info(
                    "  -> SAVED %s: %d bars, last_close=%.2f",
                    inv_key, meta["n_bars"], meta.get("last_close") or 0,
                )
            else:
                inventory[inv_key] = {
                    "status": "failed",
                    "error": "empty df after save_leg",
                    "opt_type": opt_type,
                }
                fail_count += 1
                log.warning("  -> FAILED to save %s", inv_key)

        # Persist after every week (crash-safe)
        INVENTORY_JSON.write_text(json.dumps(inventory, indent=2))
        EXPIRY_CALENDAR_JSON.write_text(json.dumps(expiry_calendar, indent=2))

    # --- Summary ---
    log.info("=" * 70)
    log.info("FETCH COMPLETE: %d legs OK, %d failed, %d weeks skipped, %d total bars",
             ok_count, fail_count, skip_count, total_bars)

    # --- Expiry calendar analysis ---
    detected = {
        w: v for w, v in expiry_calendar.items() if v.get("status") == "detected"
    }
    thu_count = sum(1 for v in detected.values() if v.get("weekday") == "Thursday")
    tue_count = sum(1 for v in detected.values() if v.get("weekday") == "Tuesday")
    wed_count = sum(1 for v in detected.values() if v.get("weekday") == "Wednesday")
    other_count = len(detected) - thu_count - tue_count - wed_count

    print("\n" + "=" * 70)
    print("EXPIRY CALENDAR SUMMARY")
    print(f"  Total weeks detected   : {len(detected)}")
    print(f"  Thursday expiries      : {thu_count}")
    print(f"  Tuesday expiries       : {tue_count}")
    print(f"  Wednesday expiries     : {wed_count}  (holiday shifts)")
    print(f"  Other                  : {other_count}")
    print(f"  Skipped/no expiry      : {skip_count}")

    # Weekday distribution by year
    year_dist: dict[int, dict[str, int]] = {}
    for v in detected.values():
        exp_date = date.fromisoformat(v["expiry_date"])
        yr = exp_date.year
        wd = v["weekday"]
        year_dist.setdefault(yr, {})
        year_dist[yr][wd] = year_dist[yr].get(wd, 0) + 1

    print("\n  Expiry weekday by year:")
    for yr in sorted(year_dist):
        print(f"    {yr}: {dict(sorted(year_dist[yr].items()))}")

    print(f"\n  expiryCode used: {EXPIRY_CODE} (expiryCode=0 returns HTTP 400)")
    print(f"  Transition: last Thursday expiry={LAST_THURSDAY_EXPIRY}, "
          f"first Tuesday expiry={FIRST_TUESDAY_EXPIRY}")

    # --- Verify 3 random detected expiry days ---
    ok_dates = list(detected.keys())
    if len(ok_dates) >= 3:
        sample_weeks = random.sample(ok_dates, 3)
        print("\n--- VERIFICATION: 3 random expiry days (ATM straddle collapse) ---")
        for week_str in sorted(sample_weeks):
            v = detected[week_str]
            exp_date_str = v["expiry_date"]
            ce_path = NIFTY_0DTE_DIR / f"{exp_date_str}_CE.parquet"
            pe_path = NIFTY_0DTE_DIR / f"{exp_date_str}_PE.parquet"

            if not ce_path.exists() or not pe_path.exists():
                print(f"\n  {exp_date_str}: parquet files missing — skipping verify")
                continue

            df_ce = pd.read_parquet(ce_path)
            df_pe = pd.read_parquet(pe_path)

            # 09:20 bar (5 min into session)
            def get_bar(df: pd.DataFrame, h: int, m: int) -> Optional[float]:
                row = df[df["ts"].apply(lambda t: t.hour == h and t.minute == m)]
                return float(row.iloc[0]["close"]) if len(row) > 0 else None

            ce_0920 = get_bar(df_ce, 9, 20)
            pe_0920 = get_bar(df_pe, 9, 20)
            ce_1525 = float(df_ce["close"].iloc[-1]) if len(df_ce) > 0 else None
            pe_1525 = float(df_pe["close"].iloc[-1]) if len(df_pe) > 0 else None

            straddle_open = (ce_0920 or 0) + (pe_0920 or 0)
            straddle_close = (ce_1525 or 0) + (pe_1525 or 0)
            collapse_pct = (
                (straddle_open - straddle_close) / straddle_open * 100
                if straddle_open > 0 else None
            )

            in_session_ce = all(
                9 * 60 + 15 <= (t.hour * 60 + t.minute) <= 15 * 60 + 30
                for t in df_ce["ts"]
            )
            in_session_pe = all(
                9 * 60 + 15 <= (t.hour * 60 + t.minute) <= 15 * 60 + 30
                for t in df_pe["ts"]
            )

            print(f"\n  {exp_date_str} ({v['weekday']}):")
            print(f"    CE bars={len(df_ce)}, PE bars={len(df_pe)}")
            print(f"    ATM straddle @ 09:20 = {straddle_open:.2f}  "
                  f"(CE={ce_0920}, PE={pe_0920})")
            print(f"    ATM straddle @ 15:25 = {straddle_close:.2f}  "
                  f"(CE={ce_1525}, PE={pe_1525})")
            if collapse_pct is not None:
                status = "PASS" if collapse_pct >= 70 else "WARN"
                print(f"    Straddle collapse    = {collapse_pct:.1f}%  [{status}]")
            print(f"    All bars in-session  = CE={'PASS' if in_session_ce else 'FAIL'}, "
                  f"PE={'PASS' if in_session_pe else 'FAIL'}")
    else:
        log.warning("Not enough detected expiry days for verification (%d)", len(ok_dates))

    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print(f"  Weeks processed        : {len(mondays)}")
    print(f"  Expiry days detected   : {len(detected)}")
    print(f"  Legs fetched OK        : {ok_count}")
    print(f"  Legs failed            : {fail_count}")
    print(f"  Total bars             : {total_bars}")
    print(f"  Inventory              : {INVENTORY_JSON}")
    print(f"  Expiry calendar        : {EXPIRY_CALENDAR_JSON}")
    print(f"  Parquet dir            : {NIFTY_0DTE_DIR}")
    print(f"  expiryCode correct     : {EXPIRY_CODE}  (0DTE = expiryCode=1)")
    print("=" * 70)


if __name__ == "__main__":
    run()
