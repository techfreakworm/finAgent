"""Parquet-backed BarSource over the backfilled store + sweep universe.

Index-as-futures caveat (ARCHITECTURE §2): expired futures are not addressable
on Dhan's API, so index-futures strategies are researched on INDEX bars with
the FUTURES cost/lot model (NIFTY_FUT/BANKNIFTY_FUT instruments). This ignores
basis drift within the day — acceptable for signal research, re-validated on
real futures bars accumulating forward from 2026-04.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Iterator, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from algotrader.core import Bar, Instrument, Segment
from algotrader.data.instruments import BANKNIFTY_FUT, NIFTY_FUT

IST = ZoneInfo("Asia/Kolkata")
PROJECT = Path(__file__).resolve().parent.parent.parent
CACHE = PROJECT / "data" / "cache"

_NIFTY50_MAP_PATH = "/home/ubuntu/projects/algo-trader/data/reference/nifty50_secid_map.json"

# Gen-1 sweep universe: 2 indices (futures cost model) + 10 most-liquid names
SWEEP_EQUITIES = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
                  "SBIN", "AXISBANK", "KOTAKBANK", "LT", "TATAMOTORS"]


def equity_instrument(symbol: str) -> Instrument:
    sid = str(json.load(open(_NIFTY50_MAP_PATH)).get(symbol, "?"))
    return Instrument(symbol=symbol, security_id=sid, segment=Segment.NSE_EQ,
                      tick_size=0.05)


def all_equities() -> list[str]:
    """All NIFTY-50 cash symbols in the validated ID map (excl. non-equities)."""
    m = json.load(open(_NIFTY50_MAP_PATH))
    return [k for k in m if k not in ("NIFTY_50", "INDIA_VIX", "NIFTYBEES")]


def universe_for(name: str) -> list[tuple[Instrument, str]]:
    """Named universes for grid specs: sweep12 | indices | all50."""
    if name == "indices":
        return [(NIFTY_FUT, "NIFTY"), (BANKNIFTY_FUT, "BANKNIFTY")]
    if name == "all50":
        return [(equity_instrument(s), s) for s in all_equities()]
    return sweep_universe()


def sweep_universe() -> list[tuple[Instrument, str]]:
    """(instrument_for_costs, data_symbol) pairs. For indices the data symbol
    differs from the cost instrument (index bars, futures costs)."""
    pairs: list[tuple[Instrument, str]] = [
        (NIFTY_FUT, "NIFTY"),
        (BANKNIFTY_FUT, "BANKNIFTY"),
    ]
    pairs += [(equity_instrument(s), s) for s in SWEEP_EQUITIES]
    return pairs


class ParquetBarSource:
    """core.BarSource over the parquet store."""

    def __init__(self, instrument: Instrument, frm: date, to: date,
                 interval: str = "5m", data_symbol: str | None = None):
        self.instrument = instrument
        sym = data_symbol or instrument.symbol
        files = sorted((CACHE / sym / interval).glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"no {interval} data for {sym}")
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
        d = df["ts"].dt.date
        self.df = df[(d >= frm) & (d <= to)].sort_values("ts").reset_index(drop=True)
        self.interval_min = {"1m": 1, "5m": 5}[interval]

    def sessions(self) -> Iterator[tuple[date, Sequence[Bar]]]:
        for day, chunk in self.df.groupby(self.df["ts"].dt.date):
            yield day, [
                Bar(instrument=self.instrument, ts_open=row.ts.to_pydatetime(),
                    interval_min=self.interval_min, open=row.open, high=row.high,
                    low=row.low, close=row.close, volume=int(row.volume))
                for row in chunk.itertuples()
            ]
