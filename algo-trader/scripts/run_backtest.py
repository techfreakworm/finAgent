"""First real backtest: ORB on actual 5y Dhan 1-min data (smoke + baseline).

Usage: .venv/bin/python scripts/run_backtest.py [SYMBOL] [FROM] [TO]
Defaults: RELIANCE 2025-06-01 2025-12-31  (mid-history window — NOT the
frozen holdout, which is the most recent ~9 months per ARCHITECTURE §3.6).
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from algotrader.core import Bar, Instrument, Segment  # noqa: E402
from algotrader.backtest.engine import BacktestEngine, DefaultSlippage  # noqa: E402
from algotrader.backtest.costs import DhanCosts  # noqa: E402
from algotrader.backtest import metrics  # noqa: E402
from algotrader.risk.engine import IntradayRiskEngine, RiskParams  # noqa: E402
from algotrader.strategies.orb import OpeningRangeBreakout  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
CACHE = PROJECT / "data" / "cache"


class ParquetBarSource:
    """core.BarSource over the backfilled parquet store (1-min bars)."""

    def __init__(self, instrument: Instrument, frm: date, to: date, interval: str = "1m"):
        self.instrument = instrument
        self.frm, self.to = frm, to
        files = sorted((CACHE / instrument.symbol / interval).glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"no {interval} data for {instrument.symbol}")
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
        d = df["ts"].dt.date
        self.df = df[(d >= frm) & (d <= to)].sort_values("ts").reset_index(drop=True)
        self.interval_min = 1 if interval == "1m" else 5

    def sessions(self):
        for day, chunk in self.df.groupby(self.df["ts"].dt.date):
            bars = [
                Bar(instrument=self.instrument, ts_open=row.ts.to_pydatetime(),
                    interval_min=self.interval_min, open=row.open, high=row.high,
                    low=row.low, close=row.close, volume=int(row.volume))
                for row in chunk.itertuples()
            ]
            yield day, bars


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    frm = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2025, 6, 1)
    to = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else date(2025, 12, 31)

    inst = Instrument(symbol=symbol, security_id="?", segment=Segment.NSE_EQ,
                      tick_size=0.05)
    risk = IntradayRiskEngine(RiskParams(
        capital=500_000, hard_floor=400_000, max_daily_loss_pct=0.02,
        per_trade_risk_pct=0.0075, max_open_positions=3, max_per_symbol=1))
    engine = BacktestEngine(
        strategies=[OpeningRangeBreakout(range_minutes=15, vol_confirm_mult=1.5,
                                         target_r_mult=2.0, stop="range_mid")],
        risk_engine=risk, cost_model=DhanCosts(), slippage_model=DefaultSlippage())

    t0 = datetime.now()
    result = engine.run(ParquetBarSource(inst, frm, to))
    elapsed = (datetime.now() - t0).total_seconds()

    trades = result.trades
    sessions = result.sessions
    print(f"\n=== ORB(15m) on {symbol} {frm}..{to} — 1-min bars ===")
    print(f"sessions: {len(sessions)} | trades: {len(trades)} | wall: {elapsed:.1f}s")
    if not trades:
        print("no trades generated")
        return
    pf, (lo, hi) = metrics.profit_factor(trades)
    print(f"net P&L: ₹{metrics.net_pnl(trades):,.0f}  "
          f"(gross ₹{sum(t.position.gross_pnl() for t in trades):,.0f}, "
          f"costs ₹{sum(t.costs.total for t in trades):,.0f}, "
          f"slippage ₹{sum(t.slippage_paid for t in trades):,.0f})")
    print(f"profit factor: {pf:.3f}  [95% CI {lo:.3f} – {hi if hi != float('inf') else 'inf'}]")
    print(f"win rate: {metrics.win_rate(trades):.1%} | max DD: ₹{metrics.max_drawdown(sessions):,.0f}")
    print(f"sharpe (exposure days): {metrics.sharpe_daily(sessions, exposure_days_only=True):.2f}")
    reasons = {}
    for t in trades:
        reasons[t.position.exit_reason.value] = reasons.get(t.position.exit_reason.value, 0) + 1
    print(f"exits: {reasons}")
    gaps = sum(1 for t in trades if t.position.gap_through_stop)
    print(f"gap-through-stop fills: {gaps}")


if __name__ == "__main__":
    main()
