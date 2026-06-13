# algo-trader — Intraday Trading System Architecture

*v1.1, 2026-06-11. v1.0 was adversarially reviewed by a 3-lens panel (quant
methodology / NSE-Dhan facts / ops-safety, workflow `wf_616b6ac1-ee7`); this
revision incorporates all blocking findings. Companion: `docs/finagent-assessment.md`.*

## 0. Mission and hard rules

- **Intraday only.** Every position opens and closes within the same NSE session.
  No new entries after **14:44:30 IST** (30s grace inside the 14:45 policy);
  voluntary exits begin **15:15**; hard force-flat at **15:19:30** — Dhan's RMS
  admin square-off starts ~15:19–15:20 and fills at uncontrolled prices
  ([Dhan KB](https://dhan.freshdesk.com/support/solutions/articles/82000832236)).
- **No live orders without explicit per-action operator approval.** Default mode
  is data + backtest + paper. The `dhanhq` SDK ships order methods, so "no live
  code" must be structural, not aspirational: the **only** broker surface in
  this codebase is `DhanDataClient`, a wrapper exposing data methods exclusively
  and raising `RuntimeError` on any order-method access. The raw SDK class is
  never imported outside that wrapper. A future live executor lives in a
  separate module guarded at import time by the env sentinel, and every order
  additionally requires per-action operator approval.
- **Daily EOD P&L report** (per-strategy + aggregate) published via soma-publish
  every trading day, with a durable local copy and alert fallback.
- **Honest evaluation beats good-looking equity curves.** The failure modes are
  known and named (§3.8): look-ahead, survivorship, data snooping, stale costs.

## 1. System overview

```
                      ┌─────────────────────────────────────────────┐
                      │ DATA LAYER (algotrader/data)                │
                      │  token_manager  — mints 24h JWT (TOTP flow),│
                      │     singleton, re-checked per request,      │
                      │     re-mint inside WS reconnect (code 807)  │
                      │  dhan_client    — DATA-ONLY SDK wrapper     │
                      │     (order methods structurally blocked)    │
                      │  instruments    — scrip master, point-in-   │
                      │     time lot sizes, T2T/series flags        │
                      │  history        — /charts/intraday 1m/5m,   │
                      │     90-day pages, 5y depth, parquet store   │
                      │     (closed-day immutability guard)         │
                      │  live_feed      — wss Quote → bar builder,  │
                      │     gap detection + REST catch-up replay,   │
                      │     90s bar watchdog, zombie-conn recv      │
                      │     timeout                                 │
                      │  option_chain   — IV/greeks snapshots       │
                      └────────────┬────────────────────────────────┘
                                   │ closed 1m/5m bars (tz-aware IST,
                                   │ session-filtered, bar_complete=True)
              ┌────────────────────┴───────────────────┐
              ▼                                        ▼
┌───────────────────────────────┐      ┌──────────────────────────────────┐
│ BACKTEST (algotrader/backtest)│      │ PAPER TRADING (algotrader/paper) │
│  event-driven bar replay      │      │  same Strategy + Risk objects    │
│  next-bar-open fills, two-    │      │  driven by live_feed bars        │
│  branch stop semantics        │      │  crash-safe: startup reconciles  │
│  DhanCosts (date-dependent    │      │  positions/breaker/entry-counts  │
│  rate schedule) + slippage    │      │  from SQLite (WAL)               │
│  session lifecycle + breakers │      │  systemd restart + 15:25 DB      │
│  walk-forward + trial registry│      │  watchdog (separate process)     │
│  metrics + reports            │      │  EOD report → soma-publish       │
└───────────────┬───────────────┘      └──────────────────────────────────┘
                │ evaluates                            ▲ identical on_bar() semantics
        ┌───────┴────────────────────────────┬─────────┴───────┐
        ▼                                    ▼                 ▼
┌──────────────────┐   ┌────────────────────────┐   ┌────────────────────┐
│ STRATEGIES       │   │ RISK ENGINE            │   │ REPORTS            │
│ Strategy ABC     │   │ sizing (risk-per-trade)│   │ daily EOD P&L      │
│ on_bar() →       │   │ stop manager           │   │ per-strategy +     │
│ OrderIntents     │   │ max-daily-loss breaker │   │ aggregate, trades, │
│ ORB / VWAP-rev / │   │ (persisted, per-bar)   │   │ costs, risk events,│
│ trend-cont / ... │   │ hard floor · exposure  │   │ equity curve, drift│
└──────────────────┘   └────────────────────────┘   └────────────────────┘
```

One strategy/risk codebase, two drivers. Identical `on_bar()` semantics between
backtest and paper is the core invariant — it eliminates backtest/live drift by
construction.

## 2. Data layer

- **Token manager** (built: `algotrader/data/token_manager.py`): Dhan tokens are
  24h JWTs. Mint via `auth.dhan.co/app/generateAccessToken` (clientId+PIN+TOTP);
  API-key consent flow is the browser-bound fallback. Refresh design:
  (a) 08:45 IST pre-open mint; (b) singleton re-validated on use (5-min cache),
  never read-once-at-startup; (c) REST 401 → re-mint and retry; (d) **websocket
  disconnect code 807 → re-mint inside the reconnect path** before
  reconstructing the WSS URL (the SDK's stock reconnect reuses the dead token —
  verified in `marketfeed.py`; we wrap it).
- **DhanDataClient**: the order-method firewall described in §0.
- **History store**: 1-min + 5-min candles via `/charts/intraday` (verified:
  5y depth, 90-day pages — [docs](https://dhanhq.co/docs/v2/historical-data/)).
  Token-bucket limiter at 5 rps / 100k-day budget with exponential backoff
  (finAgent's single-retry pattern caused silent gaps). Parquet partitioned by
  `symbol/interval/year-month`; a day is written only after its session closes.
  Bars tz-aware IST; session filter 09:15–15:30. **First-backfill check**:
  empirically verify whether the 09:15 bar contains pre-open call-auction
  volume; if so, exclude/adjust it before any ORB range definition.
- **Universe v1**: NIFTY + BANKNIFTY **index futures** (primary backtest
  instruments — no survivorship issue, deep liquidity) and NIFTY-50 equities.
  For equities, **point-in-time index membership** is reconstructed from NSE's
  historical constituent-change records; where unavailable, the backtest start
  is bounded and the residual survivorship bias documented (Indian-index studies
  put it at ~5 ppt/yr — material). Instrument metadata carries **date-dependent
  lot sizes** (see §3.2) and series flags (T2T/BE-series stocks cannot be
  shorted intraday — risk engine rejects sell-side MIS intents for them).
- **Live feed**: websocket Quote packets → in-process 1-min bar builder.
  Strategies only ever see bars with `bar_complete=True` (enforced by
  assertion); the in-progress bar is structurally invisible. **Gap handling**:
  on reconnect, fetch missed bars via REST, replay them through the
  strategy/risk pipeline as catch-up bars (bounded at 15; beyond that flag
  data-quality + mark stops "unconfirmed" until next live bar); if a gap spans
  the force-flat time, flatten immediately. Watchdog: no bar for >90s during
  market hours → alarm + REST fallback. Zombie-connection guard: recv timeout,
  not just ping success.
- **Naive datetimes are banned** in the bar pipeline (assertion at ingestion
  boundaries); all comparisons use `ZoneInfo("Asia/Kolkata")`.

## 3. Backtest engine (the credibility core)

1. **Entry fills**: signal computed on bar *t* close → fill at bar *t+1* open
   ± slippage. No same-bar fills anywhere.
2. **Stop/target fills — two-branch semantics** (review fix): if a bar *opens*
   beyond the stop (gap at open), fill at that bar's open (can be worse than
   the stop — that's the point); if the open is safe but price trades through
   the stop intrabar, fill at stop price ± slippage. Same logic mirrored for
   targets (conservative side). If both stop and target lie within one bar's
   range, **stop wins** (pessimistic tie-break). All gap-through events are
   logged and reported.
3. **Costs (`DhanCosts`)** — a **date-dependent rate schedule**, because rates
   changed repeatedly in the backtest window; the model takes the trade date:
   - Equity intraday: brokerage min(₹20, 0.03%)/executed order; STT 0.025%
     sell-side; NSE txn **0.0030699%** (Dhan pricing page, verified
     2026-06-11 — not the stale 0.00297%); SEBI ₹10/cr; stamp 0.003%
     buy-side; IPFT charge; GST 18% on (brokerage + txn + SEBI + IPFT).
   - Index futures STT (sell-side): 0.0125% → **0.02%** from 2024-10-01 →
     **0.05%** from 2026-04-01 (Budget 2026).
   - Index options STT (sell-side premium): 0.10% → **0.15%** from 2026-04-01;
     exercise 0.15% on intrinsic.
   - Every component carries a dated source comment + a unit test asserting the
     value per date band; rates re-verified against dhan.co/pricing at build.
4. **Slippage**: per-instrument — max(1 tick, k·rolling-ATR(1m) fraction), ATR
   computed strictly from bars ≤ t−1 (no centered/forward windows). Options get
   a multiplicative widening on high-IV bars. The model is **frozen before any
   holdout evaluation**; paper-trading calibration applies prospectively only.
5. **Session lifecycle**: entries blocked after 14:44:30; voluntary exits from
   15:15; force-flat 15:19:30; breaker trips halt entries for the session.
   Each trading day is an isolated episode — no overnight state.
6. **Walk-forward + anti-snooping protocol** (review fix — this is the part
   most systems silently cheat):
   - Rolling **8m train / 2m test** windows (≥4:1, regime-diverse) over ~5y;
     final **~9 months frozen holdout**.
   - **Pre-registration**: each strategy's parameter grid and entry/exit logic
     are committed to git *before* any OOS window is evaluated. Code or grid
     changes after seeing OOS results start a new registered trial generation.
   - **Trial registry**: every (strategy-variant × parameter-set) evaluated
     against any OOS data is logged; total trial counts are reported next to
     results, and OOS Sharpe is reported alongside its **Deflated Sharpe
     Ratio** given the trial count.
   - The holdout is touched **exactly once per strategy**, after candidate
     freeze; the outcome is reported regardless of result.
7. **Metrics**: net P&L after costs, profit factor **with 95% CI**, Sharpe
   (computed on *trading days with exposure*, zero-days reported separately;
   rf documented), max DD, win rate, daily P&L distribution, time-of-day P&L,
   MAE/MFE, cost share of gross edge, per-window results, pairwise
   **strategy-correlation matrix** and same-direction-day distribution
   (ORB + trend-continuation will often double the same bet — measured, and
   the risk engine's daily loss budget treats correlated entries as one).
8. **Kill criteria** (OOS, pre-registered): PF 95% CI lower bound > 1.0 and
   point estimate ≥ 1.2 · **≥ 600 trades** across OOS windows (power analysis:
   at ~50% win rate, 250 trades cannot reject breakeven) · PF ≥ 1.0 in ≥ 4 of
   6 individual OOS windows (no aggregate masking) · daily-P&L Gini below
   threshold (no <10-trade edge concentration) · max DD within budget ·
   survives +50% slippage **and** a regime-conditional stress slice
   (VIX > 20 days; expiry-proximity days) · capacity sanity: fill model declared
   valid only up to ~₹10–20L notional/signal (1–2 NIFTY lots); re-examined
   before any scaling.

## 4. Strategy slate v1

Selected for decorrelated hypotheses (breakout / reversion / trend), cost
density compatible with ₹20/order at 5-min holding scales, and v1 instruments
(index futures + liquid equities). Operator books will extend the slate.

1. **ORB — Opening Range Breakout** (NIFTY/BANKNIFTY futures): range from first
   15/30 min (after verifying 09:15-bar integrity, §2); enter on break with
   volume confirmation **defined against prior-N-days' same-time-of-day volume
   profile** (stored per-instrument; same-session baseline alone is
   underpowered); stop at range mid/opposite edge; ATR-scaled target or trail;
   flat by 15:15. Note: BANKNIFTY weeklies are gone (monthly only post-SEBI);
   NIFTY weekly expiry is **Tuesday** now — expiry-day variants must use the
   correct calendar per date.
2. **VWAP mean-reversion** (top-liquidity NIFTY-50 names): fade extensions
   ≥ k·σ from session VWAP. **σ is estimated from a rolling window of prior
   days' VWAP-deviation dispersion — never the current day's future bars**
   (unit-tested). Prior-art warning honored: finAgent's RSI intraday reversion
   produced *no surviving parameters* under honest stops; this is a nearby
   hypothesis, so it carries a higher burden of proof. The structural
   differences (volume-anchored mean vs momentum oscillator; ADX regime gate;
   liquidity-filtered universe; time-stop) are the hypothesis — if it fails the
   pre-registered OOS gates, it dies without parameter rescue.
3. **Trend continuation** (5-min): EMA(9/21) alignment + ADX floor + pullback
   entry; ATR trail; one re-entry max per direction per day.
4. *(Phase 2, post scope confirmation)* index-option debit structures on
   ORB/trend signals. Premium-selling intraday deferred (margin + tail-risk
   modeling must mature first; finAgent's strangle edge was positional theta
   and does not transfer).

Strategies implement `Strategy.on_bar(ctx) -> list[OrderIntent]` against a
`SessionContext` (closed bars, indicators, open positions, session clock, risk
state). The risk engine — not the strategy — owns sizing and breakers.

## 5. Risk engine

- **Sizing**: risk ≈ 0.75% of capital per trade (operator-tunable) from
  entry−stop distance; lot rounding for F&O; per-instrument exposure cap.
  F&O margins from the Dhan margin API (SPAN+exposure), not a hardcoded
  leverage guess; equity MIS leverage ≤ 5x (SEBI cap).
- **Stops mandatory**: `OrderIntent` without a stop is rejected.
- **Max-daily-loss breaker**: realized + unrealized ≤ −2% of capital → flatten
  all, no entries until next session. Evaluated per bar. **State persisted to
  SQLite and reloaded on startup** (a crash cannot forget a tripped breaker);
  resets only on new session_date. Correlated same-direction entries count
  against a shared budget.
- **Hard floor** (₹4L default): breach halts the system until the operator
  intervenes. Distinct from the daily breaker.
- **Concentration**: max open positions (default 3), max 1 per symbol, per-
  strategy share of the daily loss budget; T2T/BE-series short-sale rejection.
- **Crash recovery protocol** (review fix): on startup, before the first bar —
  load today's open positions, breaker state, realized P&L, and per-strategy
  entry counts from SQLite; re-register in memory; if wall-clock ≥ 15:19:30,
  force-flat immediately. Tested by SIGKILL mid-session + restart with
  assertion of no duplicate positions.

## 6. Paper trading + daily EOD P&L

- Paper executor consumes live bars; fills via the same two-branch + slippage
  model as the backtest; persistence in the account-isolated SQLite schema
  (`account_id='paper'`), **WAL mode + busy_timeout on every connection**;
  report generation runs on a separate read-only connection, query scoped to
  `session_date = today`, 10s timeout → partial report rather than a stalled
  bar loop.
- Supervision: systemd unit (restart-on-exit, alert after 3 consecutive failed
  starts via Telegram) + an **independent watchdog cron** at 15:25 IST that
  checks the DB for open positions and alerts the operator if any exist.
- **EOD report — triggered by the 15:30 session-close bar event** (not a blind
  wall-clock cron): per-strategy + aggregate net P&L (₹, % of capital), trade
  table, cost breakdown, win/loss stats, risk events, equity curve,
  paper-vs-backtest drift. Written **first** to `reports/YYYY-MM-DD.html`
  (durable local), then `soma-publish` with 3-attempt backoff; on publish
  failure, Telegram alert with the headline number + local path. Headline
  number always surfaced via notify.
- Paper period doubles as slippage calibration — applied prospectively only
  (§3.4 freeze rule).

## 7. Live trading (deferred — operator authorization required)

Not built in v1. When authorized: separate executor module (import-guarded by
the env sentinel), Super Orders (entry+target+trailing-SL server-side), the
order-update websocket, static-IP whitelisting, sandbox order-state-machine
tests (sandbox fills at fixed ₹100 / no market data — order-flow testing only),
broker position reconciliation on every restart, and per-order operator
approval. Sentinel + per-action approval are both required; neither suffices.

## 8. Security posture

- Creds: `/home/ubuntu/projects/algo-trader/.dhan-creds.env` (chmod 600,
  gitignored) + `/etc/claude-soma/algo-trader-dhan.env`; minted token in
  project `.env` (chmod 600). TOTP secret + PIN should migrate out of
  `finAgent/.env` into the 600-perm creds file (operator action noted).
- No secret values in logs, tool output, or git. Production logging never at
  DEBUG (the WSS URL embeds the token and `websockets` logs URLs at DEBUG).
- Pre-commit guard rejecting non-empty `DHAN_*` values in committed files.

## 9. Delivery phases

| Phase | Contents | Needs creds? |
|---|---|---|
| **P0 (done)** | Scaffold, instrument master, assessment, this doc, token manager | no |
| **P1** | Backtest engine + DhanCosts (date-banded, unit-tested) + walk-forward/trial-registry + metrics; strategies coded against fixture bars; crash-recovery tests | no |
| **P2** | Data layer: DhanDataClient firewall, 5y 1m/5m backfill (+09:15-bar audit, point-in-time universe), parquet store | **yes** |
| **P3** | Pre-register grids → walk-forward slate → iterate via trial registry → rank → strategy review report | yes |
| **P4** | Paper loop on live feed (gap/watchdog/reconciliation) + EOD P&L → soma-publish | yes |
| **P5** | (authorization-gated) live order path | operator go |

## 10. Open operator items (tracked via NEEDS_INPUT)

1. Current Dhan PIN (+ TOTP secret confirmation) for headless daily token mint
   — endpoint verified live, currently rejects with "Invalid Pin"; or a pasted
   24h access token to start the backfill immediately.
2. Data-APIs subscription (₹499+GST/mo) status — read from profile once a
   token works.
3. Capital ₹5L / floor ₹4L confirmation; max daily loss 2%; per-trade 0.75%.
4. Scope: equity intraday + index futures (v1 default), options phase-2.
5. Trading books via admin dropper.
