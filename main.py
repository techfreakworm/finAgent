"""
FinAgent — Main Entry Point
============================
Usage:
    python main.py              # Start scheduler (production mode)
    python main.py --paper      # Paper trading mode (default)
    python main.py --scan       # Run one-time scan and exit
    python main.py --report     # Generate report and exit
    python main.py --status     # Show current status and exit
"""

import argparse
import logging
import sys
from config import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("finagent.log"),
    ]
)
logger = logging.getLogger("finagent")


def run_scheduler():
    """Start the trading scheduler."""
    from engine import TradingEngine
    from scheduler import TradingScheduler

    engine = TradingEngine()
    scheduler = TradingScheduler(engine)

    logger.info("Starting FinAgent scheduler...")
    logger.info("Capital: ₹%s | Floor: ₹%s | Paper: %s",
                f"{config.risk.starting_capital:,.0f}",
                f"{config.risk.hard_floor:,.0f}",
                config.paper_trading)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.stop()


def run_scan():
    """Run a one-time market scan."""
    from engine import TradingEngine

    engine = TradingEngine()

    logger.info("Running one-time scan...")
    engine.run_options_scan("NIFTY")
    engine.run_equity_scan()

    # Show signals
    for signal in engine.pending_signals:
        print(f"  {signal.direction:5} {signal.symbol:20} @ ₹{signal.entry_price:>8.1f} "
              f"| margin ₹{signal.margin_required:>10,.0f} | {signal.reasoning}")

    if not engine.pending_signals:
        print("  No signals generated.")


def run_report():
    """Generate and display current report."""
    from engine import TradingEngine

    engine = TradingEngine()
    report = engine.generate_daily_report()

    print(f"\n  FinAgent Daily Report")
    print(f"  {'='*40}")
    print(f"  Capital:      ₹{report['capital']:>12,.0f}")
    print(f"  Cumulative:   ₹{report['cumulative_pnl']:>+12,.0f}")
    print(f"  Open pos:     {report['n_open_positions']:>12}")
    print(f"  Floor dist:   ₹{report['floor_distance']:>12,.0f}")
    print(f"  Paper mode:   {config.paper_trading}")


def show_status():
    """Show current system status."""
    from backend.db.models import init_db, get_strategy_summary, get_daily_pnl_history

    init_db()
    summary = get_strategy_summary()
    history = get_daily_pnl_history(5)

    print(f"\n  FinAgent Status")
    print(f"  {'='*50}")
    print(f"  Paper mode: {config.paper_trading}")
    print(f"  Starting capital: ₹{config.risk.starting_capital:,.0f}")
    print(f"  Hard floor: ₹{config.risk.hard_floor:,.0f}")

    if summary:
        print(f"\n  Strategy Performance:")
        for name, stats in summary.items():
            print(f"    {name}: {stats['total_trades']} trades, "
                  f"win rate {stats['win_rate']:.0%}, "
                  f"total P&L ₹{stats['total_pnl']:,.0f}")
    else:
        print(f"\n  No trades recorded yet.")

    if history:
        print(f"\n  Recent Daily P&L:")
        for d in history:
            print(f"    {d['date']}: Capital ₹{d['capital']:,.0f}, P&L ₹{d['cumulative_pnl']:+,.0f}")


def main():
    parser = argparse.ArgumentParser(description="FinAgent Trading System")
    parser.add_argument("--paper", action="store_true", default=True, help="Paper trading mode")
    parser.add_argument("--live", action="store_true", help="Live trading (requires explicit flag)")
    parser.add_argument("--scan", action="store_true", help="Run one-time scan")
    parser.add_argument("--report", action="store_true", help="Generate report")
    parser.add_argument("--status", action="store_true", help="Show status")

    args = parser.parse_args()

    if args.live:
        config.paper_trading = False
        logger.warning("LIVE TRADING MODE ENABLED")

    if args.scan:
        run_scan()
    elif args.report:
        run_report()
    elif args.status:
        show_status()
    else:
        run_scheduler()


if __name__ == "__main__":
    main()
