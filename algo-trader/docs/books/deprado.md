# Advances in Financial Machine Learning — Lopez de Prado (2018)
## Distillation for NIFTY/BANKNIFTY Intraday Algo System
### Reading pass 1 — June 2026

**System context:** Event-driven 1-min/5-min backtester, Rs 5L capital, NIFTY/BANKNIFTY futures + index options,
Indian market friction ~Rs 600/equity round trip, F&O STT 0.05% post-2026, lumpy lots (~Rs 5L per NIFTY lot),
walk-forward 8m/2m, ~1100 registered trials, DSR already computed, bootstrap PF CIs in place.

---

## Chapters Read

- Ch 3: Labeling (triple-barrier, meta-labeling)
- Ch 4: Sample Weights (overlapping outcomes, sequential bootstrap)
- Ch 7: Cross-Validation in Finance (purged k-fold, embargo)
- Ch 8: Feature Importance (MDI, MDA, SFI)
- Ch 10: Bet Sizing (from predicted probabilities)
- Ch 11: Dangers of Backtesting (PBO, CSCV)
- Ch 12: Backtesting through Cross-Validation (WF pitfalls, CPCV)
- Ch 13: Backtesting on Synthetic Data (OTR derivation)
- Ch 14: Backtest Statistics (PSR, DSR formula, HHI runs, classification scores)
- Ch 15: Understanding Strategy Risk (precision/frequency/payoff triangle)
- Ch 16: ML Asset Allocation (HRP — skimmed, single-strategy system)
- Ch 17: Structural Breaks (CUSUM, SADF, sub/super-martingale tests)

**Not read in depth:** Ch 1 (intro), Ch 2 (data structures — tick/dollar bars not available),
Ch 5 (fractional differentiation — noted below), Ch 6 (ensemble methods — standard RF),
Ch 9 (hyperparameter tuning — standard), Ch 18 (entropy features — tick data needed),
Ch 19 (microstructural features — tick data needed), Ch 20-22 (HPC/quantum).

---

## Key Concepts

### 1. Triple-Barrier Labeling (Ch 3, §3.4)
**What it is:** Labels observations by which of three barriers is touched first:
upper horizontal (profit-take), lower horizontal (stop-loss), or vertical (time limit).
Each barrier is a multiple of rolling volatility (EWMA std), not a fixed threshold.

**Configurations:**
- `[1,1,1]` — standard: profit-take, stop-loss, or expiry. Preferred.
- `[0,1,1]` — exit after N bars unless stopped out (use when uncertain of target).
- `[1,1,0]` — hold until profit or stop; no time limit (unrealistic intraday).

**Critical insight:** Fixed-horizon labeling is wrong because it ignores path dependence.
Our 25% straddle stop already embodies triple-barrier logic `[0,1,1]` (no upper barrier on the short straddle;
stop-loss at 25% premium expansion; exit at 15:10). This is correct per book.

**For our breadth rider:** Current 2xATR stop / 3.5xATR trail maps to `[1,1,0]` if no time limit,
or `[1,1,1]` with the 15:19:30 force-flat as vertical barrier. Both are valid.

### 2. Meta-Labeling (Ch 3, §3.6–3.7)
**What it is:** A two-stage ML system. Primary model sets the *side* (long/short).
Secondary ML model learns whether to *act or pass* (binary {0,1}). Size comes from
the secondary model's predicted probability.

**Why it matters:**
- Limits overfitting because ML never decides side — only whether to take the primary model's signal.
- Increases F1: build the primary model for high recall (catch most good signals even with noise),
  then use meta-labeling to filter false positives.
- Asymmetric barriers valid when side is known.

**For our breadth rider specifically:**
- Primary rule: breadth >= 0.72 at 10:15 → long index. Side is fixed.
- A meta-labeling layer could learn: "Given this breadth level, this ATR regime, this market structure —
  is today a day when the signal is reliable?" Features: ATR z-score, gap size, breadth signal margin above 0.72,
  day of week, VIX proxy (Nifty IV), prior day return.
- Labels: `{0, 1}` — did the trade hit 3.5xATR trail before 2xATR stop on this day?
- **Honest assessment:** With only 40-300 trades in paper history, fitting a robust secondary model
  is not feasible yet. The value is in the *framework*: track precision/recall manually now,
  and build the ML filter once we have 200+ labeled samples across regimes.

