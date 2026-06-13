"""Breadth Rider — options expression for the OPTION-C paper comparison.

Wraps BreadthRider so that when the frozen breadth signal fires for NIFTY
(BANKNIFTY is skipped — monthly options only, premia too large for 0.75%):

1. Resolve the current-week NIFTY ATM option via option_resolver:
   - CE for a LONG (bullish) signal
   - PE for a SHORT (bearish) signal
2. Request dynamic subscription of that contract via subscribe_cb.
3. Hold a pending-signal state: emit the OrderIntent on the FIRST option bar
   received (ref_price = first bar close, fill at NEXT option bar open per
   engine semantics).
4. stop_price = 55% of ref_price (i.e. −45% premium stop); target = None.
5. Square-off via manage() at ts_close ≥ 15:10 (tighter than the futures
   voluntary_exit_from=15:15 to avoid wide bid-ask at expiry close).

Sizing: the executor's IntradayRiskEngine uses the standard
    qty = floor(risk_budget / risk_per_lot)
    risk_per_lot = |ref_price − stop_price| × lot_size
                 = 0.45 × ref_price × lot_size
With budget = 0.75% × ₹5 L = ₹3750 and NIFTY lot = 65:
    max_premium_for_1lot = 3750 / (0.45 × 65) ≈ 128
Typical ATM weekly premium 100–180 → 1 lot usually fits; else rejected
(risk engine returns Rejection; the store logs "rejection" events so the
comparison tracks both fill rate and P&L).

No MinLotOverride shim for options: a real rejection means the premium is
too expensive for the 0.75% budget and should not be forced.

Paper/data only: no order-placement methods are imported or called.
"""
from __future__ import annotations

import logging
from datetime import date, time
from typing import Callable, Optional

from algotrader.core import (
    Bar,
    OrderIntent,
    Position,
    SessionContext,
    Side,
)
from algotrader.strategies.breadth_rider import BreadthRider
from algotrader.strategies.indicators import session_vwap, atr

log = logging.getLogger(__name__)

# Import lazily in resolve method to allow test injection of stubs.
_OPTION_RESOLVER_MOD = None


def _default_resolve_option(underlying, strike, opt_type, on):
    """Thin wrapper around option_resolver.resolve_option (default production path)."""
    from algotrader.data.option_resolver import resolve_option as _ro
    return _ro(underlying, strike, opt_type, on)


def _default_nearest_atm(underlying, spot):
    """Thin wrapper around option_resolver.nearest_atm_strike."""
    from algotrader.data.option_resolver import nearest_atm_strike as _na
    return _na(underlying, spot)


# Voluntary exit time for options (tighter than futures 15:15 to avoid
# wide bid-ask at weekly expiry close).
_OPT_VOLUNTARY_EXIT = time(15, 10)


