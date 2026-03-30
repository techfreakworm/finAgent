"""Execute Agent — Places approved trades (paper or live)."""

import logging
from datetime import datetime
from agents.state import TradingState

logger = logging.getLogger(__name__)


def execute_node(state: TradingState) -> dict:
    """Execute approved signals. Paper mode by default."""
    from risk.position_manager import PositionManager, Position
    from backend.db.models import save_signal, log_event
    from config import config

    approved = state.get("approved", [])
    paper = state.get("paper_mode", config.paper_trading)
    executions = []

    pm = PositionManager()

    for signal in approved:
        position = Position(
            symbol=signal["symbol"],
            strategy=signal["strategy"],
            entry_date=datetime.now(),
            entry_price=signal["entry_price"],
            quantity=signal["lot_size"],
            direction=signal["direction"],
            stop_loss=signal.get("stop_loss", 0),
            metadata=signal.get("metadata", {}),
        )

        pid = pm.add_position(position)
        mode = "PAPER" if paper else "LIVE"

        execution = {
            "position_id": pid,
            "symbol": signal["symbol"],
            "strategy": signal["strategy"],
            "direction": signal["direction"],
            "entry_price": signal["entry_price"],
            "lot_size": signal["lot_size"],
            "mode": mode,
            "timestamp": datetime.now().isoformat(),
        }
        executions.append(execution)

        # Persist
        save_signal({
            "strategy": signal["strategy"],
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "entry_price": signal["entry_price"],
            "stop_loss": signal.get("stop_loss", 0),
            "target": signal.get("target", 0),
            "lot_size": signal["lot_size"],
            "margin_required": signal.get("margin_required", 0),
            "confidence": signal.get("confidence", 0),
            "reasoning": signal.get("reasoning", ""),
            "metadata": signal.get("metadata", {}),
            "status": "EXECUTED",
        })

        log_event("TRADE_EXECUTED", f"[{mode}] {signal['direction']} {signal['symbol']} @ ₹{signal['entry_price']:.1f}", execution)

        logger.info("[%s] Executed: %s %s @ ₹%.1f, qty=%d",
                     mode, signal["direction"], signal["symbol"],
                     signal["entry_price"], signal["lot_size"])

    logger.info("Execute agent: %d trades placed (%s)", len(executions), "PAPER" if paper else "LIVE")
    return {"executions": executions}