### 3. Sample Weights and Non-IID Labels (Ch 4)
**The problem:** Any label derived from a holding period spanning multiple bars creates overlap.
If label i runs from bar 10 to bar 30, and label j from bar 25 to bar 45, they share bar 25-30.
Standard bootstrap resamples these near-duplicates heavily, inflating RF out-of-bag accuracy.

**Average uniqueness score:** For each label, compute what fraction of its bars are not
shared with any concurrent label. A score near 1 means the label is unique; near 0 means it is
almost entirely overlapping with other labels.

**Sequential bootstrap:** Instead of uniform draws, reduce probability of drawing label j
proportional to its overlap with already-drawn labels. Result: OOB accuracy falls to realistic levels.

**For our system:**
- Our 1-min/5-min intraday labels almost certainly have high overlap if we use 2xATR stops
  (trades can run 15-30 bars).
- When we eventually train any ML model on our bar data, we must apply uniqueness-weighted sampling.
- Without this, RF/gradient-boosted OOB accuracy will be meaningfully inflated.
- **Immediate action:** For the breadth rider's meta-labeling experiment, each daily trade label
  spans from 10:15 to first barrier touch. Overlap is low (at most one trade/day), so the IID
  assumption is approximately valid for daily frequency labels. Safe for now.

### 4. Purged K-Fold Cross-Validation and Embargo (Ch 7)
**Why standard k-fold fails in finance:**
- Serial correlation in features: X_t ≈ X_{t+1}, so train and test sets share information even if
  they don't overlap in time.
- Overlapping labels: if label Y_i spans bars [10, 30] and label Y_j spans [25, 45], placing i in
  train and j in test leaks information.

**Purging:** For each train/test split, drop from the training set any observation whose label
overlaps in time with any label in the testing set.

**Embargo:** After each test set, exclude the next h bars from the training set
(~1% of T usually suffices). Prevents leakage via autocorrelated features.

**Walk-forward does NOT need embargo** because training always predates testing.
But walk-forward still needs purging if triple-barrier labels span forward into the test window.

**For our walk-forward 8m/2m:**
- The 2-month OOS window is the test set. The 8-month IS window is the training set.
- If any IS label's outcome crosses the IS/OOS boundary (e.g., a position entered on the last IS
  day exits in the first OOS week), that label must be purged from training.
- Our engine uses next-bar-open fills with force-flat at 15:19:30, so the maximum label length
  is ~375 1-min bars within a single day. For intraday strategies, labels virtually never cross
  the day boundary, let alone the 8m/2m boundary. Purging is a non-issue for our use case.

**CPCV for our system:** CPCV generates φ paths from N groups with k test groups each.
For our 1100-trial registry, CPCV would require maintaining a T×N matrix of per-trial PnL.
This is a future upgrade. Our current WF with frozen holdout + bootstrap PF CI approximates
the key goal of variance reduction over a single WF path.

### 5. Feature Importance: MDI, MDA, SFI (Ch 8)
**Three methods:**
- **MDI (Mean Decrease Impurity):** Fast, in-sample, tree-specific. Every feature gets positive
  importance. Biased toward high-cardinality features. Affected by substitution effects.
- **MDA (Mean Decrease Accuracy / permutation importance):** Slow, out-of-sample, model-agnostic.
  Can conclude all features are unimportant. Must use purged k-fold CV. Affected by substitution effects.
- **SFI (Single Feature Importance):** OOS score for each feature in isolation. Immune to substitution
  effects. Misses joint effects.

**Rule:** Feature importance must be computed BEFORE backtesting. The backtest is a sanity check,
not a research tool. Backtesting-driven feature selection is scientific fraud (ASA guidelines).

**Key law (Ch 8, §8.2):**
> "Backtesting is not a research tool. Feature importance is."

**For our breadth rider:**
- Our sole signal feature is the %-above-VWAP breadth reading at 10:15. We have one feature.
  MDI/MDA/SFI produce trivial answers with one feature.
- The *regime segmentation* finding (edge concentrates on low-ATR signal days) IS a form of feature
  importance analysis: ATR on signal day is a more informative feature than the raw breadth reading.
- **Action:** When expanding the breadth rider to a meta-labeling framework, run SFI on candidates
  (ATR z-score, gap size, breadth margin, VIX proxy) before fitting any classifier.

