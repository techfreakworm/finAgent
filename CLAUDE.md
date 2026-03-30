# FinAgent — AI-Powered Trading System for Indian Markets

## Project Overview
FinAgent is a data-centric AI trading system for Indian equity and derivatives markets (NSE). It combines validated trading strategies with a local LLM (Qwen3 32B via Ollama) for market analysis, a React dashboard for monitoring, and a FastAPI backend for orchestration.

**Key differentiator:** Strategies were validated with actual expired option prices from Dhan API before any production code was written. All research/validation scripts are in `research_scratchpad/`.

## Tech Stack
- **Backend:** Python 3.12, FastAPI, LangGraph (agent orchestration), SQLite
- **Frontend:** React 19, TypeScript, Vite, Tailwind CSS v4, Recharts
- **LLM:** Qwen3 32B via Ollama (local, no API costs)
- **Data:** Dhan API (options, historical), yfinance (equity, VIX)
- **Broker costs:** Zerodha model (STT, exchange, GST, stamp, DP)
- **Deployment:** Docker Compose (backend + nginx frontend)

## Architecture

```
Data Layer (Dhan API + yfinance)
    → Feature Store (~30 features: RSI, MACD, PCR, Max Pain, VIX)
    → Strategy Engine (NIFTY strangle, equity mean reversion, momentum)
    → Risk Engine (hard floor ₹4L, margin check, position sizing)
    → LangGraph Agents (research → signal → risk gate → execute → report)
    → FastAPI Backend (REST + account system)
    → React Dashboard (9 pages, mobile responsive)
```

## Project Structure

```
finAgent/
├── config.py              # Central configuration (dataclasses)
├── engine.py              # Trading engine — orchestrates strategies + risk + DB
├── scheduler.py           # Time-based scheduler for automated trading
├── main.py                # CLI entry point (--scan, --status, --report)
│
├── data/                  # Data layer
│   ├── dhan_client.py     # Dhan REST API (options, historical, positions)
│   ├── equity_data.py     # yfinance wrapper (stocks, VIX)
│   └── option_chain.py    # Max Pain, PCR, strangle strikes, IV rank
│
├── risk/                  # Risk engine
│   ├── cost_model.py      # Zerodha F&O + delivery costs (validated)
│   ├── floor_monitor.py   # Hard floor monitoring (₹4L default)
│   ├── margin_checker.py  # Pre-trade margin gate
│   └── position_manager.py # Position tracking + P&L (LONG/SHORT)
│
├── strategies/            # Trading strategies
│   ├── base.py            # Signal dataclass + BaseStrategy ABC
│   ├── nifty_strangle.py  # NIFTY weekly short strangle (primary)
│   ├── equity_mean_reversion.py  # RSI < 30 mean reversion
│   └── equity_momentum.py       # 12-month momentum rotation
│
├── agents/                # LangGraph AI agents
│   ├── llm.py             # Local Qwen3 interface via Ollama
│   ├── state.py           # TradingState TypedDict
│   ├── graph.py           # LangGraph workflow (research→signal→risk→execute→report)
│   └── nodes/             # Agent node implementations
│       ├── research.py    # Market data gathering + AI brief
│       ├── signal.py      # Strategy signal generation + LLM reasoning
│       ├── risk_gate.py   # Risk validation (margin, floor, limits)
│       ├── execute.py     # Trade execution (paper/live)
│       └── report.py      # Daily report with AI commentary
│
├── replay/                # Historical replay mode
│   ├── data_provider.py   # Pre-fetches + aligns historical data
│   └── engine.py          # Streams candles, runs strategies in simulation
│
├── backend/               # FastAPI backend
│   ├── main.py            # FastAPI app with all routers
│   ├── task_runner.py     # Background task execution
│   ├── db/models.py       # SQLite DB with account isolation
│   └── api/               # REST endpoints
│       ├── signals.py     # Signal CRUD
│       ├── trades.py      # Trade history
│       ├── portfolio.py   # Portfolio state
│       ├── risk.py        # Risk metrics
│       ├── actions.py     # Scan/workflow/report triggers
│       ├── accounts_api.py # Account system (paper/live/replay)
│       ├── settings_api.py # Settings CRUD
│       ├── performance.py # P&L analytics, XIRR, equity curve
│       ├── replay_api.py  # Replay control (prepare/play/pause/stop)
│       └── logs_api.py    # System event logs
│
├── notifications/
│   └── telegram.py        # Telegram alerts
│
├── frontend/              # React dashboard (9 pages)
│   ├── src/pages/         # Dashboard, Performance, Signals, Trades, Risk,
│   │                      # Control, Replay, Settings, Logs
│   ├── src/components/    # Layout, StatCard, ActionButton, AccountSelector
│   └── src/lib/           # api.ts, account-context.tsx
│
├── research_scratchpad/   # All validation scripts (historical reference)
│   ├── validate.py → validate_v3.py    # Equity strategy validation
│   ├── validate_derivatives*.py         # Options strategy validation
│   ├── validate_real.py                 # Actual Dhan premium validation
│   ├── simulate_*.py                    # Multi-capital, XIRR, all-instrument tests
│   ├── VALIDATION_PLAN.md              # Validation methodology
│   └── FULL_PLAN.md                    # Complete system design plan
│
├── docker-compose.yml     # Backend (port 65432) + Frontend (port 64321)
├── Dockerfile.backend     # Python 3.12 + all deps
├── Dockerfile.frontend    # Node 24 build + nginx serve
├── nginx.conf             # Proxies /api/* and /health to backend
├── requirements.txt       # Python dependencies
└── .env.example           # Environment variable template
```

