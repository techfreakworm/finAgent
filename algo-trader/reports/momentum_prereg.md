# NIFTY-50 Cross-Sectional Momentum — PRE-REGISTRATION (for operator review)

**Date:** 2026-06-26 · **Status:** PRE-REGISTRATION — **awaiting operator approval; nothing is fit or run until approved AND the hard data prerequisites are met.** · Separate **positional/swing** research track; the intraday **0DTE/VRP options edge stays PRIMARY**. Paper-only, structural live-order lockout intact. (Designed by algo-brain via sequential-thinking; finalized by lead.)

## Honest prior (read first)
Momentum is the most robust equity anomaly globally (Jegadeesh-Titman '93; Asness '13), **BUT** in liquid India large-caps it's crowded/decayed; our cross-section is only ~50 names (a quintile = 10 names = few independent bets); and survivorship + corp-action adjustment + positional friction can **each** erase it independently. **Realistic expectation = NULL or marginal.** This protocol is designed so a null result is clean and a positive is hard to fake.

## Hypothesis
A long-only top-quintile NIFTY-50 momentum sleeve delivers positive risk-adjusted active return vs an equal-weight benchmark — and/or a **diversifying hedge** to the short-vol book (the real value to us).

## Hard data prerequisites (MUST be in place before any fit)
1. **Corp-action-ADJUSTED daily closes** — yfinance `Adj Close` (split + dividend adjusted). QC gates: (a) reconcile every |daily return| > 15% to a known corp action or discard that name-period; (b) **scrub the phantom-date artifacts** confirmed in our cache (RELIANCE/TATAMOTORS 2021-08-07/09 symmetric ±18-25% reversals, 2022-04-09/11 — bad odd-date closes, not corp actions); (c) cross-check adjusted-source returns vs our cached raw on non-corp-action days (must match ≤0.1% → validates same instrument/tz).
2. **Point-in-time NIFTY-50 membership history** (semi-annual reconstitution add/drops). ⚠️ **No membership file exists anywhere in our data** — this is the one genuine open item (see Decision below).
3. **0DTE per-day P&L stream** for the hedge correlation — exists (`scripts/zerodte_straddle.py`), aligned to the tradeable window (2022-07+).

## Signal grid (deliberately tiny — controls multiple-comparisons)
- **Formation J ∈ {6, 12} months** — the two canonical academic windows; the **only** real decision free parameter (2 values).
- **SKIP = 1 month LOCKED** (removes short-term reversal + bid-ask-bounce). **HOLD = 1 month LOCKED**, monthly non-overlapping rebalance. → **Grid = exactly 2 configs: (6,1,1) and (12,1,1).**
- **Ranking metric = raw cumulative formation return** on adjusted closes (canonical) = decision metric. Vol-scaled "Sharpe-momentum" reported as robustness-only.

