"""Risk Gate Agent — Validates signals against risk rules."""

import logging
from agents.state import TradingState

logger = logging.getLogger(__name__)


def risk_gate_node(state: TradingState) -> dict:
    """Check each signal against margin, floor, and position limits."""
    from risk.margin_checker import MarginChecker
    from risk.floor_monitor import FloorMonitor
    from config import config

    signals = state.get("signals", [])
    capital = state.get("capital", config.risk.starting_capital)
    floor = state.get("floor", config.risk.hard_floor)

    margin_checker = MarginChecker(capital)
    floor_monitor = FloorMonitor(floor, capital)

    approved = []
    rejected = []

    if floor_monitor.is_breached:
        logger.critical("Floor already breached — rejecting all signals")
        for s in signals:
            rejected.append({**s, "rejection_reason": "Floor breached — no new trades"})
        return {"approved": approved, "rejected": rejected}

    for signal in signals:
        margin = signal.get("margin_required", 0)
        reasons = []

        # Margin check
        if not margin_checker.can_trade(margin):
            reasons.append(f"Margin ₹{margin:,.0f} exceeds available ₹{margin_checker.available_margin():,.0f}")

        # Floor proximity check
        floor_distance = capital - floor
        if floor_distance < margin * 0.3:
            reasons.append(f"Too close to floor (distance ₹{floor_distance:,.0f})")

        # Confidence threshold
        if signal.get("confidence", 0) < 0.01:
            reasons.append(f"Confidence {signal.get('confidence', 0):.2f} too low")

        if reasons:
            signal_with_reason = {**signal, "rejection_reason": "; ".join(reasons)}
            rejected.append(signal_with_reason)
            logger.info("REJECTED %s %s: %s", signal.get("direction"), signal.get("symbol"), "; ".join(reasons))
        else:
            approved.append(signal)
            logger.info("APPROVED %s %s @ ₹%.1f (margin ₹%s)",
                         signal.get("direction"), signal.get("symbol"),
                         signal.get("entry_price", 0), f"{margin:,.0f}")

    logger.info("Risk gate: %d approved, %d rejected", len(approved), len(rejected))
    return {"approved": approved, "rejected": rejected}
