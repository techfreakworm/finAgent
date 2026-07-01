# FG-3 Results — Measured Fills for the 0DTE Straddle (panel-verified)

**Date:** 2026-07-02 · **Status:** COMPLETE (adversarially verified: 4-lens panel + synthesis; two
original sub-claims withdrawn, see Honesty ledger) · **Verdict: CONTINUE 1-lot, unchanged config —
PRELIMINARY (stop-cost calibration n=2 expiry-Tuesdays; re-finalize at ~5+ stop-fire days).**
Paper-only; no trading change; live-order lockout intact.

**Question attacked:** the #1 known risk of the lone surviving edge (gen6 0DTE ATM short straddle,
09:20 / 25% basket stop / flat 15:10) was fill-sensitivity — idealized BS-mid PF 1.466 fenced /
1.534 holdout degraded to 1.251 / 1.084 at 2×/3× *assumed* slippage. FG-3 replaces the assumptions
with **measured** fills from the forward /optionchain collector (9 sessions 2026-06-21→07-01,
~525-532 snaps/day, incl. BOTH stop-fire expiry Tuesdays 06-23 & 06-30 with real per-strike book).

## Machinery gate (V0)
The harness drives the **unmodified** `scripts/zerodte_straddle.py:run_day` via its module slip
globals. Fed the OLD constants it reproduces gen6 on **all six** curve points (tol 5e-4):
1× 1.466/1.534 · 2× 1.251/1.299 · 3× 1.084/1.115 (fenced/holdout). Only the three fill inputs
change between V0 and the measured runs. Panel-confirmed: no global leak, order-independent,
spot-cache slip-invariant.

## What was measured (Track 1 — robust)
- **ATM half-spread ≈ 0.12–0.14% per leg** (entry window 0.00128 expiry-Tue median, intraday
  0.00138, pooled 0.00117-0.00131; abs ~0.10 pt). The old `ENTRY_SLIP=EXIT_SLIP=1%` was **~7-8×
  too pessimistic**. Spreads stay tight through violent stop minutes (refutes gen7's
  "spreads gap on stops" mechanism).
- **Depth:** L1 quantity < 1 lot (75) on only 8.0% of entry-window / 6.2% of all ATM snaps →
  negligible for 1-lot; L1-only residual flagged.
- last15 *fractional* spread (0.00336) is denominator-inflated near expiry (premium collapses,
  rupee spread ≈ a tick); used for the square-off anyway = conservative, immaterial (<0.03 PF).

## Re-run under measured fills (Track 2 — the decision table)
`ENTRY_SLIP=0.00128, SQUAREOFF=0.00336, STOP = 0.00138 (intraday base) + drift`, where **drift is a
STRESS parameter, not a measurement** (see Honesty ledger). 258 synthetic expiry days:

| stop-drift | fenced PF | holdout PF | fenced net | maxDD | per-year PF (21→26) |
|---|---|---|---|---|---|
| **0.00 (spread-only, measured)** | **1.722** | **1.814** | ₹88,907 | −10,635 | 1.26 / 1.34 / 1.81 / 2.19 / 1.89 / 1.41 |
| 0.02 | 1.570 | 1.653 | ₹77,031 | −12,269 | 1.15 / 1.22 / 1.65 / 2.00 / 1.73 / 1.28 |
| **0.05 (stress midpoint)** | **1.387** | **1.459** | ₹59,217 | −14,719 | 1.02 / 1.07 / 1.46 / 1.76 / 1.53 / 1.13 |
| 0.11 | 1.125 | 1.182 | ₹23,588 | −20,782 | 0.83 / 0.87 / 1.19 / 1.43 / 1.24 / 0.92 |
| 0.15 | 0.999 | 1.049 | −₹165 | −25,907 | ~breakeven |

**Reading (panel-corrected framing):**
1. **Realistic entry/exit fills IMPROVE the edge** (1.466→1.722 fenced / 1.534→1.814 holdout).
   This is the robust, well-measured core finding — the fill-sensitivity fear was pointed at the
   wrong component.
