"""LangGraph shared state for the trading agent workflow."""

from typing import TypedDict, Annotated
from operator import add


class TradingState(TypedDict, total=False):
    """State passed between agent nodes in the LangGraph workflow."""

    # Research node output
    market_data: dict          # option chain, VIX, spot, equity data

    # Signal node output
    signals: Annotated[list, add]  # generated trade signals (appended across nodes)

    # Risk gate output
    approved: list             # signals that passed risk checks
    rejected: list             # signals that failed with reasons

    # Execution output
    executions: list           # trades that were executed

    # Report output
    report: str                # LLM-generated daily summary

    # Shared context
    capital: float
    floor: float
    paper_mode: bool
    timestamp: str
    error: str
