# Kite Connect vs DhanHQ — API Comparison & Verdict

**Date:** 2026-06-20 · **Scope:** data / market-feed side (our priority) · **Status:** RESEARCH ONLY — nothing signed up, paid, or connected · **Audience:** operator-only, no external sharing

Researched via the algo-brain reasoning teammate (sequential-thinking) and independently fact-checked by the lead against **live** Kite/Zerodha/Dhan docs (not model memory).

---

## TL;DR — VERDICT: **STAY ON DHAN. Do not connect Kite.**

Kite Connect offers **no data/feed advantage that justifies its cost, harder daily auth, and migration effort.** The one capability that could have justified switching — historical intraday data for **expired** weekly options — is **blocked on both platforms** (an exchange/NSE-level limit, not a vendor choice). On every other axis that matters to us, **Dhan is ahead or tied.** Kite's only genuine plus (a clean per-tick exchange timestamp) is already **neutralized by our arrival-time WS fix**, and is worth revisiting **only** as a read-only cross-check oracle (never the primary feed) **and only if** our Dhan WS fails Monday's shadow validation.

---

## Two premises, corrected

1. **"Dhan's WS feed is broken → switch."** False. The failing shadow run was **our own parser** bucketing 1-min bars by Dhan's stale last-trade-time (LTT). Dhan's transport was healthy that day: **819,582 ticks received, 0 reconnects** — it delivered ticks abundantly; we mis-bucketed them. Fixed in commit `b7b2ce4` (bucket by tick *arrival* time), pending Monday's shadow re-validation.
2. **"KiteTicker is reputed rock-solid."** Overstated. Kite's own forum documents a well-known `pykiteconnect` bug where the socket **stops receiving ticks after a reconnect**. Both feeds have WS quirks that require careful client handling; neither is a silver bullet.

---

## Dimension-by-dimension (our priorities)

