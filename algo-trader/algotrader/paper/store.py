"""SQLite persistence for paper trading (ARCHITECTURE §6).

Every table has account_id TEXT for account isolation.
WAL + busy_timeout=5000 applied on every connection.
Schema auto-created on first use.

Tables:
  positions   - open positions (for crash-recovery reconciliation)
  trades      - closed round-trip trade records
  daily_pnl   - per-day P&L summary (upsert)
  risk_state  - SessionRiskState JSON (upsert, for breaker crash recovery)
  events      - append-only event log

All DATETIME values are ISO-8601 strings with IST timezone offset.
No secrets are ever written to this module.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from algotrader.core import (
    IST,
    Instrument,
    Position,
    ProductType,
    Segment,
    Side,
)
from algotrader.risk.engine import SessionRiskState

_DEFAULT_DB = (
    Path(__file__).resolve().parent.parent.parent / "data" / "paper.db"
)


# ---------------------------------------------------------------------------
# Instrument reconstruction helper (frozen dataclass subclass)
# ---------------------------------------------------------------------------

class _ReconstructedInstrument(Instrument):
    """Instrument subclass returning a stored lot_size (crash-recovery path).

    Used only when reconstructing positions from the DB where the original
    in-memory Instrument is unavailable.  Lot size was captured at fill time.
    """

    def __init__(self, stored_lot_size: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_stored_lot_size", stored_lot_size)

    def lot_size(self, on: date) -> int:  # type: ignore[override]
        return self._stored_lot_size  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt_to_str(dt: datetime) -> str:
    return dt.astimezone(IST).isoformat(timespec="seconds")


def _str_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(IST)


# ---------------------------------------------------------------------------
# PaperStore
# ---------------------------------------------------------------------------

class PaperStore:
    """Account-isolated SQLite store for paper-trading state.

    Args:
        db_path: Path to the SQLite file.  Defaults to data/paper.db.
                 Pass a tmp_path in tests to isolate.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Connection factory — WAL + busy_timeout on every connection
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Schema (idempotent)
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        conn = self._connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS positions (
                position_id       TEXT PRIMARY KEY,
                account_id        TEXT NOT NULL,
                session_date      TEXT NOT NULL,
                strategy_id       TEXT NOT NULL,
                symbol            TEXT NOT NULL,
                security_id       TEXT NOT NULL,
                segment           TEXT NOT NULL,
                tick_size         REAL NOT NULL,
                is_derivative     INTEGER NOT NULL DEFAULT 0,
                underlying        TEXT,
                can_short         INTEGER NOT NULL DEFAULT 1,
                lot_size_at_entry INTEGER NOT NULL DEFAULT 1,
                side              TEXT NOT NULL,
                quantity          INTEGER NOT NULL,
                entry_price       REAL NOT NULL,
                entry_ts          TEXT NOT NULL,
                stop_price        REAL NOT NULL,
                target_price      REAL,
                margin_required   REAL NOT NULL DEFAULT 0,
                entry_slip        REAL NOT NULL DEFAULT 0,
                trail_atr_mult    REAL,
                time_stop_min     INTEGER,
                status            TEXT NOT NULL DEFAULT 'OPEN',
                created_at        TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_pos_account_date_status
                ON positions(account_id, session_date, status);

            CREATE TABLE IF NOT EXISTS trades (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id        TEXT NOT NULL,
                position_id       TEXT NOT NULL,
                session_date      TEXT NOT NULL,
                strategy_id       TEXT NOT NULL,
                symbol            TEXT NOT NULL,
                side              TEXT NOT NULL,
                quantity          INTEGER NOT NULL,
                entry_price       REAL NOT NULL,
                entry_ts          TEXT NOT NULL,
                exit_price        REAL NOT NULL,
                exit_ts           TEXT NOT NULL,
                exit_reason       TEXT NOT NULL,
                gross_pnl         REAL NOT NULL,
                net_pnl           REAL NOT NULL,
                costs_total       REAL NOT NULL,
                slippage_paid     REAL NOT NULL,
                created_at        TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trades_account_date
                ON trades(account_id, session_date);

            CREATE TABLE IF NOT EXISTS daily_pnl (
                account_id        TEXT NOT NULL,
                date              TEXT NOT NULL,
                realized          REAL NOT NULL DEFAULT 0,
                n_trades          INTEGER NOT NULL DEFAULT 0,
                max_dd_intraday   REAL NOT NULL DEFAULT 0,
                updated_at        TEXT NOT NULL,
                PRIMARY KEY (account_id, date)
            );

            CREATE TABLE IF NOT EXISTS risk_state (
                account_id        TEXT NOT NULL,
                session_date      TEXT NOT NULL,
                state_json        TEXT NOT NULL,
                updated_at        TEXT NOT NULL,
                PRIMARY KEY (account_id, session_date)
            );

            CREATE TABLE IF NOT EXISTS events (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id        TEXT NOT NULL,
                event_type        TEXT NOT NULL,
                message           TEXT NOT NULL,
                ts                TEXT NOT NULL,
                data              TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_events_account_type
                ON events(account_id, event_type);
            """
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def save_open_position(
        self,
        account_id: str,
        pos: Position,
        margin_required: float,
        entry_slip: float,
        trail_atr_mult: float | None = None,
        time_stop_min: int | None = None,
    ) -> None:
        """Insert a newly filled open position."""
        instr = pos.instrument
        lot_sz = instr.lot_size(pos.session_date) if instr.is_derivative else 1
        now_str = _dt_to_str(datetime.now(IST))
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO positions (
                position_id, account_id, session_date, strategy_id,
                symbol, security_id, segment, tick_size, is_derivative,
                underlying, can_short, lot_size_at_entry,
                side, quantity, entry_price, entry_ts, stop_price, target_price,
                margin_required, entry_slip, trail_atr_mult, time_stop_min,
                status, created_at
            ) VALUES (
                ?,?,?,?,  ?,?,?,?,?,  ?,?,?,  ?,?,?,?,?,?,  ?,?,?,?,  'OPEN',?
            )
            """,
            (
                pos.position_id,
                account_id,
                pos.session_date.isoformat(),
                pos.strategy_id,
                instr.symbol,
                instr.security_id,
                instr.segment.value,
                instr.tick_size,
                int(instr.is_derivative),
                instr.underlying,
                int(instr.can_short_intraday),
                lot_sz,
                pos.side.value,
                pos.quantity,
                pos.entry_price,
                _dt_to_str(pos.entry_ts),
                pos.stop_price,
                pos.target_price,
                margin_required,
                entry_slip,
                trail_atr_mult,
                time_stop_min,
                now_str,
            ),
        )
        conn.commit()
        conn.close()

    def update_position_stop(
        self, account_id: str, position_id: str, new_stop: float
    ) -> None:
        """Update trailing stop on an open position."""
        conn = self._connect()
        conn.execute(
            "UPDATE positions SET stop_price=? "
            "WHERE account_id=? AND position_id=? AND status='OPEN'",
            (new_stop, account_id, position_id),
        )
        conn.commit()
        conn.close()

    def close_open_position(self, account_id: str, position_id: str) -> None:
        """Mark position as CLOSED (called when trade record is saved)."""
        conn = self._connect()
        conn.execute(
            "UPDATE positions SET status='CLOSED' "
            "WHERE account_id=? AND position_id=?",
            (account_id, position_id),
        )
        conn.commit()
        conn.close()

    def load_open_positions(
        self, account_id: str, session_date: date
    ) -> list[dict]:
        """Return all OPEN position rows for (account_id, session_date)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM positions "
            "WHERE account_id=? AND session_date=? AND status='OPEN' "
            "ORDER BY created_at",
            (account_id, session_date.isoformat()),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def save_trade(self, account_id: str, trade_dict: dict) -> None:
        """Insert a closed trade record into the trades table."""
        now_str = _dt_to_str(datetime.now(IST))
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO trades (
                account_id, position_id, session_date, strategy_id, symbol,
                side, quantity, entry_price, entry_ts,
                exit_price, exit_ts, exit_reason,
                gross_pnl, net_pnl, costs_total, slippage_paid, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                account_id,
                trade_dict["position_id"],
                trade_dict["session_date"],
                trade_dict["strategy_id"],
                trade_dict["symbol"],
                trade_dict["side"],
                trade_dict["quantity"],
                trade_dict["entry_price"],
                trade_dict["entry_ts"],
                trade_dict["exit_price"],
                trade_dict["exit_ts"],
                trade_dict["exit_reason"],
                trade_dict["gross_pnl"],
                trade_dict["net_pnl"],
                trade_dict["costs_total"],
                trade_dict["slippage_paid"],
                now_str,
            ),
        )
        conn.commit()
        conn.close()

    def load_trades(self, account_id: str, session_date: date) -> list[dict]:
        """Return all trade rows for (account_id, session_date)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM trades WHERE account_id=? AND session_date=? "
            "ORDER BY exit_ts",
            (account_id, session_date.isoformat()),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Risk state
    # ------------------------------------------------------------------

    def save_risk_state(
        self, account_id: str, state: SessionRiskState
    ) -> None:
        """Upsert session risk state JSON (for crash recovery)."""
        now_str = _dt_to_str(datetime.now(IST))
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO risk_state (account_id, session_date, state_json, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(account_id, session_date)
            DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at
            """,
            (
                account_id,
                state.session_date.isoformat(),
                state.to_json(),
                now_str,
            ),
        )
        conn.commit()
        conn.close()

    def load_risk_state(
        self, account_id: str, session_date: date
    ) -> SessionRiskState | None:
        """Load stored risk state for today, or None if not found."""
        conn = self._connect()
        row = conn.execute(
            "SELECT state_json FROM risk_state "
            "WHERE account_id=? AND session_date=?",
            (account_id, session_date.isoformat()),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return SessionRiskState.from_json(row["state_json"])

    # ------------------------------------------------------------------
    # Daily P&L
    # ------------------------------------------------------------------

    def upsert_daily_pnl(
        self,
        account_id: str,
        session_date: date,
        realized: float,
        n_trades: int,
        max_dd_intraday: float,
    ) -> None:
        """Upsert the daily P&L summary row."""
        now_str = _dt_to_str(datetime.now(IST))
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO daily_pnl
                (account_id, date, realized, n_trades, max_dd_intraday, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(account_id, date)
            DO UPDATE SET
                realized=excluded.realized,
                n_trades=excluded.n_trades,
                max_dd_intraday=excluded.max_dd_intraday,
                updated_at=excluded.updated_at
            """,
            (
                account_id,
                session_date.isoformat(),
                realized,
                n_trades,
                max_dd_intraday,
                now_str,
            ),
        )
        conn.commit()
        conn.close()

    def load_daily_pnl(
        self, account_id: str, session_date: date
    ) -> dict | None:
        """Return the daily P&L row, or None."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM daily_pnl WHERE account_id=? AND date=?",
            (account_id, session_date.isoformat()),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def append_event(
        self,
        account_id: str,
        event_type: str,
        message: str,
        ts: datetime | None = None,
        data: dict | None = None,
    ) -> None:
        """Append an event record (fill, close, risk breaker, reconcile)."""
        if ts is None:
            ts = datetime.now(IST)
        conn = self._connect()
        conn.execute(
            "INSERT INTO events (account_id, event_type, message, ts, data) "
            "VALUES (?,?,?,?,?)",
            (
                account_id,
                event_type,
                message,
                _dt_to_str(ts),
                json.dumps(data) if data is not None else None,
            ),
        )
        conn.commit()
        conn.close()

    def load_events(
        self,
        account_id: str,
        event_type: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Return events for account_id, optionally filtered by type."""
        conn = self._connect()
        if event_type:
            rows = conn.execute(
                "SELECT * FROM events WHERE account_id=? AND event_type=? "
                "ORDER BY ts DESC LIMIT ?",
                (account_id, event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE account_id=? ORDER BY ts DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Reconciliation helper
    # ------------------------------------------------------------------

    def reconstruct_position(
        self, row: dict
    ) -> tuple[Position, float, float, float | None]:
        """Reconstruct a Position object from a DB row.

        Returns:
            (Position, margin_required, entry_slip, trail_atr_mult | None)
        """
        instr = _ReconstructedInstrument(
            stored_lot_size=int(row["lot_size_at_entry"]),
            symbol=row["symbol"],
            security_id=row["security_id"],
            segment=Segment(row["segment"]),
            tick_size=float(row["tick_size"]),
            is_derivative=bool(row["is_derivative"]),
            underlying=row["underlying"],
            can_short_intraday=bool(row["can_short"]),
        )
        pos = Position(
            position_id=row["position_id"],
            strategy_id=row["strategy_id"],
            instrument=instr,
            side=Side(row["side"]),
            quantity=int(row["quantity"]),
            entry_price=float(row["entry_price"]),
            entry_ts=_str_to_dt(row["entry_ts"]),
            stop_price=float(row["stop_price"]),
            target_price=(
                float(row["target_price"]) if row["target_price"] is not None else None
            ),
            session_date=date.fromisoformat(row["session_date"]),
            product=ProductType.INTRADAY,
        )
        margin = float(row["margin_required"])
        entry_slip = float(row["entry_slip"])
        trail_mult = (
            float(row["trail_atr_mult"]) if row["trail_atr_mult"] is not None else None
        )
        return pos, margin, entry_slip, trail_mult


# ===========================================================================
# Module-level functional API (used by eod.py, eod_watchdog.py, tests)
# ===========================================================================
#
# These functions use per-account SQLite files in data/paper/<account_id>.db
# (separate from the original PaperStore which uses data/paper.db).
# WAL + busy_timeout on every write connection; 10s timeout for read paths.
# No secrets ever written or logged.
# ===========================================================================

import logging as _logging
import sqlite3 as _sqlite3

_fn_log = _logging.getLogger(__name__ + ".fn")

_FN_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "paper"

_FN_SCHEMA = """
CREATE TABLE IF NOT EXISTS fn_trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT    NOT NULL,
    session_date  TEXT    NOT NULL,
    position_id   TEXT    NOT NULL,
    strategy_id   TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    quantity      INTEGER NOT NULL,
    entry_price   REAL    NOT NULL,
    entry_ts      TEXT    NOT NULL,
    exit_price    REAL    NOT NULL,
    exit_ts       TEXT    NOT NULL,
    exit_reason   TEXT,
    gross_pnl     REAL    NOT NULL,
    net_pnl       REAL    NOT NULL,
    costs_total   REAL    NOT NULL,
    slippage_paid REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS fn_positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT    NOT NULL,
    session_date  TEXT    NOT NULL,
    position_id   TEXT    NOT NULL,
    strategy_id   TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    quantity      INTEGER NOT NULL,
    entry_price   REAL    NOT NULL,
    entry_ts      TEXT    NOT NULL,
    stop_price    REAL,
    target_price  REAL,
    is_open       INTEGER NOT NULL DEFAULT 1,
    UNIQUE(account_id, position_id)
);
CREATE TABLE IF NOT EXISTS fn_risk_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT    NOT NULL,
    session_date  TEXT    NOT NULL,
    event_text    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fn_trades_acct_date
    ON fn_trades(account_id, session_date);
CREATE INDEX IF NOT EXISTS idx_fn_pos_acct_date_open
    ON fn_positions(account_id, session_date, is_open);
CREATE INDEX IF NOT EXISTS idx_fn_risk_acct_date
    ON fn_risk_events(account_id, session_date);
"""


def _fn_db_path(account_id: str, db_dir: "Path | None" = None) -> Path:
    d = (db_dir or _FN_DB_DIR).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{account_id}.db"


def _fn_connect_write(path: Path) -> "_sqlite3.Connection":
    conn = _sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = _sqlite3.Row
    return conn


def _fn_connect_read(path: Path) -> "_sqlite3.Connection":
    """10-second timeout for report generation (ARCHITECTURE §6)."""
    conn = _sqlite3.connect(str(path), timeout=10)
    conn.row_factory = _sqlite3.Row
    return conn


def _fn_ts(ts: datetime) -> str:
    return ts.astimezone(IST).isoformat(timespec="seconds")


def _fn_parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def init_db(account_id: str, db_dir: "Path | None" = None) -> None:
    """Create functional-API tables for *account_id*. Idempotent."""
    path = _fn_db_path(account_id, db_dir)
    with _fn_connect_write(path) as conn:
        conn.executescript(_FN_SCHEMA)
    _fn_log.debug("init_db: schema ready at %s", path)


def record_trade(
    account_id: str,
    session_date: date,
    trade: dict,
    db_dir: "Path | None" = None,
) -> None:
    """Persist a closed trade.  Keys: position_id, strategy_id, symbol, side,
    quantity, entry_price, entry_ts, exit_price, exit_ts, exit_reason,
    gross_pnl, net_pnl, costs_total, slippage_paid."""
    path = _fn_db_path(account_id, db_dir)
    init_db(account_id, db_dir)
    with _fn_connect_write(path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO fn_trades
               (account_id, session_date, position_id, strategy_id, symbol,
                side, quantity, entry_price, entry_ts,
                exit_price, exit_ts, exit_reason,
                gross_pnl, net_pnl, costs_total, slippage_paid)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                account_id, session_date.isoformat(),
                trade["position_id"], trade["strategy_id"], trade["symbol"],
                trade["side"], int(trade["quantity"]),
                float(trade["entry_price"]), _fn_ts(trade["entry_ts"]),
                float(trade["exit_price"]), _fn_ts(trade["exit_ts"]),
                trade.get("exit_reason", ""),
                float(trade["gross_pnl"]), float(trade["net_pnl"]),
                float(trade["costs_total"]), float(trade["slippage_paid"]),
            ),
        )


def record_open_position(
    account_id: str,
    session_date: date,
    pos: dict,
    db_dir: "Path | None" = None,
) -> None:
    """Persist an open position.  Keys: position_id, strategy_id, symbol,
    side, quantity, entry_price, entry_ts, stop_price?, target_price?."""
    path = _fn_db_path(account_id, db_dir)
    init_db(account_id, db_dir)
    with _fn_connect_write(path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO fn_positions
               (account_id, session_date, position_id, strategy_id, symbol,
                side, quantity, entry_price, entry_ts,
                stop_price, target_price, is_open)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
            (
                account_id, session_date.isoformat(),
                pos["position_id"], pos["strategy_id"], pos["symbol"],
                pos["side"], int(pos["quantity"]),
                float(pos["entry_price"]), _fn_ts(pos["entry_ts"]),
                float(pos["stop_price"]) if pos.get("stop_price") else None,
                float(pos["target_price"]) if pos.get("target_price") else None,
            ),
        )


def update_position_exit(
    account_id: str,
    position_id: str,
    db_dir: "Path | None" = None,
) -> None:
    """Mark position as closed (is_open = 0)."""
    path = _fn_db_path(account_id, db_dir)
    with _fn_connect_write(path) as conn:
        conn.execute(
            "UPDATE fn_positions SET is_open=0 "
            "WHERE account_id=? AND position_id=?",
            (account_id, position_id),
        )


def record_risk_event(
    account_id: str,
    session_date: date,
    event: str,
    db_dir: "Path | None" = None,
) -> None:
    """Persist a risk event string."""
    path = _fn_db_path(account_id, db_dir)
    init_db(account_id, db_dir)
    with _fn_connect_write(path) as conn:
        conn.execute(
            "INSERT INTO fn_risk_events (account_id, session_date, event_text) "
            "VALUES (?,?,?)",
            (account_id, session_date.isoformat(), event),
        )


def get_trades(
    account_id: str,
    session_date: date,
    db_dir: "Path | None" = None,
) -> list:
    """Return closed trades for *account_id* on *session_date*."""
    path = _fn_db_path(account_id, db_dir)
    if not path.exists():
        return []
    try:
        conn = _fn_connect_read(path)
        try:
            rows = conn.execute(
                """SELECT position_id, strategy_id, symbol, side, quantity,
                          entry_price, entry_ts, exit_price, exit_ts, exit_reason,
                          gross_pnl, net_pnl, costs_total, slippage_paid
                   FROM fn_trades
                   WHERE account_id=? AND session_date=?
                   ORDER BY entry_ts ASC""",
                (account_id, session_date.isoformat()),
            ).fetchall()
        finally:
            conn.close()
    except _sqlite3.OperationalError as exc:
        _fn_log.warning("get_trades error %s: %s", account_id, exc)
        return []
    return [
        {
            "position_id":   r["position_id"],
            "strategy_id":   r["strategy_id"],
            "symbol":        r["symbol"],
            "side":          r["side"],
            "quantity":      r["quantity"],
            "entry_price":   r["entry_price"],
            "entry_ts":      _fn_parse_ts(r["entry_ts"]),
            "exit_price":    r["exit_price"],
            "exit_ts":       _fn_parse_ts(r["exit_ts"]),
            "exit_reason":   r["exit_reason"],
            "gross_pnl":     r["gross_pnl"],
            "net_pnl":       r["net_pnl"],
            "costs_total":   r["costs_total"],
            "slippage_paid": r["slippage_paid"],
        }
        for r in rows
    ]


def get_open_positions(
    account_id: str,
    session_date: date,
    db_dir: "Path | None" = None,
) -> list:
    """Return open positions for *account_id* on *session_date*."""
    path = _fn_db_path(account_id, db_dir)
    if not path.exists():
        return []
    try:
        conn = _fn_connect_read(path)
        try:
            rows = conn.execute(
                """SELECT position_id, strategy_id, symbol, side, quantity,
                          entry_price, entry_ts, stop_price, target_price
                   FROM fn_positions
                   WHERE account_id=? AND session_date=? AND is_open=1
                   ORDER BY entry_ts ASC""",
                (account_id, session_date.isoformat()),
            ).fetchall()
        finally:
            conn.close()
    except _sqlite3.OperationalError as exc:
        _fn_log.warning("get_open_positions error %s: %s", account_id, exc)
        return []
    return [
        {
            "position_id":  r["position_id"],
            "strategy_id":  r["strategy_id"],
            "symbol":       r["symbol"],
            "side":         r["side"],
            "quantity":     r["quantity"],
            "entry_price":  r["entry_price"],
            "entry_ts":     _fn_parse_ts(r["entry_ts"]),
            "stop_price":   r["stop_price"],
            "target_price": r["target_price"],
        }
        for r in rows
    ]


def get_risk_events(
    account_id: str,
    session_date: date,
    db_dir: "Path | None" = None,
) -> list:
    """Return risk event strings for *account_id* on *session_date*."""
    path = _fn_db_path(account_id, db_dir)
    if not path.exists():
        return []
    try:
        conn = _fn_connect_read(path)
        try:
            rows = conn.execute(
                """SELECT event_text FROM fn_risk_events
                   WHERE account_id=? AND session_date=?
                   ORDER BY id ASC""",
                (account_id, session_date.isoformat()),
            ).fetchall()
        finally:
            conn.close()
    except _sqlite3.OperationalError as exc:
        _fn_log.warning("get_risk_events error %s: %s", account_id, exc)
        return []
    return [r["event_text"] for r in rows]


def get_daily_pnl_history(
    account_id: str,
    limit: int = 90,
    db_dir: "Path | None" = None,
) -> list:
    """Return list of (date, net_pnl) for the cumulative equity curve."""
    path = _fn_db_path(account_id, db_dir)
    if not path.exists():
        return []
    try:
        conn = _fn_connect_read(path)
        try:
            rows = conn.execute(
                """SELECT session_date, SUM(net_pnl) AS daily_net
                   FROM fn_trades WHERE account_id=?
                   GROUP BY session_date
                   ORDER BY session_date ASC LIMIT ?""",
                (account_id, limit),
            ).fetchall()
        finally:
            conn.close()
    except _sqlite3.OperationalError as exc:
        _fn_log.warning("get_daily_pnl_history error %s: %s", account_id, exc)
        return []
    return [
        (date.fromisoformat(r["session_date"]), float(r["daily_net"]))
        for r in rows
    ]
