# FG-3 — Measured Slippage / Implementation-Shortfall Model (IMPLEMENTATION-READY)

**Status:** implementation-ready (algo-brain, 2026-07-02). Supersedes the pre-draft
`reports/fg3_slippage_spec.md` (kept for provenance). Grounded in the ACTUAL
collected data + the ACTUAL live paper fills, all verified below.

**Data on hand (verified):** `data/cache/_OPTIONS/NIFTY_CHAIN_FWD/<date>.parquet`,
8 full sessions 06-22 → 07-01 (+ a 06-26 half-file), ~525-532 snaps/day.
Cadence measured: **9 sub-snaps/min in bursts 09:14-09:26 and 15:11-15:14; 1
snap/min otherwise** (~329 single-snap minutes, 23 burst-minutes/day). Two of the
eight are 0DTE expiry Tuesdays with real per-strike book: **06-23 and 06-30 — both
stop-fire trend days.** Schema per row: snap_ts (IST ISO string, sub-second),
expiry, dte (0 = expiring, 7 = next weekly), spot, strike, opt_type (CE/PE),
last_price, top_bid_price, top_bid_quantity, top_ask_price, top_ask_quantity,
implied_volatility, oi, volume, previous_close_price, previous_oi,
previous_volume, average_price, security_id, delta, gamma, theta, vega.

**Live-fill truth source (verified):** `data/paper/paper-0dte.db`, table `fn_trades`,
6 rows = 3 trades × 2 legs (06-16, 06-23, 06-30), each with entry_price, entry_ts,
exit_price, exit_ts, exit_reason, gross/net_pnl, costs_total, **slippage_paid**.

---

## 0. HEADLINE FINDINGS (measured, before any build)

These four measured facts overturn the guessed fill model and set the thesis. All
numbers below are from the 2 real 0DTE Tuesdays unless noted.

1. **ATM half-spread is ~0.13-0.15% per leg (abs ~0.10pt), and STABLE** across
   entry window, midday, and close — and *even on stop-fire minutes*. i.e. our
   modeled `ENTRY_SLIP=1%` / `EXIT_SLIP=1%` were **~7× too pessimistic**.
2. **Spread does NOT widen on fast/stop minutes.** 06-30 stop fired on a 19 pt/min
   move: ATM half-spread there 0.04-0.19% (abs 0.05-0.25 pt). 06-23 stop on a
   16 pt move: 0.05-0.19%. NIFTY ATM weekly MMs keep tight quotes through violent
   minutes. **This REFUTES gen7's core assumption** (`STOP_SLIP=2.5%`,
   "spreads widen nonlinearly on stop days"). The stop cost is real but it is NOT
   a spread-widening cost.
3. **The real stop cost is MID-CONTINUATION over the 1-min detection lag**, and it
   is large + one-sided on fast moves. Measured straddle-mid drift from the
   trigger minute to +1 min: **+1.8% (06-30), +11.2% (06-23)**; to +2 min +3.4% /
   +17.2%. So the true "stop slip" ≈ `half_spread (~0.15%) + continuation_drift
   (~2-17%)`. gen7's 2.5% was directionally right but mis-mechanised and
   understated on the worst day.
4. **The live "₹650/leg" was never measured — it is a FIXED CONSTANT the paper
   engine subtracts** (all 6 legs show exactly `slippage_paid=650.0`). The
   collector lets us measure real fill cost for the FIRST time. Measured ATM
   spread cost ≈ 0.15% × ~100-150 pt straddle ≈ **₹11-17/leg** — ~40-60× smaller
   than the assumed ₹650 on spread terms. ₹650 is too high for entry / non-stop
   exit, and too low only on the worst fast-stop leg.

**Net thesis (hypothesis for the build to confirm, NOT a pre-judged verdict):** the
fill-realistic PF very likely lands **at or ABOVE the synthetic 1× point** (which
was PF 1.466 fenced / 1.534 holdout) because the many non-stop legs get ~7×
cheaper fills, partly offset by larger-than-modeled cost on the ~57% of days the
stop fires. The pre-registered **PF ≥ 1.10 → continue-1-lot** gate looks likely to
PASS — but **PRELIMINARY**: the stop-continuation number rests on **n=2 expiry
Tuesdays**. Do NOT make a stop-trading call on n=2-3.

---

## 1. Data hygiene / QC gates (apply before any measurement)

Per-row drops: `top_bid_price <= 0` OR `top_ask_price <= 0` (pre-open 09:14 rows
are all 0/0; also stray 0-IV prints in the smile); crossed/locked books
(`top_ask_price < top_bid_price`). `mid = (bid+ask)/2`. Winsorize/flag
`half_spread_frac > 2%` as **illiquid** (never silently include; ATM won't trip it,
deep-OTM wings will — matters for P2 not here). Within a burst minute, up to 9 rows
per (strike, opt_type) — each is a distinct ~7 s snapshot; **aggregate per-minute by
MEDIAN over sub-snaps**, but keep the sub-snaps as the intra-minute path for drift.

