"""Breadth Trend-Day Rider (Gen-2).

Hypothesis: when NIFTY-50 internals are one-sided early (≥thr of stocks above
their own session VWAP at decision time), the day tends to trend; ride the
aligned direction on the traded symbol if its own price agrees, trail with
ATR, square off at session end. Few trades by construction (regime is rare).

Breadth comes from a PRECOMPUTED causal dataset (data/cache/_BREADTH/5m/
breadth.parquet, built by scripts/build_breadth.py from the 50-stock 1-min
store — every row at ts uses only bars closing ≤ ts). The strategy looks up
the row at the current bar's ts_close: strictly causal, file-cached at init.

Engine note: runs per-symbol in the single-instrument engine; the breadth
gate is shared context, the symbol's own VWAP-side check personalises entry.
"""
from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Optional

from algotrader.core import (
    Bar, OrderIntent, Position, SessionContext, Side, Strategy,
)
from algotrader.strategies.indicators import atr, session_vwap

_BREADTH_PARQUET = (Path(__file__).resolve().parent.parent.parent
                    / "data" / "cache" / "_BREADTH" / "5m" / "breadth.parquet")


_BREADTH_CACHE: dict | None = None


def breadth_at(epoch_s: int) -> tuple[float, int, float] | None:
    """(pct_above_vwap, n_stocks, net_breadth) at the 5-min boundary, or None.
    Module-level cache shared by all strategies in a process."""
    global _BREADTH_CACHE
    if _BREADTH_CACHE is None:
        _BREADTH_CACHE = {}
        if _BREADTH_PARQUET.exists():
            import pandas as pd
            df = pd.read_parquet(_BREADTH_PARQUET)
            ts = pd.to_datetime(df["ts"])
            _BREADTH_CACHE = {
                int(t.timestamp()): (float(p), int(n), float(nb))
                for t, p, n, nb in zip(ts, df["pct_above_vwap"],
                                       df["n_stocks"], df["net_breadth"])
            }
    return _BREADTH_CACHE.get(epoch_s)


