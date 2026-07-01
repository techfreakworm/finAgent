# Momentum B-triage — survivorship-CONTAMINATED upper-bound scout

**Run:** 2026-07-02 · **Protocol:** frozen `reports/momentum_prereg.md` §B · **Paper/research only.**

## VERDICT: KILL — "KILLED at available power under the frozen protocol; anomaly status undecided"

NEITHER J passed the IS gate (gates 1-4) → KILL; holdout NOT run (per frozen rule).

**Final label (per adversarial-panel synthesis):** *KILLED at available power under
the frozen protocol; anomaly status **undecided** — the slot closes on **ECONOMICS**:
a thin optimistic upper bound, likely **lower** on clean data, **unconfirmable** at
this n, and with **no hedge value**.*

> **This is an UNDERPOWERED NULL, not demonstrated absence.** Point estimates are
> uniformly weakly positive (active IR 0.60/0.75; WML IR 0.14–0.41; WML mean > 0 and
> Q5 > Q1 in every cell; net-positive @2×), but at **n = 33–39** the sample **cannot
> distinguish a real IR≈0.5–0.7 edge from zero** at the pre-registered |t|≥2.5 bar
> (which implies a minimum-detectable IR of ~1.4–1.5 iid / ~1.7–1.9 NW-consistent;
> power at the observed IR ≈ 8–10%). The universe is also survivorship-contaminated,
> so the clean point-in-time value is expected **lower**, not higher. **No deploy is
> possible under any B-triage outcome.** See `b_triage_power_note.md` for the full
> power/MDE treatment (post-hoc, decision-irrelevant to this KILL).

## Headline numbers
- **J=6 (IS, n=39):** ann IR (gross) = **0.6026** (gate ≥0.5 ✓); **NW t = 0.8851** (gate |t|≥2.5 ✗); iid t = 1.09; **MDE IR** = 1.39 iid / 1.70 NW-consistent; **power @ observed IR ≈ 8%**; mean active net-2× = 0.001512 (gate >0 ✓); sleeve maxDD = 0.2071 (gate ≤0.25 ✓); **passes IS gate: False**
- **J=12 (IS, n=33):** ann IR (gross) = **0.746** (gate ≥0.5 ✓); **NW t = 0.9857** (gate |t|≥2.5 ✗); iid t = 1.24; **MDE IR** = 1.51 iid / 1.89 NW-consistent; **power @ observed IR ≈ 10%**; mean active net-2× = 0.002819 (gate >0 ✓); sleeve maxDD = 0.2397 (gate ≤0.25 ✓); **passes IS gate: False**
- **Holdout:** NOT RUN (neither J passed the IS gate — frozen rule; single-use holdout preserved).
- **Full-sample IR decay** (IS → full, adding 2025-04+): J=6 **0.60 → 0.26**, J=12 **0.75 → 0.33** — consistent with a weak / in-sample-inflated signal.
- **Hedge (informational, n_shared=199):** Pearson +0.0794, Spearman +0.1191; E[mom | straddle worst-quartile] = −0.000765/day with SE ≈ 0.0016 → **z ≈ −0.48, 95% CI [−0.0039, +0.0024] spans zero = NULL** (no diversification case either way).

## Which gate(s) failed / passed
- **J=6:** IR≥0.5 = True; NW|t|≥2.5 & adj-Sharpe>0 = **False** (fails on the t-stat); net+ @2× = True; maxDD≤25% = True.
- **J=12:** IR≥0.5 = True; NW|t|≥2.5 & adj-Sharpe>0 = **False** (fails on the t-stat); net+ @2× = True; maxDD≤25% = True.
- The **only** failing gate is gate 2, and it fails on the **Newey-West t-stat alone**. NOTE (disclosed): the "AND DSR > 0" half of gate 2 is **vacuous at K=2** — with 2 trials the Bailey–López de Prado expected-max term `SR* = Φ⁻¹(1−1/2)·σ = 0` identically, so "DSR>0" collapses to "per-period Sharpe > 0" (trivially passed). **Gate 2 was therefore effectively a lone t-test** with no independent second safeguard. (The raw DSR probability, 0.72–0.87, is < the conventional 0.95, but that was not the pre-registered bar.)

## Diagnostics — power-limited CONSISTENCY CHECKS (report-only, never decision)
- **J=6 WML (full):** mean monthly = 0.002323 (**> 0**), NW t = 0.36; quintile Q5 > Q1 but non-monotonic.
- **J=12 WML (full):** mean monthly = 0.00336 (**> 0**), NW t = 0.57; quintile Q5 > Q1 but non-monotonic.
- **How to read these (corrected per panel):** the WML "anomaly-exists" bar (|t|≥2.0) is **itself underpowered** at n≈33 — it needs an IR of ~**1.1–1.2**, which the observed WML IR (0.14–0.41) has no power to reach; and the inter-quintile gaps (incl. Q2 > Q5 in places) sit **within ~1 SE**. So WML + monotonicity are **consistent with a weak-positive (or zero) signal** — they are **NOT** corroboration that the anomaly is absent. *(An earlier draft claimed momentum was "statistically absent"; that over-reached and is retracted — the honest state is an underpowered null, anomaly status undecided.)*

