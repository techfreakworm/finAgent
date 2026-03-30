"""
Replay API — FastAPI router for Historical Replay mode.

Provides endpoints to prepare data, control playback (play / pause / step /
stop / speed), and poll events.  The frontend polls ``/replay/events``
every ~500 ms during active replay instead of using WebSockets.
"""

import logging
import threading
from collections import deque
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.db.models import create_account, delete_account, get_account, update_account_capital
from replay.data_provider import ReplayDataProvider
from replay.engine import ReplayEngine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["replay"])

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PrepareRequest(BaseModel):
    """Body for POST /replay/prepare."""
    from_date: str
    to_date: str
    interval: str = "60"
    strategies: list[str] = Field(
        default_factory=lambda: ["nifty_strangle", "equity_mean_reversion"],
    )
    capital: float = 500_000
    floor: float = 400_000


class SpeedRequest(BaseModel):
    """Body for POST /replay/speed."""
    value: int


class StartRequest(BaseModel):
    """Body for POST /replay/start."""
    speed: int = 1


# ---------------------------------------------------------------------------
# Module-level state (per-process singleton replay)
# ---------------------------------------------------------------------------

_replay_engine: Optional[ReplayEngine] = None
_replay_data: Optional[ReplayDataProvider] = None
_replay_events: deque = deque(maxlen=5000)  # ring buffer
_replay_account_id: Optional[str] = None
_prepare_thread: Optional[threading.Thread] = None
_prepare_error: Optional[str] = None

# Monotonically increasing event counter
_event_counter: int = 0
_event_lock = threading.Lock()


def _append_event(event_type: str, data: dict) -> None:
    """Thread-safe helper to push an event into the ring buffer."""
    global _event_counter
    with _event_lock:
        _event_counter += 1
        _replay_events.append({
            "index": _event_counter,
            "type": event_type,
            "data": data,
        })


def _engine_event_handler(event_type: str, data: dict) -> None:
    """Callback wired into ``ReplayEngine.on_event``."""
    _append_event(event_type, data)

    # When a trade closes, persist capital update to the account
    if event_type == "trade" and _replay_account_id:
        cap = data.get("capital_after")
        if cap is not None:
            try:
                update_account_capital(_replay_account_id, cap)
            except Exception as exc:
                logger.error("Failed to update account capital: %s", exc)


def _current_status() -> str:
    """Derive a human-readable status string."""
    global _replay_data, _replay_engine, _prepare_thread

    if _prepare_thread and _prepare_thread.is_alive():
        return "preparing"
    if _replay_data and _replay_data.error:
        return "error"
    if _replay_engine:
        if _replay_engine.playing and _replay_engine.paused:
            return "paused"
        if _replay_engine.playing:
            return "playing"
        # Engine exists but not playing — either stopped or completed
        if _replay_engine.current_index >= _replay_engine.data.get_total():
            return "completed"
        return "stopped"
    if _replay_data and _replay_data.ready:
        return "ready"
    return "idle"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/replay/prepare")
def prepare_replay(body: PrepareRequest):
    """Create a ReplayDataProvider and start fetching data in the background.

    Also creates a dedicated replay account in the DB.
    """
    global _replay_data, _replay_engine, _replay_account_id
    global _prepare_thread, _prepare_error, _event_counter

    # Reset previous replay
    if _replay_engine:
        _replay_engine.stop()
    _replay_engine = None
    _replay_events.clear()
    _event_counter = 0
    _prepare_error = None

    # Clean up previous replay account (if any)
    if _replay_account_id:
        try:
            delete_account(_replay_account_id)
        except Exception:
            pass

    # Create fresh replay account
    _replay_account_id = f"replay_{uuid4().hex[:8]}"
    try:
        create_account(
            _replay_account_id,
            acc_type="replay",
            label=f"Replay {body.from_date} to {body.to_date}",
            capital=body.capital,
            floor=body.floor,
        )
    except Exception as exc:
        logger.error("Failed to create replay account: %s", exc)
        raise HTTPException(status_code=500, detail=f"Account creation failed: {exc}")

    # Build data provider
    _replay_data = ReplayDataProvider(
        from_date=body.from_date,
        to_date=body.to_date,
        interval=body.interval,
        strategies=body.strategies,
    )

    # Start background fetch
    def _background_prepare():
        global _prepare_error
        try:
            def on_prog(pct, msg):
                _append_event("progress", {"progress": pct, "message": msg})

            _replay_data.prepare(on_progress=on_prog)
        except Exception as exc:
            _prepare_error = str(exc)
            logger.exception("Background prepare failed")

    _prepare_thread = threading.Thread(target=_background_prepare, daemon=True)
    _prepare_thread.start()

    logger.info(
        "Replay prepare started: %s -> %s, interval=%s, strategies=%s, account=%s",
        body.from_date, body.to_date, body.interval, body.strategies,
        _replay_account_id,
    )

    return {
        "status": "preparing",
        "account_id": _replay_account_id,
        "from_date": body.from_date,
        "to_date": body.to_date,
        "interval": body.interval,
        "strategies": body.strategies,
    }


