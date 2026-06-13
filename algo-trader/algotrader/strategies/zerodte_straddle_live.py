"""0DTE expiry-day ATM short straddle — live paper strategy (ARCHITECTURE §6).

Replicates the validated scripts/zerodte_straddle.py backtest logic exactly:
  - Entry 09:20: sell 1 lot ATM CE + 1 lot ATM PE (current-week NIFTY weekly).
  - Stop: exit BOTH legs when combined premium >= 1.25 × entry combined.
  - Flat: 15:10 IST voluntary square-off.
  - NIFTY only; one straddle per expiry day.

Expiry-day self-gate: on the first bar of the session, resolve the current-week
NIFTY ATM option.  If its expiry_date == today, it is an expiry day and the
strategy arms itself; otherwise it emits nothing all session (graceful no-op).

Sizing: the standard risk engine will reject the SELL intents (huge
catastrophic stop-distance → 0 lots).  The ZerodteFixedLotEngine shim in
algotrader/paper/executor.py overrides sizing for this strategy's SELL intents
to exactly 1 lot, subject to a 1.2%-of-capital implied-risk cap.

Paper/data only: no order-placement methods are imported or called.
"""
from __future__ import annotations

import logging
from datetime import date, time
from typing import Callable, Optional

from algotrader.core import (
    Bar,
    IST,
    OrderIntent,
    Position,
    SessionContext,
    Side,
    Strategy,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default resolver imports (lazy to allow test injection)
# ---------------------------------------------------------------------------

def _default_resolve_option(underlying, strike, opt_type, on):
    from algotrader.data.option_resolver import resolve_option as _ro
    return _ro(underlying, strike, opt_type, on)


def _default_nearest_atm(underlying, spot):
    from algotrader.data.option_resolver import nearest_atm_strike as _na
    return _na(underlying, spot)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENTRY_TIME    = time(9, 20)         # ts_close of the entry trigger bar
_FLAT_TIME     = time(15, 10)        # voluntary square-off
_STOP_MULT     = 1.25                # basket stop: exit if combined >= 1.25 × entry
_CATAS_MULT    = 3.0                 # catastrophic per-leg stop (must be > ref for SELL)
_NIFTY_FUT_SYM = "NIFTY-FUT"        # underlying bar symbol to watch for entry signal


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class ZerodteStraddleLive(Strategy):
    """Live 0DTE ATM short straddle on NIFTY weekly expiry days.

    Parameters
    ----------
    subscribe_cb:
        Callable[[list[tuple[str, str, Instrument]]], None] wired to
        LiveBarFeed.subscribe_dynamic in live mode.  Pass None in replay mode;
        the strategy arms pending state regardless so test can inject option
        bars directly.
    option_resolver:
        Override for resolve_option (testing stub).
        Signature: (underlying, strike, opt_type, on) → FnoInstrument.
    nearest_atm:
        Override for nearest_atm_strike (testing stub).
        Signature: (underlying, spot) → float.
    """

    strategy_id = "zerodte_straddle"
    warmup_bars  = 0
    warmup_days  = 0

    def __init__(
        self,
        subscribe_cb: "Callable | None" = None,
        option_resolver: "Callable | None" = None,
        nearest_atm: "Callable | None" = None,
    ) -> None:
        self.subscribe_cb     = subscribe_cb
        self._resolve_option  = option_resolver or _default_resolve_option
        self._nearest_atm     = nearest_atm or _default_nearest_atm

        # Session-level state — reset on each new calendar day.
        self._session_date:    date | None = None
        self._is_expiry_day:   bool | None = None   # None = not yet determined
        self._entry_triggered: bool = False          # True after 09:20 subscribe_cb
        self._traded:          bool = False          # True once intents are emitted

        # Pending instruments (set after subscribe_cb)
        self._pending_ce: Optional[object] = None   # FnoInstrument | None
        self._pending_pe: Optional[object] = None

        # Confirmed leg instruments (after first bar arrives)
        self._ce_instr: Optional[object] = None
        self._pe_instr: Optional[object] = None

        # Entry premiums (recorded on first option bar for each leg)
        self._entry_ce: float | None = None
        self._entry_pe: float | None = None

        # Latest known premiums for basket-stop tracking
        self._ce_last: float | None = None
        self._pe_last: float | None = None

        # Latch: True once combined premium >= 1.25 × entry → exit both legs
        self._basket_exit: bool = False

    # ------------------------------------------------------------------
    # Session reset
    # ------------------------------------------------------------------

    def _reset(self, d: date) -> None:
        """Clear all per-session state for a new trading day."""
        self._session_date    = d
        self._is_expiry_day   = None
        self._entry_triggered = False
        self._traded          = False
        self._pending_ce      = None
        self._pending_pe      = None
        self._ce_instr        = None
        self._pe_instr        = None
        self._entry_ce        = None
        self._entry_pe        = None
        self._ce_last         = None
        self._pe_last         = None
        self._basket_exit     = False

    # ------------------------------------------------------------------
    # Expiry-day gate
    # ------------------------------------------------------------------

    def _determine_expiry_day(self, session_date: date) -> bool:
        """True if session_date is a NIFTY weekly expiry day.

        Resolves an arbitrary NIFTY ATM option for today; if the nearest
        expiry returned equals session_date, today is an expiry day.
        Uses a dummy strike (22000) — only the expiry_date field matters.
        """
        try:
            opt = self._resolve_option("NIFTY", 22000.0, "CE", session_date)
            exp = getattr(opt, "expiry_date", None)
            if exp is None:
                log.warning("zerodte_straddle: resolver returned no expiry_date; assuming non-expiry")
                return False
            is_exp = (exp == session_date)
            log.info(
                "zerodte_straddle: session=%s nearest_expiry=%s is_expiry_day=%s",
                session_date, exp, is_exp,
            )
            return is_exp
        except LookupError:
            log.warning("zerodte_straddle: option lookup failed for %s — treating as non-expiry", session_date)
            return False
        except Exception:
            log.exception("zerodte_straddle: _determine_expiry_day raised unexpectedly")
            return False

    # ------------------------------------------------------------------
    # on_bar — three phases
    # ------------------------------------------------------------------

    def on_bar(self, ctx: SessionContext, bar: Bar) -> list[OrderIntent]:
        """Three-phase logic:

        Phase 0 — Expiry-day gate (first bar of session):
            Resolve option to check if today is expiry day.
            If not, return [] for all subsequent bars.

        Phase 1 — Entry trigger (NIFTY-FUT bar at 09:20):
            Read NIFTY level, compute ATM strike, resolve CE + PE,
            call subscribe_cb, set pending state.  Return [].

        Phase 2 — First option bar for each leg:
            Record entry premium.  Once BOTH legs have entry premiums,
            emit two SELL OrderIntents (one per leg), 1 lot each.
        """
        d = bar.ts_open.date()
        if d != self._session_date:
            self._reset(d)

        # ── Phase 0: expiry-day determination ──────────────────────────
        if self._is_expiry_day is None:
            self._is_expiry_day = self._determine_expiry_day(d)

        if not self._is_expiry_day:
            return []   # graceful no-op on non-expiry days

        # Already traded this session — only manage() handles open positions
        if self._traded:
            return []

        sym = bar.instrument.symbol

        # ── Phase 2: CE first bar ───────────────────────────────────────
        if (
            self._pending_ce is not None
            and sym == self._pending_ce.symbol
            and self._entry_ce is None
        ):
            self._entry_ce = bar.close
            self._ce_instr = self._pending_ce
            self._ce_last  = bar.close
            log.info(
                "zerodte_straddle: CE first bar %s entry_ce=%.2f",
                sym, self._entry_ce,
            )
            return self._maybe_emit_intents()

        # ── Phase 2: PE first bar ───────────────────────────────────────
        if (
            self._pending_pe is not None
            and sym == self._pending_pe.symbol
            and self._entry_pe is None
        ):
            self._entry_pe = bar.close
            self._pe_instr = self._pending_pe
            self._pe_last  = bar.close
            log.info(
                "zerodte_straddle: PE first bar %s entry_pe=%.2f",
                sym, self._entry_pe,
            )
            return self._maybe_emit_intents()

        # ── Phase 1: NIFTY-FUT 09:20 entry trigger ─────────────────────
        if sym != _NIFTY_FUT_SYM:
            return []

        if self._entry_triggered:
            return []

        ts_close_t = bar.ts_close.astimezone(IST).time().replace(tzinfo=None)
        if ts_close_t != _ENTRY_TIME:
            return []

        # Resolve CE and PE for the current-week ATM strike
        try:
            strike = self._nearest_atm("NIFTY", bar.close)
            ce_instr = self._resolve_option("NIFTY", strike, "CE", d)
            pe_instr = self._resolve_option("NIFTY", strike, "PE", d)
        except LookupError as exc:
            log.warning("zerodte_straddle: option lookup failed at entry: %s", exc)
            return []
        except Exception:
            log.exception("zerodte_straddle: option_resolver raised at entry")
            return []

        # Subscribe both legs
        subs = [
            (ce_instr.security_id, "NSE_FNO", ce_instr),
            (pe_instr.security_id, "NSE_FNO", pe_instr),
        ]
        if self.subscribe_cb is not None:
            try:
                self.subscribe_cb(subs)
                log.info(
                    "zerodte_straddle: subscribed CE=%s PE=%s at NIFTY=%.0f strike=%.0f",
                    ce_instr.symbol, pe_instr.symbol, bar.close, strike,
                )
            except Exception:
                log.exception("zerodte_straddle: subscribe_cb raised")

        self._pending_ce      = ce_instr
        self._pending_pe      = pe_instr
        self._entry_triggered = True

        log.info(
            "zerodte_straddle: armed at 09:20 NIFTY=%.2f strike=%.0f CE=%s PE=%s",
            bar.close, strike, ce_instr.symbol, pe_instr.symbol,
        )
        return []   # intents deferred until first option bars arrive

    # ------------------------------------------------------------------
    # Intent emission helper
    # ------------------------------------------------------------------

    def _maybe_emit_intents(self) -> list[OrderIntent]:
        """Emit two SELL intents once BOTH leg entry premiums are known."""
        if self._entry_ce is None or self._entry_pe is None:
            return []   # still waiting for the other leg

        if self._traded:
            return []

        self._traded = True

        entry_combined = self._entry_ce + self._entry_pe
        log.info(
            "zerodte_straddle: emitting SELL intents CE=%.2f PE=%.2f combined=%.2f",
            self._entry_ce, self._entry_pe, entry_combined,
        )

        def _sell_intent(instr, ref: float) -> OrderIntent:
            # SELL side: stop must be ABOVE ref (catastrophic buyback stop).
            # The real basket stop (25% combined) lives in manage().
            cat_stop = ref * _CATAS_MULT
            return OrderIntent(
                strategy_id=self.strategy_id,
                instrument=instr,
                side=Side.SELL,
                ref_price=ref,
                stop_price=cat_stop,
                target_price=None,
                trail_atr_mult=None,
                reason=(
                    f"0dte_short_straddle ref={ref:.2f} "
                    f"cat_stop={cat_stop:.2f} combined={entry_combined:.2f}"
                ),
            )

        return [
            _sell_intent(self._ce_instr, self._entry_ce),
            _sell_intent(self._pe_instr, self._entry_pe),
        ]

    # ------------------------------------------------------------------
    # manage — basket stop + 15:10 flat
    # ------------------------------------------------------------------

    def manage(
        self, ctx: SessionContext, bar: Bar, position: "Position"
    ) -> "tuple[float | None, bool]":
        """Basket stop and voluntary square-off.

        Per-leg guard: only act on a bar that belongs to this position's
        instrument (mirrors BreadthRiderOptions convention).

        Basket stop: combined premium >= 1.25 × entry_combined → exit NOW.
        Flat: ts_close >= 15:10 → exit NOW.
        Sets _basket_exit latch so the other leg also exits on its next bar.
        """
        # If basket exit already latched, exit this position immediately.
        if self._basket_exit:
            return None, True

        # Guard: only act on this position's instrument bar.
        if bar.instrument.symbol != position.instrument.symbol:
            return None, False

        # Update latest known premium for this leg.
        sym = bar.instrument.symbol
        if self._ce_instr is not None and sym == self._ce_instr.symbol:
            self._ce_last = bar.close
        elif self._pe_instr is not None and sym == self._pe_instr.symbol:
            self._pe_last = bar.close

        # ── Voluntary flat at 15:10 ──────────────────────────────────
        ts_close_t = bar.ts_close.astimezone(IST).time().replace(tzinfo=None)
        if ts_close_t >= _FLAT_TIME:
            log.debug("zerodte_straddle: voluntary flat %s at %s", sym, ts_close_t)
            self._basket_exit = True   # latch so other leg exits too
            return None, True

        # ── Basket stop check ─────────────────────────────────────────
        if (
            self._entry_ce is not None
            and self._entry_pe is not None
            and self._ce_last is not None
            and self._pe_last is not None
        ):
            entry_combined  = self._entry_ce + self._entry_pe
            current_combined = self._ce_last + self._pe_last
            stop_level      = _STOP_MULT * entry_combined
            if current_combined >= stop_level:
                log.info(
                    "zerodte_straddle: basket stop triggered combined=%.2f >= %.2f (%.2f × entry)",
                    current_combined, stop_level, _STOP_MULT,
                )
                self._basket_exit = True
                return None, True

        return None, False
