# Evidence-Based Technical Analysis (Aronson) — Pass-2 Distillation

**Project context:** intraday algo, Indian markets (NIFTY/BANKNIFTY/NIFTY-50), Rs5L paper.
**Pass-1 already applied:** de Prado, Chan, Kaufman. This note does NOT repeat their points; it builds on them.
**Reading priority:** Data-mining bias mechanics; multiple-comparison problem; Monte Carlo permutation / White's Reality Check; detrending; permutation-test extension for sparse strategies.

---

## Chapter Map (plain-text extraction)

| Chapter | Subject | Our Priority |
|---------|---------|--------------|
| Ch 1 | Objective rules, detrending, look-ahead bias, costs | HIGH |
| Ch 2 | Cognitive biases — subjective TA illusion | medium |
| Ch 3 | Scientific method applied to TA | medium |
| Ch 4 | Statistical analysis fundamentals | HIGH |
| Ch 5 | Hypothesis tests: bootstrap + Monte Carlo permutation (single-rule) | HIGH |
| Ch 6 | Data-mining bias: five factors, WRC, MCP extended to N-rule case | CRITICAL |
| Ch 7 | Theories of nonrandom price motion, behavioral finance, EMH | low |
| Ch 8 | Case study rule specification — 6,402 rules on S&P 500 | medium |
| Ch 9 | Case study results: no rule survives WRC/MCP at 0.05 | HIGH |

---

## Part I — Core Mechanisms

### 1. Detrending: Why and How (Ch 1)

**Mechanism.** A rule with a long bias will look good in a bull market and bad in a bear market even if it has zero predictive power. Detrending removes this position-bias distortion so the null hypothesis "rule earns zero" is actually zero regardless of long/short skew.

**Procedure.**
1. Compute the average daily log-return of the instrument over the back-test window.
2. Subtract that average from every day's return. Now average daily drift = 0.
3. Compute rule P&L on these drift-free returns. Signal generation still uses raw prices.

**Mathematical identity** (Appendix): detrending is exactly equivalent to subtracting a benchmark whose expected return matches the rule's position bias.

**Our use:** Our engine computes returns on raw intraday bars. We already benchmark versus buy-and-hold for trend strategies, which achieves a similar goal. For the straddle we benchmark versus zero (flat premium). Formal detrending is most critical for direction strategies with known long or short bias — our breadth-gated rider is long-biased and should have its PF benchmarked against "always long" not zero.

**Friction reality check:** Aronson's case study omits trading costs intentionally (to isolate raw predictive power). We cannot afford this luxury. Rs600 equity round-trip and 0.05% F&O STT are first-order. Any rule that barely beats zero on detrended data will fail net of our costs. Minimum gross edge required before costs must be computed per strategy.

---

### 2. Look-Ahead Bias (Ch 1)

Aronson's fix: close-of-day signal → next-day open fill. We already implement next-bar-open fills. His additional caution: data series that are revised or reported with a lag must be lagged accordingly. Our Dhan data is point-in-time for prices; be careful with any fundamental or macro series added later.

---

### 3. Bootstrap Significance Test — Single Rule (Ch 5)

**When valid:** Only one rule is tested (no data mining). If you test one hypothesis with a pre-registered spec and never alter it, this applies directly.

**Procedure (10-step):**
1. Compute rule's daily returns on detrended data.
2. Zero-center: subtract mean daily return so the distribution is centered at 0 (conforming to H₀).
3. Resample with replacement N times (N = original sample length).
4. Compute mean of each resample.
5. Repeat 5,000 times to build sampling distribution.
6. p-value = fraction of 5,000 bootstrap means ≥ observed mean return.

**Bootstrap theorem guarantee:** Converges to correct sampling distribution as N → ∞. At our trade counts (40–300 trades), convergence is slower and the CI is wide — this is a structural limitation for sparse strategies (see §6 below).

---

### 4. Monte Carlo Permutation Test — Single Rule (Ch 5, Masters method)

**Key difference from bootstrap:** Does not resample the rule's returns. Instead, randomly pairs the rule's output values (+1/–1) with scrambled market returns. Destroys any predictive pairing while preserving the return distribution.

