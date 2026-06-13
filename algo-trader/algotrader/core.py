"""Core contracts for the intraday system. FROZEN for P1 implementation.

Every module (costs, fills, engine, risk, metrics, strategies, paper) builds
against these types. Implementers MUST NOT change signatures here; propose
contract changes to the lead instead.

Design invariants (see ARCHITECTURE.md):
- Strategies see only CLOSED bars (Bar.complete is asserted at boundaries).
- Signals on bar t fill at bar t+1 open (entries) — never same-bar.
- Every entry intent carries a stop; sizing is the risk engine's job.
- All datetimes are tz-aware Asia/Kolkata; naive datetimes are rejected.
- Each trading day is an isolated episode: no overnight state.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def require_ist(ts: datetime) -> datetime:
    """Reject naive datetimes at module boundaries (ops-review rule)."""
    if ts.tzinfo is None:
        raise ValueError(f"naive datetime rejected: {ts!r}")
    return ts.astimezone(IST)


# ---------------------------------------------------------------- instruments

class Segment(enum.Enum):
    NSE_EQ = "NSE_EQ"
    NSE_FNO = "NSE_FNO"
    IDX = "IDX_I"


class ProductType(enum.Enum):
    INTRADAY = "INTRADAY"   # the only product v1 trades
    CNC = "CNC"             # exists for cost-model completeness only


@dataclass(frozen=True)
class Instrument:
    symbol: str                  # canonical, e.g. "RELIANCE", "NIFTY-FUT"
    security_id: str             # Dhan SMST id
    segment: Segment
    tick_size: float
    is_derivative: bool = False
    underlying: str | None = None
    can_short_intraday: bool = True   # False for T2T/BE-series equities

    def lot_size(self, on: date) -> int:
        """Point-in-time lot size. Equities return 1. Implemented in
        instruments module via the dated schedule (NIFTY 25→75@2024-11→65@2026-01;
        BANKNIFTY 15→30@2024-11→35@2025-07→30@2026-01); this default serves equities."""
        return 1


# ----------------------------------------------------------------------- bars

@dataclass(frozen=True)
class Bar:
    instrument: Instrument
    ts_open: datetime            # bar START, tz-aware IST
    interval_min: int            # 1 or 5
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int | None = None        # futures/options only
    complete: bool = True        # strategies must only ever see True

    def __post_init__(self) -> None:
        require_ist(self.ts_open)

    @property
    def ts_close(self) -> datetime:
        from datetime import timedelta
        return self.ts_open + timedelta(minutes=self.interval_min)


# ----------------------------------------------------------------- intents

class Side(enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class OrderIntent:
    """A strategy's desire to open a position. Quantity is NOT here —
    the risk engine sizes intents. A stop is mandatory by contract."""
    strategy_id: str
    instrument: Instrument
    side: Side
    ref_price: float             # close of the signal bar (fill will differ)
    stop_price: float            # mandatory; risk engine rejects without it
    target_price: float | None = None
    trail_atr_mult: float | None = None
    time_stop_min: int | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.stop_price <= 0:
            raise ValueError("OrderIntent requires a positive stop_price")
        wrong_side = (self.side is Side.BUY and self.stop_price >= self.ref_price) or (
            self.side is Side.SELL and self.stop_price <= self.ref_price)
        if wrong_side:
            raise ValueError(
                f"stop {self.stop_price} on wrong side of ref {self.ref_price} for {self.side}"
            )  # guards finAgent's stop-sign bug class


@dataclass(frozen=True)
class SizedOrder:
    intent: OrderIntent
    quantity: int                # shares or contracts (lot-rounded)
    margin_required: float


@dataclass(frozen=True)
class Rejection:
    intent: OrderIntent
    rule: str                    # e.g. "breaker_tripped", "max_positions", "t2t_short"
    detail: str = ""


# ------------------------------------------------------------------ positions

class ExitReason(enum.Enum):
    STOP = "stop"
    TARGET = "target"
    TRAIL = "trail"
    TIME_STOP = "time_stop"
    STRATEGY = "strategy_exit"
    SQUARE_OFF = "square_off"          # 15:19:30 force-flat
    BREAKER = "daily_loss_breaker"
    FLOOR = "hard_floor"


@dataclass
class Position:
    position_id: str
    strategy_id: str
    instrument: Instrument
    side: Side
    quantity: int
    entry_price: float           # actual fill (next-bar open ± slippage)
    entry_ts: datetime
    stop_price: float            # current (may trail)
    target_price: float | None
    session_date: date
    product: ProductType = ProductType.INTRADAY
    # exit fields (set on close)
    exit_price: float | None = None
    exit_ts: datetime | None = None
    exit_reason: ExitReason | None = None
    gap_through_stop: bool = False     # fill was at open beyond stop (logged metric)

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    def gross_pnl(self) -> float:
        if self.exit_price is None:
            raise ValueError("position still open")
        sgn = 1.0 if self.side is Side.BUY else -1.0
        # quantity convention: shares for equity, CONTRACTS for derivatives —
        # consistent with risk sizing and DhanCosts; lot multiplier applied here
        # so realized P&L matches the engine's unrealized math (contract v1.1).
        ls = self.instrument.lot_size(self.session_date) if self.instrument.is_derivative else 1
        return sgn * (self.exit_price - self.entry_price) * self.quantity * ls


@dataclass(frozen=True)
class TradeRecord:
    """Closed round-trip with full cost attribution. The unit of all metrics.

    slippage_paid is the TOTAL ₹ slippage embedded in the fills
    ((entry_slip + exit_slip) × quantity × lot_size). It is attribution
    metadata ONLY: fill prices are already slippage-adjusted, so gross_pnl
    carries the slippage cost — subtracting it again would double-count
    (bug found in first live-data run, 2026-06-11)."""
    position: Position
    costs: "CostBreakdown"
    slippage_paid: float

    @property
    def net_pnl(self) -> float:
        return self.position.gross_pnl() - self.costs.total


# ---------------------------------------------------------------------- costs

@dataclass(frozen=True)
class CostBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    ipft: float
    gst: float

    @property
    def total(self) -> float:
        return (self.brokerage + self.stt + self.exchange_txn + self.sebi
                + self.stamp + self.ipft + self.gst)


class CostModel(Protocol):
    """Date-banded Dhan cost model. Rates depend on the TRADE DATE because
    STT/txn schedules changed inside the backtest window (see ARCHITECTURE §3.3)."""

    def round_trip(self, instrument: Instrument, side: Side, quantity: int,
                   entry_price: float, exit_price: float, on: date,
                   product: ProductType = ProductType.INTRADAY) -> CostBreakdown: ...


class SlippageModel(Protocol):
    """Causal slippage: may only use bars strictly before the fill bar."""

    def entry_slippage(self, instrument: Instrument, bar_history: Sequence[Bar]) -> float: ...
    def exit_slippage(self, instrument: Instrument, bar_history: Sequence[Bar],
                      reason: ExitReason) -> float: ...


# ----------------------------------------------------------------- session ctx

@dataclass(frozen=True)
class SessionClock:
    now: datetime                          # close time of the bar being processed
    no_new_entries_after: time             # 14:44:30
    voluntary_exit_from: time              # 15:15
    hard_flat_at: time                     # 15:19:30

    @property
    def can_enter(self) -> bool:
        return self.now.timetz().replace(tzinfo=None) < self.no_new_entries_after

    @property
    def must_flatten(self) -> bool:
        return self.now.timetz().replace(tzinfo=None) >= self.hard_flat_at


@dataclass(frozen=True)
class RiskSnapshot:
    capital: float
    realized_pnl_today: float
    unrealized_pnl: float
    breaker_tripped: bool
    floor_breached: bool
    open_position_count: int


class SessionContext(Protocol):
    """What a strategy may see on each bar. CLOSED bars only — the engine
    asserts Bar.complete for everything served here."""

    @property
    def clock(self) -> SessionClock: ...
    @property
    def risk(self) -> RiskSnapshot: ...

    def bars(self, instrument: Instrument, n: int) -> Sequence[Bar]:
        """Last n closed bars up to and including the current bar."""
        ...

    def prior_sessions(self, instrument: Instrument, n_days: int) -> Mapping[date, Sequence[Bar]]:
        """Closed bars of previous sessions (for volume profiles, VWAP-σ
        calibration, ATR warmup). Never includes today's future bars."""
        ...

    def open_positions(self, strategy_id: str | None = None) -> Sequence[Position]: ...