## Construction + ₹5L sizing
- **PRIMARY (deployable, drives DEPLOY/KILL):** long-only **top-quintile (10 names), equal-weight**, monthly rebalance. Benchmark = **equal-weight all-50** (not cap-weighted NIFTY — avoids confounding momentum with a size tilt).
- **DIAGNOSTIC (not deployed — we won't short India positionally):** long/short WML (top-minus-bottom quintile, dollar-neutral) → confirms the anomaly exists + quintile-spread **monotonicity** (Q5>…>Q1 = real factor, not a top-name fluke).
- ₹5L sizing: 10 × ₹50k cash equity — fine (no lot/0-lot problem). The thinness is **statistical** (10 names, ~48 monthly periods), not a sizing impossibility.

## Friction (positional cash equity, turnover-dependent)
~**0.40% round-trip per ROTATED name**: equity-**delivery** STT 0.10%/side = 0.20% RT *(confirmed — delivery rate, distinct from our 0.05% futures STT)* + brokerage/exch/stamp/GST ~0.05-0.10% + impact ~0.10%. Applied to **actual per-rebalance traded notional** (held names carry zero friction); backtest reports realized turnover. **Stress: report 1× AND 2× friction** — decision must hold at ≥1× and survive 2×.

## Split (frozen, pre-committed)
- First tradeable ~**2022-07** (needs 12mo+1mo history) → ~48–54 monthly periods (small — drives the stats).
- **FENCED in-sample → 2025-03-31** (~33 periods): selects J=6 vs J=12 via pre-registered rule (higher IS IR that also passes the IS gate).
- **FROZEN HOLDOUT 2025-04-01 → 2026-06-11** (~14 periods): IS-winning config **only, run ONCE**. 14 obs = a sign+magnitude **consistency** check (must not contradict), not a powered test — the primary quantitative bar is the IS gate.

## Statistics (small grid + small n → honest)
- **PRIMARY = annualized Information Ratio** of long-only top-quintile vs EW-50; also Sharpe + PF.
- **Newey-West (lag 3)** SE on mean monthly active return.
- Multiple-testing: 2 configs → require **|t| ≥ 2.5** (not 1.96) **AND Deflated Sharpe Ratio > 0** (Bailey-López de Prado).
- WML diagnostic: mean monthly WML + NW t + quintile-spread monotonicity.

## Pre-registered thresholds (numeric, LOCKED)
**DEPLOY-AS-STANDALONE** (long-only, net 1× friction) — ALL of:
1. IS annualized IR ≥ **0.5** vs EW-50.
2. IS active-return NW **t ≥ 2.5** AND **DSR > 0**.
3. Net **positive after 2× friction**.
4. Sleeve **maxDD ≤ 25%**.
5. HOLDOUT: active return **same sign** as IS **and IR ≥ 0** (must not contradict).
KILL if any fails.

**ANOMALY-EXISTS diagnostic** (informs keep-researching even if long-only fails): WML monthly mean > 0, NW t ≥ 2.0, ~monotonic spread. If WML significant but long-only-after-friction fails → honest verdict: *"momentum exists faintly but isn't harvestable long-only after our friction."*

## The momentum-as-short-vol-HEDGE check (could justify a MODEST standalone)
Pair {straddle daily P&L, momentum daily return} on shared 0DTE days (2022-07+); weekly aggregation secondary. Three measures:
1. Overall Pearson + Spearman corr (hypothesis ≤ 0).
2. **TAIL (the real hedge test):** E[momentum return | straddle in its WORST quartile of days] — want ≥ 0 (momentum *pays* when the straddle hurts, e.g. trend days like 06-23).
3. Combined-book daily P&L (straddle 1-lot + momentum sleeve at matched capital) vs straddle-alone: Sharpe + worst-day/maxDD.

**HEDGE-DEPLOY rule** (deploy a small diversifying sleeve even if standalone IR<0.5) — ALL of:
(a) standalone **net ≥ 0 after 2× friction** *(non-negotiable — cash is the trivial uncorrelated positive "hedge"; the sleeve must beat cash)*; (b) overall corr ≤ +0.2; (c) E[mom | straddle worst-quartile] ≥ 0; (d) combined Sharpe ≥ straddle-alone × 1.10 AND combined maxDD/worst-day not worse.
**If standalone is NEGATIVE after friction → KILL regardless of correlation.**

## Degrees-of-freedom ledger (how little room to fish)
Decision free params ≈ **1** (J ∈ {6,12}). LOCKED: skip=1, hold=1, quintile, equal-weight, raw-cum-return ranking, long-only=decision / long-short=diagnostic, single frozen holdout, all thresholds numeric & pre-committed. Tercile + vol-scaled = robustness-report-only (never decision).

## Three honest likely outcomes
1. **MOST LIKELY:** long-only-after-friction flat/marginal (IR<0.5) → clean null, KILL standalone.
2. **POSSIBLE:** passes hedge gate → deploy small diversifying sleeve; options stays primary.
3. **LEAST LIKELY:** clears standalone deploy gate → treat with the same suspicion as any positive (holdout consistency + monotonic spread + 2× survival).

---

## ⇒ DECISION NEEDED FROM OPERATOR
**Point-in-time NIFTY-50 membership history** (semi-annual reconstitution add/drops, 2021–2026) is a genuine data dependency — using *today's* 50 names historically manufactures spurious momentum (today's members were promoted for trending up = survivorship bias). Options:
- **(A) Source it** (NSE indices archives / published reconstitution lists). Recommended if feasible — gives a clean, deployable test. algo-brain is scoping the sourcing approach.
- **(B) If unobtainable:** run with today's constituents as a **survivorship-CONTAMINATED upper-bound scout** — it can **KILL** (if even the optimistic version fails) but can **NEVER DEPLOY**. Still useful as a cheap kill-test.

**Resolved (no input needed):** equity-delivery STT = 0.10%/side (correct); yfinance `Adj Close` = split+dividend adjusted (suitable for momentum ranking).

**On your approval** (and which membership path), I'll: build + QC the adjusted-data prerequisite → implement → run the pre-registered test **once** → report survive/kill + the hedge correlation. Nothing fits until then.

---

## UPDATE 2026-06-27 — data prerequisites RESOLVED (post NSE-research; supersedes the prereq/Resolved notes above)
A read-only WebFetch research workflow + lead spot-checks corrected two earlier assumptions:
- **Price data is ALREADY split+bonus back-adjusted in our Dhan cache** (verified: TATASTEEL 1:10 split 2022-07-28 → 94.75/96.00/100.20 and RELIANCE 1:1 bonus 2024-10-28 → 1334/1340/1345, both SMOOTH/no gap). → **Use the Dhan cache for survivors; do NOT use yfinance Adj-Close** (it adds dividend adjustment = a different convention; momentum runs on *price*, so split+bonus-adjusted price is correct — mixing would double-count). Residual cleaning only: the **TATAMOTORS demerger** (2025-10-14, −41% — demergers aren't ratio-adjusted) + **Aug-2021 phantom prints** → targeted scrub, not a rebuild. (My earlier "cache is UNADJUSTED" was an over-generalization from those two artifacts.)
- **Point-in-time membership is recoverable FREE — no Playwright, no paid source.** The 8 in-window reconstitutions (add/drop + effective dates) are reconstructed from Wikipedia + `nsearchives.nseindia.com` PDFs + news; build a ~16-row `data/reference/nifty50_membership.csv`.
- **Removed-but-still-listed names** (GAIL, IOC, SHREECEM, UPL, DIVISLAB) have live Dhan security_ids → backfill via `scripts/backfill.py` (same adjusted 1-min). **Delisted/merged** names (HDFC →2023-07; LTIMINDTREE 2023-07→2024-09) are absent from the scrip master → yfinance **daily** for their in-index spans only (the sole yfinance use; mixed-granularity, kept in a separate marked dir).
- **Playwright is NOT needed for this dataset** (only the Akamai-gated `www.niftyindices` app-host would need it, and everything there is substitutable). No operator data action required.

**Net data effort:** ~1 day curation (membership CSV + scrub) + ~2 h backfill. No paid source, no Playwright. Decision sequencing unchanged: **B-triage** (current-50, kill-only) → **A** (clean point-in-time) if B doesn't kill.
