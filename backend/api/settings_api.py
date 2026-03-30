"""Settings API — Read/write system configuration."""

import json
import logging
from pathlib import Path
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])

SETTINGS_FILE = Path(__file__).parent.parent.parent / "settings.json"


def _load_settings() -> dict:
    """Load settings from JSON file, or return defaults from config."""
    from config import config
    defaults = {
        "paper_mode": config.paper_trading,
        "starting_capital": config.risk.starting_capital,
        "hard_floor": config.risk.hard_floor,
        "max_single_trade_loss_pct": config.risk.max_single_trade_loss_pct,
        "max_consecutive_losers": config.risk.max_consecutive_losers,
        "nifty_enabled": True,
        "nifty_min_vix": config.nifty.min_vix_for_entry,
        "nifty_strangle_offset": config.nifty.strangle_offset,
        "equity_mr_enabled": True,
        "equity_rsi_entry": config.equity.rsi_entry,
        "equity_rsi_exit": config.equity.rsi_exit,
        "equity_stop_loss_pct": abs(config.equity.stop_loss_pct) * 100,
        "equity_max_hold_days": config.equity.max_hold_days,
        "equity_max_position_pct": config.equity.max_position_pct * 100,
        "momentum_enabled": True,
        "momentum_lookback": config.equity.momentum_lookback,
        "momentum_top_n": config.equity.momentum_top_n,
        "dhan_client_id": config.dhan.client_id[:4] + "****" if config.dhan.client_id else "",
        "dhan_connected": bool(config.dhan.access_token),
        "telegram_configured": bool(config.telegram_token),
    }
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
            defaults.update(saved)
        except Exception as e:
            logger.error("Failed to load settings.json: %s", e)
    return defaults


def _save_settings(settings: dict):
    """Save settings to JSON and update in-memory config."""
    from config import config

    # Update in-memory config
    if "paper_mode" in settings:
        config.paper_trading = settings["paper_mode"]
    if "starting_capital" in settings:
        config.risk.starting_capital = settings["starting_capital"]
    if "hard_floor" in settings:
        config.risk.hard_floor = settings["hard_floor"]
    if "nifty_min_vix" in settings:
        config.nifty.min_vix_for_entry = settings["nifty_min_vix"]
    if "nifty_strangle_offset" in settings:
        config.nifty.strangle_offset = settings["nifty_strangle_offset"]
    if "equity_rsi_entry" in settings:
        config.equity.rsi_entry = settings["equity_rsi_entry"]
    if "equity_rsi_exit" in settings:
        config.equity.rsi_exit = settings["equity_rsi_exit"]
    if "equity_stop_loss_pct" in settings:
        config.equity.stop_loss_pct = -abs(settings["equity_stop_loss_pct"]) / 100
    if "equity_max_hold_days" in settings:
        config.equity.max_hold_days = settings["equity_max_hold_days"]
    if "equity_max_position_pct" in settings:
        config.equity.max_position_pct = settings["equity_max_position_pct"] / 100
    if "momentum_lookback" in settings:
        config.equity.momentum_lookback = settings["momentum_lookback"]
    if "momentum_top_n" in settings:
        config.equity.momentum_top_n = settings["momentum_top_n"]

    # Persist to file (exclude sensitive fields)
    safe = {k: v for k, v in settings.items() if "token" not in k.lower() and "secret" not in k.lower()}
    SETTINGS_FILE.write_text(json.dumps(safe, indent=2))
    logger.info("Settings saved")


@router.get("/settings")
def get_settings():
    return _load_settings()


@router.put("/settings")
def update_settings(settings: dict):
    _save_settings(settings)
    return {"message": "Settings saved", "settings": _load_settings()}


@router.post("/settings/test-dhan")
def test_dhan_connection():
    """Test Dhan API connectivity."""
    try:
        from data.dhan_client import DhanClient
        client = DhanClient()
        expiries = client.get_expiry_list(13, "IDX_I")
        if expiries:
            return {"status": "connected", "message": f"OK — {len(expiries)} NIFTY expiries found"}
        return {"status": "error", "message": "Connected but no data returned"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/settings/test-telegram")
def test_telegram():
    """Send a test Telegram message."""
    try:
        from notifications.telegram import TelegramNotifier
        notifier = TelegramNotifier()
        if not notifier.enabled:
            return {"status": "error", "message": "Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env)"}
        notifier._send("🧪 FinAgent test message — your bot is connected!")
        return {"status": "sent", "message": "Test message sent"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/settings/llm-status")
def llm_status():
    """Check if local LLM is available."""
    try:
        from agents.llm import is_available, MODEL
        available = is_available()
        return {"available": available, "model": MODEL, "provider": "Ollama (local)"}
    except Exception as e:
        return {"available": False, "error": str(e)}
