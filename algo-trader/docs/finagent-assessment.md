# finAgent Assessment — Reuse Map for the Intraday Rebuild

*Produced 2026-06-11 by a 6-reader parallel assessment of `/home/ubuntu/finAgent`
(clone of github.com/techfreakworm/finAgent) + DhanHQ API research. Full structured
output: workflow `wf_0c520050-281`.*

## Verdict in one paragraph

finAgent is a well-built **positional/swing** system that was never intraday: its
only historical fetches are daily bars (`/charts/historical`) and expired-option
bars (`/charts/rollingoption`); the SDK's intraday endpoint (`/charts/intraday`)
and websocket feed (`DhanFeed`) are **never called anywhere**. Its three strategies
are Mon→Thu strangles, RSI delivery trades, and monthly momentum — all multi-day.
Its replay engine has the right event-driven skeleton but fills at **same-bar
close (look-ahead bias)**, back-fills leading NaNs (more look-ahead), claims
walk-forward in a docstring while implementing none, and checks risk hourly. Its
own stricter scratchpad test (`backtest_proper.py`) concluded **"NO PARAMETERS
SURVIVED BOTH MODES"** for the equity strategy once intraday-Low stop checks were
required — i.e., the claimed profitability did not survive honest simulation.
The "+₹4.64L (intraday stops)" commit claim is daily-bar simulation with Low-side
stop approximation, not intraday trading.

## Safety-relevant fact

The execute path (`agents/nodes/execute.py`, `engine.py`) contains **no broker
order placement code at all** — "LIVE" mode only changes a log string; both modes
write to SQLite only. finAgent could never have placed a real order. The rebuild
keeps live-order code structurally absent until the operator authorizes that
phase (and then gates every order per-action).

## Reuse map

| Asset (finAgent path) | Verdict | Notes |
|---|---|---|
| `config.py` security-ID maps (NIFTY-50, VIX=21, NIFTYBEES=10576) | **reuse** | IDs accurate; copy in. **But its lot-size logic is STALE** — see correction below |
| `data/option_chain.py` (max pain, PCR, strangle strikes, IV rank — pure functions) | **reuse** | No I/O deps |
| `research_scratchpad/validate_dhan_migration.py` instrument-master + ID-mapping + fetch scaffolding | **reuse** | Proves v2 endpoints/payloads; swap endpoint to `/charts/intraday` |
| `backend/api/performance.py` (XIRR, Sharpe, max-DD, profit factor, equity curve) | **reuse/adapt** | Directly serves our daily EOD report |
| `docker-compose.yml` (IST tz, env injection, WAL volume, log caps) | **reuse** | Add live-feed worker service later |
| `data/dhan_client.py` (auth session, 429 retry, option-chain calls) | **adapt** | ADD intraday candles + websocket; harden rate limiting (token bucket, not single retry) |
| `data/cache.py` (immutable-only gzip JSON cache, today-guard) | **adapt** | Sound design; we use parquet but keep the guard idea: cache a day only after 15:30 IST |
| `backend/db/models.py` (account-isolated SQLite: trades/signals/positions/daily_pnl/events) | **adapt** | Add full DATETIME entry/exit, product_type; keep paper/live/replay account isolation |
| `replay/engine.py` (event-driven candle loop + cost deduction + equity curve) | **adapt** | Right pattern; fix same-bar-close fills → next-bar-open, add 15:20 square-off, per-bar risk |
| `strategies/base.py` (Signal dataclass + 2-method ABC) | **adapt** | Add product_type, bar timeframe, entry cutoff, exit deadline; fix should_exit to receive time |
| `risk/floor_monitor.py` | **adapt** | Logic sound; drive per-bar not hourly; fix one-way breach latch; add separate max-daily-loss breaker |
| `risk/position_manager.py` | **adapt** | Add session_date + product_type + persistence; replace premium-notional margin proxy |
| `risk/cost_model.py` (ZerodhaCosts) | **rewrite** | Wrong broker; no MIS equity path. Correct MIS math exists in `validate_v3.py` but was never promoted. Build `DhanCosts` |
| `risk/margin_checker.py` | **rewrite** | Heuristic SPAN proxy; no MIS leverage concept; use Dhan margin API + leverage table |
| `replay/data_provider.py` | **rewrite** | Ignores interval for equities (always daily); `bfill()` look-ahead; ATM-only options |
| `scheduler.py` | **rewrite** | 30s wall-clock polling, swing-only gates; need bar-aligned dispatcher |
| `agents/*` LangGraph pipeline | **adapt-later** | DAG skeleton fine; LLM role was cosmetic-only; not needed for v1 backtest loop |
| `strategies/nifty_strangle.py`, `equity_momentum.py` | **drop** | Structurally multi-day |
| `research_scratchpad/simulate_capital_levels.py`, VALIDATION_PLAN.md | **drop** | Daily-bar methodology; superseded |

