"""Historical intraday backfill from Dhan /charts/intraday → parquet store.

Design (ARCHITECTURE §2):
- Fetch 1-min only; 5-min is derived locally by exact OHLCV resample.
- 90-day request windows (API cap), adaptive halving on failure/empty.
- Token-bucket rate limiting at 4 rps (under the 5 rps Data-API cap),
  exponential backoff on 429/5xx — finAgent's single-retry caused silent gaps.
- Timestamps normalized to BAR-START, tz-aware IST, session 09:15–15:29 starts.
  Dhan stamps are resolved empirically once per run (see _detect_stamp_mode).
- Storage: data/cache/{SYMBOL}/1m/{YYYY-MM}.parquet, written only for days
  whose session has closed (immutability guard).
"""
from __future__ import annotations

import json
import threading
import time as time_mod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from algotrader.data.token_manager import get_valid_token, _load_env

IST = ZoneInfo("Asia/Kolkata")
API = "https://api.dhan.co/v2"
SESSION_FIRST_START = time(9, 15)
SESSION_LAST_START = time(15, 29)   # last 1-min bar starts 15:29
CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "cache"


class _Bucket:
    """Simple token bucket: `rate` requests/sec shared across threads."""

    def __init__(self, rate: float):
        self._interval = 1.0 / rate
        self._lock = threading.Lock()
        self._next = 0.0

    def take(self) -> None:
        with self._lock:
            now = time_mod.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + self._interval
        if wait > 0:
            time_mod.sleep(wait)


@dataclass(frozen=True)
class FetchSpec:
    symbol: str
    security_id: str
    exchange_segment: str   # NSE_EQ | IDX_I | NSE_FNO
    instrument: str         # EQUITY | INDEX | FUTIDX