**Procedure (10-step):**
1. Obtain detrended daily market returns.
2. Obtain rule's output values in original time order.
3. Randomly pair (permute without replacement) output values with market returns → one noise-rule mean.
4. Repeat 5,000 times → sampling distribution.
5. p-value = fraction of 5,000 permuted means ≥ observed mean.

**Why prefer MCP for our sparse case:** Bootstrap requires zero-centering and resampling the rule's own returns — when we have only 40–100 trades, the empirical return distribution is poorly estimated and bootstrap CIs balloon. MCP avoids this by using the raw market return distribution (thousands of daily bars) as the reference — it has better power in small-trade-count scenarios.

**Critical implementation note:** When testing N > 1 rules in a data-mining context, use the identical permutation pairings across all rules in each iteration. This preserves inter-rule correlation structure, which is one of the five bias factors.

---

### 5. White's Reality Check (WRC) — Extended to N-Rule Case (Ch 6)

**Problem WRC solves:** When you test N rules and pick the best, ordinary p-values are wrong. The best rule among 50 zero-merit rules routinely shows +37% return and a naive p-value of 0.0005. Against the correct sampling distribution (of the maximum mean among 50 means), that same 37% has p = 0.45 — completely insignificant.

**WRC procedure:**
1. Collect daily returns for all N rules.
2. Zero-center each rule's returns by subtracting its mean (H₀: each rule earns zero).
3. Resample dates with replacement; for each resample, compute mean return for every rule.
4. Take the maximum across all N rules → one draw from the "best of N worthless rules" distribution.
5. Repeat 500+ times.
6. p-value = fraction of draws exceeding the best real rule's observed mean.

**Key finding from case study (Ch 9):** 6,402 rules on S&P 500, best rule earned 10.25% with naive single-rule p = 0.0005. After WRC correction, p = 0.45. Zero rules were significant. About 320 rules had naive p < 0.05 exactly as expected by chance.

**Romano-Wolf enhancement:** Increases test power (reduces type-II errors) when the rule universe includes rules with negative expected returns (e.g., inverse rules). We include inverse rules in our grid by convention — apply R-W version.

**Patented / public domain status:** WRC patent held by Halbert White / Quantmetrics. MCP (Masters) in public domain. For our purposes, implement MCP + Romano-Wolf enhancement.

---

### 6. Five Factors Determining Data-Mining Bias (Ch 6)

This is the most implementation-relevant section for our project.

**Factor 1 — Number of rules tested:** More rules = larger bias. Increases logarithmically. Good news: the incremental bias from adding more rules drops off quickly once ~30 rules are exceeded, as long as there are sufficient observations. **Our situation:** pre-registered grid of N variations; for deflated Sharpe we count N = FAMILY count not grid points (already done). Aronson quantifies why this matters: best of 256 zero-merit rules with 2-month history has bias of +200%/year. Best of 256 with 1,000-month history has bias of ~3%/year.

**Factor 2 — Number of observations:** Most important factor. Bias falls steeply as observations increase. Bias stabilizes quickly once many rules are tested — beyond ~30 rules, doubling rules barely moves the bias IF observations are sufficient. **Our pain:** 40–300 trades in 2-year window. For daily-bar strategies these are 40–300 observations of the performance metric. MCP uses daily bar count (~500 bars/year), not trade count, for the permutation distribution. Recommendation in §7 below.

**Factor 3 — Rule correlation:** Highly correlated rules (parameter tweaks of same concept) reduce effective N. Our within-family parameter grids are highly correlated → actual bias smaller than raw N implies. Cross-family tests (e.g., straddle vs. trend) are uncorrelated → full bias applies.

**Factor 4 — Positive outlier returns:** Heavy-tailed return distributions (options, gap events) amplify bias. The straddle's rare tail blow-ups (uncapped short-gamma days) are an example. With small samples, one catastrophic day that happened NOT to occur in-sample inflates observed PF. **Action:** bootstrap CIs for the straddle should be computed with a block-bootstrap (preserving GARCH clustering) not i.i.d. resample.

**Factor 5 — Variance in expected returns across rules:** If one rule is genuinely far superior, it is likely to be correctly identified even in noisy conditions (its merit shines through). If all rules are similarly mediocre, the winner is likely just lucky. **Our situation:** the straddle is structurally distinct (short vol vs. directional) — comparison across families adds noise but the within-family winner is relatively well-identified.