# ------------------------------------------------------------------ strategy

class Strategy(ABC):
    """Implementations: ORB, VWAP-reversion, trend-continuation. Strategies
    decide direction/levels; the risk engine decides size; the engine decides
    fills. Strategies are stateless across sessions by contract."""

    strategy_id: str
    warmup_bars: int = 30                  # bars needed before first signal
    warmup_days: int = 10                  # prior sessions needed (profiles/σ)

    @abstractmethod
    def on_bar(self, ctx: SessionContext, bar: Bar) -> list[OrderIntent]: ...

    def manage(self, ctx: SessionContext, bar: Bar,
               position: Position) -> tuple[float | None, bool]:
        """Optional per-bar management of an open position.
        Returns (new_stop_or_None, exit_now). Default: no change."""
        return None, False


# --------------------------------------------------------------- risk engine

class RiskEngine(Protocol):
    def size(self, intent: OrderIntent, snapshot: RiskSnapshot,
             on: date) -> SizedOrder | Rejection: ...

    def on_bar(self, snapshot: RiskSnapshot) -> Sequence[ExitReason]:
        """Breaker/floor evaluation per bar. Non-empty result ⇒ engine
        flattens everything with the given reason and halts entries."""
        ...


# -------------------------------------------------------------------- results

@dataclass
class SessionResult:
    session_date: date
    trades: list[TradeRecord] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    risk_events: list[str] = field(default_factory=list)
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    cost_total: float = 0.0


@dataclass
class BacktestResult:
    sessions: list[SessionResult]

    @property
    def trades(self) -> list[TradeRecord]:
        return [t for s in self.sessions for t in s.trades]


class BarSource(Protocol):
    """Backtest: parquet history reader. Paper: live bar builder.
    Yields (session_date, ordered complete bars across instruments)."""

    def sessions(self) -> Iterable[tuple[date, Sequence[Bar]]]: ...
