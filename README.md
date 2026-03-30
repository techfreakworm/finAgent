# FinAgent

AI-powered trading system for Indian equity and derivatives markets (NSE). Combines validated trading strategies with a local LLM for market analysis, a React dashboard for monitoring, and automated risk management.

## Features

- **3 Validated Strategies** — NIFTY short strangle (options), RSI mean reversion (equity), momentum rotation
- **Local AI** — Qwen3 32B via Ollama for market briefs, signal reasoning, daily reports
- **9-Page Dashboard** — Dashboard, Performance, Signals, Trades, Risk, Control Panel, Replay, Settings, Logs
- **Account Isolation** — Separate Paper / Live / Replay accounts with isolated P&L
- **Historical Replay** — Stream historical data and watch strategies execute in real-time
- **Full Cost Model** — Zerodha F&O costs (STT, exchange, GST, stamp, DP charges, slippage)
- **Risk Engine** — Hard floor monitoring, margin checking, circuit breakers
- **Mobile Responsive** — Hamburger menu, stacked cards, scrollable tables

## Quick Start

### Prerequisites
- Python 3.12+
- Node.js 24+ (via nvm)
- Docker & Docker Compose
- [Dhan](https://dhan.co) trading account (free API access)
- [Ollama](https://ollama.com/download) (optional — for AI features)

### Setup

```bash
# Clone
git clone git@github.com:techfreakworm/finAgent.git
cd finAgent

# Configure
cp .env.example .env
# Edit .env with your Dhan API credentials

# Option A: Docker (recommended)
docker compose build
docker compose up -d
# Dashboard: http://localhost:64321
# API: http://localhost:65432

# Option B: Local development
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd frontend && nvm use v24 && npm install && cd ..

# Start backend
uvicorn backend.main:app --port 8000

# Start frontend (separate terminal)
cd frontend && npm run dev -- --port 3000
```

### Optional: Local LLM

```bash
# Install Ollama from https://ollama.com/download
ollama pull qwen3:32b
# The system auto-detects Ollama and uses it for AI features
```

## Usage

### From the Dashboard (no terminal needed)
1. Open **http://localhost:64321** (Docker) or **http://localhost:3000** (dev)
2. Click **"Scan Markets"** — finds trade signals from live market data
3. Click **"Run AI Workflow"** — full AI pipeline with market analysis
4. Go to **Signals** — approve or reject pending signals
5. Go to **Replay** — test strategies on historical data
6. Go to **Settings** — configure strategy parameters

### From CLI
```bash
python main.py --scan      # One-time market scan
python main.py --status    # System status
python main.py --report    # Generate daily report
python main.py             # Start automated scheduler
```

## Architecture

```
Dhan API + yfinance → Feature Store (RSI, MACD, PCR, Max Pain, VIX)
    → Strategy Engine (strangle, mean reversion, momentum)
    → Risk Engine (floor ₹4L, margin gate, position sizing)
    → LangGraph Agents (research → signal → risk → execute → report)
    → FastAPI Backend (REST API + account system)
    → React Dashboard (9 pages, mobile responsive)
```

## Validated Strategy Performance

All strategies validated with real market data and full transaction costs before production code was written.

| Strategy | XIRR | Win Rate | Data Source |
|----------|------|----------|-------------|
| NIFTY Short Strangle | +42% to +95% | 87% | Actual Dhan expired option prices |
| Equity RSI Mean Reversion | +8.5% to +9.7% | 58% | yfinance + Zerodha costs |
| Equity Momentum | ~7% CAGR | 50% | yfinance monthly |

See `research_scratchpad/` for all validation scripts and detailed findings.

## Project Structure

```
finAgent/
├── config.py              # Central configuration
├── engine.py              # Trading engine orchestrator
├── data/                  # Dhan API + yfinance wrappers
├── risk/                  # Cost model, floor monitor, margin checker
├── strategies/            # NIFTY strangle, equity MR, momentum
├── agents/                # LangGraph AI workflow + Ollama LLM
├── replay/                # Historical replay engine
├── backend/               # FastAPI + SQLite + account system
├── frontend/              # React 19 + Tailwind + Recharts
├── research_scratchpad/   # Validation scripts (historical reference)
├── docker-compose.yml     # Docker deployment
└── CLAUDE.md              # Detailed guide for AI assistants
```

## Configuration

All settings are configurable via the **Settings** page or `.env` file:

| Setting | Default | Description |
|---------|---------|-------------|
| Starting Capital | ₹5,00,000 | Initial trading capital |
| Hard Floor | ₹4,00,000 | Force-exit all positions if breached |
| NIFTY Min VIX | 12.0 | Minimum VIX for strangle entry |
| RSI Entry | 30 | Buy when RSI drops below this |
| RSI Exit | 50 | Sell when RSI recovers above this |
| Stop Loss | 5% | Max loss per equity trade |
| Paper Mode | true | Always starts in paper mode |

## API

All endpoints under `/api/` prefix. See `CLAUDE.md` for full list.

```bash
curl http://localhost:65432/health
curl http://localhost:65432/api/portfolio
curl http://localhost:65432/api/signals
curl -X POST http://localhost:65432/api/actions/scan
```

## License

MIT