ATM definition: `atm = round(median_spot_that_minute / 50) * 50`. For **parameter
estimation** use rolling ATM (strike nearest spot each minute) → clean distribution.
For **real-book replay (Track 3)** FIX the strike at the entry-minute ATM and follow
it (matches the held position).

---

## 2. Fill-model components (all from real top-of-book)

Definitions (per leg): `half_spread_frac = (ask - bid) / (2*mid)`. Entry SELL fill
≈ bid = `mid*(1 - half_spread_frac)`. Exit/stop BUY fill ≈ ask =
`mid*(1 + half_spread_frac)`.

**(1) Half-spread (measurable NOW, n=8 sessions).** Report median + IQR + p90 per
leg, split by state: {entry 09:16-25} · {midday 10:00-14:30} · {last 15 min} ·
{stop-fire minutes}. Measured anchors (0DTE ATM): ~0.13-0.15% all states.

**(2a) Entry mid-drift (timing cost at entry).** Straddle-mid change decision→fill
over the execution-latency proxy (1-2 sub-snaps ≈ 7-14 s), signed for a SELL
(mid RISING = favorable). Measured: near-zero in expectation but **huge variance on
fast opens** — 06-30 straddle mid ranged **22% over 09:16-20**; 06-23 ~5%. Model
the CENTRAL entry cost = half_spread only (gamma makes late entry into a move
roughly self-hedging for a straddle SELL); carry the opening-drift as an
**uncertainty band, not a mean cost**, and flag that 09:20+ entry has lower drift
variance (feeds Test (b)).

**(2b) Stop-exit mid-drift (the dominant real cost).** At the first minute the
1-min combined mark ≥ 1.25×entry (trigger), measure straddle-mid drift to +1 min
(the detection→fill lag) and +2 min, signed for a BUY-back (mid RISING = adverse).
Measured points: **+1.8% (06-30), +11.2% (06-23)** at +1 min. **n=2 → PROVISIONAL:
do NOT fit a state-dependent curve. Use a sensitivity BAND {2%, 5%, 11%}** in the
re-run and report all three; keep gen7's 2.5% as a labelled reference point.

**(2c) Non-stop exit drift (15:10 square-off).** Falls in the close burst
(15:11-14, 9 snaps/min). Cost = half_spread + small close drift; measure and
report — much cheaper than stops.

**(3) Depth / 1-lot (75) impact.** Fraction of ATM snaps where top-of-book qty on
the fill side < 75. Measured: **entry-window bidqty<75 = 3-8%; all-session
askqty<75 = 7%** (median depths 585-1235). **Negligible for ATM 1-lot** — FLAG as a
known unmodeled residual (collector stores L1 only, so when L1<75 we can flag but
not measure the walk). Material later for P2 wings; note it, don't model it here.

**(4) Implementation shortfall (₹/leg).** `decision_mid − achieved_fill`, entry +
exit, per leg — the apples-to-apples replacement for the assumed ₹650/leg.

---

## 3. Three tracks (they answer DIFFERENT questions — keep separate)

The collector covers 8 sessions / 2 expiry-Tue; the gen6 decision population is
258 synthetic expiry days. So we CANNOT "re-run gen6 over its population on the
collector" — we estimate PARAMETERS from real data, inject them into the synthetic
backtest, and separately replay the 2 real Tuesdays.

**Track 1 — parameter estimation (n=8 sessions, 2 expiry-Tue).** Produce the §2
distributions/points. Output `reports/fg3/fill_model_params.json` + a per-session
table.

**Track 2 — re-run gen6 under measured params (n=258 synthetic days).** In
`scripts/zerodte_straddle.py` replace the three constants:
- `ENTRY_SLIP 0.01 → 0.0015` (measured half-spread)
- `EXIT_SLIP 0.01 → 0.0015`
- `STOP_SLIP 0.025 → 0.0015 + stop_drift`, run stop_drift ∈ **{0.02, 0.05, 0.11}**
Re-derive the fenced/holdout PF at each stop_drift and **locate reality on the
existing synthetic 1×/2×/3× curve** (1.47/1.25/1.08 fenced). Output
`reports/fg3/gen6_measured_fills_eval.json`. **This is what the PF≥1.10 gate keys
on.** Report the number; do not pre-judge it.

**Track 3 — real-book replay (n=2 real Tue, illustrative).** On 06-23 & 06-30:
entry credit = real ATM **bid** at entry minute; intraday mark = real chain
**mids** at the fixed entry-ATM strike; stop fires on real combined mid ≥ 1.25×;
exit = real **ask** at the fill minute (+1-min lag on a stop). Compare per-day P&L
vs (a) the synthetic gen6 value-path for those 2 days and (b) the 3 live paper
trades. Output `reports/fg3/realbook_replay.json`.

