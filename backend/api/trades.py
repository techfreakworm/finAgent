"""Trades API routes — account-scoped."""

from fastapi import APIRouter
from backend.db.models import get_trades, get_strategy_summary
from backend.api.accounts_api import get_active_account_id

router = APIRouter(tags=["trades"])


@router.get("/trades")
def list_trades(strategy: str = None, limit: int = 100, account: str = None):
    acc = account or get_active_account_id()
    return get_trades(account_id=acc, strategy=strategy, limit=limit)


@router.get("/trades/summary")
def trades_summary(account: str = None):
    acc = account or get_active_account_id()
    return get_strategy_summary(account_id=acc)
