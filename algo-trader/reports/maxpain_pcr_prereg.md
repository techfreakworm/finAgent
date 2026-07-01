# Max-Pain / PCR / OI-Wall Range-Favorability Filter for the 0DTE Straddle — PRE-REGISTRATION

**Date:** 2026-07-02 · **Status:** PRE-REGISTRATION — awaiting operator/lead review; **nothing is
fit and no verdict is drawn until this is approved and evaluation data accrues forward.** Paper-only,
live-order lockout intact. Designed by algo-brain (sequential-thinking gate; grounded in the 9
collected sessions, DESIGN-ONLY). The ungated Tue 0DTE ATM short straddle stays PRIMARY and is the
CONTROL; this filter is only allowed to decide **trade vs skip**, never to alter the trade.

## Honest prior (read first)
**LOW** (concur with the lead). Max-pain/PCR "pinning" is a real dealer-gamma phenomenon but is
**weak intraday and strongest in the last hour**, not from the open; a morning reading has limited
power over the *whole-day* range, and a 25%-basket stop can fire on an exogenous move long before
any pin asserts. P1 already showed that a day-quality gate (VIX terciles) which looked great fenced
**FAILED OOS** because it discarded the winners — this filter risks the identical failure. And a
DESIGN-ONLY red flag (n=2, burned, hypothesis-generating ONLY, see §7): both collected expiry
Tuesdays (06-23, 06-30) had a **small** morning pin-gap (~0.17–0.20%, i.e. naively "pin-favorable")
yet **both were trend-day LOSSES**. So the realistic expectation is **NULL / early-kill**. The value
here is **cheap falsification of a real-OI signal from data we are collecting anyway**, plus a small
chance of a genuine range-predictor. This protocol is designed so a null is clean and a positive is
hard to fake — same discipline as the momentum pre-reg.

## Hypothesis
On a 0DTE expiry morning, when spot opens **near the max-pain strike** with **balanced two-sided OI
positioning** (PCR near balance, OI walls bracketing spot), dealer hedging tends to **pin spot into
a tighter realized range** → the short straddle's ideal weather (theta harvested, stop not hit).
When spot is **far from max-pain** / positioning is **lopsided**, a trend/breakout day is more
likely → the straddle's kill scenario. This is a **range / day-quality covariate, NOT a direction
call** — the explicit distinction from the KILLED VIX body-gate (that gate tried to time *whether*
to be in the vol trade on a direction/level proxy; this predicts *realized range*, to which the
straddle P&L is monotone).

