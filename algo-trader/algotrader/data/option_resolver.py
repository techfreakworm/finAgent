"""Point-in-time NSE index-option resolver (ARCHITECTURE §2, §4 phase-2).

Resolves a (underlying, strike, opt_type, date) tuple to a ``FnoInstrument``
carrying the real Dhan security_id, tick size, and point-in-time lot size.

Source: data/instruments/api-scrip-master.csv (NSE, SEM_INSTRUMENT_NAME='OPTIDX').
Symbol format: "<UNDERLYING>-<MonYYYY>-<strike>-<CE|PE>" e.g. "NIFTY-Jun2026-24350-CE".

Expiry selection rules:
  NIFTY     — NEAREST expiry date >= on (weekly W + monthly M; NIFTY has Tue weeklies).
  BANKNIFTY — NEAREST MONTHLY expiry date >= on (SEM_EXPIRY_FLAG == 'M'; SEBI abolished
               BANKNIFTY weeklies — ARCHITECTURE §4 note).

Paper/data only: no order methods are imported or called.
"""
from __future__ import annotations

import logging
import threading
import time as _time_mod
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
import requests

from algotrader.core import Segment
from algotrader.data.instruments import FnoInstrument

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

_SCRIP_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "instruments" / "api-scrip-master.csv"
)
_DOWNLOAD_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
_MAX_AGE_SECS = 86_400          # 24 h
_DOWNLOAD_TIMEOUT = 60          # seconds

Underlying = Literal["NIFTY", "BANKNIFTY"]
OptType    = Literal["CE", "PE"]

# Strike grids (nearest-ATM rounding)
_GRID: dict[str, float] = {"NIFTY": 50.0, "BANKNIFTY": 100.0}

# ---------------------------------------------------------------------------
# In-memory scrip-master cache (module-level, thread-safe)
# ---------------------------------------------------------------------------

_df_cache: pd.DataFrame | None = None
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Refresh / load helpers
# ---------------------------------------------------------------------------

def refresh_scrip_master(force: bool = False) -> None:
    """Download api-scrip-master.csv if the local copy is older than 24 h.

    The URL is public and requires no auth.  On download failure the existing
    file (if any) is left intact and a warning is logged.

    Parameters
    ----------
    force:
        Re-download unconditionally, ignoring the file age.
    """
    _SCRIP_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not force and _SCRIP_PATH.exists():
        age_secs = _time_mod.time() - _SCRIP_PATH.stat().st_mtime
        if age_secs < _MAX_AGE_SECS:
            log.debug("scrip master is fresh (%.0f s old); skipping download", age_secs)
            return
        log.info(
            "scrip master is %.1f h old (> 24 h); re-downloading from %s",
            age_secs / 3600,
            _DOWNLOAD_URL,
        )
    else:
        if not _SCRIP_PATH.exists():
            log.info("scrip master not found; downloading from %s", _DOWNLOAD_URL)
        else:
            log.info("force-refreshing scrip master from %s", _DOWNLOAD_URL)

    try:
        r = requests.get(_DOWNLOAD_URL, timeout=_DOWNLOAD_TIMEOUT, stream=True)
        r.raise_for_status()
        tmp = _SCRIP_PATH.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
        tmp.replace(_SCRIP_PATH)
        log.info("scrip master updated: %s", _SCRIP_PATH)
    except requests.RequestException as exc:
        log.warning("scrip master download failed (%s); using existing file", exc)

    # Invalidate in-memory cache so next call to _load_df re-reads
    global _df_cache
    with _cache_lock:
        _df_cache = None


