"""Signal Agent — Runs strategies and generates trade signals."""

import logging
from datetime import datetime
from agents.state import TradingState

logger = logging.getLogger(__name__)


def signal_node(state: TradingState) -> dict:
    """Run all strategies against market data and produce signals."""
    from strategies.nifty_strangle import NiftyStrangle
    from strategies.equity_mean_reversion import EquityMeanReversion
    from config import config

    market_data = state.get("market_data", {})
    capital = state.get("capital", config.risk.starting_capital)
    now = datetime.now()

    signals = []

    # NIFTY strangle
    try:
        strangle = NiftyStrangle()
        strangle_data = {
            "option_chain": market_data.get("option_chain"),
            "vix": market_data.get("vix", 0),
            "spot": market_data.get("spot", 0),
            "current_date": now,
            "open_positions": [],  # TODO: pass from state
        }
        strangle_signals = strangle.generate_signals(strangle_data)
        for s in strangle_signals:
            signals.append({
                "strategy": s.strategy,
                "symbol": s.symbol,
                "direction": s.direction,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "lot_size": s.lot_size,
                "margin_required": s.margin_required,
                "confidence": s.confidence,
                "reasoning": s.reasoning,
                "metadata": s.metadata,
            })
    except Exception as e:
        logger.error("Strangle signal generation failed: %s", e)

    # Equity mean reversion
    try:
        mr = EquityMeanReversion()
        mr_data = {
            "stock_prices": market_data.get("stock_prices", {}),
            "capital": capital,
            "current_date": now,
        }
        mr_signals = mr.generate_signals(mr_data)
        for s in mr_signals:
            signals.append({
                "strategy": s.strategy,
                "symbol": s.symbol,
                "direction": s.direction,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "lot_size": s.lot_size,
                "margin_required": s.margin_required,
                "confidence": s.confidence,
                "reasoning": s.reasoning,
                "metadata": s.metadata,
            })
    except Exception as e:
        logger.error("Mean reversion signal generation failed: %s", e)

    # Enhance signal reasoning with LLM
    if signals:
        try:
            from agents.llm import ask, is_available
            if is_available():
                brief = state.get("market_data", {}).get("brief", "")
                for sig in signals:
                    enhanced = ask(
                        prompt=(
                            f"Market context: {brief}\n\n"
                            f"Signal: {sig['direction']} {sig['symbol']} at ₹{sig['entry_price']:.1f}\n"
                            f"Strategy: {sig['strategy']}\n"
                            f"Original reasoning: {sig['reasoning']}\n\n"
                            f"Enhance this reasoning with market context. "
                            f"Should the trader take this trade? Any risks to watch? 2-3 sentences max."
                        ),
                        system="You are a trading assistant. Enhance trade signal reasoning with actionable context. Be concise.",
                        temperature=0.2,
                        max_tokens=200,
                    )
                    sig["reasoning"] = enhanced
                    sig["llm_enhanced"] = True
        except Exception as e:
            logger.warning("LLM signal enhancement failed: %s", e)

    logger.info("Signal agent: generated %d signals", len(signals))
    return {"signals": signals}
