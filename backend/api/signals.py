"""Signals API routes — account-scoped."""

from fastapi import APIRouter, Query
from backend.db.models import get_signals, get_connection
from backend.api.accounts_api import get_active_account_id

router = APIRouter(tags=["signals"])


@router.get("/signals")
def list_signals(status: str = None, limit: int = 50, account: str = None):
    acc = account or get_active_account_id()
    return get_signals(account_id=acc, status=status, limit=limit)


@router.post("/signals/{signal_id}/approve")
def approve_signal(signal_id: int):
    conn = get_connection()
    conn.execute("UPDATE signals SET status = 'APPROVED' WHERE id = ? AND status = 'PENDING'", (signal_id,))
    conn.commit()
    updated = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    conn.close()
    return dict(updated) if updated else {"error": "Signal not found"}


@router.post("/signals/{signal_id}/reject")
def reject_signal(signal_id: int, reason: str = "Manual rejection"):
    conn = get_connection()
    conn.execute(
        "UPDATE signals SET status = 'REJECTED', reasoning = reasoning || ' | Rejected: ' || ? WHERE id = ? AND status = 'PENDING'",
        (reason, signal_id)
    )
    conn.commit()
    conn.close()
    return {"status": "rejected", "signal_id": signal_id}
