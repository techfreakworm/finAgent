"""Logs API — account-scoped."""

from fastapi import APIRouter
from backend.db.models import get_events
from backend.api.accounts_api import get_active_account_id

router = APIRouter(tags=["logs"])


@router.get("/logs")
def get_logs(limit: int = 100, event_type: str = None, account: str = None):
    acc = account or get_active_account_id()
    return get_events(account_id=acc, event_type=event_type, limit=limit)
