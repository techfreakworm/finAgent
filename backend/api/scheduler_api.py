"""Scheduler API — start/stop/status for the automated trading scheduler."""

import logging
import threading
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scheduler"])

_scheduler_thread: Optional[threading.Thread] = None
_engine = None
_scheduler = None


def _is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def start_scheduler():
    """Start the scheduler in a background thread."""
    global _scheduler_thread, _engine, _scheduler

    if _is_running():
        logger.info("Scheduler already running")
        return

    from engine import TradingEngine
    from scheduler import TradingScheduler

    _engine = TradingEngine()
    _scheduler = TradingScheduler(_engine)

    def _run():
        try:
            _scheduler.start()
        except Exception as exc:
            logger.exception("Scheduler crashed: %s", exc)

    _scheduler_thread = threading.Thread(target=_run, daemon=True, name="scheduler")
    _scheduler_thread.start()
    logger.info("Scheduler started in background thread")


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.stop()
        logger.info("Scheduler stopped")


@router.get("/scheduler/status")
def scheduler_status():
    """Return scheduler running state and next actions."""
    from datetime import datetime

    running = _is_running()
    now = datetime.now()

    return {
        "running": running,
        "current_time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "market_hours": 9 <= now.hour < 16 and now.weekday() < 5,
    }


@router.post("/scheduler/start")
def start_scheduler_endpoint():
    """Start the automated trading scheduler."""
    if _is_running():
        return {"status": "already_running"}
    start_scheduler()
    return {"status": "started"}


@router.post("/scheduler/stop")
def stop_scheduler_endpoint():
    """Stop the automated trading scheduler."""
    if not _is_running():
        return {"status": "not_running"}
    stop_scheduler()
    return {"status": "stopped"}
