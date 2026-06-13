# algo-trader — intraday algorithmic trading on DhanHQ (NSE)

Standing project for the operator's Dhan demat account. **Intraday only** — every
position is opened and closed within the same session (square-off by 15:20 IST).

## Hard safety rule

**No live / real-money orders, ever, without explicit per-action operator
approval.** Default mode is data + backtest + paper trading. Live order paths are
additionally gated by `ALGOTRADER_LIVE_ENABLE` (see `.env.example`) and do not
exist in the codebase until the operator authorizes that phase.

## Layout

```
algotrader/
├── config.py      # IST session times, risk params, safety gate, Dhan creds (env)
├── data/          # DhanHQ client: instrument master, intraday candles, live feed, cache
├── backtest/      # event-driven intraday bar replay, Dhan cost model, metrics, walk-forward
├── strategies/    # Strategy interface + intraday strategies (ORB, VWAP reversion, …)
├── risk/          # position sizing, stops, max-daily-loss circuit breaker
├── paper/         # paper-trading executor on live data
└── reports/       # daily EOD P&L (per-strategy + aggregate) → soma-publish
scripts/           # fetch_data / run_backtest / eod_report entrypoints
tests/
```

## Provenance

Supersedes `/home/ubuntu/finAgent` (techfreakworm/finAgent) for the intraday
mission: that system's validated strategies were weekly/positional and its replay
engine was hourly-granularity at best. Useful parts (Dhan client patterns, cost
modeling approach, prior negative results) are reused per the assessment in
`docs/finagent-assessment.md`.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill Dhan creds (never commit)
.venv/bin/pytest
```
