"""
FinAgent Scheduler — Orchestrates all trading activities on schedule.

Schedule (IST):
  Monday 9:00 AM   — Fetch NIFTY option chain, generate strangle signals
  Monday 9:20 AM   — Execute approved strangle trades
  Mon-Thu hourly    — MTM monitoring, floor check
  Thursday 3:15 PM  — Options expiry / close positions
  Daily 3:35 PM     — EOD equity scan (RSI signals)
  Daily 3:40 PM     — Execute equity trades
  Monthly last week — BANKNIFTY monthly strangle
  Daily 4:00 PM     — Generate daily report, send Telegram
"""

import logging
import time
from datetime import datetime, timedelta
from config import config

logger = logging.getLogger(__name__)


class TradingScheduler:
    """Main scheduler that coordinates all trading activities."""

    def __init__(self, engine):
        """
        Args:
            engine: TradingEngine instance that has methods like
                    run_options_scan(), run_equity_scan(), monitor_mtm(),
                    close_expiring_positions(), generate_report()
        """
        self.engine = engine
        self.running = False

    def start(self):
        """Start the scheduler loop. Blocks until stopped."""
        self.running = True
        logger.info("Scheduler started. Paper mode: %s", config.paper_trading)

        while self.running:
            now = datetime.now()
            weekday = now.weekday()  # 0=Mon, 3=Thu, 5=Sat
            hour = now.hour
            minute = now.minute

            try:
                # Skip weekends
                if weekday >= 5:
                    self._sleep_until_next_weekday()
                    continue

                # Pre-market (9:00-9:15)
                if hour == 9 and minute == 0:
                    if weekday == 0:  # Monday
                        logger.info("Monday pre-market: scanning NIFTY options")
                        self.engine.run_options_scan("NIFTY")
                    self._sleep_minutes(1)

                # Market open execution (9:20)
                elif hour == 9 and minute == 20:
                    if weekday == 0:
                        logger.info("Executing approved option trades")
                        self.engine.execute_pending_signals("nifty_strangle")
                    self._sleep_minutes(1)

                # Hourly MTM during market hours (9:30-15:00)
                elif 9 <= hour < 15 and minute == 30 and weekday < 5:
                    logger.debug("Hourly MTM check")
                    self.engine.monitor_mtm()
                    self._sleep_minutes(1)

                # Thursday expiry close (15:15)
                elif weekday == 3 and hour == 15 and minute == 15:
                    logger.info("Thursday expiry: closing option positions")
                    self.engine.close_expiring_positions()
                    self._sleep_minutes(1)

                # EOD equity scan (15:35)
                elif hour == 15 and minute == 35 and weekday < 5:
                    logger.info("EOD: scanning equity signals")
                    self.engine.run_equity_scan()
                    self._sleep_minutes(1)

                # EOD equity execution (15:40)
                elif hour == 15 and minute == 40 and weekday < 5:
                    logger.info("Executing equity trades")
                    self.engine.execute_pending_signals("equity_mean_reversion")
                    self.engine.execute_pending_signals("equity_momentum")
                    self._sleep_minutes(1)

                # Daily report (16:00)
                elif hour == 16 and minute == 0 and weekday < 5:
                    logger.info("Generating daily report")
                    self.engine.generate_daily_report()
                    self._sleep_minutes(1)

                # Monthly BANKNIFTY (last Monday of month, 9:05)
                elif weekday == 0 and hour == 9 and minute == 5:
                    if self._is_last_week_of_month(now):
                        logger.info("Monthly BANKNIFTY strangle scan")
                        self.engine.run_options_scan("BANKNIFTY")
                    self._sleep_minutes(1)

                else:
                    # Sleep 30 seconds between checks
                    time.sleep(30)

            except Exception as e:
                logger.error("Scheduler error: %s", e, exc_info=True)
                time.sleep(60)

    def stop(self):
        self.running = False
        logger.info("Scheduler stopped")

    def _sleep_minutes(self, minutes: int):
        time.sleep(minutes * 60)

    def _sleep_until_next_weekday(self):
        """Sleep until Monday 8:50 AM."""
        now = datetime.now()
        days_ahead = 7 - now.weekday()  # days until next Monday
        next_monday = now.replace(hour=8, minute=50, second=0) + timedelta(days=days_ahead)
        sleep_seconds = (next_monday - now).total_seconds()
        if sleep_seconds > 0:
            logger.info("Weekend: sleeping until %s", next_monday)
            time.sleep(min(sleep_seconds, 3600))  # wake hourly to check

    @staticmethod
    def _is_last_week_of_month(dt: datetime) -> bool:
        next_week = dt + timedelta(days=7)
        return next_week.month != dt.month
