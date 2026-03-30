"""Risk API routes — account-scoped."""

from fastapi import APIRouter
from config import config
from backend.db.models import get_connection, get_daily_pnl_history, get_account
from backend.api.accounts_api import get_active_account_id

router = APIRouter(tags=["risk"])


@router.get("/risk")
def get_risk_status(account: str = None):
    acc_id = account or get_active_account_id()
    acc = get_account(acc_id) or {}

    history = get_daily_pnl_history(account_id=acc_id, days=30)
    latest = history[0] if history else {}

    capital = acc.get("current_capital", latest.get("capital", config.risk.starting_capital))
    floor = acc.get("hard_floor", config.risk.hard_floor)
    floor_distance = capital - floor
    floor_pct = floor_distance / capital * 100 if capital > 0 else 0

    capitals = [h.get("capital", capital) for h in reversed(history)] or [capital]
    peak = max(capitals)
    drawdown = (capital - peak) / peak * 100 if peak > 0 else 0

    conn = get_connection()
    worst = conn.execute(
        "SELECT MIN(pnl_net) as worst, symbol, exit_date FROM trades WHERE account_id = ? AND pnl_net IS NOT NULL",
        (acc_id,)
    ).fetchone()
    conn.close()

    return {
        "account_id": acc_id,
        "capital": capital,
        "hard_floor": floor,
        "floor_distance": floor_distance,
        "floor_distance_pct": floor_pct,
        "floor_breached": capital < floor,
        "peak_capital": peak,
        "drawdown_pct": drawdown,
        "margin_used": latest.get("margin_used", 0),
        "margin_utilization_pct": latest.get("margin_used", 0) / capital * 100 if capital > 0 else 0,
        "worst_trade": dict(worst) if worst and worst["worst"] else None,
        "vix": latest.get("vix"),
        "max_loss_allowed": acc.get("starting_capital", config.risk.starting_capital) * config.risk.max_single_trade_loss_pct,
    }