def _load_df() -> pd.DataFrame:
    """Load (and cache) the NSE OPTIDX rows from the scrip master CSV.

    Refreshes the file if it is older than 24 h.  Returns a DataFrame with
    columns: symbol, security_id, expiry_date (date), expiry_flag, strike,
    opt_type, tick_size, lot_units.
    """
    global _df_cache
    with _cache_lock:
        if _df_cache is not None:
            return _df_cache

    # Refresh stale file before loading (outside the lock to avoid blocking)
    refresh_scrip_master()

    if not _SCRIP_PATH.exists():
        raise FileNotFoundError(
            f"scrip master not found at {_SCRIP_PATH} and download failed"
        )

    raw = pd.read_csv(
        _SCRIP_PATH,
        usecols=[
            "SEM_EXM_EXCH_ID",
            "SEM_INSTRUMENT_NAME",
            "SEM_SMST_SECURITY_ID",
            "SEM_TRADING_SYMBOL",
            "SEM_LOT_UNITS",
            "SEM_EXPIRY_DATE",
            "SEM_STRIKE_PRICE",
            "SEM_OPTION_TYPE",
            "SEM_TICK_SIZE",
            "SEM_EXPIRY_FLAG",
        ],
        dtype={
            "SEM_SMST_SECURITY_ID": str,
            "SEM_TRADING_SYMBOL": str,
            "SEM_OPTION_TYPE": str,
            "SEM_EXPIRY_FLAG": str,
            "SEM_EXM_EXCH_ID": str,
            "SEM_INSTRUMENT_NAME": str,
        },
    )

    # Keep only NSE OPTIDX rows
    df = raw[
        (raw["SEM_EXM_EXCH_ID"] == "NSE")
        & (raw["SEM_INSTRUMENT_NAME"] == "OPTIDX")
    ].copy()

    # Parse expiry date (strip time component)
    df["expiry_date"] = pd.to_datetime(
        df["SEM_EXPIRY_DATE"], errors="coerce"
    ).dt.date

    df = df.dropna(subset=["expiry_date"])

    # Rename for convenience
    df = df.rename(
        columns={
            "SEM_SMST_SECURITY_ID": "security_id",
            "SEM_TRADING_SYMBOL":   "symbol",
            "SEM_OPTION_TYPE":      "opt_type",
            "SEM_TICK_SIZE":        "tick_size",
            "SEM_LOT_UNITS":        "lot_units",
            "SEM_STRIKE_PRICE":     "strike",
            "SEM_EXPIRY_FLAG":      "expiry_flag",
        }
    )
    df["strike"]    = df["strike"].astype(float)
    df["tick_size"] = df["tick_size"].astype(float)

    with _cache_lock:
        _df_cache = df

    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def nearest_atm_strike(underlying: Underlying, spot: float) -> float:
    """Round *spot* to the nearest valid strike on the grid for *underlying*.

    NIFTY grid: 50 points.  BANKNIFTY grid: 100 points.
    Uses standard rounding (half-up via Decimal if needed — but for grid
    multiples Python's round() suffices).
    """
    grid = _GRID[underlying]
    # Use integer arithmetic to avoid float rounding artefacts
    rounded = round(spot / grid) * grid
    return float(rounded)


def resolve_option(
    underlying: Underlying,
    strike: float,
    opt_type: OptType,
    on: date,
) -> FnoInstrument:
    """Resolve an option leg to a ``FnoInstrument`` with the real security_id.

    Parameters
    ----------
    underlying:
        "NIFTY" or "BANKNIFTY".
    strike:
        Strike price (must lie on the instrument grid — use nearest_atm_strike
        to snap a spot price first).
    opt_type:
        "CE" or "PE".
    on:
        The trading date.  Expiry selection is point-in-time: nearest expiry
        >= *on*.  NIFTY can return weekly (W) or monthly (M) expiries;
        BANKNIFTY returns monthly (M) only.

    Returns
    -------
    FnoInstrument
        With ``security_id`` from the scrip master, ``tick_size`` from the
        scrip master, and ``lot_size()`` from the existing dated schedule in
        ``algotrader.data.instruments``.

    Raises
    ------
    LookupError
        If no matching option is found in the scrip master.
    """
    df = _load_df()

    # Filter by underlying prefix: symbol starts with "<UNDERLYING>-"
    prefix = f"{underlying}-"
    mask = (
        df["symbol"].str.startswith(prefix)
        & (df["opt_type"] == opt_type)
    )

    # BANKNIFTY: monthly expiries only (SEBI abolished weeklies)
    if underlying == "BANKNIFTY":
        mask &= df["expiry_flag"] == "M"

    # Expiry >= on
    mask &= df["expiry_date"] >= on

    # Strike match (float comparison with a small tolerance < ½ grid step)
    # NIFTY grid = 50, tolerance = 1; BANKNIFTY grid = 100, tolerance = 1
    tol = 1.0
    mask &= (df["strike"] - strike).abs() < tol

    candidates = df[mask]

    if candidates.empty:
        # Provide a helpful error message
        raise LookupError(
            f"No {underlying} {opt_type} option found for strike={strike} "
            f"on or after {on} in the scrip master "
            f"({'monthly only' if underlying == 'BANKNIFTY' else 'weekly+monthly'}). "
            f"File: {_SCRIP_PATH}. "
            "Try refresh_scrip_master() if the file is stale."
        )

    # Select the nearest (earliest) expiry >= on
    nearest_expiry: date = candidates["expiry_date"].min()
    row = candidates[candidates["expiry_date"] == nearest_expiry].iloc[0]

    return FnoInstrument(
        symbol=str(row["symbol"]),
        security_id=str(row["security_id"]),
        segment=Segment.NSE_FNO,
        tick_size=float(row["tick_size"]),
        is_derivative=True,
        underlying=underlying,
        can_short_intraday=True,
        expiry_date=nearest_expiry,
    )
