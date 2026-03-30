# FinAgent v3 — Validated, Data-Centric AI Trading System

## Context

This plan is **refined from 6 rounds of validation** using real market data:
- **v1**: Index-level ML failed (NIFTY too efficient for direction prediction)
- **v2**: Individual stock strategies passed (momentum, mean-reversion, EMA trend)
- **v3**: All 3 equity strategies survived real Zerodha costs (STT, exchange, GST, slippage)
- **Derivatives v1**: Simulated premiums showed 91% win rate — but were 3x overstated
- **Derivatives v2**: Re-validated with **actual expired option prices from Dhan API** — still profitable, but honest numbers
- **Multi-instrument**: Tested NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, ITC, PNB — only **NIFTY and BANKNIFTY are profitable**
- **Multi-capital**: Tested ₹50K–₹5L — options impossible below ₹1L, equity works at any level

### Validated Numbers (Real Premiums, All Costs)

| Strategy | XIRR | Win% | Max DD | Floor Breach? | Min Capital |
|----------|------|------|--------|---------------|-------------|
| **NIFTY Short Strangle** | +42% to +95% | 87% | -7% to -10% | Never | ₹2L |
| **BANKNIFTY Short Strangle** | +30% | 87% | ~-8% | Never | ₹3L |
| **Equity RSI Mean Reversion** | +8.5% to +9.7% | 58% | <-5% | Never | ₹50K |
| **Equity Momentum (12mo)** | ~+7% CAGR | 50% | -23% | Never | ₹50K |
| FINNIFTY Strangle | -30% | — | — | YES | — |
| MIDCPNIFTY Strangle | -34% | — | — | YES | — |
| Stock Options (ITC, PNB) | -13% to -83% | — | — | YES | — |
| NIFTY Index ML (XGBoost) | Negative | 56% | — | — | — |

### What DOESN'T Work (Killed by Validation)
- Predicting NIFTY index direction with ML (too efficient)
- Options selling on FINNIFTY, MIDCPNIFTY, individual stocks (loses money)
- VIX regime filter in sustained low-vol periods (VIX averaged 13.5)
- Volume-based FII/DII proxy (zero correlation — need actual NSE data)
- Any F&O strategy at ₹50K capital (margin blocks everything)

### Hard Constraints
- **Capital**: ₹5,00,000
- **Hard floor**: ₹4,00,000 (exit all positions immediately if breached, no new trades)
- **Brokers**: Dhan (data API), Zerodha (execution + cost model)
- **Post-SEBI Nov 2024**: NIFTY lot=75, BANKNIFTY lot=30, weekly options only on NIFTY

---

## Architecture (Simplified from v2)

```
┌──────────────────────────────────────────────────────────────┐
│                     DATA LAYER                                │
│  Dhan API (options, historical) + yfinance (equity, VIX)     │
│  TimescaleDB + Redis                                         │
│  Price | NIFTY/BN Options | FII/DII | Sentiment | Macro     │
└───────────────────────┬──────────────────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────────────────┐
│                  FEATURE STORE (~30 features)                 │
│  Technical (RSI, EMA, MACD, ATR, BB, vol)                    │
│  Options (PCR, Max Pain, IV rank, FII LR)                    │
│  Macro (VIX level, VIX change, market breadth)               │
└───────────────────────┬──────────────────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────────────────┐
│                  STRATEGY ENGINE                              │
│  Tier 1: NIFTY Short Strangle (primary — XIRR 42-95%)       │
│  Tier 2: BANKNIFTY Short Strangle (secondary — XIRR 30%)    │
│  Tier 3: Equity RSI Mean Reversion (starter — XIRR 8.5%)    │
│  Tier 4: Equity Momentum (supplementary — CAGR 7%)          │
└───────────────────────┬──────────────────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────────────────┐
│              RISK ENGINE (Hard Floor = ₹4L)                   │
│  Intra-week MTM monitoring → forced exit on breach           │
│  Margin check before every trade                             │
│  Zerodha cost model (STT, exchange, GST, stamp, DP, slip)    │
└───────────────────────┬──────────────────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────────────────┐
│              AI AGENTS (LangGraph)                            │
│  Research → Signal → Risk Gate → Execute → Report            │
└───────────────────────┬──────────────────────────────────────┘
                        ▼
┌──────────────────────────────────────────────────────────────┐
│              DASHBOARD + ALERTS (React + Telegram)            │
└──────────────────────────────────────────────────────────────┘
```

