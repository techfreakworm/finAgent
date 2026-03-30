"""Action endpoints — Scan, Workflow, Report, Close positions."""

import logging
from fastapi import APIRouter
from backend.task_runner import runner

logger = logging.getLogger(__name__)
router = APIRouter(tags=["actions"])


def _run_scan():
    """Execute market scan (runs in background thread)."""
    from engine import TradingEngine
    engine = TradingEngine()
    engine.run_options_scan("NIFTY")
    engine.run_equity_scan()
    signals = engine.pending_signals
    return {
        "signals_found": len(signals),
        "signals": [{"symbol": s.symbol, "direction": s.direction, "price": s.entry_price} for s in signals],
    }


def _run_workflow():
    """Execute full AI agent workflow (runs in background thread)."""
    from agents.graph import run_workflow
    result = run_workflow(paper_mode=True)
    return {
        "signals": len(result.get("signals", [])),
        "approved": len(result.get("approved", [])),
        "rejected": len(result.get("rejected", [])),
        "executed": len(result.get("executions", [])),
        "report": result.get("report", ""),
    }


def _run_report():
    """Generate daily report (runs in background thread)."""
    from engine import TradingEngine
    engine = TradingEngine()
    return engine.generate_daily_report()


@router.post("/actions/scan")
def trigger_scan():
    """Run a market scan (options + equity). Returns immediately with task_id."""
    task_id = runner.run_task("scan", _run_scan)
    return {"task_id": task_id, "message": "Scan started"}


@router.post("/actions/workflow")
def trigger_workflow():
    """Run full AI agent workflow. Returns immediately with task_id."""
    task_id = runner.run_task("workflow", _run_workflow)
    return {"task_id": task_id, "message": "AI workflow started"}


@router.post("/actions/report")
def trigger_report():
    """Generate daily report. Returns immediately with task_id."""
    task_id = runner.run_task("report", _run_report)
    return {"task_id": task_id, "message": "Report generation started"}


@router.get("/actions/status/{task_id}")
def get_task_status(task_id: str):
    """Poll status of a background task."""
    status = runner.get_status(task_id)
    if not status:
        return {"error": "Task not found"}
    return status


@router.get("/actions/tasks")
def list_tasks():
    """List recent tasks."""
    return runner.get_all()


@router.post("/actions/close-all")
def close_all_positions():
    """Emergency: close all open positions."""
    from engine import TradingEngine
    engine = TradingEngine()
    engine._exit_all_positions("MANUAL_CLOSE")
    return {"message": "All positions closed"}
