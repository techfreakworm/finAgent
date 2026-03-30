"""Equity market data via yfinance."""

import logging
import pandas as pd
import yfinance as yf
from config import config

logger = logging.getLogger(__name__)


class EquityData:
    """Wrapper for yfinance equity data with clean DataFrame output."""

    @staticmethod
    def _flatten(df: pd.DataFrame) -> pd.DataFrame:
        """Flatten yfinance MultiIndex columns."""
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df

    def get_stock_data(self, symbol: str, period: str = "5y") -> pd.DataFrame | None:
        """Download OHLCV data for a single stock."""
        try:
            df = yf.download(symbol, period=period, progress=False)
            df = self._flatten(df)
            if len(df) < 10:
                logger.warning("Insufficient data for %s (%d rows)", symbol, len(df))
                return None
            return df
        except Exception as e:
            logger.error("Failed to download %s: %s", symbol, e)
            return None

    def get_multiple_stocks(self, symbols: list[str], period: str = "5y") -> dict[str, pd.DataFrame]:
        """Download data for multiple stocks. Skips failures."""
        result = {}
        for sym in symbols:
            df = self.get_stock_data(sym, period)
            if df is not None:
                result[sym] = df
        logger.info("Loaded %d/%d stocks", len(result), len(symbols))
        return result

    def get_nifty(self, period: str = "5y") -> pd.DataFrame | None:
        """Get NIFTY 50 index data."""
        return self.get_stock_data("^NSEI", period)

    def get_india_vix(self, period: str = "5y") -> pd.DataFrame | None:
        """Get India VIX data."""
        return self.get_stock_data("^INDIAVIX", period)

    def get_nifty_universe(self, period: str = "5y") -> dict[str, pd.DataFrame]:
        """Get all stocks in the NIFTY universe from config."""
        return self.get_multiple_stocks(config.equity.universe, period)