**Key simplification from v2**: Dropped 6-layer data lake down to what's actually proven. Dropped 80-100 features to ~30 validated ones. Dropped non-performing instruments.

---

## PART 1: VALIDATED STRATEGIES (in priority order)

### Strategy 1: NIFTY Weekly Short Strangle (PRIMARY)

**Validated XIRR: +42% to +95% depending on capital**
**Data source: Actual expired option prices from Dhan rollingoption API**

**Mechanics:**
- Entry: Monday open — sell ATM+2 CE and ATM-2 PE (1 SD OTM strangle)
- Exit: Thursday expiry (options expire worthless or close at intrinsic)
- Lot size: 75 (post Nov 2024)
- Margin per trade: ~₹2.1L
- Minimum capital: ₹2L (to take most trades), ideal: ₹3L+

**Entry filters (from validated features):**
- VIX > 12 (premium worth collecting)
- Margin < 90% of available capital
- No existing open position

**Exit rules:**
- Primary: Hold to weekly expiry
- Hard floor exit: If intra-week MTM pushes capital below ₹4L, exit immediately at market with 2x slippage
- Emergency: 2x premium received as stop-loss (if short CE premium doubles, exit that leg)

**Validated performance (2 years, 109 trades):**
- Win rate: 87.2%
- Avg win: ₹7,946 | Avg loss: ₹-15,740
- Worst single trade: ₹-35,817 (-7.2% of ₹5L)
- Max drawdown: -7.3%
- Sharpe: 2.88
- Profit factor: 3.43
- Total costs: ₹14,893 + ₹25,100 slippage = 0.55% of premium
- Profitable months: 19/25

### Strategy 2: BANKNIFTY Monthly Short Strangle (SECONDARY)

**Validated XIRR: +30% at ₹5L capital**

**Mechanics:**
- Same as NIFTY but monthly expiry (weekly discontinued post SEBI Nov 2024)
- Lot size: 30 (post Nov 2024)
- Margin per trade: ~₹2.0L
- Minimum capital: ₹3L

**Why secondary:**
- Lower XIRR than NIFTY (30% vs 42-95%)
- Monthly expiry = fewer trades = more capital sitting idle
- But adds diversification — different behavior from NIFTY

### Strategy 3: Equity RSI Mean Reversion (STARTER / LOW-CAPITAL)

**Validated XIRR: +8.5% to +9.7%**

**Mechanics:**
- Universe: NIFTY 50 stocks (top 20 by liquidity)
- Entry: RSI(14) < 30 (oversold)
- Exit: RSI > 50 (mean reversion) OR -5% stop OR 20-day timeout
- Position size: 20% of capital per trade
- Trade type: Delivery (CNC) — ₹0 brokerage on Zerodha

**Validated performance (5 years, 184 trades):**
- Win rate: 58%
- Profit factor: 1.22-1.39
- Floor never breached at any capital level
- Works from ₹50K upward (no margin needed)

**Costs:** ~0.25% round-trip (STT dominant, ₹0 brokerage on delivery)

### Strategy 4: Equity Momentum (SUPPLEMENTARY)

**Validated: CAGR 7.4% at ₹5L (Sharpe 0.33)**

**Mechanics:**
- Universe: NIFTY 50 stocks
- Rank by 12-month return, skip last 1 month
- Buy top 5 stocks, equal weight, rebalance monthly
- Delivery trades

**Note:** Lower Sharpe than mean reversion but provides diversification. Can run alongside Strategy 3 without additional margin.

---

## PART 2: CAPITAL SCALING PATH

| Phase | Capital | Strategies Active | Expected XIRR |
|-------|---------|-------------------|---------------|
| **Phase 1: Learn** | ₹50K | Equity RSI only | ~8.5% |
| **Phase 2: Scale** | ₹1L | Equity RSI + partial NIFTY options (38/109 weeks) | ~40-50% blended |
| **Phase 3: Full** | ₹2L | Full NIFTY options + Equity RSI | ~60-80% blended |
| **Phase 4: Diversify** | ₹3L+ | NIFTY + BANKNIFTY options + Equity | ~50-70% blended |
| **Phase 5: Target** | ₹5L | All strategies, full capacity | ~42-50% XIRR |