---

### 7. Out-of-Sample Testing: WRC/MCP vs. Walk-Forward (Ch 6)

Aronson lists three deficiencies of naive hold-out testing:
1. The hold-out period is consumed after first use — not re-usable.
2. Reduces data available for mining.
3. Partitioning fraction is arbitrary.

WRC/MCP avoid all three: they use ALL data for mining AND provide valid p-values. Walk-forward solves the time-ordering problem (data must not be future-contaminated) which WRC/MCP do not address explicitly.

**Our methodology already does:** 8m train / 2m test walk-forward + single-shot frozen holdout. This is superior to either method alone. We should ADD a permutation test within each walk-forward fold as a fold-level significance gate, not just a PF gate.

**Three-segment scheme (Ch 9):** For complex rules (ML) use train / test / validate. For fixed-complexity rules, train / test (WF) is sufficient. Our strategies are fixed-complexity (pre-registered) → two-segment WF is correct.

---

### 8. Sparse-Strategy Problem — Permutation Test Add-On

This is not explicitly covered in Aronson but is a direct application of his MCP framework to our known pain.

**Why bootstrap CI is weak for 40–300 trades:**
- Bootstrap theorem requires N → ∞ for convergence.
- With 40 trades, bootstrapped mean has very wide sampling distribution.
- PF of 1.47–1.53 could plausibly occur by luck at this sample size.
- Bootstrap CI for PF at N=40 will span from ~0.7 to ~2.5 — essentially uninformative.

**Why MCP is better for sparse strategies:**
- MCP uses the daily market return series (500+ bars/year, ~2,000 bars over 4 years) NOT the trade count.
- The permutation pairs rule output value (long/short/flat) on each day with a scrambled daily market return.
- Even if the rule trades only 40 times, there are 2,000 output values — most "flat/neutral". The distribution of noise-rule means is still well-estimated from 2,000 pairings.
- This is MCP's key advantage for intraday sparse strategies.

**Proposed implementation (straddle-specific permutation test):**
1. Encode straddle output: +1 on expiry days (short straddle held), 0 on non-expiry days.
2. Collect daily P&L of the straddle (including the non-trading zero-return days) → 2,000+ observations.
3. Run MCP: scramble the daily market-vol return series, re-pair with the +1/0 output vector.
4. Compute mean return of noise-rule for each permutation (5,000 runs).
5. Derive p-value for observed straddle mean return.
6. For the breadth-gated trend rider: output is +1/0/-1 on each 5-min bar → tens of thousands of output values → MCP has very high power.

**Expected result given our PFs:** Straddle with PF 1.5 over ~160 trades/2 years is likely to show MCP p ~ 0.05–0.15, not the typical 0.01 claimed by naive tests. This is the honest bound.

---

### 9. Data-Snooping Bias (Prior Research Contamination) (Ch 9)

Distinct from in-sample data-mining bias. Occurs when we include rules that performed well in published research. The number of rules tested across all prior researchers is unknown, making it impossible to compute a correct N.

**Mitigation used in case study:** Enumerate rules combinatorially (all parameter combos within a defined form) rather than cherry-picking known winners. The set is defined before looking at results.

**Our methodology:** Pre-registration with trial registry is exactly this mitigation. The FAMILY count in deflated Sharpe accounts for the number of families tested. However, if we chose NIFTY SHORT STRADDLE because we "heard it works" from other traders, there is implicit snooping bias. The holdout PF 1.53 on data never touched during development is our primary defense.

---

### 10. Detrending Applied to Our Strategies (Upgrade)

**For the breadth-gated trend rider:** NIFTY trends upward ~15%/year in the 2019–2025 sample. A long-biased strategy will look artificially good. Benchmark must be "always long 1 lot" not "zero return." We already use excess-return PF but should formalize detrending so the null hypothesis is exactly zero.

**For the straddle:** The straddle's P&L is path-dependent (Greeks, not just direction). Classical detrending applies to the theta-decay daily PnL series, not to underlying. The relevant null is: does the straddle earn more than a risk-free rate given its vol of returns? Use Sharpe vs zero-risk-free (already done via Deflated Sharpe).

