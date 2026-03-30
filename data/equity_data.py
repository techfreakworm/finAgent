"""Equity market data via Dhan API (no yfinance dependency)."""

import logging
import pandas as pd
from config import config
from data.dhan_client import DhanClient

logger = logging.getLogger(__name__)


class EquityData:
    """Fetches equity OHLCV, VIX, and NIFTYBEES data via Dhan API."""

    def __init__(self):
        self.client = DhanClient()

    def _to_dataframe(self, data: dict | None) -> pd.DataFrame | None:
        """Convert Dhan API response to a clean DataFrame."""
        if not data or "close" not in data or not data["close"]:
            return None

        df = pd.DataFrame({
            "Open": data.get("open", []),
            "High": data.get("high", []),
            "Low": data.get("low", []),
            "Close": data["close"],
            "Volume": data.get("volume", []),
        })

        if "timestamp" in data and data["timestamp"]:
            df.index = pd.to_datetime(data["timestamp"], unit="s")
            df.index = df.index.tz_localize(None)  # strip timezone

        df = df.sort_index()
        return df if len(df) >= 10 else None

    def get_stock_data(self, symbol: str, from_date: str = "2021-01-01",
                       to_date: str = None) -> pd.DataFrame | None:
        """Download daily OHLCV for a single stock by NSE symbol."""
        if to_date is None:
            to_date = pd.Timestamp.now().strftime("%Y-%m-%d")

        sec_id = config.equity.universe_map.get(symbol)
        if not sec_id:
            logger.warning("No security ID for %s", symbol)
            return None

        data = self.client.get_historical_daily(sec_id, "NSE_EQ", "EQUITY", from_date, to_date)
        df = self._to_dataframe(data)
        if df is not None:
            logger.debug("Loaded %s: %d rows", symbol, len(df))
        else:
            logger.warning("No data for %s (ID=%d)", symbol, sec_id)
        return df

    def get_stock_by_id(self, security_id: int, from_date: str = "2021-01-01",
                        to_date: str = None, exchange: str = "NSE_EQ",
                        instrument: str = "EQUITY") -> pd.DataFrame | None:
        """Download daily OHLCV by Dhan security ID directly."""
        if to_date is None:
            to_date = pd.Timestamp.now().strftime("%Y-%m-%d")

        data = self.client.get_historical_daily(security_id, exchange, instrument, from_date, to_date)
        return self._to_dataframe(data)

    def get_multiple_stocks(self, symbols: list[str] = None,
                            from_date: str = "2021-01-01") -> dict[str, pd.DataFrame]:
        """Download data for multiple stocks. Defaults to full NIFTY 50."""
        import time
        if symbols is None:
            symbols = list(config.equity.universe_map.keys())

        result = {}
        for sym in symbols:
            df = self.get_stock_data(sym, from_date)
            if df is not None:
                result[sym] = df
            time.sleep(0.3)  # rate limit

        logger.info("Loaded %d/%d stocks", len(result), len(symbols))
        return result

    def get_nifty(self, from_date: str = "2021-01-01") -> pd.DataFrame | None:
        """Get NIFTY 50 index data."""
        return self.get_stock_by_id(13, from_date, exchange="IDX_I", instrument="INDEX")

    def get_india_vix(self, from_date: str = "2021-01-01") -> pd.DataFrame | None:
        """Get India VIX data."""
        return self.get_stock_by_id(
            config.equity.india_vix_security_id, from_date,
            exchange="IDX_I", instrument="INDEX",
        )

    def get_niftybees(self, from_date: str = "2021-01-01") -> pd.DataFrame | None:
        """Get NIFTYBEES ETF data (for NIFTY index exposure)."""
        return self.get_stock_by_id(config.equity.niftybees_security_id, from_date)

    def get_nifty_universe(self, from_date: str = "2021-01-01") -> dict[str, pd.DataFrame]:
        """Get all NIFTY 50 stocks."""
        return self.get_multiple_stocks(from_date=from_date)

    # Backward-compatible alias
    def get_stock_data_yf(self, symbol: str, period: str = "5y") -> pd.DataFrame | None:
        """Legacy method — converts yfinance-style symbol to Dhan fetch."""
        clean = symbol.replace(".NS", "").replace(".BO", "")
        return self.get_stock_data(clean)
