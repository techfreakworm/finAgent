"""Execute Agent — Places approved trades (paper or live)."""

import logging
from datetime import datetime
from uuid import uuid4
from agents.state import TradingState

logger = logging.getLogger(__name__)


def execute_node(state: TradingState) -> dict:
    """Execute approved signals. Paper mode by default."""
    from backend.db.models import (
        save_signal, save_position, log_event,
        update_account_capital, get_account, has_open_position,
    )
    from backend.api.accounts_api import get_active_account_id
    from config import config

    approved = state.get("approved", [])
    paper = state.get("paper_mode", config.paper_trading)
    executions = []

    account_id = get_active_account_id()
    acc = get_account(account_id)
    capital = acc["current_capital"] if acc else state.get("capital", config.risk.starting_capital)

    for signal in approved:
        # Skip if already holding this symbol+strategy
        if has_open_position(signal["symbol"], signal["strategy"], account_id):
            logger.info("Skipping %s — already have open position", signal["symbol"])
            continue

        margin = signal.get("margin_required", 0)

        # Check capital before opening
        if margin > capital:
            logger.info("Skipping %s — insufficient capital (need ₹%.0f, have ₹%.0f)",
                        signal["symbol"], margin, capital)
            continue

        pid = uuid4().hex[:8]
        mode = "PAPER" if paper else "LIVE"

        # Deduct margin from capital
        capital -= margin
        update_account_capital(account_id, capital)

        # Persist position to DB
        direction = "SHORT" if signal["direction"] == "SELL" else "LONG"
        save_position({
            "id": pid,
            "symbol": signal["symbol"],
            "strategy": signal["strategy"],
            "direction": direction,
            "entry_date": datetime.now().isoformat(),
            "entry_price": signal["entry_price"],
            "quantity": signal["lot_size"],
            "stop_loss": signal.get("stop_loss", 0),
            "target": signal.get("target", 0),
            "margin_required": margin,
            "metadata": signal.get("metadata", {}),
        }, account_id=account_id)

        execution = {
            "position_id": pid,
            "symbol": signal["symbol"],
            "strategy": signal["strategy"],
            "direction": signal["direction"],
            "entry_price": signal["entry_price"],
            "lot_size": signal["lot_size"],
            "margin": margin,
            "mode": mode,
            "timestamp": datetime.now().isoformat(),
        }
        executions.append(execution)

        # Persist signal
        save_signal({
            "strategy": signal["strategy"],
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "entry_price": signal["entry_price"],
            "stop_loss": signal.get("stop_loss", 0),
            "target": signal.get("target", 0),
            "lot_size": signal["lot_size"],
            "margin_required": margin,
            "confidence": signal.get("confidence", 0),
            "reasoning": signal.get("reasoning", ""),
            "metadata": signal.get("metadata", {}),
            "status": "EXECUTED",
        }, account_id=account_id)

        log_event("TRADE_OPENED",
                  f"[{mode}] {signal['direction']} {signal['symbol']} @ ₹{signal['entry_price']:.1f} | margin ₹{margin:,.0f} | capital ₹{capital:,.0f}",
                  execution, account_id=account_id)

        logger.info("[%s] Executed: %s %s @ ₹%.1f, qty=%d, margin=₹%.0f, capital=₹%.0f",
                     mode, signal["direction"], signal["symbol"],
                     signal["entry_price"], signal["lot_size"], margin, capital)

    logger.info("Execute agent: %d trades placed (%s), capital=₹%.0f",
                len(executions), "PAPER" if paper else "LIVE", capital)
    return {"executions": executions}
