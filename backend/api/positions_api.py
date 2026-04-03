"""Positions API — open position tracking, account-scoped."""

import logging
from fastapi import APIRouter, HTTPException
from backend.db.models import (
    get_open_positions, close_position_db,
    update_account_capital, get_account,
)
from backend.api.accounts_api import get_active_account_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["positions"])


@router.get("/positions")
def list_positions(account: str = None):
    """List all open positions for the active account."""
    acc_id = account or get_active_account_id()
    return get_open_positions(acc_id)


@router.post("/positions/{position_id}/close")
def close_position(position_id: str, account: str = None):
    """Manually close a paper position, releasing margin back to capital."""
    acc_id = account or get_active_account_id()
    acc = get_account(acc_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")

    # Find the position
    positions = get_open_positions(acc_id)
    pos = next((p for p in positions if p["id"] == position_id), None)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found or already closed")

    # Close position and release margin
    margin = pos.get("margin_required", 0)
    new_capital = acc["current_capital"] + margin
    close_position_db(position_id, acc_id)
    update_account_capital(acc_id, new_capital)

    logger.info("Position %s closed manually, margin ₹%.0f released, capital ₹%.0f",
                position_id, margin, new_capital)

    return {
        "position_id": position_id,
        "margin_released": margin,
        "capital": new_capital,
    }