**PCA cross-validation check (Ch 8, §8.4.2):**
If PCA ranking of features (unsupervised) agrees with MDI/MDA ranking (supervised), this is
confirmatory evidence against overfitting. Apply this check if we ever fit an ML overlay.

### 6. Bet Sizing from Predicted Probabilities (Ch 10)
**Formula:** For binary outcome {-1, +1}, given classifier probability p[x]:
- Test statistic: z = (p[x] - 0.5) / sqrt(p[x](1-p[x])/N) (but book simplifies to CDF-based)
- Bet size: m = 2 * Z[z] - 1, where Z[.] is the Normal CDF. m ∈ [-1, 1].

**Practical implementation:**
- Bet size scales smoothly from 0 to 1 as confidence increases from 50% to 100%.
- Average active bets (overlapping holding periods) to avoid excess turnover.
- Discretize: round m to nearest stepSize (e.g., 0.25 or 0.50) to prevent micro-adjustments.

**For our system with lumpy lots:**
- At Rs 5L capital, one NIFTY futures lot (75 units × ~24,000 = Rs 18L notional) exceeds capital.
  We cannot size fractionally on futures anyway.
- **This framework applies to Option-C:** If meta-labeling gives confidence 0.65 on a signal day,
  we might express it as 1 ATM option debit at defined risk. The sizing formula is intellectually
  correct but practically moot for 1-lot-or-nothing futures sizing.
- For the 0DTE straddle (CONFIRMED strategy): sizing is binary (enter/skip), which is equivalent
  to the meta-labeling filter. No fractional sizing needed.

### 7. The Dangers of Backtesting (Ch 11)
**Seven sins (Luo et al. 2014) + additional:**
1. Survivorship bias
2. Look-ahead bias
3. Storytelling (ex-post narratives)
4. Data mining / snooping
5. Transaction costs (hard to simulate accurately)
6. Outliers driving results
7. Shorting costs ignored

**Additional dangers:** Multiple testing / selection bias (the big one for us).

**Key insight:** A flawless backtest is still likely wrong if you have run many trials.
Becoming expert means having run thousands of backtests — so even the best result is suspect.

**PBO (Probability of Backtest Overfitting) via CSCV:**
1. Collect PnL matrix M of size T×N (N trials, T observation periods).
2. Partition into S equal subsets; form all combinations of S/2 subsets.
3. For each combination, find best IS strategy n*, observe its OOS rank.
4. PBO = fraction of combinations where IS-optimal strategy underperforms OOS median.

**Relevance:** We compute DSR already, which accounts for trial count. PBO is complementary and
provides a non-parametric check. For our ~1100 registered trials, PBO would require maintaining
the full T×N PnL matrix — a future upgrade.

### 8. Deflated Sharpe Ratio (Ch 14, §14.7.3) — Verification of Our Implementation

**PSR formula:**
```
PSR[SR*] = Z[ (SR_hat - SR*) * sqrt(T-1) / sqrt(1 - gamma3*SR_hat + (gamma4-1)/4 * SR_hat^2) ]
```
Where gamma3 = skewness, gamma4 = kurtosis, T = number of returns.

**DSR formula:**
```
SR* = sqrt(V[SR]) * ( (1-gamma)*Z^-1[1 - 1/N] + gamma*Z^-1[1 - 1/(N*e)] )
DSR = PSR[SR*]
```
Where:
- V[SR] = variance of SR estimates across all N trials
- N = number of independent trials
- gamma = Euler-Mascheroni constant ≈ 0.5772
- e = Euler's number ≈ 2.718

**Critical correctness checks for our implementation:**
1. **N must count independent trials, not total backtests.** If two trials share the same parameter
   with only minor variation, they are not fully independent. The book counts "trials" as distinct
   strategy configurations. For our 1100-trial registry, if many trials are variants of the same
   strategy (e.g., breadth threshold swept from 0.65 to 0.80 in steps of 0.01), the effective N is
   not 1100. We should use the count of *meaningfully distinct* strategy families tested.
2. **V[SR] is variance across trials, not variance of one SR over time.** It must be computed
   from the distribution of Sharpe ratios from all N trials, not from bootstrap CIs of a single trial.
3. **Non-normality adjustment:** The PSR adjustment (gamma3, gamma4 terms) uses the *observed*
   skewness and kurtosis of the *selected strategy's returns*, not the distribution of SR estimates.
