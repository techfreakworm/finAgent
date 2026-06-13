"""Live incremental breadth computation for the NIFTY-50 equity universe.

Consumes 1-minute bars via .on_bar(symbol, bar) and maintains per-symbol
cumulative session VWAP.  At each 5-minute decision boundary (09:20 … 15:25 IST)
it computes the breadth snapshot using the SAME formulas as
``scripts/build_breadth.py`` — cited below — with strict causal semantics:
only bars whose ts_open is strictly before the boundary minute are used.

Formula provenance (scripts/build_breadth.py → compute_breadth):
  vwap           = cumsum(close*vol) / cumsum(vol)        (per symbol, per session)
  above_vwap     = last_close >= vwap                     (>= ; line 179)
  advance        = last_close > first_open                (>  ; line 180)
  n_stocks       = count of symbols with ≥1 bar before D
  pct_above_vwap = sum(above_vwap) / n_stocks            (line 199)
  adv_frac       = sum(advance)    / n_stocks            (line 200)
  net_breadth    = 2 * adv_frac − 1                      (line 201/207)

Causality: decision at minute D uses bars with t_min < D (bar-START stamped),
matching build_breadth.py's searchsorted side='left' idiom (line 163–164).

.at(epoch_s) semantics match algotrader/strategies/breadth_rider.breadth_at:
  returns (pct_above_vwap, n_stocks, net_breadth) or None.

Design contract:
- No import of live_feed: the runner wires the two together.
- Bars must be tz-aware IST; naive datetimes are rejected (core.require_ist).
- Internal state resets automatically when the session date changes.
- seed_from_history(date) backfills from the parquet store for mid-session startup.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from algotrader.core import Bar, IST, require_ist

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (mirror scripts/build_breadth.py)
# ---------------------------------------------------------------------------

# Session start: 09:15 — first valid 1-min bar start (minutes since midnight)
_SESSION_START_MIN: int = 9 * 60 + 15  # 555

# Decision boundaries: 09:20, 09:25, …, 15:25  (74 values)
# Derived from: np.arange(9*60+20, 15*60+26, 5) in build_breadth.py
_DECISION_MINS: tuple[int, ...] = tuple(range(9 * 60 + 20, 15 * 60 + 26, 5))
_DECISION_SET: frozenset[int] = frozenset(_DECISION_MINS)

# Path to parquet cache (mirrors build_breadth.py's CACHE layout)
_PROJECT = Path(__file__).resolve().parent.parent.parent
_CACHE = _PROJECT / "data" / "cache"


# ---------------------------------------------------------------------------
# LiveBreadth
# ---------------------------------------------------------------------------

class LiveBreadth:
    """Incremental market-breadth aggregator for the NIFTY-50 equity universe.

    Each instance tracks a single trading session.  The session resets
    automatically whenever a bar from a new calendar date is received.

    Args:
        symbols: The complete list of equity symbols to track (order irrelevant).
                 Symbols not in this list are silently ignored by on_bar.
    """

    def __init__(self, symbols: list[str]) -> None:
        self._symbols: list[str] = list(symbols)
        self._symbol_set: frozenset[str] = frozenset(symbols)

        # --- mutable session state (reset on new date) ---
        self._session_date: Optional[date] = None

        # Cumulative VWAP numerator: sum(close * volume) per symbol
        self._cv_cum: dict[str, float] = {}
        # Cumulative volume denominator: sum(volume) per symbol
        self._v_cum: dict[str, float] = {}
        # First bar's open for the session (used for advance computation)
        self._first_open: dict[str, float] = {}
        # Most-recent close seen this session (used at each decision boundary)
        self._last_close: dict[str, float] = {}

        # Pre-computed snapshots: epoch_s → (pct_above_vwap, n_stocks, net_breadth)
        self._snapshots: dict[int, tuple[float, int, float]] = {}
        # Which decision t_mins have already been computed
        self._computed_decisions: set[int] = set()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def on_bar(self, symbol: str, bar: Bar) -> None:
        """Ingest a completed 1-minute bar for *symbol*.

        Called by the runner for each bar emitted by live_feed (or by the
        replay driver in tests).  Bars must be complete (Bar.complete=True)
        and carry tz-aware ts_open.

        Decision snapshots are computed lazily the first time a bar whose
        ts_open falls on a decision boundary arrives — BEFORE that bar's
        data is added to the cumulative state, preserving causality.
        """
        if symbol not in self._symbol_set:
            return

        ts_ist = require_ist(bar.ts_open)
        bar_date = ts_ist.date()

        # --- session boundary: reset on new date ---
        if self._session_date is None or bar_date != self._session_date:
            self._reset_session(bar_date)

        t_min = ts_ist.hour * 60 + ts_ist.minute

        # Drop pre-session bars (defensive; mirrors build_breadth.py line 130-131)
        if t_min < _SESSION_START_MIN:
            return

        # Trigger pending decision computations BEFORE updating state.
        # When this bar's ts_open lands on a decision boundary D, all cumulative
        # state currently reflects bars with ts_open < D (strict causality).
        # We also catch any skipped boundaries (if no symbol had a bar exactly
        # at an earlier boundary).
        if t_min in _DECISION_SET:
            for d in _DECISION_MINS:
                if d > t_min:
                    break
                if d not in self._computed_decisions:
                    self._compute_snapshot(d)

        # Update cumulative state with this bar
        self._ingest_raw(symbol, bar.open, bar.close, bar.volume)

    def at(self, epoch_s: int) -> tuple[float, int, float] | None:
        """Return the breadth snapshot at *epoch_s* (Unix seconds, int).

        Matches the return type and semantics of
        ``algotrader.strategies.breadth_rider.breadth_at``:
            (pct_above_vwap, n_stocks, net_breadth)  or  None if no data.
        """
        return self._snapshots.get(epoch_s)

    def seed_from_history(
        self,
        session_date: date,
        until: Optional[datetime] = None,
    ) -> None:
        """Backfill state from the parquet store for *session_date*.

        Loads 1-min bars from ``data/cache/<SYMBOL>/1m/YYYY-MM.parquet`` for
        every symbol in the universe, filters to rows whose ts_open is on
        *session_date* and strictly before *until* (defaults to wall-clock
        ``datetime.now(IST)``), then feeds them through the same ingestion
        pipeline in ts_open order.

        Call this once at startup when the process starts mid-session; the
        state will reflect the partial day already traded.  A subsequent
        on_bar call from the live feed picks up seamlessly.

        Args:
            session_date: The trading date to backfill (usually today).
            until:        Exclusive cutoff for bar ts_open (tz-aware IST).
                          Defaults to current wall-clock time.  Pass a fixed
                          datetime in tests to make loading deterministic.
        """
        import pandas as pd  # deferred — not every caller needs pandas

        cutoff = until if until is not None else datetime.now(tz=IST)
        require_ist(cutoff)

        ym = session_date.strftime("%Y-%m")
        frames: list[pd.DataFrame] = []

        for sym in self._symbols:
            parquet_path = _CACHE / sym / "1m" / f"{ym}.parquet"
            if not parquet_path.exists():
                continue
            try:
                df = pd.read_parquet(parquet_path, columns=["ts", "open", "close", "volume"])
            except Exception:  # noqa: BLE001
                log.warning("seed_from_history: could not read %s", parquet_path)
                continue

            # Ensure IST-aware timestamps (mirrors build_breadth.py lines 86-89)
            if df["ts"].dt.tz is None:
                df["ts"] = df["ts"].dt.tz_localize(IST)
            else:
                df["ts"] = df["ts"].dt.tz_convert(IST)

            # Filter to this session date only, strictly before cutoff
            mask = (df["ts"].dt.date == session_date) & (df["ts"] < cutoff)
            df = df.loc[mask].copy()
            if df.empty:
                continue

            df["symbol"] = sym
            frames.append(df)

        if not frames:
            log.debug("seed_from_history: no data found for %s", session_date)
            return

        big = pd.concat(frames, ignore_index=True)
        # Sort by ts_open (then symbol for deterministic tie-breaking)
        big = big.sort_values(["ts", "symbol"]).reset_index(drop=True)

        # Feed through the same ingestion pipeline as on_bar
        for row in big.itertuples(index=False):
            ts_ist: datetime = row.ts
            bar_date = ts_ist.date()

            if self._session_date is None or bar_date != self._session_date:
                self._reset_session(bar_date)

            t_min = ts_ist.hour * 60 + ts_ist.minute
            if t_min < _SESSION_START_MIN:
                continue

            sym: str = row.symbol
            if sym not in self._symbol_set:
                continue

            # Trigger pending decisions BEFORE updating state
            if t_min in _DECISION_SET:
                for d in _DECISION_MINS:
                    if d > t_min:
                        break
                    if d not in self._computed_decisions:
                        self._compute_snapshot(d)

            self._ingest_raw(sym, row.open, row.close, row.volume)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_session(self, new_date: date) -> None:
        """Clear all per-session state and start fresh for *new_date*."""
        self._session_date = new_date
        self._cv_cum.clear()
        self._v_cum.clear()
        self._first_open.clear()
        self._last_close.clear()
        self._snapshots.clear()
        self._computed_decisions.clear()
        log.debug("LiveBreadth: session reset to %s", new_date)

    def _ingest_raw(
        self,
        symbol: str,
        open_px: float,
        close_px: float,
        volume: int | float,
    ) -> None:
        """Update cumulative state for *symbol* with one 1-min bar's data."""
        if symbol not in self._first_open:
            self._first_open[symbol] = open_px

        self._cv_cum[symbol] = self._cv_cum.get(symbol, 0.0) + close_px * float(volume)
        self._v_cum[symbol] = self._v_cum.get(symbol, 0.0) + float(volume)
        self._last_close[symbol] = close_px

    def _compute_snapshot(self, decision_t_min: int) -> None:
        """Compute and store the breadth snapshot at *decision_t_min*.

        Uses the CURRENT cumulative state — which, because this method is
        always called BEFORE the triggering bar is ingested, represents only
        bars with ts_open strictly before the decision boundary.

        Formula (verbatim from scripts/build_breadth.py compute_breadth):
          pct_above_vwap = sum(last_close >= vwap) / n_stocks   [line 199]
          adv_frac       = sum(last_close > first_open) / n_stocks [line 200]
          net_breadth    = 2 * adv_frac − 1                     [line 207]
        """
        if self._session_date is None:
            return

        n: int = 0
        above_vwap_count: int = 0
        advance_count: int = 0

        for sym in self._symbols:
            last_close = self._last_close.get(sym)
            if last_close is None:
                # Symbol has no bar before this decision boundary — skip entirely
                continue

            n += 1
            v_cum = self._v_cum.get(sym, 0.0)
            cv_cum = self._cv_cum.get(sym, 0.0)
            first_open = self._first_open.get(sym)

            # VWAP: mirrors build_breadth.py line 176 (np.where vv>0 else nan)
            # If volume is zero, vwap is undefined (nan) → treated as not above_vwap
            if v_cum > 0:
                vwap = cv_cum / v_cum
                if last_close >= vwap:      # >= : mirrors line 179
                    above_vwap_count += 1
            # else: vwap = nan → not above_vwap (count unchanged)

            # Advance: last_close > first_open (strict > mirrors line 180)
            if first_open is not None and last_close > first_open:
                advance_count += 1

        if n == 0:
            self._computed_decisions.add(decision_t_min)
            return

        pct_above_vwap: float = above_vwap_count / n
        adv_frac: float = advance_count / n
        net_breadth: float = 2.0 * adv_frac - 1.0

        # Build epoch-second key matching breadth_rider.breadth_at's cache format
        d = self._session_date
        dt = datetime(
            d.year, d.month, d.day,
            decision_t_min // 60, decision_t_min % 60,
            tzinfo=IST,
        )
        epoch_s = int(dt.timestamp())

        self._snapshots[epoch_s] = (pct_above_vwap, n, net_breadth)
        # Ops visibility (2026-06-12 lesson): every boundary snapshot is logged
        # so a blind/starved breadth feed is visible in the runner log.
        from datetime import datetime as _dt, timezone as _tz
        log.info("breadth %s: pct_above_vwap=%.3f n=%d net=%.3f",
                 _dt.fromtimestamp(epoch_s, tz=IST).strftime("%H:%M"),
                 pct_above_vwap, n, net_breadth)
        self._computed_decisions.add(decision_t_min)
        log.debug(
            "LiveBreadth [%s %02d:%02d] pct_vwap=%.4f adv=%.4f net=%.4f n=%d",
            d,
            decision_t_min // 60,
            decision_t_min % 60,
            pct_above_vwap,
            adv_frac,
            net_breadth,
            n,
        )
