"""RSI Mean Reversion Strategy — Validated XIRR 8.5-9.7% on NIFTY 50 stocks."""

import logging
from datetime import datetime
import ta
from config import config
from strategies.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class EquityMeanReversion(BaseStrategy):
    """
    Buy oversold stocks (RSI < 30), exit when RSI recovers to 50.
    Works at any capital level. Delivery trades (₹0 brokerage on Zerodha).
    """

    def __init__(self):
        self.cfg = config.equity

    def generate_signals(self, data: dict) -> list[Signal]:
        """
        Scan stocks for RSI oversold conditions.

        Expected data keys:
            stock_prices: dict[str, pd.DataFrame] — OHLCV per stock
            capital: float — available capital
            current_date: datetime
        """
        stock_prices = data.get("stock_prices", {})
        capital = data.get("capital", config.risk.starting_capital)
        current_date = data.get("current_date", datetime.now())

        signals = []

        for symbol, df in stock_prices.items():
            if df is None or len(df) < 20:
                continue

            try:
                rsi = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
                latest_rsi = rsi.iloc[-1]

                if latest_rsi < self.cfg.rsi_entry:
                    entry_price = float(df["Close"].iloc[-1])
                    position_value = capital * self.cfg.max_position_pct
                    qty = int(position_value / entry_price) if entry_price > 0 else 0

                    if qty <= 0:
                        continue

                    stop_loss = entry_price * (1 + self.cfg.stop_loss_pct)  # -5%
                    # Estimate delivery cost for margin_required (STT + exchange + stamp)
                    cost_estimate = position_value * 0.003  # ~0.3% round trip

                    signals.append(Signal(
                        strategy="equity_mean_reversion",
                        symbol=symbol,
                        direction="BUY",
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        target=0,  # exit on RSI > 50, not fixed target
                        lot_size=qty,
                        margin_required=position_value + cost_estimate,
                        confidence=min(0.8, (self.cfg.rsi_entry - latest_rsi) / 30),
                        reasoning=(
                            f"{symbol} RSI={latest_rsi:.1f} (oversold < {self.cfg.rsi_entry}). "
                            f"Entry ₹{entry_price:.1f}, SL ₹{stop_loss:.1f}, Qty {qty}."
                        ),
                        timestamp=current_date,
                        metadata={"rsi": float(latest_rsi), "position_value": position_value},
                    ))

                    logger.info("Signal: BUY %s, RSI=%.1f, price=₹%.1f, qty=%d",
                                symbol, latest_rsi, entry_price, qty)

            except Exception as e:
                logger.error("Error processing %s: %s", symbol, e)

        return signals

    def should_exit(self, position, current_data: dict) -> tuple[bool, str]:
        """
        Check exit conditions for an open equity position.

        Expected current_data keys:
            current_rsi: float
            current_price: float
            current_date: datetime
        """
        current_rsi = current_data.get("current_rsi", 50)
        current_price = current_data.get("current_price", 0)
        current_date = current_data.get("current_date", datetime.now())

        # RSI target hit
        if current_rsi > self.cfg.rsi_exit:
            return True, "target_rsi"

        # Stop loss
        if current_price > 0 and current_price <= position.entry_price * (1 + self.cfg.stop_loss_pct):
            return True, "stop_loss"

        # Timeout
        days_held = (current_date - position.entry_date).days
        if days_held > self.cfg.max_hold_days:
            return True, "timeout"

        return False, ""
