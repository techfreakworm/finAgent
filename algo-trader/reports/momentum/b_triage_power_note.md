# Momentum B-triage — POST-HOC power & robustness supplement

**Status:** POST-HOC, **decision-irrelevant to the frozen KILL** (the verdict in
`b_triage_eval.json` / `b_triage_summary.md` stands unchanged). This note was
written after an adversarial 4-lens panel confirmed the KILL-WITH-CAVEATS; every
number below was **cross-checked against the panel's independent derivation** and
matches. It was hand-authored (not emitted by the frozen `scripts/momentum_b_triage.py`)
and does not alter any frozen decision number. **Run:** 2026-07-02.

Panel synthesis in one line: **KILLED at available power under the frozen
protocol; anomaly status UNDECIDED (an underpowered null), and the slot closes on
ECONOMICS** — a thin optimistic upper bound, likely lower on clean data,
unconfirmable at this n, and with no hedge value.

---

## 1. Why the pre-registered t≥2.5 bar is (partly) a power ceiling

At n = 33–39 monthly active-return observations, a per-period Sharpe maps to an
annualised IR by ×√12, and the iid t-stat is `sr·√n`. The pre-registered
|t|≥2.5 bar therefore implies a **minimum detectable IR** that is very high:

| J  | n  | observed IR | observed NW t | iid t | MDE IR (iid, t=2.5) | NW/iid ratio | MDE IR (NW-consistent) | power at observed IR |
|----|----|-------------|---------------|-------|----------------------|--------------|-------------------------|----------------------|
| 6  | 39 | 0.60        | 0.89          | 1.09  | **1.39**             | 0.82         | **1.70**                | ~8%                  |
| 12 | 33 | 0.75        | 0.99          | 1.24  | **1.51**             | 0.80         | **1.89**                | ~10%                 |

The active returns carry mild positive autocorrelation, so the Newey-West (lag 3)
t is ~0.80× the iid t; clearing |NW t|≥2.5 needs an IR of **1.70–1.89**. Power to
detect even a genuinely strong edge at the bar is low:

| true IR | power @ t≥2.5, n=33 | n=39 | n=48 |
|---------|---------------------|------|------|
| 0.50    | ~5%                 | ~6%  | ~7%  |
| 0.80    | ~12%                | ~15% | ~18% |
| 1.00    | ~20%                | ~24% | ~31% |
| 1.40    | ~43%                | ~51% | ~62% |

**Reading:** a normal-sized momentum edge (published India/global long-short
momentum IRs cluster ~0.4–0.8) would *usually fail* this bar at n≈33. The bar is
demanding relative to the sample — this is a real power limitation, not a defect
in the estimate.

## 2. The honest state is an UNDERPOWERED NULL (not "absence")

The point estimates are **uniformly, weakly positive** across every cell:
active IR 0.60 / 0.75; WML IR 0.14–0.41; WML mean monthly > 0; Q5 > Q1 in every
configuration; net-positive after 2× friction. Nothing points *negative*.

With n = 33–39, the sample **cannot distinguish a real IR≈0.5–0.7 edge from zero**
at the pre-registered bar. So the correct label is *underpowered null* — the
anomaly's existence is **undecided**, not demonstrated-absent. (An earlier draft
of the summary over-reached by calling momentum "statistically absent"; that claim
is retracted here — see §4.)

## 3. Gate 2 had no independent second safeguard (DSR vacuous at K=2)

The pre-reg's gate 2 was "|NW t| ≥ 2.5 **AND** DSR > 0". With K = 2 trials the
Bailey–López de Prado expected-max-under-null term is
`SR* = Φ⁻¹(1 − 1/2)·σ_SR = Φ⁻¹(0.5)·σ_SR = 0` **identically**, so the adjusted
Sharpe (SR − SR*) collapses to SR and "DSR > 0" reduces to "per-period Sharpe > 0"
— which both cells trivially pass. **Gate 2 was therefore effectively a lone
t-test**; the DSR provided no second, independent safeguard at K = 2. (The raw DSR
probability was 0.72–0.87, below the conventional 0.95, but that bar was not the
pre-registered one.) Disclosed for completeness; it does not change the KILL,
which the t-test alone already produces.

## 4. WML + monotonicity are power-limited consistency checks — NOT evidence of absence

An earlier draft used the WML anomaly diagnostic and quintile non-monotonicity as
*corroboration of absence*. The panel corrected this and it is corrected here:

- The WML "anomaly-exists" bar (|t| ≥ 2.0) is **itself underpowered** at this n —
  it requires an IR of ~**1.1–1.2** (2.0·√(12/n)), which the observed WML IR
  (0.14–0.41) has no power to reach. A null WML t (0.22 / 0.60) at an unreachable
  bar is *uninformative about absence*.
- The quintile ordering is weakly `Q5 > Q1` but non-monotonic (Q2 sits above Q5 in
  places); those inter-quintile gaps are **within ~1 standard error** of each
  other given ~33 periods and 10 names/quintile.

So WML + monotonicity are best read as **power-limited consistency checks that are
consistent with a weak-positive (or zero) signal** — not as proof the anomaly is
absent.

## 5. Tail-hedge is a NULL, not a sign failure

E[momentum daily return | straddle in its worst quartile] = −0.000765/day, with
SE ≈ 0.0016 → z ≈ −0.48, 95% CI ≈ [−0.0039, +0.0024], which **spans zero**. The
overall correlation is a low positive (Pearson +0.08 / Spearman +0.12, n_shared =
199). So the hedge relationship is **undecided-to-absent**: there is no
diversification case *either way*. (Earlier framing as a "tail-hedge sign failure"
is retracted — the negative point estimate is not statistically distinguishable
from zero.)