**Build validation gate (adversarial-verify target):** Track-2 harness fed the OLD
constants (1%/2.5%/1%) must reproduce gen6 EXACTLY — fenced PF 1.466 / holdout
1.534 — proving only the fill inputs changed (same machinery-validation pattern as
P1/P2/TN-1). Then swap to measured params.

---

## 4. The killer cross-check (component the lead flagged) — findings

Matching the collector's real book to the live paper fills at each trade's own
entry_ts/exit_ts (nearest snap), fixed strike:

- **Strikes reconcile.** Live 06-23 traded 24100, 06-30 traded 23950 — both are the
  collector strike whose real mid is closest to the live fill. (Live ATM differs
  from collector-ATM by one 50-pt step because the two systems sampled a
  fast-moving open at slightly different sub-seconds — benign.)
- **Prices diverge on the fast open by ~5-20 pt** because spot moved ~36 pt in 34 s
  at 09:16 on 06-30 (verified sub-snap trace: 23950 CE 81.30@09:16:01 →
  66.35@09:16:35). The live paper CE prints sat 5-20 pt below the clean book →
  **the live REST 1-min option feed is unreliable intra-open** (re-confirms the
  known 06-09/06-13 stale-print risk). **The collector, not the live paper fill, is
  the fill-truth going forward.**
- **₹650/leg was assumed, not measured** (all 6 legs exactly 650.0). Real ATM spread
  cost ≈ ₹11-17/leg; real variable cost is mid-drift on fast exits (≤~11-17% of the
  straddle on the worst leg = well above ₹650 there, far below it everywhere else).

**Fold-in recommendation:** the live paper engine's flat ₹650/leg slippage should
eventually be replaced by the FG-3 measured model (half-spread + state-dependent
exit drift) so paper P&L stops being systematically mis-stated (currently
pessimistic on calm legs, optimistic on fast-stop legs).

---

## 5. Folded-in tests (FG-3 hosts, both real-data now)

**(a) TN-1 fill-RELIABILITY sub-thesis** ("spot-trigger beats premium-% when the
quote is wide/stale"): **premise largely FALSIFIED for ATM NIFTY weeklies** — the
quote is tight (~0.15%) and continuously updating even on stop minutes. On a fast
move both a premium stop and a spot stop eat the SAME mid-continuation lag (the
future gaps too) → a spot trigger gives **no fill advantage**. Report quote
reliability (spread, update-gap, crossed-book freq) around stop events to close it;
expected resolution = **keep the 25% premium stop** (consistent with the TN-1 KILL).
Low priority, cheap from the same data.

**(b) Entry-timing re-test 09:16/09:20/09:30 on REAL fills** (no synthetic-τ
artifact): per entry-time report {median half-spread, mid-drift variance over
±1 min, illiquid-flag rate} across sessions (2 expiry-Tue @ dte=0 + 6 non-expiry @
dte=7 for a broader read). Early signal: 09:16 is high-variance on fast opens;
expect 09:20-09:30 quieter — a **clean real-book replacement** for the
artifact-prone synthetic TN-2. **PRELIMINARY (n=2 expiry-Tue).**

---

## 6. Pre-registered decision + honesty guardrails

- **real-fill PF ≥ 1.10 → continue 1-lot.** **PF < 1.0 → stop/rethink.** Keyed on
  Track-2 at the CENTRAL stop_drift (5%), reported across the {2,5,11}% band.
- **This verdict is PRELIMINARY.** The 258-day backtest population is large, but the
  fill-model CALIBRATION (esp. stop-continuation drift and entry-timing variance)
  rests on **2 expiry Tuesdays**. Per the operator's guardrail and the FG-3
  pre-draft: **do NOT finalize the state-dependent stop-slip, and do NOT make a
  stop-trading call, on n=2-3.** Finalize at **~5+ stop-fire days (~months)**.
- Cross-validate the measured params against each new live paper trade's own book
  as expiry Tuesdays accumulate.

## 7. Output artifacts
1. `reports/fg3/fill_model_params.json` — §2 params + per-session table.
2. `reports/fg3/gen6_measured_fills_eval.json` — Track-2 PF vs 1×/2×/3× curve, per
   stop_drift.
3. `reports/fg3/realbook_replay.json` — Track-3 P&L on 06-23/06-30 + live-fill
   cross-check.
4. Short results note = the adversarial-verify target.

## 8. Methodological lesson (carried from TN-2, now reinforced)
Synthetic-BS with entry-implied τ0 is fragile to opening-window entry-premium noise.
The real book CONFIRMS the opening minute is genuinely treacherous (22% straddle-mid
range in 5 min on a fast open) — so the TN-2 "09:20 dip" was noise, and real fills
are the right instrument. Prefer real book; treat synthetic opening entries as
unreliable.