class BreadthRiderOptions(BreadthRider):
    """Options expression of the frozen BreadthRider signal.

    Inherits all breadth-signal logic from BreadthRider but:
    - Only trades NIFTY (target_symbol hard-wired to "NIFTY-FUT").
    - Converts the futures signal to an ATM weekly options entry.
    - Defers OrderIntent until the first option bar arrives (pending state).
    - Uses a fixed premium stop (55% of entry premium, i.e. −45% stop).
    - Exits via manage() at ≥ 15:10 (no ATR trail for options).

    Parameters
    ----------
    All BreadthRider kwargs are forwarded unchanged.  Extra parameters:

    subscribe_cb:
        Callable[[list[tuple[str, str, Instrument]]], None] that adds a new
        instrument to the live feed.  Signature matches
        LiveBarFeed.subscribe_dynamic.  Pass None in replay mode (no feed);
        the strategy handles None gracefully (no subscription sent, pending
        state is still set so the test can inject option bars directly).
    option_resolver:
        Optional override for the resolve_option call — used in tests to
        inject a stub that avoids loading the scrip master CSV.
        Signature: (underlying, strike, opt_type, on) -> FnoInstrument.
    nearest_atm:
        Optional override for nearest_atm_strike — same motivation.
        Signature: (underlying, spot) -> float.
    """

    strategy_id = "breadth_rider_options"
    warmup_bars = 0
    warmup_days = 5

    # The underlying target symbol (NIFTY futures bar that carries the signal)
    _NIFTY_FUT_SYMBOL = "NIFTY-FUT"

    def __init__(
        self,
        breadth_thr: float = 0.80,
        decision_time: "time | str | None" = time(10, 15),
        window_end: "time | str | None" = None,
        fresh_cross: bool = True,
        stop_atr_mult: float = 1.5,       # inherited but NOT used (options use premium stop)
        trail_atr_mult: float = 2.5,       # inherited but NOT used (no trailing for options)
        min_stocks: int = 30,
        breadth_lookup=None,
        subscribe_cb: "Callable | None" = None,
        option_resolver: "Callable | None" = None,
        nearest_atm: "Callable | None" = None,
    ) -> None:
        # Hard-wire target_symbol to NIFTY-FUT — BANKNIFTY options are monthly
        # only and premia are too large for the 0.75% budget.
        super().__init__(
            breadth_thr=breadth_thr,
            decision_time=decision_time,
            window_end=window_end,
            fresh_cross=fresh_cross,
            stop_atr_mult=stop_atr_mult,
            trail_atr_mult=trail_atr_mult,
            min_stocks=min_stocks,
            target_symbol=self._NIFTY_FUT_SYMBOL,
            breadth_lookup=breadth_lookup,
        )
        self.subscribe_cb = subscribe_cb
        self._resolve_option = option_resolver or _default_resolve_option
        self._nearest_atm = nearest_atm or _default_nearest_atm

        # Pending state — set when the breadth signal fires, cleared when the
        # first option bar arrives and the intent is emitted.
        self._pending_option_instr: Optional[object] = None   # FnoInstrument | None
        self._pending_opt_type: Optional[str] = None           # "CE" | "PE" | None

    # ------------------------------------------------------------------
    # Session reset
    # ------------------------------------------------------------------

    def _reset(self, d: date) -> None:
        super()._reset(d)
        self._pending_option_instr = None
        self._pending_opt_type = None

    # ------------------------------------------------------------------
    # on_bar — two-phase logic
    # ------------------------------------------------------------------

    def on_bar(self, ctx: SessionContext, bar: Bar) -> list[OrderIntent]:
        """Two-phase logic:

        Phase A — option bar arrives while pending:
            Emit OrderIntent with ref_price = bar.close and
            stop_price = 0.55 × ref_price.  Executor fills at NEXT option
            bar open (standard next-bar semantics).

        Phase B — NIFTY-FUT bar at decision time:
            Check breadth signal (identical to BreadthRider).  On signal:
            resolve the ATM option, call subscribe_cb, set pending state.
            Return [] — no intent yet (option price unknown until Phase A).
        """
        d = bar.ts_open.date()
        if d != self._session_date:
            self._reset(d)

        # ── Phase A: first option bar received ──────────────────────────
        if (
            self._pending_option_instr is not None
            and bar.instrument.symbol == self._pending_option_instr.symbol
        ):
            if not ctx.clock.can_enter:
                # Too late to enter — discard pending intent (count as missed)
                log.info(
                    "breadth_rider_options: discarding pending %s — entry window closed",
                    self._pending_option_instr.symbol,
                )
                self._pending_option_instr = None
                self._pending_opt_type = None
                self._traded = True
                return []

            ref = bar.close
            if ref <= 0:
                log.warning("breadth_rider_options: option bar.close=%s <= 0; skip", ref)
                self._pending_option_instr = None
                self._traded = True
                return []

            stop = ref * 0.55      # −45% of premium; BUY so stop < ref → valid
            instr = self._pending_option_instr
            self._pending_option_instr = None
            self._pending_opt_type = None
            self._traded = True

            return [OrderIntent(
                strategy_id=self.strategy_id,
                instrument=instr,
                side=Side.BUY,          # always BUY (CE or PE depending on signal)
                ref_price=ref,
                stop_price=stop,
                target_price=None,
                trail_atr_mult=None,    # no trail for options
                reason=f"options_entry ref={ref:.2f} stop={stop:.2f} (-45% premium stop)",
            )]

        # ── Phase B: check breadth signal on NIFTY-FUT bars ─────────────
        # Skip if already traded or can't enter
        if self._traded or not ctx.clock.can_enter:
            return []

        # Only process NIFTY-FUT bars (target_symbol filter)
        if bar.instrument.symbol != self._NIFTY_FUT_SYMBOL:
            return []

        # Decision time check (identical to BreadthRider)
        now_t = bar.ts_close.timetz().replace(tzinfo=None)
        if self.window_end is None:
            if now_t != self.decision_time:
                return []
        else:
            if not (self.decision_time <= now_t <= self.window_end):
                return []

        # Breadth gate
        row = self._lookup(int(bar.ts_close.timestamp()))
        if row is None:
            return []
        pct_above, n_stocks, _net = row
        if n_stocks < self.min_stocks:
            return []

        if pct_above >= self.breadth_thr:
            side = Side.BUY
            opt_type = "CE"
        elif pct_above <= 1.0 - self.breadth_thr:
            side = Side.SELL    # direction of underlying — we BUY a PE
            opt_type = "PE"
        else:
            return []

        # Fresh-cross filter (window mode)
        if self.window_end is not None and self.fresh_cross:
            prev = self._lookup(int(bar.ts_close.timestamp()) - 300)
            if prev is not None:
                p_prev = prev[0]
                if p_prev >= self.breadth_thr or p_prev <= 1.0 - self.breadth_thr:
                    return []

        # Own-symbol VWAP agreement (on the underlying NIFTY-FUT bars)
        today = list(ctx.bars(bar.instrument, 400))
        vw = session_vwap(today)
        if not vw:
            return []
        own_above = bar.close > vw[-1]
        if (side is Side.BUY and not own_above) or (side is Side.SELL and own_above):
            return []

        # ATR check (need at least 10 bars)
        series = atr(today, period=10) if len(today) >= 10 else []
        a = series[-1] if series else 0.0
        if not a or a != a or a <= 0:
            return []

        # Resolve ATM option instrument
        try:
            strike = self._nearest_atm("NIFTY", bar.close)
            option_instr = self._resolve_option("NIFTY", strike, opt_type, d)
        except LookupError as exc:
            log.warning("breadth_rider_options: option lookup failed: %s", exc)
            return []
        except Exception:
            log.exception("breadth_rider_options: option_resolver raised unexpectedly")
            return []

        # Request dynamic subscription so the feed delivers option bars
        if self.subscribe_cb is not None:
            try:
                self.subscribe_cb(
                    [(option_instr.security_id, "NSE_FNO", option_instr)]
                )
                log.info(
                    "breadth_rider_options: subscribed %s (breadth=%.2f opt_type=%s)",
                    option_instr.symbol, pct_above, opt_type,
                )
            except Exception:
                log.exception("breadth_rider_options: subscribe_cb raised")

        # Set pending state — intent emitted on first option bar (Phase A)
        self._pending_option_instr = option_instr
        self._pending_opt_type = opt_type
        # Do NOT set _traded here — we wait for the option bar

        log.info(
            "breadth_rider_options: signal %s breadth=%.2f -> pending %s %s strike=%.0f",
            side.value, pct_above, opt_type, option_instr.symbol, strike,
        )
        return []   # intent deferred until option bar arrives

    # ------------------------------------------------------------------
    # manage — time-based exit only (no ATR trail for options)
    # ------------------------------------------------------------------

    def manage(
        self, ctx: SessionContext, bar: Bar, position: "Position"
    ) -> "tuple[float | None, bool]":
        """Exit at or after 15:10 IST (tighter than futures 15:15).

        Only acts on bars for the option instrument that the position is in.
        Returns (None, True) to trigger a voluntary exit when ts_close ≥ 15:10.
        """
        # Guard: only manage on the exact instrument bar of the open position
        if bar.instrument.symbol != position.instrument.symbol:
            return None, False

        now_t = bar.ts_close.timetz().replace(tzinfo=None)
        if now_t >= _OPT_VOLUNTARY_EXIT:
            log.debug(
                "breadth_rider_options: voluntary exit %s at %s",
                position.instrument.symbol, now_t,
            )
            return None, True

        return None, False