4. **T is the number of return observations used to compute SR_hat,** not trading days.
   For our walk-forward OOS, T should be the OOS period length in bars (or daily returns if aggregated).
5. **SR_hat should be in non-annualized form for PSR, then annualized separately** to avoid
   mixing annualization factors.

**Red flag if we are doing wrong:** If our DSR is close to our annualized SR regardless of N,
we are likely not applying the SR* adjustment correctly. DSR should be materially lower than raw SR
when N is large (e.g., 1100 trials). For 1100 trials with typical V[SR]=0.5, SR* ≈ 2.0+.

### 9. Backtest Statistics — Runs and HHI (Ch 14, §14.5)
**HHI (Herfindahl-Hirschman) concentration index:**
- h+ = 1 - sum(w+_i^2) / sum(w+_i)^2 where w+_i is each positive return's weight in total positive PnL
- h- = same for negative returns
- h[t] = concentration of bets by calendar month

**Ideal strategy has:** high SR, high bet count, high hit ratio, low h+, low h-, low h[t].

**For us:** Our 0DTE straddle has negative skew (small wins, occasional large losses). It will show
h+ low (wins distributed evenly) but h- potentially high (losses concentrated on a few bad days).
Monitor 95th percentile drawdown and time-under-water alongside PF.

### 10. Strategy Risk: The Precision/Frequency/Payoff Triangle (Ch 15)
**Symmetric payoffs formula:**
```
Sharpe_ratio = (2p - 1) * sqrt(n)
```
Where p = precision (fraction of bets that win), n = bets per year.

**For our 0DTE straddle (CONFIRMED, holdout PF 1.53):**
- Expiry days only: ~52 bets/year (one per expiry)
- To achieve SR=2: p must satisfy 2 = (2p-1)*sqrt(52), so p = 0.5 + 2/(2*sqrt(52)) ≈ 0.638
- This is the minimum precision required — below this, the strategy fails its target.
- **Stress test:** If friction increases by 50% (our standard stress), effective payoff π- becomes
  more negative, requiring higher p. Check implied precision at stressed costs.

**Asymmetric payoffs formula:**
```
SR = sqrt(n) * [p*pi+ + (1-p)*pi-] / [(pi+ - pi-)^2 * p*(1-p)]^(1/2)
```
The book provides a closed-form for minimum p given {pi-, pi+, n, SR*}.

**For the breadth rider:**
- ~40-300 trades in backtest. At n=60 trades/year, symmetric case: SR=2 requires p=0.634.
- Breadth rider had ~1.5-2x ATR positive payoff vs 2x ATR stop → pi+/pi- ≈ 1.75 (asymmetric).
- At asymmetric payoffs with pi+/|pi-| = 1.75, n=60, SR=2: required p ≈ 0.57 (lower than symmetric).
- **Key vulnerability:** The edge concentrates on low-ATR days. On those days, precision is probably
  higher. On high-ATR days, precision likely drops below break-even. The aggregate metrics mask this.

**Strategy failure probability (Ch 15, §15.4):**
Bootstrap the distribution of p from observed trade outcomes. If P(p < p_min) > 5%, treat strategy as too risky.
This is what our bootstrap PF CI already approximates. The book formalizes it.

---

## Structural Breaks as Features (Ch 17)

**CUSUM test (Brown-Durbin-Evans):** Cumulative forecast errors. Detects when regression
coefficients shift. Computationally feasible on 1-min bars.

**SADF (Supremum ADF):** Backward-expanding window ADF tests. Detects explosive
(bubble-like) behavior in price series. Computationally expensive — O(T^2) ADF regressions.
On 5-min NIFTY bars (T ≈ 75 bars/day × 250 days = 18,750), feasible in seconds.

**Sub/super-martingale tests (SMT):** Polynomial, exponential, power trend specifications.
More flexible than ADF; penalization parameter φ adjusts sensitivity to short vs long bubbles.

**For the breadth rider:**
- SADF computed on NIFTY log-price in the morning session (9:15-10:15) could add predictive content.
  An explosive SADF (large positive value at 10:15) might predict a regime where breadth-driven
  momentum is likely to be sustained. This is a concrete signal candidate for the meta-labeling layer.