**Phase 1 is critical** — it's not about returns, it's about proving discipline: following signals, managing stops, not overriding the system.

---

## PART 3: DATA SOURCES (Validated & Accessible)

### What We Actually Need (trimmed from v2's 6 layers)

| Data | Source | API | Cost | Validated? |
|------|--------|-----|------|-----------|
| **NIFTY/BN historical option prices** | Dhan rollingoption API | REST | Free | YES — 5 years, minute-level, IV+OI+spot |
| **Live option chain** | Dhan option chain API | REST | Free | YES — all strikes, Greeks, bid/ask |
| **Expiry list** | Dhan expiry list API | REST | Free | YES |
| **Equity OHLCV (daily)** | yfinance | Python lib | Free | YES — 5 years, all NIFTY 50 |
| **India VIX** | yfinance (^INDIAVIX) | Python lib | Free | YES |
| **NIFTY index** | yfinance (^NSEI) | Python lib | Free | YES |
| **Margin calculator** | Dhan margin API | REST | Free | Available |
| **Positions/orders** | Dhan trading API | REST | Free | Available |

### Dropped from v2 Plan (Not Validated / Not Needed Yet)
- ~~FII/DII data from NSE~~ — proxy failed validation; actual NSE scraping can be added later
- ~~FinBERT sentiment~~ — not validated yet; add in future iteration
- ~~Google Trends, SIAM, POSOCO~~ — alt data not tested
- ~~Fundamental data (Screener.in)~~ — not needed for options strategies
- ~~Reddit/Twitter sentiment~~ — not validated
- ~~TimescaleDB~~ — overkill for current scale; SQLite or plain PostgreSQL sufficient

### Data Collection Schedule (Simplified)

| When | What | API |
|------|------|-----|
| **Monday 9:00 AM** | Fetch NIFTY option chain for current week expiry | Dhan |
| **Monday 9:15 AM** | Calculate Max Pain, PCR, select strangle strikes | Computed |
| **Monday 9:20 AM** | Place strangle sell orders (if margin OK) | Dhan/Zerodha |
| **Mon-Thu hourly** | Monitor MTM, check hard floor | Dhan |
| **Thursday 3:15 PM** | Options expire / close positions | Auto |
| **Daily 3:35 PM** | Fetch EOD equity data, check RSI signals | yfinance |
| **Daily 3:40 PM** | Place equity buy/sell orders if signal | Zerodha |
| **Monthly last week** | BANKNIFTY monthly strangle entry | Dhan |

---

## PART 4: FEATURES (Reduced to What's Proven)

### ~30 Features (down from 80-100 in v2)

**Technical (for equity strategies):**
- RSI(14) — core signal for mean reversion
- EMA 9/21 crossover — trend entry
- EMA 20/50 ratio — trend strength
- ADX — trend filter (>20 for trend trades)
- MACD histogram — momentum confirmation
- Bollinger %B — overbought/oversold
- ATR (relative) — volatility for stop placement
- Volume relative to 20d MA — confirmation

**Options-derived (for strangle strategies):**
- PCR (OI) — overall sentiment
- Max Pain — strike selection
- ATM IV — premium sizing
- IV Rank (current IV vs 52-week range) — entry filter
- VIX level — regime proxy
- VIX 5d change — momentum of fear

**Regime:**
- VIX level buckets (<12, 12-16, 16-20, 20+)
- Market breadth (% stocks > 200 DMA) — from yfinance

**Momentum (for stock selection):**
- 12-month return (skip 1 month) — primary ranking factor
- Relative strength vs NIFTY — sector tilt

---

## PART 5: RISK ENGINE

### Hard Rules (Non-Negotiable)

1. **Hard floor: ₹4,00,000** — If capital drops below this at ANY point (intra-week MTM check), exit ALL positions immediately at market. No new trades until capital is manually replenished.

2. **Position exit on floor breach** — Not a "halt." A forced market-order exit with 2x normal slippage modeled. P&L only counts fully closed trades.

3. **Margin gate** — Never enter a trade if margin > 90% of available capital.

