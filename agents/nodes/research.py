"""Research Agent — Gathers market data and synthesizes a brief."""

import logging
from datetime import datetime
from agents.state import TradingState

logger = logging.getLogger(__name__)


def research_node(state: TradingState) -> dict:
    """
    Fetch option chain, VIX, equity data.
    Compute Max Pain, PCR, and produce a market snapshot.
    """
    from data.dhan_client import DhanClient
    from data.equity_data import EquityData
    from data.option_chain import compute_max_pain, compute_pcr
    from config import config

    logger.info("Research agent: gathering market data")

    dhan = DhanClient()
    equity = EquityData()
    market_data = {"timestamp": datetime.now().isoformat()}

    # NIFTY option chain
    try:
        expiries = dhan.get_expiry_list(config.nifty.security_id, "IDX_I")
        if expiries:
            chain = dhan.get_option_chain(config.nifty.security_id, "IDX_I", expiries[0])
            if chain:
                inner = chain.get("data", {}).get("data", {})
                spot = inner.get("last_price", 0)
                max_pain = compute_max_pain(chain)
                pcr = compute_pcr(chain)

                market_data["option_chain"] = chain
                market_data["spot"] = spot
                market_data["max_pain"] = max_pain
                market_data["pcr"] = pcr
                market_data["expiry"] = expiries[0]

                logger.info("NIFTY spot=%.1f, MaxPain=%.0f, PCR=%.2f", spot, max_pain, pcr["pcr_oi"])
    except Exception as e:
        logger.error("Option chain fetch failed: %s", e)

    # India VIX
    try:
        from datetime import timedelta
        vix_from = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        vix_df = equity.get_india_vix(from_date=vix_from)
        if vix_df is not None and len(vix_df) > 0:
            vix = float(vix_df["Close"].iloc[-1])
            market_data["vix"] = vix
            logger.info("India VIX=%.1f", vix)
        else:
            market_data["vix"] = 14.0
    except Exception as e:
        logger.error("VIX fetch failed: %s", e)
        market_data["vix"] = 14.0

    # Equity data for RSI scanning
    try:
        from datetime import timedelta
        eq_from = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        stock_data = equity.get_nifty_universe(from_date=eq_from)
        market_data["stock_prices"] = stock_data
        logger.info("Loaded %d stocks for equity scan", len(stock_data))
    except Exception as e:
        logger.error("Equity data fetch failed: %s", e)
        market_data["stock_prices"] = {}

    # Build brief — use LLM if available, else fallback to template
    raw_brief = (
        f"NIFTY spot: {market_data.get('spot', 'N/A')}, "
        f"India VIX: {market_data.get('vix', 'N/A')}, "
        f"Max Pain: {market_data.get('max_pain', 'N/A')}, "
        f"PCR (OI): {market_data.get('pcr', {}).get('pcr_oi', 'N/A')}, "
        f"Total CE OI: {market_data.get('pcr', {}).get('total_ce_oi', 'N/A')}, "
        f"Total PE OI: {market_data.get('pcr', {}).get('total_pe_oi', 'N/A')}, "
        f"Nearest expiry: {market_data.get('expiry', 'N/A')}, "
        f"Stocks scanned: {len(market_data.get('stock_prices', {}))}"
    )

    try:
        from agents.llm import ask, is_available
        if is_available():
            ai_brief = ask(
                prompt=raw_brief,
                system=(
                    "You are a concise Indian stock market analyst. "
                    "Given today's NIFTY data, write a 3-4 sentence market brief. "
                    "Comment on: trend direction, VIX level (is it high/low historically?), "
                    "what PCR and Max Pain suggest for the week, and any actionable insight. "
                    "Be direct, no fluff. Use INR."
                ),
                temperature=0.2,
                max_tokens=300,
            )
            market_data["brief"] = ai_brief
            market_data["llm_used"] = True
            logger.info("AI market brief generated")
        else:
            market_data["brief"] = raw_brief
            market_data["llm_used"] = False
    except Exception as e:
        logger.warning("LLM brief failed, using template: %s", e)
        market_data["brief"] = raw_brief
        market_data["llm_used"] = False

    logger.info("Market brief: %s", market_data["brief"][:200])

    return {"market_data": market_data}