## Key Design Decisions

### Account System (Paper / Live / Replay)
Every row in the DB is tagged with `account_id`. Paper and Live are permanent accounts. Each replay session creates a temporary account. The frontend filters all data by the active account — one-click switching in the sidebar.

### Strategy Validation Results
All strategies were validated BEFORE building the production system:
- **NIFTY Short Strangle:** XIRR 42-95%, 87% win rate (tested with actual Dhan expired option prices)
- **Equity Mean Reversion (RSI):** XIRR 8.5-9.7%, 58% win rate (with full Zerodha costs)
- **Equity Momentum:** CAGR 7.4% (monthly rotation)

**Known issue:** The replay engine shows worse results than validation scripts because replay checks stop-losses at hourly granularity while validation used daily-only data. See `research_scratchpad/` for the validation scripts and detailed findings.

### Risk Rules
- Hard floor: ₹4,00,000 (force-exit all positions if breached)
- Max margin utilization: 90%
- Max single trade loss: 12% of capital
- NIFTY lot size: 75 (post Nov 2024 SEBI change)

### LLM Integration
Qwen3 32B runs locally via Ollama. Used for:
- Market brief generation (research agent)
- Signal reasoning enhancement (signal agent)
- Daily report commentary (report agent)
Set `think=False` when calling Ollama to get direct answers without reasoning tokens.

## Environment Setup

```bash
# 1. Clone and setup
cp .env.example .env
# Edit .env with your Dhan API credentials

# 2. Python backend (local dev)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Frontend (local dev)
cd frontend && nvm use v24 && npm install && cd ..

# 4. Ollama (optional — for AI features)
# Install from https://ollama.com/download
ollama pull qwen3:32b

# 5. Docker (production)
docker compose build
docker compose up -d
# Frontend: http://localhost:64321
# Backend API: http://localhost:65432
```

## Running

```bash
# One-time scan
python main.py --scan

# Full AI workflow
python -c "from agents.graph import run_workflow; run_workflow()"

# Start scheduler (automated trading)
python main.py

# API server only
uvicorn backend.main:app --port 8000

# Docker
docker compose up -d
```

## API Endpoints
All under `/api/` prefix. Most accept `?account=paper|live|replay_xxx` param.

- `GET /health` — System health + LLM status
- `GET /api/portfolio` — Capital, P&L, positions
- `GET /api/signals` — Trade signals
- `GET /api/trades` — Trade history
- `GET /api/risk` — Risk metrics
- `GET /api/performance` — P&L analytics, XIRR
- `GET /api/accounts` — List accounts
- `POST /api/actions/scan` — Trigger market scan
- `POST /api/actions/workflow` — Run AI workflow
- `POST /api/replay/prepare` — Start replay data fetch
- `GET /api/settings` — Current settings

## Current Status & Known Issues

### Working
- All 9 dashboard pages (mobile responsive)
- Account system (paper/live/replay isolation)
- NIFTY option chain fetching via Dhan API
- Equity RSI scanning (found real HDFCBANK signal)
- AI workflow with Qwen3 32B market briefs
- Full F&O cost model (Zerodha)
- Docker deployment

### Known Issues
1. **Replay backtesting shows worse results than validation scripts** — hourly stop-loss checking vs daily-only in validation. Needs proper reconciliation.
2. **NIFTY strangle in replay** generates few trades — option data alignment between Dhan rollingoption and yfinance timestamps can have gaps.
3. **Frontend replay page** — events polling can miss fast replays (50x speed). Status polling fallback added but not perfect.

### TODO
- Proper backtesting engine that matches validation methodology exactly
- Walk-forward optimization in replay mode
- Iron Condor strategy (defined risk alternative to strangle)
- FII/DII data integration (actual NSE data, not proxy)
- WebSocket for real-time replay streaming (replace polling)
- Telegram bot integration testing
