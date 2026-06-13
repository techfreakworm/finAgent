# Volatility Trading, 2nd Ed — Sinclair (2013)
## Distillation for NIFTY/BANKNIFTY 0DTE Short Straddle + Index Trend Rider
### Pass-2 read; Pass-1 (de Prado / Chan / Kaufman) already applied — build on, do not repeat.

---

## Priority Context

**Strategy 1 (confirmed survivor):** NIFTY 0DTE expiry-day ATM short straddle — sell 09:20, 25% straddle-value stop, flat 15:10.
**Strategy 2 (in paper):** Breadth-gated index trend rider, futures leg vs debit-option leg.
**Capital:** Rs 5L, 1-lot scale, Indian markets. F&O STT 0.05% post-2026, equity intraday ~Rs 600 round trip.

---

## Sections Covered

- Ch 1: Option Pricing (BSM mechanism, gamma/theta relationship)
- Ch 2: Volatility Measurement (all estimators: C2C, Parkinson, GK, RS, YZ, barrier)
- Ch 3: Stylized Facts (vol clustering, mean reversion, return distribution)
- Ch 4: Volatility Forecasting (EWMA, GARCH, variance premium measurement, vol cones)
- Ch 5: Implied Volatility Dynamics (level/slope/curvature PCA, mean reversion of IV, smile dynamics)
- Ch 7: Distribution of Hedged Positions (path dependency, hedging-vol choice)
- Ch 8: Money Management (Kelly for continuous distributions, Kelly for short-vol/negative skew, bankroll, Browne)
- Ch 9: Trade Evaluation (performance measures, persistence, hypothesis testing with few trades)
- Ch 11: Generating Returns through Volatility (VRP evidence, straddle/strangle backtest results)
- Ch 12: The VIX (VIX construction, VIX as IV filter)
- Ch 14: Life Cycle of a Trade (pretrade checklist, execution, posttrade)
- Ch 15: Conclusion

---

## Chapter 4 — Variance Risk Premium: The Mechanism

### Core finding

Implied volatility is persistently above realized volatility for equity indices. Sinclair documents the VIX vs 30-day rolling close-to-close spread for the S&P 500: **average spread is ~3.09 vol points** (VIX almost always above realized). This spread is not constant — it narrows at high VIX levels (the market expects mean reversion, compressing the risk-neutral premium) and is proportionally larger at low VIX levels.

**Why VRP exists for indices (not individual stocks):**
1. Sellers are providing insurance — there is a structural risk premium.
2. Index options have steep put skew driven by correlation premium and crash fear. Much of the variance premium comes from the OTM put wing, not the ATM straddle alone.
3. Buyer-side behavioral effects: lottery-seeking call buyers inflate call prices; crash-fearing put buyers inflate put prices.
4. Market-maker bid bias: MMs quote options slightly rich to protect themselves (rational business behavior).

**What Sinclair is explicit about:** The existence of VRP does NOT mean you should always sell implied volatility. Insurance premiums alone are not sufficient edge — option sellers can't reinvest the premium like insurance companies. You need the spread to be *unusually large* relative to historical norms. See pre-trade filter procedure below.

### Pre-trade VRP Filter (Implementable Procedure)