class BreadthRider(Strategy):
    strategy_id = "breadth_rider"
    warmup_bars = 0
    warmup_days = 5

    def __init__(
        self,
        breadth_thr: float = 0.80,
        decision_time: "time | str | None" = time(10, 15),
        window_end: "time | str | None" = None,
        fresh_cross: bool = True,
        stop_atr_mult: float = 1.5,
        trail_atr_mult: float = 2.5,
        min_stocks: int = 30,
        target_symbol: "str | None" = None,
        breadth_lookup=None,   # callable epoch_s -> (pct, n, net); None = static parquet
    ) -> None:
        """v2 (Gen-4): if window_end is set, decision_time becomes the START of
        a continuous entry window and entry triggers on the FIRST bar in
        [decision_time, window_end] whose breadth is extreme (optionally only
        on a FRESH crossing — previous bar not extreme). Single-snapshot v1
        behaviour (window_end=None) is preserved for reproducibility.

        target_symbol: if set, only bars whose instrument.symbol matches this
        string are processed.  Allows multiple BreadthRider instances in the
        same strategy list without cross-instrument signal pollution.
        """
        def _t(v):
            if isinstance(v, str):
                hh, mm = v.split(":")
                return time(int(hh), int(mm))
            return v
        decision_time = _t(decision_time)
        window_end = _t(window_end)
        self.breadth_thr = breadth_thr
        self.decision_time = decision_time
        self.window_end = window_end
        self.fresh_cross = fresh_cross
        self.stop_atr_mult = stop_atr_mult
        self.trail_atr_mult = trail_atr_mult
        self.min_stocks = min_stocks
        self._lookup = breadth_lookup or breadth_at
        self.target_symbol = target_symbol

        self._session_date: Optional[date] = None
        self._traded = False

    # ------------------------------------------------------------------


    def _reset(self, d: date) -> None:
        self._session_date = d
        self._traded = False

    # ------------------------------------------------------------------

    def on_bar(self, ctx: SessionContext, bar: Bar) -> list[OrderIntent]:
        # Filter by target instrument if configured
        if self.target_symbol is not None and bar.instrument.symbol != self.target_symbol:
            return []
        d = bar.ts_open.date()
        if d != self._session_date:
            self._reset(d)
        if self._traded or not ctx.clock.can_enter:
            return []
        now_t = bar.ts_close.timetz().replace(tzinfo=None)
        if self.window_end is None:
            # v1: act exactly at the single decision bar
            if now_t != self.decision_time:
                return []
        else:
            if not (self.decision_time <= now_t <= self.window_end):
                return []

        row = self._lookup(int(bar.ts_close.timestamp()))
        if row is None:
            return []
        pct_above, n_stocks, _net = row
        if n_stocks < self.min_stocks:
            return []

        if pct_above >= self.breadth_thr:
            side = Side.BUY
        elif pct_above <= 1.0 - self.breadth_thr:
            side = Side.SELL
        else:
            return []

        if self.window_end is not None and self.fresh_cross:
            # fresh crossing: previous 5-min boundary must NOT be extreme
            prev = self._lookup(int(bar.ts_close.timestamp()) - 300)
            if prev is not None:
                p_prev = prev[0]
                if p_prev >= self.breadth_thr or p_prev <= 1.0 - self.breadth_thr:
                    return []

        instrument = bar.instrument
        today = list(ctx.bars(instrument, 400))
        # own-symbol agreement: price on the same side of its session VWAP
        vw = session_vwap(today)
        if not vw:
            return []
        own_above = bar.close > vw[-1]
        if (side is Side.BUY and not own_above) or (side is Side.SELL and own_above):
            return []

        # entry ATR: period 10 — at the 10:15 decision only ~12 five-min bars
        # exist, so ATR(14) was structurally undefined (zero-trades bug)
        series = atr(today, period=10) if len(today) >= 10 else []
        a = series[-1] if series else 0.0
        if not a or a != a or a <= 0:
            return []

        sgn = 1.0 if side is Side.BUY else -1.0
        stop_px = bar.close - sgn * self.stop_atr_mult * a
        if (side is Side.BUY and stop_px >= bar.close) or \
           (side is Side.SELL and stop_px <= bar.close):
            return []

        self._traded = True
        return [OrderIntent(
            strategy_id=self.strategy_id, instrument=instrument, side=side,
            ref_price=bar.close, stop_price=stop_px, target_price=None,
            trail_atr_mult=self.trail_atr_mult,
            reason=f"breadth={pct_above:.2f} n={n_stocks} own_vwap_side={'+' if own_above else '-'}",
        )]

    # ------------------------------------------------------------------

    def manage(self, ctx: SessionContext, bar: Bar,
               position: Position) -> tuple[float | None, bool]:
        """ATR trail from entry; never loosens. Voluntary flat from 15:15.

        Also enforces target_symbol filter so a multi-instrument PaperExecutor
        does not apply manage() to positions belonging to another strategy instance.
        """
        if self.target_symbol is not None and bar.instrument.symbol != self.target_symbol:
            return None, False
        if bar.instrument.symbol != position.instrument.symbol:
            return None, False
        now_t = bar.ts_close.timetz().replace(tzinfo=None)
        if now_t >= ctx.clock.voluntary_exit_from:
            return None, True

        today = list(ctx.bars(position.instrument, 400))
        series = atr(today, period=14) if len(today) >= 14 else []
        a = series[-1] if series else 0.0
        if not a or a != a or a <= 0:
            return None, False
        sgn = 1.0 if position.side is Side.BUY else -1.0
        trail = bar.close - sgn * self.trail_atr_mult * a
        if (position.side is Side.BUY and trail > position.stop_price) or \
           (position.side is Side.SELL and trail < position.stop_price):
            return trail, False
        return None, False