- CUSUM of residuals from a rolling VWAP regression could detect "structural break from equilibrium"
  which would be a buy signal if paired with breadth gate.

**Honest assessment:** Full SADF is compute-intensive for a daily pipeline but feasible in Python
in under 1 second on 75 bars of 5-min data. Worth implementing as a candidate feature.

---

## Methodology Upgrade Proposals

### 1. DSR N-Count Audit (PRIORITY — small effort, high impact)
**What to do:** Audit our trial registry. Count the number of *distinct strategy families* tested
(not total parameter grid points). If we swept breadth thresholds from 0.65 to 0.80 in steps of
0.01 (16 variants) and ATR multipliers from 1.5 to 3.5 in steps of 0.25 (9 variants), that is
one strategy family with 144 configurations — not 144 independent trials. The effective N for DSR
should be the number of conceptually distinct hypotheses tested, not the grid size.

**Recommended practice:** Tag each trial in the registry with a "strategy family ID." DSR N = number
of distinct family IDs. This will likely increase SR* and lower DSR, giving a more honest picture.

### 2. Triple-Barrier Label Audit
**What to do:** Verify that our backtester's signal exits use barrier logic consistent with
the triple-barrier framework. Specifically:
- Breadth rider: upper barrier = 3.5xATR trail touch (approximately), lower barrier = 2xATR stop,
  vertical barrier = 15:19:30. This is `[1,1,1]` — correct.
- 0DTE straddle: no upper barrier (theta decay is the mechanism, not a fixed profit take), lower
  barrier = 25% straddle premium expansion, vertical = 15:10. This is `[0,1,1]` — correct.
- **Action:** Document explicitly which barrier configuration each strategy uses so future
  meta-labeling work uses the right label generator.

### 3. Per-Window Consistency Reporting (medium effort, high impact)
**What to do:** For walk-forward windows, report SR and PF *per window* in addition to aggregate.
The book (Ch 12) warns that WF aggregate metrics can mask per-window inconsistency. A strategy
that is profitable in 4 of 5 windows but loses catastrophically in the 5th window has different
risk than one with uniformly moderate performance.

**How:** Add a histogram of per-window PF and SR to our reporting. Flag strategies where any single
window's PF < 0.8 (near break-even after friction), even if aggregate is positive.

### 4. HHI Runs Concentration Metric (small effort, medium impact)
**What to do:** Add HHI concentration indices to our backtest report:
- h+ = concentration of positive returns (we want low — distributed wins)
- h- = concentration of negative returns (we want low — no catastrophic single-loss dependence)
- h[month] = concentration of bets by calendar month (seasonality check)

**Relevance:** Our 0DTE straddle likely has h- spike if a few expiry days cause most of the losses.
If h- > 0.3 on the confirmed strategy, investigate those loss-day conditions.

### 5. Structural Break Feature for Breadth Rider (medium effort, medium impact)
**What to do:** Add SADF computed on first 75 bars of 5-min NIFTY log-price as a candidate feature
for the meta-labeling filter. Also add CSW-CUSUM (Chu-Stinchcombe-White) on levels.

**Implementation:** Compute at 10:10 (before signal window at 10:15). If SADF > 1.5 (mild explosive),
flag as "trending morning." If SADF < -1.5, flag as "mean-reverting morning."

**Hypothesis:** Breadth-gated momentum trades should work better when the morning already shows
explosive (trending) SADF, consistent with the signal. This is a testable filter.

### 6. Strategy Risk Verification: Minimum Precision Analysis (small effort, high impact)
**What to do:** For each live/paper strategy, compute the minimum precision p_min needed to achieve
our target SR (e.g., SR=1.0 after costs), and bootstrap P(p < p_min) from observed trade outcomes.
If P(failure) > 10%, flag strategy for review.

**For the 0DTE straddle:** Current holdout PF 1.53. Compute implied precision at observed win/loss
payoff ratio. Bootstrap P(actual p < p_min) across the 8m/2m windows.

---

## Sizing Risk Insights

1. **At Rs 5L and NIFTY lots of ~Rs 5L notional each, we are at minimum-lot-size capital.**
   The book's bet sizing framework (Ch 10) assumes fractional sizing is possible. For us, it is binary.
   Any "sizing" decision is purely: enter 1 lot or skip. This is correct — do not force fractional lots.