4. **Single lot only** — 1 lot NIFTY, 1 lot BANKNIFTY max. No pyramiding at current capital.

5. **No stock F&O** — Validated that stock options lose money. Only index options.

### Monitoring Cadence

| Check | Frequency | Action on Breach |
|-------|-----------|-----------------|
| MTM vs hard floor | Every hour during market | Exit all positions |
| Margin utilization | Before each trade | Skip trade |
| Weekly P&L | End of week | Log and report |
| Max single-trade loss | Per trade | Auto-exit at 2x premium |
| Strategy drawdown | Weekly | Pause strategy if 5 consecutive losers |

---

## PART 6: TECH STACK (Simplified)

### Backend
| Component | Technology | Why |
|-----------|-----------|-----|
| Language | Python 3.11+ | Everything validated in Python |
| API | FastAPI | Async, type-safe, auto-docs |
| Broker Data | dhanhq SDK | Validated — options, historical, live chain |
| Equity Data | yfinance | Validated — 5 years, all NIFTY 50 |
| Technical Analysis | ta (Python) | Validated — RSI, EMA, MACD, BB, ADX |
| ML (future) | XGBoost, hmmlearn | Validated in v1 (regime detection passed) |
| XIRR | pyxirr | Validated |
| Database | SQLite → PostgreSQL | SQLite for MVP, migrate later |
| Cache | Redis | Live option chain, feature cache |
| Scheduler | APScheduler | Monday open, daily EOD, hourly MTM |
| Alerts | python-telegram-bot | Trade signals, floor warnings, daily P&L |
| Agent Orchestration | LangGraph | Research → Signal → Risk → Execute |
| LLM | Claude Sonnet | Trade reasoning, report generation |
| MCP | Official Python SDK | Tool access for agents |

### Frontend
| Component | Technology |
|-----------|-----------|
| Framework | React 19 + TypeScript + Vite |
| Charts | Lightweight Charts (TradingView), Recharts |
| Agent Viz | React Flow |
| Styling | Tailwind CSS |
| Real-time | Socket.IO |

### Infrastructure
| Component | Technology |
|-----------|-----------|
| Containers | Docker Compose |
| Deployment | VPS or GCP Cloud Run |
| Domain | finagent.yourdomain.com |

---

## PART 7: PROJECT STRUCTURE

```
finagent/
├── docker-compose.yml
├── .env                               # Dhan + Zerodha keys
├── .gitignore
├── README.md
│
├── strategies/                        # Core trading strategies
│   ├── nifty_strangle.py              # NIFTY weekly short strangle
│   ├── banknifty_strangle.py          # BANKNIFTY monthly short strangle
│   ├── equity_mean_reversion.py       # RSI < 30 mean reversion
│   ├── equity_momentum.py             # 12-month momentum rotation
│   └── base.py                        # Base strategy interface
│
├── risk/                              # Risk management
│   ├── floor_monitor.py               # Hard floor ₹4L monitoring
│   ├── margin_checker.py              # Pre-trade margin validation
│   ├── position_manager.py            # Track open positions
│   └── cost_model.py                  # Zerodha cost calculator
│
├── data/                              # Data collection
│   ├── dhan_client.py                 # Dhan API wrapper (options, historical)
│   ├── equity_data.py                 # yfinance equity + VIX data
│   ├── option_chain.py                # Live option chain + Max Pain + PCR
│   └── cache.py                       # Redis / file cache
│
├── features/                          # Feature engineering
│   ├── technical.py                   # RSI, EMA, MACD, BB, ADX, ATR
│   ├── options.py                     # PCR, Max Pain, IV rank, VIX
│   └── store.py                       # Feature computation + caching
│
├── agents/                            # LangGraph AI agents
│   ├── graph.py                       # Main workflow
│   ├── state.py                       # TradingState schema
│   ├── nodes/
│   │   ├── research.py                # Market analysis + data synthesis
│   │   ├── signal.py                  # Strategy signal generation
│   │   ├── risk_gate.py               # Risk check + position sizing
│   │   ├── execute.py                 # Order placement
│   │   └── report.py                  # Daily/weekly report
│   └── prompts/                       # LLM system prompts
│
├── mcp_servers/                       # MCP tool servers
│   ├── market_data/                   # Price + options data tools
│   └── broker/                        # Order + position tools
│
├── backend/                           # FastAPI
│   ├── main.py
│   ├── api/                           # REST routes
│   ├── websocket.py                   # Real-time updates
│   └── db/                            # SQLite/PostgreSQL models
│
├── frontend/                          # React dashboard
│   ├── src/pages/                     # Dashboard, Signals, P&L, Risk
│   └── src/components/
│
├── scripts/
│   ├── backtest.py                    # Run strategy backtests
│   ├── paper_trade.py                 # Paper trading mode
│   └── daily_report.py               # Generate daily report
│
├── tests/
│   ├── test_strategies/
│   ├── test_risk/
│   └── test_data/
│
└── validation/                        # All validation scripts (already done)
    ├── validate.py                    # v1 — index ML (failed)
    ├── validate_v2.py                 # v2 — equity strategies (passed)
    ├── validate_v3.py                 # v3 — with real costs (passed)
    ├── validate_derivatives.py        # derivatives v1 (simulated)
    ├── validate_derivatives_v2.py     # derivatives v2 (real Dhan data)
    ├── validate_real.py               # final real premium validation
    ├── simulate_capital_levels.py     # multi-capital test
    ├── simulate_xirr.py              # XIRR calculations
    └── simulate_all_instruments.py    # multi-instrument comparison
```

