"""Report Agent — Generates daily summary."""

import logging
from datetime import datetime
from agents.state import TradingState

logger = logging.getLogger(__name__)


def report_node(state: TradingState) -> dict:
    """Generate a human-readable daily trading report."""
    from config import config

    market = state.get("market_data", {})
    signals = state.get("signals", [])
    approved = state.get("approved", [])
    rejected = state.get("rejected", [])
    executions = state.get("executions", [])
    capital = state.get("capital", config.risk.starting_capital)
    floor = state.get("floor", config.risk.hard_floor)

    lines = [
        f"📊 FinAgent Daily Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"{'='*50}",
        "",
        f"💰 Capital: ₹{capital:,.0f}",
        f"🛡️  Floor: ₹{floor:,.0f} (distance: ₹{capital - floor:,.0f})",
        f"📋 Mode: {'PAPER' if state.get('paper_mode', True) else 'LIVE'}",
        "",
    ]

    # Market brief
    brief = market.get("brief", "No market data")
    lines.append(f"🌐 Market: {brief}")
    lines.append("")

    # Signals summary
    lines.append(f"📡 Signals Generated: {len(signals)}")
    lines.append(f"  ✅ Approved: {len(approved)}")
    lines.append(f"  ❌ Rejected: {len(rejected)}")
    lines.append(f"  🔄 Executed: {len(executions)}")
    lines.append("")

    # Executed trades
    if executions:
        lines.append("📈 Trades:")
        for ex in executions:
            lines.append(f"  [{ex['mode']}] {ex['direction']} {ex['symbol']} "
                         f"@ ₹{ex['entry_price']:.1f} (qty={ex['lot_size']})")
        lines.append("")

    # Rejections
    if rejected:
        lines.append("🚫 Rejected Signals:")
        for rej in rejected:
            lines.append(f"  {rej.get('symbol', '?')}: {rej.get('rejection_reason', 'unknown')}")
        lines.append("")

    lines.append(f"{'='*50}")
    template_report = "\n".join(lines)

    # Enhance with LLM commentary
    try:
        from agents.llm import ask, is_available
        if is_available():
            ai_commentary = ask(
                prompt=(
                    f"Here is today's trading activity:\n\n{template_report}\n\n"
                    f"Write a brief 3-4 sentence commentary: "
                    f"How did the day go? Any concerns about risk? "
                    f"What should the trader watch for tomorrow? Be direct and practical."
                ),
                system="You are a trading advisor writing end-of-day commentary for a retail algo trader in Indian markets. Be concise and actionable.",
                temperature=0.3,
                max_tokens=250,
            )
            report = template_report + "\n\n💬 AI Commentary:\n" + ai_commentary
        else:
            report = template_report
    except Exception as e:
        logger.warning("LLM report enhancement failed: %s", e)
        report = template_report

    logger.info("Report generated (%d chars)", len(report))
    return {"report": report}