2. **Implied precision for our strategies:** 0DTE straddle with ~Rs 600 friction and typical
   straddle premium of Rs 200 (each side), 25% stop means Rs 100 max loss per lot. If we model
   payoff as theta decay over holding period vs catastrophic stop-hit, the win/loss ratio is
   approximately +80/−100 (rough). At that ratio, break-even precision = 100/(80+100) = 0.556.
   So p > 0.556 is needed. Check our observed hit ratio — it should be reported.

3. **The precision/frequency trade-off (Ch 15):** With 52 expiry trades/year, the SR is very
   sensitive to precision. A 3-percentage-point drop in hit rate (from 0.65 to 0.62) reduces
   annualized SR by ~0.6. This makes the daily breaker (2% daily P&L loss) critical — it prevents
   one catastrophic day from destroying precision stats.

4. **Regime dependence and "aggregate masking" (our known pain):** The book addresses this
   implicitly in the CPCV framework: generate multiple paths and report the *distribution* of
   SRs. Our bootstrap PF CI partially addresses this. The upgrade is to split performance by
   market regime (high-ATR / low-ATR days) and report separate precision estimates per regime.

5. **Option-C debit spreads (PENDING paper comparison):** The book (Ch 10) warns that bet sizing
   from predicted probabilities is only meaningful when the position can be sized proportionally.
   For defined-risk option debits at 0.75% per-trade risk, the relevant question is not how large
   to size but whether to trade at all. The meta-labeling framework (enter/skip binary decision)
   is the correct framing — not continuous bet sizing.

---

## Strategy Ideas from Book

### A. Meta-Labeling Filter on Breadth Rider
**Source:** Ch 3, §3.6–3.7 and Ch 8 (feature importance first)
**Idea:** Build a binary classifier that predicts whether today's breadth signal will succeed.
Features: morning SADF, ATR percentile rank (last 20 signal days), gap vs. prior close,
breadth signal margin above 0.72 threshold, day-of-week.
**Fit:** Requires ~200+ labeled samples minimum. Currently in paper — collect data first.
Overfitting risk is high with 40-300 trades; use SFI to screen features before fitting anything.

### B. SADF Morning Momentum Feature
**Source:** Ch 17, §17.4.2
**Idea:** Compute SADF on 5-min NIFTY bars from 9:15 to 10:10. If SADF > critical value (≈1.5),
"morning is trending." Use as gating condition alongside breadth gate. Requires 15 bars minimum.
**Fit:** Straightforward to compute. Test in paper alongside existing breadth rider without
changing the base signal. Add as second condition: both breadth >= 0.72 AND SADF > 0.

### C. Structural Break → Regime Switch Alert
**Source:** Ch 17, §17.3 (CUSUM)
**Idea:** Run daily CSW-CUSUM on NIFTY log-price. When CUSUM exceeds critical value, flag as
"regime transition." During transitions, increase stop multiplier (from 2x to 2.5x ATR) or skip
the breadth rider signal entirely. This addresses the 2022 vol vs 2023-25 grind regime dependency.
**Fit:** Practical — CUSUM on daily close prices updated each morning. Computationally trivial.

---

## What We Already Do Right (per book standards)

1. **Pre-registered trial grid with ~1100 entries** — satisfies the book's requirement to log all
   trials before computing DSR (Ch 14, Third Law of Backtesting).
2. **Walk-forward 8m/2m with frozen holdouts used exactly once** — correct per Ch 12's WF guidelines.
   Holdout integrity preserved.
3. **Bootstrap PF CIs** — addresses the "single-path variance" problem of WF (Ch 12, §12.5).
4. **Deflated Sharpe (Bailey-LdP) computed** — this is the book's DSR formula (Ch 14, §14.7.3).
   Subject to N-count audit above.
5. **+50% slippage stress** — consistent with Ch 14's "dollar performance per turnover" metric.
   The book would call this a "resilience-to-costs" sanity check.
6. **Plateau-over-spike selection** — directly addresses Ch 12's warning that WF backtests exhibit
   high variance; selecting stable strategies over peak-SR strategies is the right practice.
