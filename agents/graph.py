"""LangGraph workflow — Research → Signal → Risk Gate → Execute → Report."""

import logging
from langgraph.graph import StateGraph, START, END
from agents.state import TradingState
from agents.nodes.research import research_node
from agents.nodes.signal import signal_node
from agents.nodes.risk_gate import risk_gate_node
from agents.nodes.execute import execute_node
from agents.nodes.report import report_node

logger = logging.getLogger(__name__)


def build_graph() -> StateGraph:
    """Build and compile the trading agent workflow graph."""

    graph = StateGraph(TradingState)

    # Add nodes
    graph.add_node("research", research_node)
    graph.add_node("signal", signal_node)
    graph.add_node("risk_gate", risk_gate_node)
    graph.add_node("execute", execute_node)
    graph.add_node("report", report_node)

    # Define edges: sequential flow
    graph.add_edge(START, "research")
    graph.add_edge("research", "signal")
    graph.add_edge("signal", "risk_gate")
    graph.add_edge("risk_gate", "execute")
    graph.add_edge("execute", "report")
    graph.add_edge("report", END)

    compiled = graph.compile()
    logger.info("Trading agent graph compiled: research → signal → risk_gate → execute → report")
    return compiled


def run_workflow(capital: float = None, paper_mode: bool = True) -> dict:
    """Run the full trading workflow and return final state."""
    from config import config
    from datetime import datetime

    if capital is None:
        capital = config.risk.starting_capital

    initial_state = {
        "capital": capital,
        "floor": config.risk.hard_floor,
        "paper_mode": paper_mode,
        "timestamp": datetime.now().isoformat(),
        "signals": [],
    }

    graph = build_graph()
    result = graph.invoke(initial_state)

    # Print report
    report = result.get("report", "")
    if report:
        print(report)

    return result