## Known bugs found (avoid importing them)

- `equity_mean_reversion.py:52` — stop-loss sign inversion risk (`1 + stop_loss_pct` for a BUY; only correct if config value is negative).
- `replay/data_provider.py:459` — `.bfill()` after reindex = look-ahead at series start.
- `engine.py:434` — entry at same-bar close (look-ahead).
- `floor_monitor.py` — `_breached` latch never resets across sessions.
- `agents/nodes/signal.py:31` — open_positions hardcoded `[]` → duplicate signal risk.
- `position_manager.get_total_margin_used()` — premium-notional as margin (wrong both directions).

## Prior negative results worth keeping (don't re-test blindly)

- Index-direction ML (XGBoost on NIFTY): failed.
- Option selling on FINNIFTY / MIDCPNIFTY / single stocks: loses money.
- Volume-based FII/DII proxy: zero correlation.
- VIX regime filter in sustained low-vol: no value.
- Equity RSI mean-reversion with honest intraday-Low stops: **no surviving parameters**.

## DhanHQ v2 API facts (researched 2026-06-11; verify empirically on first fetch)

- **Auth**: access tokens are **24-hour JWTs**. API Key + Secret (12-month validity, SEBI-era) regenerate tokens daily via `generateAccessToken` (needs client_id + PIN + TOTP). **Static IP whitelisting mandatory for Order APIs** (not for data). SDK `dhanhq==2.2.0`, `DhanContext(client_id, token)`.
- **Intraday historical**: `POST /v2/charts/intraday`, intervals 1/5/15/25/60-min, **5 years lookback**, max 90 days/request → paginate. OI included for F&O.
- **Live feed**: `wss://api-feed.dhan.co` — Ticker/Quote/Full(+depth+OI) binary packets; 5,000 instruments/conn, 5 conns; ping/pong 10s/40s. Order-update ws: `wss://api-order-update.dhan.co`.
- **Option chain**: `/v2/optionchain` (+`/expirylist`) with IV + greeks; 1 req per underlying+expiry per 3s.
- **Rate limits**: Data 5 rps & 100k/day; Quote REST 1 rps; Orders 10 rps (SEBI), 25 modifications/order cap.
- **Orders (later phase)**: product `INTRADAY` (RMS auto-square-off EOD), Super Orders = entry+target+trailing-SL in one call; CO/BO; Forever (GTT); DAY/IOC.
- **Sandbox** (`sandbox.dhan.co`): order-flow testing only — every fill at price 100, **no market data**. Useless for strategy P&L; fine for order-state-machine tests pre-live.
- **Cost**: Data APIs subscription ₹499+GST/month (confirm active).

## Post-review corrections (3-lens adversarial panel, wf_616b6ac1-ee7)

- **Lot sizes (finAgent's are stale)** — point-in-time schedule required:
  NIFTY 25 → **75** (Nov-2024) → **65** (Jan-2026 series); BANKNIFTY 15 →
  **30** (Nov-2024) → **35** (Jul-2025) → **30** (Jan-2026). Confirm exact
  series cutoffs vs NSE circulars FAOP64506/FAOP70616 during implementation.
- **NSE equity txn charge**: 0.0030699% per dhan.co/pricing (2026-06-11), not
  0.00297%. GST base includes IPFT.
- **F&O STT (Budget 2026, eff. 2026-04-01)**: futures sell-side 0.02% → **0.05%**;
  options premium sell-side 0.10% → **0.15%** (exercise 0.15% intrinsic). The
  cost model must be date-banded; pre-Apr-2026 backtest windows use old rates.
- **NIFTY weekly expiry is Tuesday** (single-weekly-expiry regime); BANKNIFTY
  has no weekly anymore (monthly only).
- **Websocket token death**: token expiry mid-session surfaces as binary
  disconnect code **807** (not HTTP 401); the SDK's stock reconnect reuses the
  dead token — our feed wrapper must re-mint inside the reconnect path.
- **Auth (verified live 2026-06-11)**: headless mint `POST
  auth.dhan.co/app/generateAccessToken?dhanClientId&pin&totp` works (currently
  rejects on stale PIN); API key+secret flow requires a browser consent step —
  not sufficient alone for headless use.