## Signal source — FORCED to prior-session-close OI (documented data-quality finding)
**Decisive finding from the collected data (DESIGN-ONLY):** the collector's **live `oi` field is
unreliable in the morning.** Total-book live OI inflates to **150–187% of the prior close by 09:25,
then collapses to ~10–15% at 09:30** (a feed recalc/reset artifact, not real dynamics). It is
therefore unusable as a causal morning signal. In contrast, **`previous_oi` (prior-session CLOSING
OI per strike) is fully known before the open, stable, and yields sensible values.** This is also
the theoretically-correct source: pinning is about the **standing dealer book that must be hedged
into expiry** (yesterday's close), not the first-5-minutes 0DTE churn.
**→ ALL signals below are computed from `previous_oi`, once, PRE-OPEN. Zero intraday-drift risk
(constraint (d) satisfied by construction).** Live-OI and volume-based variants are
**robustness-report-only**, never decision inputs.

Reference spot `spot0` = the first clean spot print in 09:15–09:20 (the straddle's entry spot).
Chain = the EXPIRING (dte=0) leg over the collected strike range (ATM±600 @ 50 pt).

## Signal definitions (all from `previous_oi`, causal)
- **MAX-PAIN strike** `MP = argmin_K [ Σ_s max(K−s,0)·Oce(s) + Σ_s max(s−K,0)·Ope(s) ]`
  (settle strike minimizing total intrinsic paid to holders = dealers' least-pain pin).
  *Design-only check: MP = 24100 (06-23), 24000 (06-30).*
- **PIN-GAP** `= |spot0 − MP| / spot0` — **PRIMARY signal, used CONTINUOUS.** Small = spot already
  near the pin = pinning-favorable. *(06-23: 0.20%; 06-30: 0.17%.)*
- **PCR** `= Σ Ope / Σ Oce`; **PCR-IMBALANCE** `= |ln PCR|` — **DIAGNOSTIC only** (direction-lore,
  weakest theory). *(PCR 0.89 / 0.76 design-only.)*
- **OI WALLS** `CALL_WALL = argmax_K Oce(K)`, `PUT_WALL = argmax_K Ope(K)`; **wall-bracket flag** =
  `PUT_WALL ≤ spot0 ≤ CALL_WALL`; **wall-concentration** = max-wall-OI / same-side-total-OI —
  **DIAGNOSTIC only.**

## Degrees-of-freedom ledger (near-zero discretion)
Signal source `previous_oi` = **forced** (data quality). Primary signal = **PIN-GAP, continuous**
(canonical, 0 free params). Trade/skip classifier for the P&L stage = **within-sample MEDIAN
pin-gap split** (guarantees a ~50/50 partition, symmetric, **no absolute threshold to tune, no
P&L-fitting**, 0 free params). Trade itself = the **frozen gen6 config** (entry 09:20 / 25% basket
stop / flat 15:10), unchanged. PCR, walls, absolute thresholds, live-OI, volume = **report-only,
never decision.** ⇒ **~0 discretionary free parameters.**

## Power reality — stated up front (the B-triage lesson)
Per-trade 0DTE-straddle P&L SD ≈ **₹2,500–3,500** (gen6: avg +₹303, worst −₹5,106; live
+3,726/−3,548/−3,155). Consequences (α=.05, 80% power):
- Detecting a realistic filter edge of **~₹500–1,500/day** on P&L needs **~50–140 expiry Tuesdays
  (~1–3+ years)**. At n=20 Tue (≈ end-2026) the **MDE is ~₹2,200–3,100/day** — only an implausibly
  huge effect is detectable. **A P&L-significance KEEP/KILL is therefore NOT reachable near-term.**
- **We do NOT pose an unreachable P&L significance bar as the near-term gate** (that was the
  B-triage trap). Instead the near-term endpoint is the **MECHANISM** (below), which IS powered soon.

## Two-stage decision rule

### STAGE 1 — MECHANISM test (near-term; powered at n≈16–25 eval-Tue)
Does morning pin-proximity predict a tighter realized range? **Realized range** = intraday
`(spot_high − spot_low) / spot_open` (robustness: `|close−open|/open`, and the straddle's max
adverse excursion). Realized-range CV ≈ **0.71** across the collected sessions — **far tighter than
the ±₹3–4k P&L swing**, so this is estimable fast.
- **Test:** one-sided **Spearman ρ(PIN-GAP, realized_range) > 0** (bigger gap → bigger range).
  Powered: ρ=0.6 detectable at n≈16, ρ=0.5 at n≈23, ρ=0.4 at n≈37 (1-sided, 80%).
- **EARLY-KILL** if, at the n=25 look, ρ ≤ 0 or its upper CI excludes ρ ≥ ~0.3 (association absent
  or wrong sign) → the filter **cannot** help P&L because it cannot even predict range. Clean,
  **powered** near-term null (expected ~early 2027).
- Ancillary power: log the **same signals for the non-expiry near-weekly (dte=7)** chain each
  session (more days) — mechanism-test-ONLY auxiliary sample (never enters the 0DTE P&L decision).

### STAGE 2 — P&L gating test (long-horizon; MONITORED, n≈40+, ~2027+)
Only pursued if Stage-1 mechanism SURVIVES. Median-split each eval-Tue into FAVORABLE (trade) vs
UNFAVORABLE (skip) on PIN-GAP; compare against the ungated control on the SAME days. **KEEP** (deploy
a skip-rule) requires ALL:
- **K1** favorable-only PF ≥ ungated PF **+0.15** AND favorable expectancy ≥ ungated **+₹300/day**.
- **K2 (MANDATORY — the P1 lesson):** **SKIPPED-days mean P&L < 0**, on **evaluation-Tue only
  (OOS)** — we must have correctly avoided net-losers. *If skipped days are net-POSITIVE → KILL
  (P1's exact failure mode).*
- **K3** two-sample favorable > unfavorable, significant at the look's α-boundary via
  **block-bootstrap / sign test** (trades not iid).
- **K4** skip fraction ∈ **[15%, 60%]** (skips ~0% ⇒ inert; ~100% ⇒ just "don't trade").
- **K5** survives **2× fill stress AND the FG-3 measured-fill model** (consistency with our fill work).
**KILL** at any powered look if K2 fails decisively, or the effect CI excludes a useful edge.

### Interim-look schedule + alpha-spending (pre-committed; eval-Tue count)
Evaluation begins at the **3rd forward expiry-Tue** (see §7). Looks:
| look | n eval-Tue | ~when | purpose |
|---|---|---|---|
| 1 | 8 | ~early Sep 2026 | mechanism early-kill screen (sign + CI); no KEEP |
| 2 | 15 | ~late Oct 2026 | mechanism early-kill + effect-vs-MDE |
| 3 | 25 | ~Jan 2027 | mechanism VERDICT; first powered P&L look |
| 4 | 40, then annual | ~Apr–May 2027+ | powered P&L KEEP/KILL |
Two-sided **α=0.05 total**, spent **O'Brien-Fleming-style** (very stringent early, e.g. nominal
α≈0.001 at looks 1–2) so peeking cannot inflate a false-KEEP. Early-KILL uses a **directional
sign+CI** rule (killing early only risks a false-negative on a weak edge we could not have banked
anyway — an acceptable asymmetry). No decision between scheduled looks.

## What the harness must log (per expiry-Tue → append one row to `reports/maxpain_pcr_eval.jsonl`)
**Pre-open signal block (from `previous_oi`, causal):** date, expiry, spot0, per-strike prev_oi
CE/PE over the collected range, MP, PIN-GAP, PCR, |ln PCR|, CALL_WALL, PUT_WALL, wall_bracket_flag,
wall_concentration, running-median-split label (FAVORABLE/UNFAVORABLE). **End-of-day outcome block:**
spot open/high/low/close, realized range_pct, |close−open|_pct, the day's straddle
entry/exit/net-P&L (from the live 0DTE trade), stop_fired flag, straddle MAE. **Ancillary:** the same
signal block for the dte=7 near-weekly (mechanism-only). Also persist a `_designonly` flag on the
06-23/06-30 rows so they can never silently enter an evaluation aggregate.

## Data provenance / burned set (constraint (e), explicit)
The **2 collected expiry Tuesdays 06-23 & 06-30 are DESIGN-ONLY / BURNED for evaluation** — used
solely to (i) verify the signals are computable against the real schema, (ii) establish the
live-OI-unreliability finding that forced the `previous_oi` choice, and (iii) generate the honest
prior. **Evaluation (all interim looks and the ρ/P&L statistics) starts at the 3rd forward
expiry-Tue and NEVER includes 06-23/06-30.** The small-gap-yet-loss observation on those 2 days is
**hypothesis-generating only**, not evidence.

## Three honest likely outcomes (mirrors the momentum pre-reg)
1. **MOST LIKELY:** Stage-1 mechanism ρ ≈ 0 → **clean, powered EARLY-KILL** (~end-2026/early-2027).
   Cheap falsification achieved; move on.
2. **POSSIBLE:** mechanism ρ > 0 holds, but the P&L benefit stays **unconfirmable for years** →
   honest verdict *"pin-proximity predicts range faintly but a tradeable straddle edge is not
   demonstrable at our n — keep monitoring, do NOT deploy."*
3. **LEAST LIKELY:** mechanism AND the powered P&L gate (K1–K5, esp. K2 skipped-days-negative OOS)
   both clear → deploy a skip-rule, treated with the same suspicion as any positive (concentration
   check, OOS skipped-negative mandatory, 2×/FG-3 survival).

## Guardrails carried from prior kills
- **P1/VIX-gate:** a gate that discards days must **prove the skipped days were net-negative, OOS**
  (K2) — not merely reshuffle P&L. Non-negotiable.
- **B-triage:** MDE stated; **no unreachable significance bar posed as a near-term gate**; sequential
  design with pre-committed interim looks + alpha-spending; the powered near-term endpoint is the
  low-variance MECHANISM, not high-variance P&L.
- **Signal causality:** `previous_oi`, pre-open — no intraday leakage possible.

---
**ERRATUM 2026-07-02 (diagnostic field only):** the design-only PCR reference values quoted above
(0.89 / 0.76) came from a hand-windowed strike basket; the harness's canonical OI-PCR over the full
dte=0 basket computes 0.965 (06-23) / 0.671 (06-30). PCR is explicitly DIAGNOSTIC-only (never in the
decision rule), so this changes nothing pre-registered; the harness values are canonical going
forward. Primary signals (MAX-PAIN, PIN-GAP) reproduce exactly.