---

## PART 8: IMPLEMENTATION PHASES (20 days)

*Reduced from 30 days — dropped unvalidated components*

### Phase 1: Core Strategies + Risk (Days 1-6)

**Day 1-2: Project setup + NIFTY strangle**
- [ ] Project scaffold, Docker Compose (Redis, SQLite)
- [ ] `dhan_client.py` — wrapper around Dhan API (option chain, rollingoption, expiry list, margin)
- [ ] `nifty_strangle.py` — entry (Monday) / exit (Thursday) logic
- [ ] `cost_model.py` — Zerodha F&O costs (already validated in scripts)

**Day 3: Risk engine**
- [ ] `floor_monitor.py` — hourly MTM check, forced exit on ₹4L breach
- [ ] `margin_checker.py` — pre-trade margin validation
- [ ] `position_manager.py` — track open positions, unrealized P&L

**Day 4: Equity strategies**
- [ ] `equity_mean_reversion.py` — RSI < 30 entry, RSI > 50 exit
- [ ] `equity_momentum.py` — 12-month momentum, monthly rebalance
- [ ] `equity_data.py` — yfinance wrapper for NIFTY 50 stocks

**Day 5: BANKNIFTY + features**
- [ ] `banknifty_strangle.py` — monthly expiry version
- [ ] `option_chain.py` — live chain analysis (Max Pain, PCR, IV rank)
- [ ] `features/technical.py` + `features/options.py`

**Day 6: Scheduler + Telegram**
- [ ] APScheduler: Monday entry, hourly MTM, Thursday exit, daily EOD scan
- [ ] Telegram bot: trade alerts, floor warnings, daily P&L summary
- [ ] Paper trading mode (log trades without executing)

### Phase 2: AI Agents + API (Days 7-12)

**Day 7-8: LangGraph workflow**
- [ ] `state.py` — TradingState (positions, capital, signals, regime)
- [ ] `research.py` — analyze option chain, VIX, PCR, market conditions
- [ ] `signal.py` — generate trade signals from strategies
- [ ] `risk_gate.py` — margin check, floor check, approve/reject
- [ ] `execute.py` — place orders via Dhan API
- [ ] `graph.py` — wire agents into workflow

**Day 9: MCP servers**
- [ ] Market data MCP server (option chain, historical, VIX)
- [ ] Broker MCP server (orders, positions, margin)

**Day 10-11: FastAPI backend**
- [ ] API routes: /signals, /positions, /pnl, /risk, /settings
- [ ] WebSocket for real-time updates
- [ ] SQLite models for trades, signals, daily P&L
- [ ] Authentication (JWT)

**Day 12: Integration testing**
- [ ] End-to-end: scheduler triggers → agent workflow → signal → risk check → paper trade
- [ ] Verify costs match validation scripts
- [ ] Test floor breach scenario

### Phase 3: Dashboard (Days 13-16)

