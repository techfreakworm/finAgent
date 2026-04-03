"""Accounts API — CRUD for paper/live/replay accounts."""

import logging
from uuid import uuid4
from fastapi import APIRouter
from backend.db.models import get_accounts, get_account, create_account, delete_account, update_account_capital
from config import config

logger = logging.getLogger(__name__)
router = APIRouter(tags=["accounts"])

# In-memory active account (persists per server process)
_active_account = "paper"


def get_active_account_id() -> str:
    return _active_account


@router.get("/accounts")
def list_accounts():
    return get_accounts()


@router.get("/accounts/active")
def active_account():
    acc = get_account(_active_account)
    return acc or {"id": "paper", "type": "paper", "label": "Paper Trading"}


@router.put("/accounts/active")
def set_active_account(body: dict):
    global _active_account
    account_id = body.get("account_id", "paper")
    acc = get_account(account_id)
    if not acc:
        return {"error": f"Account '{account_id}' not found"}
    _active_account = account_id
    logger.info("Active account switched to: %s (%s)", account_id, acc["label"])
    return {"active": account_id, "label": acc["label"]}


@router.post("/accounts")
def create_new_account(body: dict):
    acc_type = body.get("type", "replay")
    label = body.get("label", f"Replay {uuid4().hex[:6]}")
    capital = body.get("capital", 500000)
    floor = body.get("floor", capital * 0.8)

    if acc_type not in ("paper", "live", "replay"):
        return {"error": "Invalid account type"}

    acc_id = body.get("id") or f"{acc_type}_{uuid4().hex[:8]}"
    return create_account(acc_id, acc_type, label, capital, floor)


@router.delete("/accounts/{account_id}")
def remove_account(account_id: str):
    try:
        delete_account(account_id)
        return {"message": f"Account '{account_id}' deleted"}
    except ValueError as e:
        return {"error": str(e)}


@router.post("/accounts/{account_id}/reset")
def reset_account(account_id: str):
    """Clear all data for an account and reset capital to starting value."""
    from backend.db.models import get_connection

    acc = get_account(account_id)
    if not acc:
        return {"error": f"Account '{account_id}' not found"}

    conn = get_connection()
    for table in ["trades", "signals", "daily_pnl", "events", "positions"]:
        conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
    conn.commit()
    conn.close()

    starting = acc.get("starting_capital", config.risk.starting_capital)
    update_account_capital(account_id, starting)

    logger.info("Account '%s' reset: all data cleared, capital restored to ₹%.0f", account_id, starting)
    return {"message": f"Account '{account_id}' reset", "capital": starting}