| Dimension | DhanHQ | Kite Connect | Winner |
|---|---|---|---|
| **WS / market-data feed** (our #1) | Raw v2 binary feed; transport solid (819k ticks/0 reconnects); per-tick **LTT is stale/coarse** (the bug we hit) → we now bucket by arrival time | KiteTicker (ltp/quote/full modes); provides per-tick **`exchange_timestamp` + `last_trade_time`** (the correct bucketing clock); but has a documented post-reconnect tick-loss bug | **Kite edge on the clock, neutralized by our fix**; transport ~par |
| **WS limits** | 5000 instruments/connection, 3 connections | 3000 instruments/connection, 3 connections | **Dhan** (marginal) |
| **Historical: equity/index** | **5 years**, 1-min, **90-day** pages | 3 years, 1-min, **60-day** pages, 3 req/s | **Dhan** (more history, bigger pages) |
| **Historical: EXPIRED options (intraday)** | ❌ No (scrip master drops expired contracts) | ❌ **No** (instrument master returns **live tokens only**; `continuous` backfill is **futures-only**; NSE **reuses** tokens post-expiry) | **Tie — both blocked (this was the deciding edge, and it's absent)** |
| **Live option chain + Greeks** | `/optionchain`: **one call** → per-strike last / bid / ask (+qty) / **IV** / **δ γ θ vega** / OI / security_id, by named expiry (verified live) | **No chain endpoint, no IV/Greeks** — assemble from the instruments dump + per-strike `/quote` (≤500/call) and **compute Greeks yourself** | **Dhan (clearly)** |
| **Auth / daily token** | Headless **TOTP mint** (clientId+PIN+TOTP → access token) — runs unattended at 08:45 today | `access_token` **expires 6 AM daily (regulatory)**; obtained via **interactive browser login + 2FA → request_token from redirect**; headless automation is fragile/discouraged | **Dhan (decisively)** — Kite is an unattended-cron regression |
| **Python SDK** | Our own raw implementation, fully controlled, just hardened | `pykiteconnect` mature, but the reconnect bug above | Wash |
| **Order / execution** (paper now) | Free order APIs; works | Standard order types/margins | Not decisive (we're paper) |
| **Cost** | **Data API ₹499/mo** (real-time + historical; order APIs free) | **₹500/mo** Connect bundle (WS + historical + trading; no separate add-on) | **Tie (~₹1)** |

---

## The deciding factor: historical data for expired options

The only thing that could have justified onboarding a second broker is the ability to pull **historical intraday candles for already-expired weekly option contracts** — that would unblock the clean historical option backtests we currently **cannot** run (today our option backtests are synthetic-BS approximations, which we've shown can produce artifacts).

**Neither platform can do it.** This is an **NSE-level constraint**: the exchange exposes instrument tokens only for *live* contracts and *reuses* tokens after expiry, so a cached token later returns the *wrong* contract. Kite's `continuous` historical backfill is explicitly **futures-only**. Dhan's scrip master likewise retains **zero** expired contracts.

➡️ **Consequence:** real pinned-strike option history is **forward-collectable only**, on *either* broker. We already have a verified plan for that on Dhan (`/optionchain` snapshots), so Kite adds nothing here.

---

## Cost — and a correction to the "Dhan is free" assumption

- **Kite Connect:** ₹500/month per app (bundle includes WebSocket + historical).
- **Dhan:** order/execution APIs are free, **but the *Data* API is ₹499/month** (real-time + historical) — **not free.**

So **cost is a tie.** It is neither a reason to prefer Dhan nor a barrier to Kite. ⚠️ **Action:** confirm our current Dhan data-plan billing, since we depend on that data API.

---

## Verdict & the role of Monday's WS-shadow result

**Verdict: stay on Dhan.** Switching would actively *harm* us — an auth regression for unattended crons, less history (3y vs our 5y), a weaker live option chain (lose one-call IV+Greeks), and weeks of re-engineering against a 24M-bar cache that's already Dhan-built — to gain essentially nothing we need.

**The only conditional exception is gated on Monday (2026-06-22):**
- **If the fixed Dhan WS passes the shadow CLEAN** → even the cross-check rationale evaporates. **Do nothing on Kite.**
- **If the Dhan WS still fails Monday _and_ we cannot calibrate the fix from the raw frames we're already capturing** → *then* reconsider Kite purely as a **read-only cross-check oracle** (its clean `exchange_timestamp` validates our Dhan bars), **never as the primary/trade feed.** Even then, exhausting the Dhan fix from captured frames is cheaper than onboarding a second broker with harder auth.

The real unlock for our actual data gap remains **forward `/optionchain` collection on Dhan** — independent of this decision.

---

## Conditional operator action list — ONLY if, post-Monday, you decide a cross-check oracle is warranted

*(If Dhan passes Monday: no action.)*

1. **Zerodha account holder** logs into the Kite developer console (`developers.kite.trade`) → create an app → obtain **`api_key` + `api_secret`**. Cost: **₹500/month** per app, billed on use.
2. **Register a redirect URL** for the login postback.
3. **Daily auth** (plan for this being semi-manual — headless automation is fragile and discouraged by Zerodha):
   `kite.zerodha.com/connect/login?v=3&api_key=…` → user logs in + 2FA → capture `request_token` from the redirect → `SHA-256(api_key + request_token + api_secret)` → POST `/session/token` → `access_token` (expires 6 AM next day).
4. **Integrate read-only:** `pykiteconnect` KiteTicker (full mode) as a parallel tick stream → compare bucketed bars vs Dhan as an oracle. **Read-only / paper, no orders.**

*Nothing in this list has been done. It executes only on your explicit go.*

---

## Bottom line

Keep Dhan. Validate the Dhan WS fix on Monday; stand up forward `/optionchain` collection (pending your approval). Kite would cost the same money, impose harder daily auth, and require migration effort to gain a single feed-clock advantage we've already engineered around. Revisit Kite-as-oracle only if Monday's shadow fails.

---

### Sources (live, fetched 2026-06-20)
- Kite historical (expired tokens, `continuous` futures-only): `https://kite.trade/docs/connect/v3/historical/`
- Kite auth / daily 6 AM token expiry: `https://kite.trade/docs/connect/v3/user/`
- Kite market quotes (no chain/Greeks endpoint): `https://kite.trade/docs/connect/v3/market-quotes/`
- Kite WebSocket (`exchange_timestamp`): `https://kite.trade/docs/connect/v3/websocket/`
- KiteTicker post-reconnect tick-loss bug: `https://kite.trade/forum/discussion/6841`
- NSE token reuse / expired-option historical: `https://kite.trade/forum/discussion/12880`, `https://tradingqna.com/t/.../194727`
- Kite Connect pricing (₹500/mo): `https://zerodha.com/products/api/`
- Dhan data API ₹499/mo subscription: `https://dhan.co/support/platforms/dhanhq-api/how-does-the-dhanhq-data-api-subscription-work/`
