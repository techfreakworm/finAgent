"""Telegram notifications for FinAgent."""

import logging
import requests
from config import config

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Send trade alerts and reports via Telegram bot."""

    def __init__(self):
        self.token = config.telegram_token
        self.chat_id = config.telegram_chat_id
        self.enabled = bool(self.token and self.chat_id)

        if not self.enabled:
            logger.info("Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env)")

    def _send(self, text: str):
        """Send a message via Telegram Bot API."""
        if not self.enabled:
            return

        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=10)

            if resp.status_code != 200:
                logger.error("Telegram send failed: %s", resp.text[:200])
        except Exception as e:
            logger.error("Telegram error: %s", e)

    def send_signal(self, signal: dict):
        """Notify about a new trade signal."""
        text = (
            f"🔔 <b>New Signal</b>\n"
            f"{signal.get('direction', '?')} {signal.get('symbol', '?')}\n"
            f"Strategy: {signal.get('strategy', '?')}\n"
            f"Price: ₹{signal.get('entry_price', 0):.1f}\n"
            f"Margin: ₹{signal.get('margin_required', 0):,.0f}\n"
            f"Confidence: {signal.get('confidence', 0):.0%}\n"
            f"\n{signal.get('reasoning', '')[:200]}"
        )
        self._send(text)

    def send_trade(self, trade: dict):
        """Notify about an executed trade."""
        mode = trade.get("mode", "PAPER")
        text = (
            f"✅ <b>Trade Executed [{mode}]</b>\n"
            f"{trade.get('direction', '?')} {trade.get('symbol', '?')}\n"
            f"@ ₹{trade.get('entry_price', 0):.1f} × {trade.get('lot_size', 0)}\n"
            f"Strategy: {trade.get('strategy', '?')}"
        )
        self._send(text)

    def send_daily_report(self, report: str):
        """Send the daily summary report."""
        # Telegram has 4096 char limit
        if len(report) > 4000:
            report = report[:4000] + "\n... (truncated)"
        self._send(report)

    def send_floor_warning(self, capital: float, floor: float):
        """Critical alert when approaching or breaching floor."""
        distance = capital - floor
        text = (
            f"🚨 <b>FLOOR WARNING</b>\n\n"
            f"Capital: ₹{capital:,.0f}\n"
            f"Floor: ₹{floor:,.0f}\n"
            f"Distance: ₹{distance:,.0f}\n\n"
            f"{'⛔ FLOOR BREACHED — All positions exited!' if distance <= 0 else '⚠️ Approaching floor — reduce risk!'}"
        )
        self._send(text)

    def send_trade_closed(self, trade: dict):
        """Notify about a closed trade with P&L."""
        pnl = trade.get("pnl_net", 0)
        emoji = "💚" if pnl > 0 else "🔴"
        text = (
            f"{emoji} <b>Trade Closed</b>\n"
            f"{trade.get('symbol', '?')} — {trade.get('exit_reason', '?')}\n"
            f"P&L: ₹{pnl:+,.0f}\n"
            f"Entry: ₹{trade.get('entry_price', 0):.1f} → Exit: ₹{trade.get('exit_price', 0):.1f}"
        )
        self._send(text)
