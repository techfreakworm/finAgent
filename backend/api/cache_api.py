"""Cache management API — stats and clear."""

import logging
from fastapi import APIRouter
from data.cache import DhanDataCache

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cache"])

_cache = DhanDataCache()


@router.get("/cache/stats")
def cache_stats():
    """Return cache file count and total size."""
    return _cache.stats()


@router.post("/cache/clear")
def cache_clear():
    """Delete all cached API responses."""
    deleted = _cache.clear()
    return {"deleted": deleted}