7. **Causal ATR slippage** — no look-ahead in cost model (Ch 11 sin: #5 transaction costs).
8. **Point-in-time lot sizes** — eliminates survivorship/reconstitution bias in sizing (Ch 11 sin: #1).
9. **15:19:30 force-flat** — implements the vertical barrier of the triple-barrier method for
   all intraday strategies (Ch 3, §3.4).
10. **Date-banded costs including 2026 STT hikes** — correct per Ch 11 sin: #5.

---

## Contradictions and Cautions

1. **CPCV vs. our WF:** The book argues (Ch 12, §12.5) that WF has high variance in SR estimates
   because each decision is made on a different (growing) sample size. CPCV reduces this variance
   by generating many paths. Our single WF path + bootstrap approximates this but does not fully
   replace CPCV. For a 2-year backtest with our trade frequency (~200-400 trades), the variance
   of the WF SR estimate is non-trivial. The bootstrap PF CI helps but operates on trade PnL,
   not on path-level SR distributions.

2. **Meta-labeling requires ML, but we have sparse data:** The book builds meta-labeling models on
   thousands of observations. Our 40-300 trades are 1-2 orders of magnitude smaller. Any ML model
   fit on this data will overfit. The meta-labeling framework is correct in principle; the data
   volume is insufficient for the full implementation now.

3. **DSR N-count: dependent vs independent trials:** The book specifies N = number of *independent*
   trials. In practice, parameter sweeps on the same strategy are not independent. If we have been
   counting each parameter combination as a separate N, our SR* is too high (conservative DSR —
   overly penalizes the strategy). If we count only strategy families, our SR* may be too low
   (aggressive DSR — under-penalizes). The honest count is somewhere in between and requires
   subjective judgment about what constitutes a "distinct hypothesis."

4. **The book's assumption of stationarity vs. our known regime change:** The book's PSR/DSR
   framework assumes the SR is a stationary parameter with estimation error. In reality, our
   strategies have SR ≈ 2 in 2023-25 grind regimes and SR ≈ 0 or negative in 2022 vol regime.
   This is not estimation error — it is structural non-stationarity. PSR/DSR cannot distinguish
   "regime-dependent strategy that happens to look good in the sample period" from "genuinely
   skilled strategy." The structural break tests (Ch 17) and per-window consistency check
   (Methodology Upgrade #3) are the mitigations.

5. **Triple-barrier symmetry assumption:** Ch 13 (synthetic data) shows that for mean-reverting
   processes (O-U), symmetric barriers (`[1,1,1]` with equal PT and SL) are typically suboptimal.
   The optimal rule often has a larger stop than profit-take (especially for short half-lives).
   Our breadth rider uses 2xATR stop and 3.5xATR trail, which is asymmetric but the other way —
   larger profit target than stop. This may be appropriate for momentum (not mean-reversion), but
   worth verifying against the Ch 13 heat-maps: our strategy should be characterized as
   momentum/trending, not O-U.

6. **Bet sizing at 1-lot minimum is coarse:** The bet sizing framework of Ch 10 provides optimal
   sizing based on predicted probabilities. At Rs 5L capital, 1 NIFTY lot is already our entire
   capital. The framework's continuous sizing cannot be applied. This is a hard constraint that the
   book does not address — it is an Indian-market small-capital reality, not a methodological flaw.

---

## De-prioritized Chapters — Brief Notes

**Ch 2 (Data Structures — tick/dollar/volume bars):** Tick data not available. Dollar bars have
better statistical properties than time bars (more homoscedastic). If tick data becomes available,
priority switch to dollar bars for feature construction. Current 1-min/5-min time bars are
second-best but standard for Indian exchange data.

**Ch 5 (Fractional Differentiation):** Preserves memory in features while achieving stationarity
(between raw price and full diff). Relevant if we ever build price-level features. Currently moot
as our only signal is breadth (stationary) and ATR (stationary).

**Ch 6 (Ensemble Methods):** Standard RF/bagging theory. Relevant only when we have enough data
to fit an ML model. Note from Ch 4: use `max_samples=average_uniqueness` when bagging on
overlapping financial labels.

**Ch 18-19 (Entropy/Microstructural Features):** Require tick data (bid/ask, order flow imbalance,
VPIN). Not currently available. Note for future: if Dhan provides tick stream, entropy of order
flow could be a useful signal.

---

## Files Reference
- Source: `/tmp/books/deprado.txt` (plain text extraction of Wiley 2018 first edition)
- This distillation: `/home/ubuntu/projects/algo-trader/docs/books/deprado.md`