## Contamination DIRECTION (corrected per panel) — the pure-survivor upper bound
- The bias direction is subtler than "all survivors help." **Recent dropouts still
  in the cache** (BPCL, BRITANNIA, HEROMOTOCO, INDUSINDBK) are a **PESSIMISTIC drag**
  — they were dropped for weak recent performance, so keeping them *hurts* the sleeve.
- Excluding them (a **pure-survivor** cut) *lifts* J=12 IS IR **0.746 → 1.145** and
  NW t **0.99 → 1.41** (independently reproduced). **t = 1.41 is the TRUE optimistic
  upper bound — and it still fails ≪ 2.5, which makes the kill-only conclusion
  airtight** (even the most favourable survivor cut cannot clear the bar).
- The **genuine upward bias** is the **older dropouts ABSENT from the cache**
  (GAIL, IOC, SHREECEM, UPL, DIVISLAB, and delisted/merged HDFC & LTIMINDTREE);
  recovering those is what a clean point-in-time test would require.

## Data debt (verified IMMATERIAL to this verdict; must fix before future equity work)
- **SUNPHARMA cache is corrupted:** cached close range **[108.60, 423.35]** vs a real
  ~₹1600 mega-cap, with **2024-02 and 2024-03 entirely missing** and a spurious ~−38%
  splice across the gap. **Immaterial here** — SUNPHARMA appears in the top quintile
  **0/47** periods (J=12) and **2/53** (J=6). **Must be re-fetched + QC-reconciled**
  before any future equity-universe research.
- **ITC** to re-verify too (cached range **[68.35, 277.00]**; the ~68 minimum is
  suspicious — corp-action / scale check needed).

## Successor test (per panel)
- An **A-test as originally planned** (current-cache point-in-time on the same
  ~2021-06→2026 window) **adds essentially zero power** — it only pulls the optimistic
  estimate *down* toward the clean value while keeping n≈33–39 (a foregone kill for a
  costly membership build). **Not worth doing** (task #4 stood down).
- The **only informative successor** = a **new pre-registration on ~10–14y of DAILY
  data** (yfinance survivors + delisted, genuine point-in-time membership, **n≈120–170**,
  a power-feasible gate, non-trivial small-K deflation, **SUNPHARMA fix as prerequisite**).
  **Optional, low-priority vs the primary 0DTE/VRP options edge — operator's call.**

## Resolved protocol ambiguities
- **Split by formation month t** (t ≤ 2025-03-31 → IS; t ≥ 2025-04-01 → holdout; IS n=33 for J=12 matches the pre-reg's ~33). IR gate on GROSS active returns; friction handled by the separate net-2× gate. "Net positive after 2× friction" read as mean **net-active** (sleeve−benchmark) > 0.
- **"DSR > 0" gate:** the reused Bailey–López de Prado `deflated_sharpe` returns a probability in (0,1) (a literal "DSR>0" is vacuous) AND is **degenerate at K=2** (SR*=0 → gate 2 = a lone t-test; see the gates section). Resolved as intent = adjusted Sharpe (SR−SR*) > 0; moot for the verdict (gate 2 fails on the t-stat regardless).
- **"IS gate" for winner selection** read as passing ALL IS-side gates (1–4). Both J fail gate 2 → neither passes → KILL, holdout preserved unused. Under a looser IR-only reading the holdout would run for J=12, but it still fails gate 2 → **KILL either way**.
- **Monthly-last-price convention** (each name's last valid close per calendar month) for ranking/holding, rather than requiring the exact panel-wide last-trading-date (which would spuriously drop a name that traded all month but not on that one day).
- **Eligibility:** a name needs a monthly-last close for every month in [t−1−J .. t+1]. TATAMOTORS excluded from formation+holding for t ≥ 2025-09 (demerger 2025-10-14, −41%, not ratio-adjusted, inside the formation window).
- **Final holding period is partial** (May-2026 end → 2026-06-11 data cutoff) to match the frozen holdout end exactly.
- **QC (panel-vindicated):** all Sat/Sun rows dropped (neutralises the named 2021-08 / 2022-04 phantom prints — weekend-adjacent, no >15% residual). 20 |daily return|>15% events reconciled: 19 KEPT as real market-event moves (Adani-Hindenburg, 2024-06-04 election day, IndusInd, etc.), 1 TATAMOTORS demerger handled by the from-2025-09 exclusion. No move dropped as an artifact (both mid-run corrections were confirmed legitimate and empirically inert).

**Full numbers:** `reports/momentum/b_triage_eval.json` · **Power/MDE supplement:** `reports/momentum/b_triage_power_note.md` (post-hoc, decision-irrelevant) · **Universe (50 names):** pinned in the JSON.