2. **Overall survival is CONDITIONAL on stop-continuation cost**, which is the one thin input:
   the stop fires on 57% of fenced days, so PF is nearly a pure function of drift. Observed
   clean-book detection-lag drift on the 2 real stop days: **−1.15% / +1.61% / +5.26%** (three
   replays: collector-ATM both days + live strike 06-23). These estimates are
   trigger-time/entry-premium SENSITIVE (same day 06-23 gives −1.15% at K24050 vs +5.26% at
   K24100 purely because the barrier is first touched at different minutes) — treat them as
   fragile points; the true drift dispersion is plausibly WIDER, which is an independent reason
   the stress band, not any point, governs (and cuts against ~0%-drift complacency: worst
   observed is a real +5%). The 0–15% band brackets these with heavy pessimism;
   **the pre-reg gate (PF≥1.10) holds across 0–11% and breaches only at ~11.5%;
   breakeven ~15%** — no observed day is near either.
3. **Regime transport disclosed:** 2026-measured spreads applied to 2021-22 are optimistic there
   (per-year PF 1.02/1.07 at the stress midpoint). The **go-forward read is the Tue-era holdout
   column (1.459 central-stress, 1.814 spread-only)** — the regime the spreads were measured in.

**Pre-registered decision:** real-fill PF ≥ 1.10 → **CONTINUE 1-lot** (holds across the entire
plausible band). PF < 1.0 would have meant stop/rethink — not triggered under any observed-drift
scenario. **PRELIMINARY:** the binding calibration rests on n=2 stop days; do NOT treat as a
final fill model or any go-bigger signal; re-estimate at ~5+ stop-fire days (~months).

## Live cross-check (Track 3 — illustrative, n=2)
Dual-strike real-book replay (entry=real bid, marks=real mid, stop on combined mid ≥1.25×, exit=
real ask +1-min lag) vs the live paper fills:

| day | strike | replay net | live net | reconciliation |
|---|---|---|---|---|
| 06-23 | 24050 (collector-ATM) | −₹1,435 | — | different position than live |
| 06-23 | **24100 (live strike)** | **−₹2,729** | −₹3,548 | exit ask-sum 139.4 vs live 139.85 ✓ |
| 06-30 | 23950 (both) | −₹2,237 | −₹3,155 | exit 157.5 vs live 160.0 ✓ |

- **Exits reconcile closely** once the strike is matched — the collector book is a valid
  fill-truth reference.
- The live-vs-replay net gap decomposes to **stale-low live ENTRY prints on fast opens**
  (₹689 / ₹653 of credit) **+ the flat ₹650/leg constant** (₹1,300/day, never measured), NOT
  cheaper stop fills. (Re-confirms the REST 1-min option feed is unreliable intra-open.)
- **Fold-in:** replace the paper engine's flat ₹650/leg with the measured state-dependent
  half-spread model (rupee-floored) — in progress on `feature/measured-fill-model`.

## Honesty ledger (claims corrected by the adversarial panel)
- **WITHDRAWN:** "+11.2% measured stop-drift (06-23)" — a stale-print artifact: it anchors to the
  live 11:56 stop, where the clean book's combined mid (92.10) was *below* the stop level (110.28);
  the clean book never triggered there. Clean-book drift at the true trigger: −1.15%.
- **WITHDRAWN:** "the flat ₹650/leg OVERSTATED the live losses" — confounded by strike mismatch
  (replay 24050 vs live 24100; correct-strike loss ≈ 2× the mismatched one) and by the stale
  entry basis. The ₹650-replacement recommendation stands; the "overstated" claim does not.
- **RE-LABELED:** the {2,5,11}% drift band is a pessimistic STRESS band (5% = midpoint,
  coincidentally ≈ the worst observed clean-book drift 5.26%), not a fitted state-dependent model.

## Folded-in sub-tests
- **TN-1 fill-reliability sub-thesis: FALSIFIED for ATM** — quotes are tight and live through stop
  minutes; a spot trigger buys no fill advantage. The 25% premium basket stop stays.
- **Entry-timing (real-book):** 09:16 is high-variance on fast opens (06-30 straddle mid ranged
  22% in 5 min); 09:20+ settles. PRELIMINARY n=2 — no config change; revisit with more Tuesdays.

## Standing actions
1. Keep trading 1 lot, ungated, unchanged config (live n=3 record +3,726/−3,548/−3,155 is within
   the designed loss profile; both losses were stop-capped trend days).
2. `feature/measured-fill-model`: measured slippage into the paper engine (review before merge).
3. Re-estimate drift & spread at each new expiry-Tue; finalize the fill model at ~5+ stop days.
4. Artifacts: `reports/fg3/{fill_model_params,gen6_measured_fills_eval,realbook_replay}.json`,
   spec `reports/fg3_slippage_spec_impl.md`, harness `scripts/fg3_measured_fills.py`.