---

## Part II — What Aronson Does NOT Cover / Friction Kills

### Institutional-Only Items (Do Not Apply)

1. **6,400-rule grids with WRC software:** We operate 3–5 strategy families, not thousands. Full WRC overkill; MCP with Romano-Wolf is sufficient and free.
2. **Daily-frequency signal testing on equity indexes:** Aronson's case study is daily-bar S&P. Our strategies are intraday (1-min/5-min). MCP framework is identical but the return vector is intraday bars, not days.
3. **Long-only reversal rules:** All his case-study rules are binary long/short. We operate short-only straddle and long-biased trend. Null hypothesis formulation differs.

### Where Aronson's Advice Fails Our Friction

1. **"Omit trading costs to study pure predictive power"** (Ch 1): At Rs600/round-trip equity or 0.05% STT on F&O, a rule that earns +3% gross per year (about the level of his best rules) earns deeply negative net. Every significance test in our engine must include costs. Cost-omitted significance is a false positive factory.

2. **"Use annualized return as primary statistic"**: For intraday strategies, annualized return depends on leverage and position sizing in a way that is arbitrary. PF and per-trade risk-reward are more stable. His MCP procedure works identically with any mean return metric — we substitute daily P&L in Rs.

3. **"1,000+ months is adequate sample"**: We have 4–6 years of NIFTY/BANKNIFTY weekly expiry data. That is 200–300 expiry days for the straddle. Aronson's bias curves show that at 200 observations and best-of-10, data-mining bias is ~10%/year — material. We cannot ignore this.

4. **"Correlation among rules reduces bias"**: True for parameter variants of same strategy. Does NOT help when comparing structurally different families (straddle vs. trend rider). Family count N for deflated Sharpe should remain at full FAMILY count.

---

## Part III — Methodology Confirmations

Things our existing methodology already does correctly per Aronson:

1. **Pre-registered grid + trial registry** = Aronson's combinatorial enumeration (Ch 6 §"Prior-research-snooping bias"). Confirmed correct.
2. **Single-shot frozen holdout** = Validation set in three-segment scheme (Ch 9). Only valid once. Confirmed correct — do not peek.
3. **Walk-forward 8m/2m** = Walk-forward two-segment scheme (Ch 6, Fig 6.57). Confirmed correct.
4. **Deflated Sharpe with FAMILY N** = Correct use of multiple-comparison correction (though Aronson would use WRC; DSR with N is a valid alternative). The N = family count not grid count is correct per Factor 1 + Factor 3 (correlated parameter variants shrink effective N).
5. **PF < 0.8 per-fold guard** = Type-II error guard; Aronson notes that when one or more rules have negative expected returns, WRC/MCP lose power. Our per-fold PF gate screens out regressions.
6. **Bootstrap PF CIs** = Directionally correct. Switch to MCP for sparse strategies (§8 above).
7. **+50% slippage stress** = Conservative bound on what Aronson calls "sampling variability" in performance statistics. Confirmed directionally correct.

---

## Part IV — Proposed Permutation Test Implementation

### Algorithm for Sparse Strategy MCP (Implementable in ~50 lines Python)

```
Input:
  daily_pnl[T]      : daily P&L vector (Rs), length T (trading days), zero on non-trading days
  signal[T]         : daily signal vector (+1, -1, 0), same length
  detrended_ret[T]  : detrended daily market returns (close-to-close, mean-subtracted)
  N_perms = 5000

Step 1: Compute observed mean daily P&L (or mean return on detrended data).

Step 2: For each permutation i in 1..N_perms:
    - Randomly permute detrended_ret → shuffled_ret (WITHOUT replacement, matching signal sign)
    - noise_pnl = signal * shuffled_ret * (position_size_Rs)
    - noise_mean[i] = mean(noise_pnl)

Step 3: p_value = fraction of noise_mean >= observed_mean

Step 4: Report p_value with CI band.
```

**When to apply:**
- Straddle: T = 2,000+ days, signal = 1 on ~80 expiry days/year
- Trend rider: T = number of 5-min bars (~60,000 over 2 years), signal = +1/0/-1 per bar
- Run separately per walk-forward fold; also run on full in-sample for monitoring

