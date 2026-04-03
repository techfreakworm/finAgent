"""Portfolio API routes — account-scoped."""

from fastapi import APIRouter
from config import config
from backend.db.models import (
    get_daily_pnl_history, get_strategy_summary, get_account,
    get_open_positions_summary,
)
from backend.api.accounts_api import get_active_account_id

router = APIRouter(tags=["portfolio"])


@router.get("/portfolio")
def get_portfolio(account: str = None):
    acc_id = account or get_active_account_id()
    acc = get_account(acc_id) or {}

    summary = get_strategy_summary(account_id=acc_id)
    pos_summary = get_open_positions_summary(acc_id)

    cash = acc.get("current_capital", config.risk.starting_capital)
    starting = acc.get("starting_capital", config.risk.starting_capital)
    floor = acc.get("hard_floor", config.risk.hard_floor)
    margin_used = pos_summary["total_margin"]
    n_open = pos_summary["count"]

    # Total portfolio value = cash + margin in open positions
    portfolio_value = cash + margin_used
    cumulative_pnl = portfolio_value - starting

    total_trades = sum(s.get("total_trades", 0) for s in summary.values())
    total_pnl = sum(s.get("total_pnl", 0) for s in summary.values())

    return {
        "account_id": acc_id,
        "account_type": acc.get("type", "paper"),
        "account_label": acc.get("label", "Paper Trading"),
        "capital": portfolio_value,
        "available_capital": cash,
        "starting_capital": starting,
        "hard_floor": floor,
        "floor_distance": portfolio_value - floor,
        "cumulative_pnl": cumulative_pnl,
        "total_trades": total_trades + n_open,
        "total_realized_pnl": total_pnl,
        "unrealized_pnl": 0,
        "open_positions": n_open,
        "margin_used": margin_used,
        "paper_mode": acc.get("type", "paper") == "paper",
        "strategies": summary,
    }


@router.get("/portfolio/history")
def portfolio_history(days: int = 90, account: str = None):
    acc = account or get_active_account_id()
    return get_daily_pnl_history(account_id=acc, days=days)