@router.get("/replay/status")
def replay_status():
    """Return the current state of the replay system."""
    status = _current_status()
    result: dict = {
        "status": status,
        "account_id": _replay_account_id,
    }

    if _replay_data:
        result["progress"] = _replay_data.progress
        result["progress_message"] = _replay_data.progress_message
        result["total_candles"] = _replay_data.total_candles
        result["ready"] = _replay_data.ready
        if _replay_data.error:
            result["error"] = _replay_data.error

    if _replay_engine:
        result["current_index"] = _replay_engine.current_index
        result["playing"] = _replay_engine.playing
        result["paused"] = _replay_engine.paused
        result["speed"] = _replay_engine.speed
    else:
        result["current_index"] = 0

    if _prepare_error:
        result["error"] = _prepare_error

    return result


@router.post("/replay/start")
def start_replay(body: StartRequest):
    """Create a ReplayEngine from the prepared data and start playback."""
    global _replay_engine

    if not _replay_data or not _replay_data.ready:
        raise HTTPException(status_code=400, detail="Data not ready. Call /replay/prepare first.")

    if _replay_engine and _replay_engine.playing:
        raise HTTPException(status_code=400, detail="Replay already playing. Stop it first.")

    if not _replay_account_id:
        raise HTTPException(status_code=400, detail="No replay account. Call /replay/prepare first.")

    account = get_account(_replay_account_id)
    if not account:
        raise HTTPException(status_code=404, detail=f"Account {_replay_account_id} not found.")

    capital = account["starting_capital"]
    floor = account["hard_floor"]

    _replay_engine = ReplayEngine(
        data_provider=_replay_data,
        strategies=_replay_data.strategies,
        starting_capital=capital,
        hard_floor=floor,
        account_id=_replay_account_id,
    )
    _replay_engine.on_event = _engine_event_handler
    _replay_engine.set_speed(body.speed)
    _replay_engine.play()

    logger.info("Replay started: speed=%d, total_candles=%d", body.speed, _replay_data.total_candles)
    return {"status": "playing", "speed": body.speed, "total_candles": _replay_data.total_candles}


@router.post("/replay/pause")
def pause_replay():
    """Pause playback."""
    if not _replay_engine:
        raise HTTPException(status_code=400, detail="No active replay.")
    _replay_engine.pause()
    return {"status": "paused", "current_index": _replay_engine.current_index}


@router.post("/replay/resume")
def resume_replay():
    """Resume playback from a paused state."""
    if not _replay_engine:
        raise HTTPException(status_code=400, detail="No active replay.")
    _replay_engine.resume()
    return {"status": "playing", "current_index": _replay_engine.current_index}


@router.post("/replay/step")
def step_replay():
    """Advance exactly one candle."""
    if not _replay_engine:
        raise HTTPException(status_code=400, detail="No active replay.")
    if _replay_engine.playing and not _replay_engine.paused:
        raise HTTPException(
            status_code=400,
            detail="Cannot step while playing. Pause first.",
        )
    _replay_engine.step()
    return {
        "status": "stepped",
        "current_index": _replay_engine.current_index,
        "total_candles": _replay_engine.data.get_total(),
    }


@router.post("/replay/stop")
def stop_replay():
    """Stop playback entirely."""
    if not _replay_engine:
        raise HTTPException(status_code=400, detail="No active replay.")
    _replay_engine.stop()
    return {"status": "stopped", "current_index": _replay_engine.current_index}


@router.post("/replay/speed")
def set_speed(body: SpeedRequest):
    """Change playback speed (candles per second, 1-100)."""
    if not _replay_engine:
        raise HTTPException(status_code=400, detail="No active replay.")
    _replay_engine.set_speed(body.value)
    return {"speed": _replay_engine.speed}


@router.get("/replay/events")
def get_events(since: int = 0):
    """Return events since a given index (for polling).

    The frontend should poll this endpoint every ~500 ms during active
    replay, passing the last ``index`` it received as the ``since``
    query parameter.

    Returns
    -------
    dict
        ``events`` -- list of ``{index, type, data}`` dicts.
        ``latest_index`` -- the highest event index returned (pass this
        as ``since`` on the next poll).
    """
    with _event_lock:
        events = [e for e in _replay_events if e["index"] > since]

    latest = events[-1]["index"] if events else since
    return {"events": events, "latest_index": latest}


@router.get("/replay/state")
def get_state():
    """Return the current replay trading state.

    Includes capital, open positions, signal/trade counts, and the
    equity curve.
    """
    if not _replay_engine:
        return {
            "status": _current_status(),
            "capital": 0,
            "equity": 0,
            "positions": [],
            "signals_count": 0,
            "trades_count": 0,
        }
    state = _replay_engine.get_state()
    state["status"] = _current_status()
    state["account_id"] = _replay_account_id
    return state


@router.get("/replay/summary")
def get_summary():
    """Return the full replay summary (best called after replay ends).

    Includes total P&L, win rate, max drawdown, per-strategy stats,
    and a downsampled equity curve.
    """
    if not _replay_engine:
        raise HTTPException(status_code=400, detail="No replay engine. Run a replay first.")

    summary = _replay_engine.get_summary()
    summary["status"] = _current_status()
    summary["account_id"] = _replay_account_id
    return summary