**Comparison to bootstrap CI:**
- Bootstrap CI on 80-trade straddle: ~95% CI spans [0.6, 2.4] PF — useless
- MCP on 2,000 daily P&L observations: much tighter and honest
- Both should be reported; MCP is primary significance test

---

## Part V — Stop Calibration Insight (Straddle 25% Stop Fires 57% of Days)

Aronson does not directly address stop placement but his framework implies:

1. **The stop is a rule parameter.** Testing multiple stop levels (15%, 20%, 25%, 30%) constitutes data mining. The stop level must be pre-registered and counted in the deflated Sharpe N.
2. **57% stop-fire rate is a sample statistic subject to sampling error.** The sampling distribution of "fraction of days stopped" is wide at N=160 trades. Bootstrap the stop-fire rate CI to see if 57% is distinguishable from, say, 40%.
3. **Permutation test for stop calibration:** Define "null stop" = stop placed at 100% (never fires). Permute the daily straddle excursion distribution to ask: does a 25% stop generate significantly better risk-adjusted PnL than no stop? This is a clean single-rule test (no mining) if the 25% was pre-registered.
4. **Our heavy-tailed options environment** = Factor 4 concern. One Nifty crash day in the holdout period can dominate mean return. Block-bootstrap (block size = 5 days to preserve IV clustering) is more conservative than i.i.d. bootstrap for straddle evaluation.

---

## Part VI — Chapter-Level Reference Notes

### Ch 5: Hypothesis Tests (pp. 217–255)
- Bootstrap procedure: 10 steps, uses rule's own daily returns resampled with replacement.
- MCP procedure: 10 steps (Masters), uses market return series permuted without replacement.
- Bootstrap tests H₀: rule's expected return ≤ 0. MCP tests H₀: rule output values randomly paired with market changes.
- p-value < 0.05: conventionally significant. p < 0.01: highly significant. p = 0.10: "possibly significant."
- Both methods produce same answer on detrended data; MCP preferred as public domain.

### Ch 6: Data-Mining Bias (pp. 255–329)
- Core equation: Observed Performance = Expected Performance +/- Randomness
- Best rule's OOS performance = in-sample performance minus the luck component. Luck does not repeat.
- Key table: best of 256 ATRs, 2-month history → 200%/year bias. 100-month history → 18%/year. 1,000-month history → 3%/year.
- Markowitz-Xu shrinkage: H' = R + B(H - R). Rough correction; B between 0 and 1.
- WRC/MCP both fail (lose power) when universe contains rules with strongly negative expected returns and high variance. Romano-Wolf enhancement fixes this.

### Ch 9: Case Study Results (pp. 441–475)
- 6,402 rules tested on S&P 500, 1980–2005 (25 years, ~6,000 daily bars).
- Best rule: 10.25%/year gross on detrended data. Naive p = 0.0005. WRC/MCP-corrected p = 0.45.
- Conclusion: zero rules with statistically significant predictive power for S&P 500.
- More seasoned/efficient markets (S&P, DJIA) show no significant rules. Less developed markets (Russell 2000, NASDAQ, emerging markets) may have some.
- **Our implication:** NIFTY/BANKNIFTY is a less-seasoned, less-efficient market with known structural features (expiry dynamics, retail option buying, hedger demand). Our edge hypotheses have stronger theoretical grounding than generic TA rules tested on S&P. This is the correct framing.

---

## Summary: 5 Actionable Upgrades

| # | Upgrade | Effort | Impact |
|---|---------|--------|--------|
| 1 | Implement MCP permutation test for straddle (50-line Python, appended to backtest) | small | high |
| 2 | Formal detrending for trend rider: subtract average daily return from bar returns before PF computation | small | medium |
| 3 | Switch from i.i.d. bootstrap to block-bootstrap (block=5 days) for straddle CI estimation | small | medium |
| 4 | Add MCP fold-level significance gate (p < 0.15 per fold) alongside existing PF gate | medium | medium |
| 5 | Run Markowitz-Xu shrinkage check on current strategy PFs to bound expected OOS performance | small | low |

---

*Distilled 2026-06-13. Source: Aronson, D. "Evidence-Based Technical Analysis." Wiley, 2007.*