class HistoryFetcher:
    def __init__(self, rate_per_sec: float = 4.0):
        self._bucket = _Bucket(rate_per_sec)
        self._token = get_valid_token()
        self._client_id = _load_env().get("DHAN_CLIENT_ID", "")
        self._stamp_offset_min: int | None = None  # 0 if bar-start, +interval if bar-end

    # ------------------------------------------------------------- raw fetch
    def _post(self, payload: dict, max_tries: int = 5) -> dict | None:
        for attempt in range(max_tries):
            self._bucket.take()
            try:
                r = requests.post(
                    f"{API}/charts/intraday",
                    headers={"access-token": self._token,
                             "client-id": self._client_id,
                             "Content-Type": "application/json"},
                    json=payload, timeout=60)
            except requests.RequestException:
                time_mod.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:           # token died mid-run → re-mint once per attempt
                self._token = get_valid_token() or self._token
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time_mod.sleep(2 ** attempt)
                continue
            # 400-class: caller decides (window too large / no data)
            return None
        return None

    def fetch_window(self, spec: FetchSpec, frm: date, to: date) -> pd.DataFrame:
        """Fetch 1-min bars for [frm, to] inclusive; adaptive window halving."""
        body = {
            "securityId": spec.security_id,
            "exchangeSegment": spec.exchange_segment,
            "instrument": spec.instrument,
            "interval": "1",
            "fromDate": f"{frm} 09:15:00",
            "toDate": f"{to} 15:30:00",
        }
        data = self._post(body)
        if data and data.get("timestamp"):
            return self._to_frame(data)
        if (to - frm).days <= 5:
            return pd.DataFrame()              # genuinely empty (holiday stretch / no data)
        mid = frm + (to - frm) / 2
        left = self.fetch_window(spec, frm, mid)
        right = self.fetch_window(spec, mid + timedelta(days=1), to)
        return pd.concat([left, right], ignore_index=True) if len(left) or len(right) \
            else pd.DataFrame()

    def _to_frame(self, data: dict) -> pd.DataFrame:
        df = pd.DataFrame({
            "ts": pd.to_datetime(pd.Series(data["timestamp"], dtype="int64"),
                                 unit="s", utc=True).dt.tz_convert(IST),
            "open": data["open"], "high": data["high"],
            "low": data["low"], "close": data["close"],
            # some instruments return float volumes — round, don't coerce-crash
            "volume": pd.Series(data["volume"], dtype="float64").round().astype("int64"),
        })
        if "open_interest" in data and isinstance(data["open_interest"], list) and data["open_interest"]:
            df["oi"] = pd.Series(data["open_interest"], dtype="int64")
        return df

    # ----------------------------------------------------- stamp normalization
    def detect_stamp_mode(self, spec: FetchSpec, probe_day: date) -> int:
        """Return minutes to SUBTRACT so ts == bar START.
        If a full day's first stamp is 09:15 → bars are start-stamped (0).
        If 09:16 (1-min) → end-stamped (subtract interval)."""
        df = self.fetch_window(spec, probe_day, probe_day)
        if df.empty:
            raise RuntimeError(f"stamp probe got no data for {spec.symbol} {probe_day}")
        first = df["ts"].min().time()
        if first == time(9, 15):
            return 0
        if first == time(9, 16):
            return 1
        raise RuntimeError(f"unexpected first-bar stamp {first} for {spec.symbol}")

    def normalize(self, df: pd.DataFrame, offset_min: int) -> pd.DataFrame:
        if df.empty:
            return df
        out = df.copy()
        if offset_min:
            out["ts"] = out["ts"] - pd.Timedelta(minutes=offset_min)
        # session filter on bar-START times; drop dupes; sort
        t = out["ts"].dt.time
        out = out[(t >= SESSION_FIRST_START) & (t <= SESSION_LAST_START)]
        out = out.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)
        return out

    # ---------------------------------------------------------------- storage
    @staticmethod
    def store(symbol: str, df: pd.DataFrame, interval: str = "1m") -> list[Path]:
        """Write month-partitioned parquet; only fully-closed days (immutability)."""
        if df.empty:
            return []
        today = datetime.now(IST).date()
        now_t = datetime.now(IST).time()
        cutoff = today if now_t > time(15, 35) else today - timedelta(days=1)
        df = df[df["ts"].dt.date <= cutoff]
        written = []
        for period, chunk in df.groupby(df["ts"].dt.strftime("%Y-%m")):
            p = CACHE / symbol / interval / f"{period}.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():  # merge with existing, dedupe on ts
                old = pd.read_parquet(p)
                old["ts"] = pd.to_datetime(old["ts"]).dt.tz_convert(IST)
                chunk = (pd.concat([old, chunk], ignore_index=True)
                         .drop_duplicates(subset="ts").sort_values("ts"))
            chunk.reset_index(drop=True).to_parquet(p, index=False)
            written.append(p)
        return written

    @staticmethod
    def resample_5m(df: pd.DataFrame) -> pd.DataFrame:
        """Exact 5-min OHLCV from 1-min bars, aligned to 09:15 boundaries."""
        if df.empty:
            return df
        g = df.set_index("ts").groupby(pd.Grouper(freq="5min", origin="start_day",
                                                  offset="9h15min"))
        out = pd.DataFrame({
            "open": g["open"].first(), "high": g["high"].max(),
            "low": g["low"].min(), "close": g["close"].last(),
            "volume": g["volume"].sum(),
        }).dropna(subset=["open"]).reset_index()
        if "oi" in df.columns:
            out["oi"] = g["oi"].last().dropna().reset_index(drop=True)
        return out


# ------------------------------------------------------------------ integrity

def integrity_report(symbol: str, interval: str = "1m") -> dict:
    """Per-instrument integrity: day count, per-day bar stats, dupes, bounds."""
    files = sorted((CACHE / symbol / interval).glob("*.parquet"))
    if not files:
        return {"symbol": symbol, "days": 0, "bars": 0, "issues": ["no data"]}
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
    dupes = int(df.duplicated(subset="ts").sum())
    t = df["ts"].dt.time
    out_of_session = int(((t < SESSION_FIRST_START) | (t > SESSION_LAST_START)).sum())
    per_day = df.groupby(df["ts"].dt.date).size()
    short_days = {str(d): int(n) for d, n in per_day.items() if n < 300}  # <300 of 375 bars
    issues = []
    if dupes:
        issues.append(f"{dupes} duplicate timestamps")
    if out_of_session:
        issues.append(f"{out_of_session} bars outside 09:15–15:29 starts")
    return {
        "symbol": symbol, "days": int(per_day.size), "bars": int(len(df)),
        "first": str(df['ts'].min()), "last": str(df['ts'].max()),
        "median_bars_per_day": float(per_day.median()),
        "short_days": short_days, "dupes": dupes, "issues": issues,
    }
