"""
Historical Replay Data Provider.

Pre-fetches and aligns all historical market data required to replay
strategies over a date range.  Supports NIFTY index, India VIX, equity
universe (yfinance), and expired NIFTY options (Dhan rollingoption API).
"""

import logging
from datetime import datetime
from typing import Callable, Optional

import numpy as np
import pandas as pd
import ta
from config import config
from data.dhan_client import DhanClient
from data.equity_data import EquityData

logger = logging.getLogger(__name__)

# Intervals accepted by Dhan rollingoption API (numeric strings).
_VALID_INTRADAY_INTERVALS = {"1", "5", "15", "60"}


class ReplayDataProvider:
    """Pre-fetch and serve aligned historical data for replay.

    Parameters
    ----------
    from_date : str
        Start date in ``YYYY-MM-DD`` format.
    to_date : str
        End date in ``YYYY-MM-DD`` format.
    interval : str
        Candle interval: ``"1"``, ``"5"``, ``"15"``, ``"60"`` (minutes) or
        ``"D"`` (daily).
    strategies : list[str] | None
        Strategy names to prepare data for.  Defaults to both
        ``nifty_strangle`` and ``equity_mean_reversion``.
    """

    def __init__(
        self,
        from_date: str,
        to_date: str,
        interval: str = "60",
        strategies: Optional[list[str]] = None,
    ) -> None:
        self.from_date = from_date
        self.to_date = to_date
        self.interval = interval
        self.strategies = strategies or ["nifty_strangle", "equity_mean_reversion"]

        # Fetched data stores
        self.data: dict[str, pd.DataFrame] = {}
        self.timestamps: list[pd.Timestamp] = []
        self.total_candles: int = 0
        self.ready: bool = False
        self.progress: int = 0        # 0-100
        self.progress_message: str = ""
        self.error: Optional[str] = None

        # Helpers
        self._dhan = DhanClient()
        self._equity = EquityData()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, on_progress: Optional[Callable[[int, str], None]] = None) -> None:
        """Fetch all historical data needed for the configured strategies.

        Calls *on_progress(pct, message)* at each milestone so the API
        layer can relay progress to the frontend.
        """
        try:
            self._update_progress(0, "Starting data fetch...", on_progress)

            need_equity = "equity_mean_reversion" in self.strategies
            need_options = "nifty_strangle" in self.strategies

            # Step 1 -- NIFTY index (always needed)
            self._update_progress(5, "Fetching NIFTY 50 index...", on_progress)
            self._fetch_nifty_index()

            # Step 2 -- India VIX (always needed)
            self._update_progress(15, "Fetching India VIX...", on_progress)
            self._fetch_india_vix()

            # Step 3 -- Equity universe
            if need_equity:
                self._update_progress(25, "Fetching equity universe...", on_progress)
                self._fetch_equity_universe(on_progress)
            else:
                self._update_progress(50, "Skipping equity (not selected)", on_progress)

            # Step 4 -- NIFTY ATM options
            if need_options:
                self._update_progress(60, "Fetching NIFTY ATM options (CE)...", on_progress)
                self._fetch_nifty_options("CALL")
                self._update_progress(75, "Fetching NIFTY ATM options (PE)...", on_progress)
                self._fetch_nifty_options("PUT")
            else:
                self._update_progress(80, "Skipping options (not selected)", on_progress)

            # Step 5 -- Calculate RSI for each stock
            if need_equity:
                self._update_progress(85, "Calculating RSI indicators...", on_progress)
                self._calculate_rsi()

            # Step 6 -- Align timestamps
            self._update_progress(90, "Aligning timestamps...", on_progress)
            self._align_timestamps()

            self.ready = True
            self._update_progress(100, "Data ready", on_progress)
            logger.info(
                "ReplayDataProvider ready: %d candles, %s -> %s, interval=%s",
                self.total_candles, self.from_date, self.to_date, self.interval,
            )

        except Exception as exc:
            self.error = str(exc)
            self._update_progress(0, f"Error: {exc}", on_progress)
            logger.exception("Failed to prepare replay data")
            raise

    def get_candle(self, index: int) -> dict:
        """Return market data at a single timestamp index.

        Returns
        -------
        dict
            ``timestamp`` -- the timestamp for this candle.
            ``nifty``     -- dict with ``o``, ``h``, ``l``, ``c``, ``v``.
            ``vix``       -- float, India VIX value.
            ``stocks``    -- dict mapping symbol to ``{close, rsi}``.
            ``options``   -- dict with ``ce_close``, ``pe_close``, ``iv``,
                             ``oi``, ``spot``.
        """
        if not self.ready or index < 0 or index >= self.total_candles:
            return {}

        ts = self.timestamps[index]
        result: dict = {"timestamp": ts}

        # NIFTY index
        nifty_df = self.data.get("nifty")
        if nifty_df is not None and index < len(nifty_df):
            row = nifty_df.iloc[index]
            result["nifty"] = {
                "o": float(row.get("Open", 0)),
                "h": float(row.get("High", 0)),
                "l": float(row.get("Low", 0)),
                "c": float(row.get("Close", 0)),
                "v": float(row.get("Volume", 0)),
            }
        else:
            result["nifty"] = {"o": 0, "h": 0, "l": 0, "c": 0, "v": 0}

        # VIX
        vix_df = self.data.get("vix")
        if vix_df is not None and index < len(vix_df):
            result["vix"] = float(vix_df.iloc[index].get("Close", 0))
        else:
            result["vix"] = 0.0

        # Stocks
        stocks: dict = {}
        for sym in config.equity.universe:
            key = f"stock_{sym}"
            df = self.data.get(key)
            if df is not None and index < len(df):
                row = df.iloc[index]
                stocks[sym] = {
                    "close": float(row.get("Close", 0)),
                    "rsi": float(row.get("rsi", 50)),
                }
        result["stocks"] = stocks

        # Options
        ce_df = self.data.get("options_ce")
        pe_df = self.data.get("options_pe")
        opt: dict = {"ce_close": 0, "pe_close": 0, "iv": 0, "oi": 0, "spot": 0}
        if ce_df is not None and index < len(ce_df):
            ce_row = ce_df.iloc[index]
            opt["ce_close"] = float(ce_row.get("close", 0))
            opt["iv"] = float(ce_row.get("iv", 0))
            opt["oi"] = float(ce_row.get("oi", 0))
            opt["spot"] = float(ce_row.get("spot", 0))
        if pe_df is not None and index < len(pe_df):
            pe_row = pe_df.iloc[index]
            opt["pe_close"] = float(pe_row.get("close", 0))
            # Use PE IV / OI if CE was empty
            if opt["iv"] == 0:
                opt["iv"] = float(pe_row.get("iv", 0))
            if opt["spot"] == 0:
                opt["spot"] = float(pe_row.get("spot", 0))
        result["options"] = opt

        return result

    def get_view_up_to(self, index: int) -> dict[str, pd.DataFrame]:
        """Return all data sliced to ``[:index+1]`` (no look-ahead).

        This is the view a strategy should use for calculations that
        depend on historical context (e.g. RSI, moving averages).

        Returns
        -------
        dict[str, pd.DataFrame]
            Same keys as ``self.data`` but each DataFrame is truncated.
        """
        if not self.ready:
            return {}
        end = index + 1
        return {key: df.iloc[:end].copy() for key, df in self.data.items()}

    def get_total(self) -> int:
        """Return the total number of aligned candles."""
        return self.total_candles

    # ------------------------------------------------------------------
    # Internal fetching helpers
    # ------------------------------------------------------------------

    def _update_progress(
        self, pct: int, message: str, callback: Optional[Callable] = None,
    ) -> None:
        self.progress = pct
        self.progress_message = message
        logger.info("Replay data progress: %d%% — %s", pct, message)
        if callback:
            try:
                callback(pct, message)
            except Exception:
                pass

    def _dhan_to_df(self, data: dict) -> pd.DataFrame:
        """Convert Dhan API response dict to DataFrame."""
        if not data or "close" not in data or not data["close"]:
            return pd.DataFrame()
        df = pd.DataFrame({
            "Open": data.get("open", []),
            "High": data.get("high", []),
            "Low": data.get("low", []),
            "Close": data["close"],
            "Volume": data.get("volume", []),
        })
        if "timestamp" in data and data["timestamp"]:
            df.index = pd.to_datetime(data["timestamp"], unit="s")
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
        return df.sort_index()

    def _fetch_nifty_index(self) -> None:
        """Fetch NIFTY 50 index via Dhan."""
        try:
            data = self._dhan.get_historical_daily(
                13, "IDX_I", "INDEX", self.from_date, self.to_date,
            )
            df = self._dhan_to_df(data)
            if df.empty:
                logger.warning("NIFTY index data is empty from Dhan")
            else:
                logger.info("Fetched NIFTY index: %d rows", len(df))
            self.data["nifty"] = df
        except Exception as exc:
            logger.error("Failed to fetch NIFTY index: %s", exc)
            self.data["nifty"] = pd.DataFrame()

    def _fetch_india_vix(self) -> None:
        """Fetch India VIX via Dhan."""
        try:
            data = self._dhan.get_historical_daily(
                config.equity.india_vix_security_id, "IDX_I", "INDEX",
                self.from_date, self.to_date,
            )
            df = self._dhan_to_df(data)
            if df.empty:
                logger.warning("India VIX data is empty from Dhan")
            else:
                logger.info("Fetched India VIX: %d rows", len(df))
            self.data["vix"] = df
        except Exception as exc:
            logger.error("Failed to fetch India VIX: %s", exc)
            self.data["vix"] = pd.DataFrame()

    def _fetch_equity_universe(
        self, on_progress: Optional[Callable] = None,
    ) -> None:
        """Fetch all NIFTY 50 stocks via Dhan API."""
        import time as _time
        universe = config.equity.universe_map
        total_stocks = len(universe)

        for i, (sym, sec_id) in enumerate(universe.items()):
            try:
                data = self._dhan.get_historical_daily(
                    sec_id, "NSE_EQ", "EQUITY", self.from_date, self.to_date,
                )
                df = self._dhan_to_df(data)
                if not df.empty and len(df) > 20:
                    self.data[f"stock_{sym}"] = df
                    logger.debug("Fetched %s: %d rows", sym, len(df))
                else:
                    logger.warning("Empty/short data for %s (ID=%d)", sym, sec_id)
            except Exception as exc:
                logger.error("Failed to fetch %s: %s", sym, exc)

            _time.sleep(0.3)  # rate limit

            pct = 25 + int(30 * (i + 1) / total_stocks)
            self._update_progress(
                pct, f"Fetched {i + 1}/{total_stocks} stocks ({sym})", on_progress,
            )

    def _fetch_nifty_options(self, option_type: str) -> None:
        """Fetch expired ATM NIFTY options via Dhan rollingoption API.

        Parameters
        ----------
        option_type : str
            ``"CALL"`` or ``"PUT"``.
        """
        interval_val = int(self.interval) if self.interval in _VALID_INTRADAY_INTERVALS else 60
        label = "CE" if option_type == "CALL" else "PE"

        raw = self._dhan.get_historical_options(
            security_id=config.nifty.security_id,
            instrument=config.nifty.instrument,
            strike="ATM",
            option_type=option_type,
            from_date=self.from_date,
            to_date=self.to_date,
            expiry_flag="MONTH",
            expiry_code=1,
            interval=interval_val,
        )

        if not raw or not raw.get("timestamp"):
            logger.warning("No %s option data returned from Dhan", label)
            self.data[f"options_{label.lower()}"] = pd.DataFrame()
            return

        # Build DataFrame from the raw dict
        timestamps = [
            pd.Timestamp(t, unit="s") if isinstance(t, (int, float))
            else pd.Timestamp(t)
            for t in raw["timestamp"]
        ]

        df = pd.DataFrame({
            "close": raw.get("close", []),
            "iv": raw.get("iv", []),
            "oi": raw.get("oi", []),
            "spot": raw.get("spot", []),
        }, index=pd.DatetimeIndex(timestamps))

        logger.info("Fetched %s options: %d rows", label, len(df))
        self.data[f"options_{label.lower()}"] = df

    # ------------------------------------------------------------------
    # Indicator calculations
    # ------------------------------------------------------------------

    def _calculate_rsi(self, window: int = 14) -> None:
        """Calculate RSI(14) on DAILY data and map back to intraday candles.

        RSI must ALWAYS be computed on daily closes regardless of the replay
        interval.  A 14-period RSI on hourly candles is 14 hours — meaningless.
        The correct approach: resample to daily, compute RSI, then forward-fill
        the daily RSI value onto each intraday candle of that day.
        """
        for key, df in list(self.data.items()):
            if not key.startswith("stock_") or df.empty:
                continue
            if "Close" not in df.columns:
                continue
            try:
                # Resample to daily (last close of each day)
                daily_close = df["Close"].resample("D").last().dropna()

                if len(daily_close) < window + 1:
                    df["rsi"] = np.nan
                    continue

                # Compute RSI on daily closes
                daily_rsi = ta.momentum.RSIIndicator(
                    daily_close, window=window,
                ).rsi()

                # Map daily RSI back to each intraday candle via date
                daily_rsi_df = daily_rsi.to_frame(name="rsi")
                daily_rsi_df.index = daily_rsi_df.index.normalize()

                # For each row in the original df, look up the RSI for that date
                df["_date"] = df.index.normalize()
                df["rsi"] = df["_date"].map(daily_rsi_df["rsi"]).values
                df.drop(columns=["_date"], inplace=True)

                # Forward-fill any NaN RSI values
                df["rsi"] = df["rsi"].ffill()

                logger.debug("Calculated daily RSI for %s (%d daily bars)", key, len(daily_close))
            except Exception as exc:
                logger.error("RSI calculation failed for %s: %s", key, exc)
                df["rsi"] = np.nan

    # ------------------------------------------------------------------
    # Timestamp alignment
    # ------------------------------------------------------------------

    def _align_timestamps(self) -> None:
        """Build a common ordered timestamp index across all data.

        Data sources may have slightly different trading calendars (e.g.
        yfinance vs Dhan options).  We take the intersection of the NIFTY
        index timestamps and each data set, forward-filling gaps in the
        smaller sets so every index position maps to a valid row.
        """
        nifty_df = self.data.get("nifty")
        if nifty_df is None or nifty_df.empty:
            logger.error("Cannot align: no NIFTY index data")
            self.total_candles = 0
            return

        # Use NIFTY index as the master timeline — strip timezone for consistency
        master_index = nifty_df.index
        if master_index.tz is not None:
            master_index = master_index.tz_localize(None)
            nifty_df.index = master_index
            self.data["nifty"] = nifty_df

        # Re-index every DataFrame to the master timeline
        for key in list(self.data.keys()):
            df = self.data[key]
            if df.empty:
                continue
            # Ensure the DataFrame has a DatetimeIndex
            if not isinstance(df.index, pd.DatetimeIndex):
                try:
                    df.index = pd.to_datetime(df.index)
                except Exception:
                    continue
            # Strip timezone to match master
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            # Reindex to master, forward-fill gaps, then back-fill leading NaNs
            self.data[key] = df.reindex(master_index, method="ffill").bfill()

        self.timestamps = list(master_index)
        self.total_candles = len(self.timestamps)
        logger.info("Aligned %d candles on master NIFTY timeline", self.total_candles)
