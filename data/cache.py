"""Disk-based cache for Dhan API historical data responses.

Historical market data is immutable — once a trading day is over, its
OHLCV data never changes.  This cache stores raw API responses as
gzipped JSON files keyed by a SHA-256 hash of (endpoint + payload),
eliminating redundant API calls and rate-limit waits on repeated replays.

Only caches endpoints that return immutable historical data:
  - /charts/historical   (daily OHLCV)
  - /charts/rollingoption (expired option candles)
"""

import gzip
import hashlib
import json
import logging
import os
from datetime import date, datetime

logger = logging.getLogger(__name__)

# Endpoints whose responses are immutable historical data.
_CACHEABLE_ENDPOINTS = frozenset({
    "/charts/historical",
    "/charts/rollingoption",
})

# Default cache directory — Docker mounts ./cache:/app/cache for persistence.
_DEFAULT_CACHE_DIR = os.environ.get("FINAGENT_CACHE_DIR", "cache/dhan")


class DhanDataCache:
    """Gzipped-JSON disk cache for Dhan API responses.

    Parameters
    ----------
    cache_dir : str
        Directory for cached files.  Created automatically if missing.
    """

    def __init__(self, cache_dir: str = _DEFAULT_CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, endpoint: str, payload: dict) -> dict | None:
        """Return cached response, or ``None`` on miss."""
        if not self._is_cacheable(endpoint, payload):
            return None

        path = self._path_for(endpoint, payload)
        if not os.path.exists(path):
            logger.debug("Cache MISS: %s", endpoint)
            return None

        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Cache HIT: %s (%s)", endpoint, os.path.basename(path)[:12])
            return data
        except Exception as exc:
            logger.warning("Cache read error (%s), deleting: %s", path, exc)
            self._safe_delete(path)
            return None

    def put(self, endpoint: str, payload: dict, response: dict) -> None:
        """Store *response* in the cache if the endpoint is cacheable."""
        if not self._is_cacheable(endpoint, payload):
            return
        if not response:
            return

        path = self._path_for(endpoint, payload)
        try:
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump(response, f)
            logger.debug("Cached: %s -> %s", endpoint, os.path.basename(path))
        except Exception as exc:
            logger.warning("Cache write error: %s", exc)
            self._safe_delete(path)

    def clear(self) -> int:
        """Delete all cached files.  Returns the number of files removed."""
        count = 0
        if not os.path.isdir(self.cache_dir):
            return count
        for name in os.listdir(self.cache_dir):
            if name.endswith(".json.gz"):
                self._safe_delete(os.path.join(self.cache_dir, name))
                count += 1
        logger.info("Cache cleared: %d files deleted", count)
        return count

    def stats(self) -> dict:
        """Return cache statistics."""
        if not os.path.isdir(self.cache_dir):
            return {"files": 0, "size_bytes": 0, "size_mb": 0.0}

        files = [
            n for n in os.listdir(self.cache_dir) if n.endswith(".json.gz")
        ]
        total_bytes = sum(
            os.path.getsize(os.path.join(self.cache_dir, n)) for n in files
        )
        return {
            "files": len(files),
            "size_bytes": total_bytes,
            "size_mb": round(total_bytes / (1024 * 1024), 2),
            "cache_dir": self.cache_dir,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_cacheable(self, endpoint: str, payload: dict) -> bool:
        """Decide whether this request should use the cache.

        Rules:
        1. Endpoint must be in the allow-list.
        2. ``toDate`` must be strictly in the past (today's data may still
           be updating intraday).
        """
        if endpoint not in _CACHEABLE_ENDPOINTS:
            return False

        to_date_str = payload.get("toDate") or payload.get("to_date")
        if to_date_str:
            try:
                to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
                if to_date >= date.today():
                    return False
            except ValueError:
                pass

        return True

    def _cache_key(self, endpoint: str, payload: dict) -> str:
        """Deterministic key from endpoint + payload."""
        canonical = f"{endpoint}:{json.dumps(payload, sort_keys=True)}"
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def _path_for(self, endpoint: str, payload: dict) -> str:
        key = self._cache_key(endpoint, payload)
        return os.path.join(self.cache_dir, f"{key}.json.gz")

    @staticmethod
    def _safe_delete(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass
