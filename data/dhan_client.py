"""Dhan REST API client for FinAgent."""

import json
import logging
import time
from datetime import datetime, timedelta
import requests
from config import config
from data.cache import DhanDataCache

logger = logging.getLogger(__name__)


class DhanClient:
    """Wrapper around Dhan REST API for market data and trading."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": config.dhan.access_token,
            "client-id": config.dhan.client_id,
        })
        self.base_url = config.dhan.base_url
        self._cache = DhanDataCache()

    def _post(self, endpoint: str, payload: dict) -> dict | None:
        # Check cache first (only hits for immutable historical endpoints)
        cached = self._cache.get(endpoint, payload)
        if cached is not None:
            return cached

        try:
            resp = self.session.post(
                f"{self.base_url}{endpoint}",
                data=json.dumps(payload),
                timeout=30,
            )
            if resp.status_code == 429:
                logger.warning("Rate limited on %s, retrying in 5s", endpoint)
                time.sleep(5)
                resp = self.session.post(
                    f"{self.base_url}{endpoint}",
                    data=json.dumps(payload),
                    timeout=30,
                )
            if resp.status_code == 200:
                result = resp.json()
                self._cache.put(endpoint, payload, result)
                return result
            logger.error("POST %s → %d: %s", endpoint, resp.status_code, resp.text[:200])
            return None
        except Exception as e:
            logger.error("POST %s failed: %s", endpoint, e)
            return None

    def _get(self, endpoint: str) -> dict | list | None:
        try:
            resp = self.session.get(f"{self.base_url}{endpoint}", timeout=30)
            if resp.status_code == 200:
                return resp.json()
            logger.error("GET %s → %d", endpoint, resp.status_code)
            return None
        except Exception as e:
            logger.error("GET %s failed: %s", endpoint, e)
            return None

    def get_option_chain(self, security_id: int, exchange_segment: str, expiry: str) -> dict | None:
        """Fetch live option chain for a given underlying and expiry."""
        return self._post("/optionchain", {
            "UnderlyingScrip": security_id,
            "UnderlyingSeg": exchange_segment,
            "Expiry": expiry,
        })

    def get_expiry_list(self, security_id: int, exchange_segment: str) -> list[str]:
        """Get available expiry dates for an underlying."""
        result = self._post("/optionchain/expirylist", {
            "UnderlyingScrip": security_id,
            "UnderlyingSeg": exchange_segment,
        })
        if not result or not isinstance(result, dict):
            return []
        data = result.get("data", [])
        # Response can be {"data": [...]} or {"data": {"data": [...], "status": "success"}}
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            inner = data.get("data", [])
            return inner if isinstance(inner, list) else []
        return []

    def get_historical_options(
        self,
        security_id: int,
        instrument: str,
        strike: str,
        option_type: str,
        from_date: str,
        to_date: str,
        expiry_flag: str = "MONTH",
        expiry_code: int = 1,
        interval: int = 60,
    ) -> dict:
        """Fetch expired option data in 30-day chunks with rate limiting."""
        all_data = {"close": [], "iv": [], "oi": [], "spot": [], "timestamp": []}
        current = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")

        while current < end:
            chunk_end = min(current + timedelta(days=29), end)
            result = self._post("/charts/rollingoption", {
                "exchangeSegment": "NSE_FNO",
                "interval": str(interval),
                "securityId": security_id,
                "instrument": instrument,
                "expiryFlag": expiry_flag,
                "expiryCode": expiry_code,
                "strike": strike,
                "drvOptionType": option_type,
                "requiredData": ["close", "iv", "oi", "spot"],
                "fromDate": current.strftime("%Y-%m-%d"),
                "toDate": chunk_end.strftime("%Y-%m-%d"),
            })
            time.sleep(0.8)

            if result:
                key = "ce" if option_type == "CALL" else "pe"
                opt_data = result.get("data", {}).get(key, {})
                if opt_data and opt_data.get("timestamp"):
                    for field in all_data:
                        vals = opt_data.get(field, [])
                        if vals:
                            all_data[field].extend(vals)

            current = chunk_end + timedelta(days=1)

        logger.info("Fetched %d data points for %s %s %s",
                     len(all_data["timestamp"]), instrument, strike, option_type)
        return all_data

    def get_historical_daily(self, security_id: int, exchange_segment: str,
                             instrument: str, from_date: str, to_date: str) -> dict | None:
        """Fetch daily OHLCV candles."""
        return self._post("/charts/historical", {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "fromDate": from_date,
            "toDate": to_date,
        })

    def get_positions(self) -> list:
        """Get current open positions from broker."""
        return self._get("/positions") or []

    def get_fund_limits(self) -> dict | None:
        """Get available fund limits."""
        return self._get("/fundlimit")