**Day 13: Core dashboard**
- [ ] Main page: capital, P&L chart, open positions, recent trades
- [ ] Strategy performance cards (NIFTY strangle, BANKNIFTY, equity)

**Day 14: Signals + risk**
- [ ] Signals page: pending, approved, executed, with agent reasoning
- [ ] Risk page: capital vs floor, margin utilization, drawdown chart

**Day 15: Backtest + trade log**
- [ ] Trade log with filters (strategy, date, P&L)
- [ ] Backtest page with equity curves per strategy

**Day 16: Agent activity + polish**
- [ ] Agent workflow visualization (React Flow)
- [ ] Dark mode, responsive, loading states

### Phase 4: Deployment + Paper Trading (Days 17-20)

**Day 17-18: Deploy**
- [ ] Dockerize all services
- [ ] Deploy to VPS
- [ ] Domain: finagent.yourdomain.com
- [ ] HTTPS, environment variables, secrets

**Day 19: Paper trading week**
- [ ] Run system live in paper mode for 1 full week
- [ ] Compare paper P&L with backtest expectations
- [ ] Fix any timing, data, or execution issues

**Day 20: Documentation + portfolio**
- [ ] README with architecture diagram, setup, screenshots
- [ ] Portfolio case study with XIRR numbers from validation
- [ ] Blog post: "I Validated 6 Trading Strategies Before Writing a Single Line of Production Code"

---

## PART 9: VERIFIED EDGE (Honest Assessment)

### What Actually Generates Alpha
1. **NIFTY options premium selling** — 87% of weeks, options expire worthless or near-zero. Theta decay is the edge. XIRR 42-95%.
2. **RSI oversold bounces** — NIFTY 50 stocks consistently bounce from RSI < 30. 58% win rate with 1.39 profit factor. XIRR ~9%.
3. **Discipline** — The system doesn't FOMO, revenge trade, or override stops. This alone beats most retail traders.

### What Doesn't Generate Alpha (Killed by Validation)
- ML direction prediction on NIFTY index (too efficient)
- Options on FINNIFTY, MIDCPNIFTY, individual stocks (all lose money)
- VIX regime filtering (VIX too low in recent years)
- Sentiment analysis (not validated)
- Alternative data (not validated)
- Volume-based institutional flow proxy (zero correlation)

### Realistic Expectations
- **₹50K start**: Equity only, XIRR ~8.5%, ₹19K profit over 2 years
- **₹2L**: Full NIFTY options, XIRR ~87%, potential ₹5.3L profit over 2 years
- **₹5L**: NIFTY + BANKNIFTY, XIRR ~42%, ₹5.3L+ over 2 years
- **Bad months happen**: 6 out of 25 months were negative even on the best strategy
- **Worst single week**: -₹35,817 (7.2% of ₹5L capital)
- **Hard floor was never breached** in any simulation

---

## PART 10: VERIFICATION PLAN

1. **Paper trading**: Run for 2 full weeks before any real capital
2. **Compare paper vs backtest**: Weekly P&L should be within 30% of backtest average
3. **Cost verification**: Actual Zerodha charges should match cost model
4. **Floor test**: Manually simulate a floor breach to verify forced exit works
5. **Telegram alerts**: Verify all alert types fire correctly
6. **Scaling test**: Start with ₹50K equity only, scale to ₹1L after 1 profitable month
7. **Monthly review**: Compare actual XIRR vs validated XIRR. If >2x worse for 3 months, pause and investigate.

---

## PART 11: FUTURE ITERATIONS (After v3 Proves Itself)

Only add these AFTER the core system runs profitably for 3+ months:

1. **Actual FII/DII data** — Scrape NSE reports, validate the real institutional flow signal
2. **FinBERT sentiment** — Add news sentiment as a VIX-like regime signal
3. **HMM regime detection** — Validated in isolation, integrate as strategy filter
4. **Multi-lot scaling** — Trade 2-3 lots when capital grows beyond ₹10L
5. **Iron Condor** — Defined-risk version of strangle for capital efficiency
6. **BANKNIFTY weekly** — If SEBI re-allows weekly options
7. **Sector rotation** — Monthly rotation into top-momentum NIFTY sectors
8. **Options buying (debit spreads)** — For directional trades during high-conviction regimes