1. Measure your target underlying's ATM IV (India India VIX analog or NIFTY ATM 0DTE straddle mid-price implied vol).
2. Measure rolling realized volatility for the same horizon (for 0DTE: use intraday realized vol, 5-min bars, Yang-Zhang or GK estimator annualized).
3. Compute IV minus RV = current spread.
4. Compute the historical average of this spread (rolling 252-day window of daily IV-minus-30d-RV is Sinclair's S&P proxy). Call this the "long-run premium."
5. The **spread-in-excess-of-premium** = (current IV - RV) - long-run premium. This is the actionable signal.
6. Sinclair uses VIX level as a regime filter: **sell straddles / strangles only when the VIX-analog is below 35** (or equivalently, below its own EWMA with lambda=0.95). His results:
   - Raw strangle selling (QQQ, 2000-2010): Ann. return 21.7%, Sharpe 1.14, max DD 32.7%.
   - VIX-below-35 filter: Ann. return 17.8%, Sharpe 1.30, max DD 31.4% — **smoother, avoids 2008-type blowups**.
   - VIX-below-EWMA filter: Ann. return 21.8%, Sharpe 1.68, max DD 10.3% — best, but curve-fit risk on specific MA parameter.

**Application to 0DTE NIFTY straddle:**
- Maintain a rolling 20-day India VIX (or NIFTY ATM IV) history. Compute 20-day EMA.
- Pre-trade gate: only sell the straddle if India VIX is below its 20-day EMA (or below some absolute threshold like 22-25 — calibrate on local data).
- Separately: compute yesterday's 30-min open-to-close realized vol. If IV/RV ratio is < 1.1 (spread is thin), skip — the edge is insufficient to cover Rs 600+ round-trip friction.

---

## Chapter 2 — Volatility Measurement: Which Estimator for 0DTE

For measuring realized vol to compare against the straddle's implied vol, Sinclair reviews five estimators:

| Estimator | Efficiency vs C2C | Key Weakness |
|---|---|---|
| Close-to-close | 1x | Very noisy; 30-day window ±25% at 95% CI |
| Parkinson (H-L range) | ~5x | Biased low (discrete sampling); ignores overnight |
| Garman-Klass | ~8x | More biased low than Parkinson in finite samples |
| Rogers-Satchell | Better in trending | Handles drift; still biased low |
| Yang-Zhang | ~14x (best case) | Includes open-jump component; biased high in GK component |

**Rule: 30 daily closes gives ±25% confidence interval on vol estimate — unacceptably wide for a sparse-trade system.**

**For 0DTE intraday realized vol:**
- Use 5-min bar Yang-Zhang (handles overnight and opening gaps) or Garman-Klass on 5-min data.
- Annualize with 252 × 6.5 hours = ~1638 5-min bars/year for intraday, or use just that day's bar count.
- The barrier estimator (time to cross a fixed corridor) is ~1.7x more efficient than same-frequency C2C and is a natural fit for streaming data. Not widely implemented but tractable.
- **Sinclair's practical recommendation:** Use multiple estimators and look for agreement. The correlations between estimators on real market data are much higher than on simulated data (>0.9 for S&P). Significant divergence between estimators signals a regime anomaly worth investigating before selling.

---

## Chapter 4 — Volatility Forecasting: EWMA vs GARCH

### EWMA
- Simple, transparent, widely used.
- λ between 0.90 and 0.99.
- **Flaw:** Does not encode mean reversion. Forecast for day+1 = same as day+2 = ... It is a random walk in volatility. If today's vol is high, it stays high forever in the EWMA forecast.
- **Also flaw:** Treats all past shocks as equally important, decaying exponentially — but earnings gaps are single events and shouldn't produce a lingering ghost.

### GARCH(1,1)
- Adds long-run mean variance V_L. Process: σ²(t) = ω + α·r²(t-1) + β·σ²(t-1).
- When α+β < 1, variance reverts to the long-run mean. Forecast is a dampened exponential back to V_L.
- **Practical problem:** Log-likelihood function is very flat — fitting is numerically unstable. Need ≥1,000 data points. Use variance targeting (set ω = V_L × (1-α-β)) to stabilize.
- **Confirmed** by Sinclair: intraday data has persistent seasonality (U-shaped intraday vol pattern) which breaks GARCH assumptions. **GARCH is best fit to daily close-to-close data, not 5-min bars.**
- GARCH family extensions (EGARCH, GJR-GARCH) capture asymmetry (negative shocks increase vol more than positive). EGARCH is the preferred form for equity indices because of the leverage effect.

### What Sinclair says both get wrong

**A point forecast of volatility is not what a short-vol trader needs.** You need the *distribution of realized volatility over the option lifetime.* The right tool is the **volatility cone** — the historical percentile distribution of rolling N-day realized vol for each N. Selling 0DTE when today's ATM IV is at the 90th percentile of the historical 1-day realized vol cone is a sensible plan. Selling because GARCH forecasts 12% when IV is 15% is less sensible because the cone might show 1-day realized vol has been as high as 40%.

**Volatility cone procedure:**
1. Calculate realized vol (Yang-Zhang or C2C) over 1, 5, 10, 21-day non-overlapping windows across the full history.
2. Record min, 10th, 25th, 50th, 75th, 90th, max percentiles for each window.
3. At trade time: where does today's ATM IV sit on the 1-day cone? If it is above the 75th percentile, the straddle is selling rich relative to history.
4. If IV is in the bottom quartile of the cone — insufficient edge. Skip.
5. Adjust for overlapping-window bias using the Hodges-Tompkins factor (multiply variance by (2h-1)(h-1)/(6h(n-h+1)) where h is window and n total days) when building the cone from rolling data.

---

## Chapter 5 — Implied Volatility Dynamics

### What moves IV (PCA evidence)

From Alexander (2001) on S&P/Nikkei options:
- **65–80% of IV surface variation is a parallel level shift** (ATM vol moving up or down).
- 5–15% is slope/skew tilting.
- ~5% is curvature (smile curvature).

**Implication for 0DTE straddle:** You are primarily trading the ATM IV level. Skew dynamics (which option is richest) are second-order for a delta-neutral straddle. Monitor level first; use skew only for wing management.

### IV Mean Reversion (Tradeable Definition)

Sinclair's trader's definition: a series is mean-reverting if methods that assume moves reverse are profitable. VIX passes this test. A Bollinger-band strategy (sell 2σ above EWMA, buy 2σ below, exit next day) gave 62.2% winners, avg winner 1.05, avg loser 0.93 on VIX from 1990.

**Key autocorrelation data for VIX:** daily –0.04, weekly –0.21, monthly –0.12. The weekly autocorrelation is material — if India VIX had a large spike last week, expect mean reversion this week. This is a timing input for the straddle (prefer to sell after IV spikes, not after extended low-IV periods).

### What this says about our 25% stop

Sinclair's explicit advice (Ch 14 and Ch 8): "We should never get stopped out of a trade just because we have lost money. We exit trades if we no longer like them... We expect high volatility to be transient, so stopping ourselves out after every high-volatility burst is not a good idea."

**This is in direct tension with our mechanical 25% straddle-value stop.** Sinclair's framework would evaluate whether the stop fires because of a regime change or transient noise. If IV has spiked but the VRP is still positive (realized still below implied), the book suggests staying in. Our stop at 25% fires on 57% of days — this is high, and Sinclair's framework suggests we are exiting too many mean-reverting volatility spikes prematurely.

**Sinclair's preferred stop logic:** "Instead of a stop based purely on our P/L, we have a plan based on our edge evaluation and our initial trade size. Trades going bad shouldn't be too painful if they are kept small enough." The implicit stop is position-size-driven, not mechanical P&L-driven.

---

## Chapter 11 — Generating Returns through Volatility: Straddle/Strangle Evidence

### Empirical results (QQQ, 2000–2010, strategy-based margining, $100K capital)

| Strategy | Ann Return | Sharpe | Max DD |
|---|---|---|---|
| Sell 10δ strangle | 41.6% | 1.32 | 20.6% |
| Sell 20δ strangle | 93.3% | 1.21 | 34.2% |
| Sell 30δ strangle | 64.1% | 1.14 | 32.9% |
| Sell ATM straddle | 58.6% | 1.14 | 32.7% |
| Sell ATM straddle + VIX<35 filter | improved DD | — | — |

Note: These are **delta-hedged** results. Our 0DTE straddle is unhedged. This changes the risk/return profile substantially.

**Critical Sinclair finding: VRP is an index effect, not an individual stock effect.** Table 11.1 shows selling straddles on Dow Jones individual stocks showed no persistent premium. "The variance premium is largely an index effect." This directly confirms our strategy choice: selling NIFTY index options is the right vehicle.

**Why ATM straddle Sharpe is lower than 10δ strangle:** ATM short straddles have the largest gamma exposure. Every move hits the P&L immediately. Strangles at 10δ give time to breathe. For 0DTE, there is no "time to breathe" — gamma is extreme from open. This is our regime-specific challenge.

### Skewness premium decomposition

About half the variance premium on S&P 500 OTM puts is due to the realized skewness (negative correlation between returns and volatility), not pure crash fear. For ATM straddles, both legs participate. Selling the put leg into a falling market hurts both from delta (short put goes ITM) and from vol expansion (negative correlation between spot and IV). **The ATM straddle stop needs to account for vol-of-vol on the downside more than on the upside** — losses are asymmetric.

---

## Chapter 8 — Money Management: Kelly for Short-Vol Negative Skew

### Kelly for continuous distributions

For small edge (the realistic case), the Kelly fraction simplifies to:

**f* = E[payoff] / Var[payoff]**

This is implementable. For the 0DTE straddle:
- E[payoff]: estimated from historical mean daily P&L on the straddle.
- Var[payoff]: estimated from variance of daily P&L on the straddle.

**With 40–300 trades in the backtest**, the Kelly estimate is highly uncertain. Sinclair shows that the standard deviation of the Kelly fraction estimator scales as 1/√N. With N=100 trades, the std dev of f̂ is ~1/10 of the edge divided by variance — still very wide. **Sinclair's advice: trade a fraction of the estimated Kelly, biased significantly downward.**

Bayesian correction: the naive win-rate estimate always overstates edge. The correct Bayesian Kelly fraction uses (w+1)/(N+2) rather than w/N as the win probability estimate, shrinking toward 0.5. With 100 trades at 55% win rate, this matters.

### Fractional Kelly in practice

Table 8.1 (probability of doubling before halving):
- Full Kelly: 66.7%
- 80% Kelly: 73.9%
- 60% Kelly: 83.4%
- 40% Kelly: 94.1%
- 20% Kelly: 99.8%

At 1-lot scale, we cannot truly implement fractional Kelly because 1 lot is binary — you trade it or you don't. But the principle applies to choosing when to trade at all. **Trading only when the edge filter is clearly met (IV >> RV cone percentile 75%) is equivalent to fractional Kelly — skip the marginal trades.**

### What Kelly says about negative skew payoffs

The Kelly criterion is general — it applies to any distribution. For short-vol strategies:
- The distribution is left-skewed (many small wins, occasional large loss).
- The formula f* = E/Var implicitly penalizes high variance, which for negative-skew distributions includes those large losses.
- **Sinclair is explicit: adding arbitrary price-based stops to a trading system is a poor idea.** A loss is not evidence of being wrong in a VRP trade. But a stop does reduce the variance in the Kelly formula, which increases the Kelly fraction. This is a paradox: stops increase the Kelly-optimal size but may throw away good trades.

The resolution: the stop should be calibrated to the volatility of volatility, not an arbitrary percentage of premium received. If your straddle loss exceeds 3σ of the historical daily P&L distribution, that is a meaningful stop trigger. Our 25% premium stop is an ad hoc rule that should be validated against this criterion.

### "Bankroll" definition for Indian retail

Sinclair: "Bankroll is the amount you can lose before the strategy is abandoned." For us: Rs 5L capital, but we should not treat the full Rs 5L as bankroll for a single strategy. A sensible bankroll is the capital allocated to just this strategy. If each strategy leg gets 60% of capital (Rs 3L), then Kelly fraction applies to Rs 3L, not Rs 5L.

At 1-lot scale, the haircut/margin for one NIFTY ATM short straddle is roughly Rs 80–120K (exchange SPAN). If bankroll is Rs 3L, and one-lot margin is Rs 1L, you are already running at high leverage relative to the Kelly-optimal fraction. This suggests sizing is already at or above Kelly for many strategies at this capital level.

### Alternative: Browne targeting scheme

If you have a specific target return (e.g., 50% of bankroll in a quarter), Browne's dynamic sizing is optimal. It ramps up aggressively early in the session (when far from goal) and dials down as the goal is approached. For paper trading with a defined test horizon, this framework is useful for deciding "am I on track?"

---

## Chapter 7 — Hedging Path Dependency (Applied to Unhedged 0DTE)

We run the 0DTE straddle unhedged (no delta hedge). Sinclair shows that even perfectly predicting realized vol does not guarantee profit on a hedged position — due to discrete hedging and path dependency. For an **unhedged** straddle:

- We are taking on full delta exposure from the moment the underlying moves.
- The P&L depends on where the underlying ends up at 15:10 (our flat time), not just on average realized vol.
- The relevant risk is not average vol but peak gamma exposure near the strike combined with the path of the underlying.

**Sinclair on pin risk (Chapter 7):** When an option expires near the strike, "expiring at a strike greatly increases the feedback effects of dynamic hedging of the market makers. If they are long gamma, they will all be buying below the strike and selling above... compressing realized volatility." This is the pin effect — expiry-day vol tends to be suppressed near large open interest strikes. For us this is usually favorable (we're short straddle; if pin occurs near our strike, theta harvesting dominates), but it can cause chaotic terminal hedging behavior from MMs.

**Key hedging-vol result from Ch 7:**

| Hedge at | P&L Smoothness | Final P&L Certainty |
|---|---|---|
| Realized vol | Noisy intraday | More certain at expiry |
| Implied vol | Smooth intraday | More uncertain at expiry |

Since we don't hedge at all, we get the worst of both worlds: noisy intraday AND uncertain final. This is the honest cost of our simplicity. The mitigation is: (a) keep the trade sized so that the largest historical daily loss is survivable, (b) rely on the VRP being structurally positive to win on average.

---

## Chapter 9 — Trade Evaluation with Sparse Statistics

### The Sharpe ratio problem for 40–300 trade strategies

Sinclair (citing Lo 2002): The standard deviation of the measured Sharpe ratio is approximately √((1 + SR²/2)/T) where T is the number of observations. **With 100 daily results, an observed Sharpe of 1.5 has a standard deviation of ~0.15.** This is still narrow enough to be useful, but the estimated SR for 40 trades is nearly meaningless.

**What to track instead (Sinclair's daily trade statistics checklist):**
- Average daily P&L (gross and net of costs).
- % winning days.
- Average winning day / average losing day ratio.
- Maximum win and maximum loss (stress test: can you cope with 2× the max loss?).
- Maximum drawdown (magnitude and duration).
- Cumulative P&L shape (K ratio preferred over Sharpe for path-dependent strategies).

**For the straddle specifically** (direct Sinclair quote): "a short straddle position will win on more days than it loses but its losses will tend to be larger than its wins." Monitor: are the wins consistent with the VRP edge? Are the losses correlated with specific market regimes?

### Performance persistence test (few trades)

With 40–300 trades, can we detect persistence? The cross-product ratio (CPR) test splits the period in two and checks if winners in sub-period 1 are winners in sub-period 2. With N=100 trades (50 per period), the test has very low power. **Recommendation: use the K ratio (slope of log-cumulative-returns regression divided by its standard error) as the primary diagnostic. It is a t-score — directly interpretable as confidence that the cumulative return is positive.**

Hurst exponent (Ch 9): Sinclair gives a simple approximation: H ≈ 0.5 + log(R/S) / log(2n^0.5). H > 0.5 = persistent P&L stream. For our straddle, we expect H > 0.5 (consistent edge = persistent drift). If the rolling H drops below 0.5, the strategy's edge may have degraded.

### Absolute threshold for "stop trading"

Sinclair's example: a loss beyond (roughly) the level that would make you reevaluate the fundamental premise. For a short-vol strategy: "stopping ourselves out after every high-volatility burst is not a good idea" but "a 30% drawdown starts to raise questions of survival."

For us at Rs 5L: a Rs 1.5L drawdown (30%) should trigger a pause and regime review. This is the circuit breaker level. Note: our existing 2% daily breaker is much tighter — that prevents single-day catastrophe; the 30% level is the strategy-level drawdown review threshold.

---

## Chapter 2/14 — The Pretrade Checklist (Sinclair's Full Process)

From Ch 14 (AAPL trade walkthrough):

1. **Measure realized vol** using multiple estimators (table the numbers like Ch 14 Table 14.1).
2. **Place implied vol in context** using the volatility cone. Where is today's ATM IV in the historical distribution?
3. **Compare implied vs realized spread** to the "normal" spread for this underlying and to the broad market (VIX analog).
4. **Identify the catalyst** — why is IV priced where it is? For 0DTE: is today expiry? Is there an event (RBI meeting, large Index rebalance)?
5. **Compute expected P&L** as vega × (IV - forecast RV). For 0DTE unhedged, this is approximate; the actual payoff depends on gamma/path.
6. **Size the trade** with reference to bankroll and Kelly fraction. For 1-lot: the decision is binary — trade or don't.
7. **Pre-establish exit criteria:** not just a stop but edge reassessment. If IV compresses 30% by 12:00 (IV crush), book the profit early.
8. **Posttrade:** compare actual RV with the initial IV. Did we predict correctly? Did the trade PnL match the expected vega × vol spread?

---

## Key Contradictions and Cautions for Our Setup

### 1. Sinclair's stop advice vs our mechanical 25% stop

Sinclair: "Adding arbitrary price-based stops to a trading system is a poor idea." He would prefer we exit when edge disappears (IV drops near realized), not when the loss hits a dollar threshold.

**Our position:** At 1-lot paper trading scale, mechanical stops are operationally necessary and risk-control appropriate. But the 57% fire rate suggests calibration is off. The stop may be too tight, capturing normal daily noise rather than genuine regime changes. Consider calibrating to 2σ of historical daily straddle P&L instead of 25% of premium received.

### 2. VRP premium doesn't survive friction at sub-scale

For Rs 600 round trip on a straddle that may collect Rs 2,000–3,500 in premium (2-lot NIFTY ATM), friction is 17–30% of premium received. Sinclair's book assumes institutional-quality fills (penny bid-ask). In Indian F&O on expiry day, the ATM straddle bid-ask spread can be Rs 5–15. For 2 legs × 2 sides = 4 crosses, that's Rs 20–60 in spread cost alone plus STT. The VRP edge must be substantial — above the 75th percentile of the IV-RV cone — to justify the trade at this cost structure.

### 3. Straddle VRP is partly skewness premium, not just ATM vol

Sinclair shows ~50% of S&P index VRP comes from OTM puts. Our ATM straddle captures the ATM premium but misses most of the OTM put premium. Conversely, if markets drop sharply on expiry day, our short put is suddenly the dominant risk. **The straddle is not a clean VRP capture — it has significant negative tail exposure.** A properly VRP-harvesting position would be a strangle, but strangles require higher margin at 1-lot scale.

### 4. GARCH fitting on 1-min intraday bars

Sinclair is explicit: "Daily data seems to be the natural timescale to use for GARCH modeling." GARCH(1,1) fitted to 1-min or 5-min bars will have poor out-of-sample performance due to microstructure contamination and persistent intraday seasonality. Our realized vol estimator for the 0DTE filter should be computed from daily close-to-close data, not 5-min intraday bars.

### 5. The index vs individual stock distinction

VRP is an index effect, not present in individual stocks. NIFTY and BANKNIFTY qualify as broad indices. This supports the strategy design. But BANKNIFTY is more volatile and more sector-concentrated (banking), so its VRP structure may differ. We should build separate IV-RV cones for NIFTY and BANKNIFTY and not assume the same filter thresholds apply.

---

## Methodology Upgrades Derived from Sinclair

### U1: IV-RV Spread Pre-trade Filter (High Impact, Medium Effort)

Before entering the 0DTE straddle, compute:
- 30-min India VIX or NIFTY ATM straddle implied vol at 09:20 entry.
- Rolling 20-day realized vol (YZ or GK estimator on daily bars).
- Long-run average IV-RV spread (rolling 252-day window).
- Current spread minus long-run average = "excess premium."
- **Filter: only sell if excess premium > 0 AND India VIX < 20-day EMA of India VIX.**

This is the Sinclair Ch 4/Ch 11 filter translated to our context.

### U2: Volatility Cone for 0DTE Context (Medium Impact, Small Effort)

Build a historical cone for 1-day realized vol (NIFTY, last 3 years of daily data). At 09:20 entry, check where today's ATM IV sits on the cone.
- IV above 75th percentile of 1-day cone → strong sell signal.
- IV between 50th and 75th → marginal, proceed only if other filters pass.
- IV below 50th → do not sell.

**This is the most directly implementable upgrade from Sinclair.**

### U3: Stop Recalibration to Vol-of-Vol (Medium Impact, Medium Effort)

Replace the 25% premium stop with: compute daily std dev of straddle P&L from paper-trading history (or backtest). Set stop at 2σ of that distribution. If σ of daily P&L is Rs 800, stop at Rs 1,600 loss (2σ), not at 25% of premium.

This is the Sinclair Ch 8 / Ch 9 approach: stops anchored to P&L distribution, not arbitrary premium fractions.

### U4: K Ratio as Primary Diagnostic (Low Impact, Small Effort)

Add K ratio computation to the paper trading report. This is the slope of log-cumulative-returns over its standard error. For the straddle with ~20 trades so far, this gives a more honest confidence interval than Sharpe ratio.

### U5: Post-trade IV vs RV Attribution (Medium Impact, Small Effort)

After each expiry-day straddle trade, log:
- Entry IV (straddle mid / underlying).
- Realized vol (computed from 5-min bars of NIFTY for that day).
- Expected P&L from vega × (IV - RV).
- Actual P&L.
- Difference = path-dependency / stop / execution drag.

Tracking this over 50+ trades will separate IV prediction accuracy from execution/stop drag, following Sinclair's Ch 14 methodology.

---

## What We Already Do Right (Confirms Sinclair)

1. **Index options, not individual stocks** — VRP is structural for indices. Confirmed.
2. **Multiple vol estimators** — we use GK, Yang-Zhang, and GARCH. Sinclair recommends the same. Confirmed.
3. **Walk-forward with out-of-sample holdout** — Sinclair emphasizes edge must be genuine, not backtest artifact. Our frozen holdout methodology is the right approach.
4. **Per-trade records and attribution** — Sinclair's Ch 9 is a match for our EOD reporting framework. Confirmed.
5. **No arbitrary discretionary overrides of system signals** — Sinclair warns against managers/traders subjectively sizing down during bad patches. We pre-commit to the system. Confirmed.
6. **Pre-registered trading plan** — Sinclair's pretrade checklist (Ch 14) mirrors our trial registry. Confirmed.
7. **Regime-gated entry** (breadth gate for trend rider) — Sinclair's VIX filter approach. Directionally correct.
8. **Capital sizing that prevents ruin** — 1-lot scale with 2% daily breaker is conservative relative to any Kelly-optimal fraction at Rs 5L. Confirmed.

---

## References to Original Text

- VRP evidence and filter: Ch 4 (lines 2906–3026), Ch 11 (lines 8489–8810).
- EWMA vs GARCH: Ch 4 (lines 2498–2754).
- Vol cones: Ch 4 (lines 2766–2843).
- Kelly for continuous distributions: Ch 8 (lines 5500–5592).
- Kelly for short-vol: Ch 8 (lines 5693–5730), Ch 8 (lines 6426–6443).
- Browne targeting: Ch 8 (lines 6018–6076).
- Stop-loss philosophy: Ch 8 (lines 6436–6443), Ch 14 (lines 9595–9604).
- Path dependency of unhedged options: Ch 7 (lines 4933–5070).
- Straddle performance statistics: Ch 9 (lines 6659–6660).
- Volatility estimators: Ch 2 (lines 1469–1739).
- IV dynamics and mean reversion: Ch 5 (lines 3174–3315).
- Pre-trade checklist: Ch 14 (lines 9349–9620).
- Performance measures, sparse data: Ch 9 (lines 6760–7001).