## 6. Contamination DIRECTION correction — the pure-survivor upper bound

The universe is today's 50 NIFTY names applied historically, but the direction of
the bias is subtler than "all survivors help":

- **Recent dropouts still in the cache** (BPCL, BRITANNIA, HEROMOTOCO, INDUSINDBK)
  are a **pessimistic drag**, not a boost — they were *dropped* for weak recent
  performance, so keeping them *hurts* the momentum sleeve. Excluding them (a
  pure-survivor cut) *lifts* J=12 IS IR **0.746 → 1.145** and NW t **0.99 → 1.41**
  (independently reproduced). **t = 1.41 is the TRUE optimistic upper bound**, and
  it still fails the bar by a wide margin (≪ 2.5) — which makes the kill-only
  conclusion *airtight*: even the most favourable survivor cut cannot clear the bar.
- The **genuine upward bias** comes from the **older dropouts that are ABSENT from
  the cache entirely** (GAIL, IOC, SHREECEM, UPL, DIVISLAB, and the delisted/merged
  HDFC and LTIMINDTREE). Those are the names whose omission manufactures spurious
  momentum; recovering them is what a clean point-in-time test would need.

## 7. Full-sample IR decay (undisputed)

The IS IR does not persist out of the IS window: J=6 **0.60 → 0.26**, J=12
**0.75 → 0.33** (IS → full sample, adding the 2025-04+ periods). The panel did not
dispute this. It is consistent with a weak / in-sample-inflated signal rather than
a stable edge, and reinforces that the modest IS point estimates should not be
over-read.

## 8. Why the slot closes, and the only informative successor

Given (a) even the pure-survivor optimistic upper bound (t = 1.41) fails the bar,
(b) the clean point-in-time value is *lower* (the true upward bias is the absent
older dropouts), (c) the edge is unconfirmable at n ≈ 33–39, and (d) no hedge
value — the momentum slot **closes on economics**.

Per the panel, an **A-test as originally planned (current-cache point-in-time on
the same ~2021-06→2026 window) adds essentially zero power** — it would only
*reduce* the optimistic estimate toward the clean value while keeping n ≈ 33–39,
i.e. a foregone kill for a costly membership build. **Not worth doing.**

The **only informative successor** would be a **new pre-registration on ~10–14
years of DAILY data**: yfinance survivors + delisted names, genuine point-in-time
NIFTY membership, **n ≈ 120–170** (power-feasible for a realistic gate),
non-trivial small-K deflation, and the **SUNPHARMA cache fix as a hard
prerequisite** (§9). This is **optional and low-priority** relative to the primary
0DTE/VRP options edge — **operator's call**.

## 9. Data debt (must fix before any future equity-universe work)

- **SUNPHARMA cache is corrupted.** Cached close range is **[108.60, 423.35]**
  against a real mega-cap price of ~₹1600, with **2024-02 and 2024-03 entirely
  missing** and a spurious ~−38% splice across that gap. **Verified immaterial to
  THIS verdict** (SUNPHARMA appears in the top quintile 0/47 periods for J=12 and
  2/53 for J=6), but the series **must be re-fetched and QC-reconciled** before any
  future equity-universe research.
- **ITC** should also be re-verified (cached range **[68.35, 277.00]**; min ~68
  is suspicious and warrants a corp-action / scale check).

---

**Frozen decision artifacts (unchanged):** `reports/momentum/b_triage_eval.json`,
`reports/momentum/b_triage_summary.md`. Backtest engine: `scripts/momentum_b_triage.py`
(untouched). Verdict: **KILL**.

---

## POST-HOC ADDENDUM 2 (2026-07-02): data-integrity robustness re-run

After the B-triage, the flagged SUNPHARMA corruption was root-caused: the legacy
finAgent secid map had **SUNPHARMA→14788 (= SPARC)** and **ITC→10453 (= SATIN)** —
two of the 50 cached series were entirely different (mid-cap) instruments, faithfully
fetched under the wrong names. Both were re-fetched with the authoritative NSE-EQ ids
(3351 / 1660; verified ranges SUNPHARMA ₹664→1,916, ITC ₹197→527; zero |ret|>15%
events), the canonical map moved into the repo (`data/reference/nifty50_secid_map.json`),
and the frozen `momentum_b_triage.py` was re-run ONCE on the corrected panel as a
labeled robustness check (NOT a re-decision — the decision stands on the pre-registered
run):

| config | pre-reg run (impostor data) | corrected-data re-run |
|---|---|---|
| J=6  | IR 0.603, NW t 0.885 | IR 0.552, NW t 0.775 |
| J=12 | IR 0.746, NW t 0.986 | IR 0.957, NW t 1.276 |
| verdict | KILL (g2 fails both) | **KILL (g2 fails both, identical gate pattern)** |

Reading: the impostor series were immaterial-to-mildly-pessimistic (J=12 point estimate
improves with real data), consistent with the panel's contamination-direction analysis.
The significance-gate outcome — and therefore the KILL — is unchanged. Full re-run
artifact: `reports/momentum/b_triage_rerun_fixed_data.json`. The breadth series
(`_BREADTH/5m/breadth.parquet`) was also rebuilt from the corrected cache
(gold-match tests 15/15 green).
