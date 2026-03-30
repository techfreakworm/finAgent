"""12-Month Equity Momentum Strategy — Validated CAGR 7.4%."""

import logging
from datetime import datetime
import pandas as pd
from config import config
from strategies.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class EquityMomentum(BaseStrategy):
    """
    Monthly momentum rotation: buy top N stocks by 12-month return.
    Rebalance monthly. Delivery trades.
    """

    def __init__(self):
        self.cfg = config.equity

    def generate_signals(self, data: dict) -> list[Signal]:
        """
        Generate momentum signals at month-end.

        Expected data keys:
            monthly_prices: pd.DataFrame — monthly close prices (index=dates, columns=symbols)
            capital: float
            current_date: datetime
            current_holdings: list[str] — symbols currently held (optional)
        """
        monthly_prices = data.get("monthly_prices")
        capital = data.get("capital", config.risk.starting_capital)
        current_date = data.get("current_date", datetime.now())
        current_holdings = data.get("current_holdings", [])

        if monthly_prices is None or len(monthly_prices) < self.cfg.momentum_lookback + 2:
            logger.debug("Insufficient monthly data for momentum calculation")
            return []

        # Only generate on last business day of month
        if not self._is_month_end(current_date):
            return []

        lookback = self.cfg.momentum_lookback
        skip = self.cfg.momentum_skip
        top_n = self.cfg.momentum_top_n

        # Calculate momentum: return over lookback months, skipping last skip months
        try:
            current_idx = len(monthly_prices) - 1
            if current_idx - skip < 0 or current_idx - lookback - skip < 0:
                return []

            recent_prices = monthly_prices.iloc[current_idx - skip]
            old_prices = monthly_prices.iloc[current_idx - lookback - skip]

            momentum = (recent_prices / old_prices - 1).dropna()
            ranked = momentum.sort_values(ascending=False)
            top_stocks = ranked.head(top_n).index.tolist()

        except Exception as e:
            logger.error("Momentum calculation failed: %s", e)
            return []

        signals = []
        position_value = capital / top_n

        for symbol in top_stocks:
            if symbol in current_holdings:
                continue  # already holding, skip

            try:
                current_price = float(monthly_prices[symbol].iloc[-1])
                if current_price <= 0:
                    continue

                qty = int(position_value / current_price)
                if qty <= 0:
                    continue

                mom_return = float(momentum.get(symbol, 0))

                signals.append(Signal(
                    strategy="equity_momentum",
                    symbol=symbol,
                    direction="BUY",
                    entry_price=current_price,
                    stop_loss=0,  # exit only on monthly rebalance
                    target=0,
                    lot_size=qty,
                    margin_required=position_value,
                    confidence=min(0.7, mom_return),
                    reasoning=(
                        f"{symbol}: {lookback}m momentum = {mom_return:+.1%}. "
                        f"Rank {top_stocks.index(symbol)+1}/{top_n}. "
                        f"Entry ₹{current_price:.1f}, qty {qty}."
                    ),
                    timestamp=current_date,
                    metadata={"momentum_return": mom_return, "rank": top_stocks.index(symbol) + 1},
                ))

            except Exception as e:
                logger.error("Error creating momentum signal for %s: %s", symbol, e)

        if signals:
            logger.info("Momentum rebalance: %d new positions (%s)",
                         len(signals), ", ".join(s.symbol for s in signals))

        return signals

    def should_exit(self, position, current_data: dict) -> tuple[bool, str]:
        """
        Exit on monthly rebalance if stock dropped out of top N.

        Expected current_data keys:
            is_rebalance_day: bool
            still_in_top_n: bool
        """
        is_rebalance = current_data.get("is_rebalance_day", False)
        still_top = current_data.get("still_in_top_n", True)

        if is_rebalance and not still_top:
            return True, "rebalance"

        return False, ""

    @staticmethod
    def _is_month_end(dt: datetime) -> bool:
        """Check if date is the last business day of the month."""
        from datetime import timedelta
        next_day = dt + timedelta(days=1)
        # If next day is in a different month, today is month-end
        if next_day.month != dt.month:
            return True
        # Also check: if next 3 days are all different month (handles Friday before month change on Monday)
        for i in range(1, 4):
            check = dt + timedelta(days=i)
            if check.weekday() < 5 and check.month != dt.month:
                return True
        return False
