# Distillation: Positional Option Trading — Euan Sinclair
Pass-2 reading for the intraday algo project (NIFTY/BANKNIFTY 0DTE short straddle + Option-C comparison).
Sections read: Ch 1 (BSM/theta-gamma), Ch 2 (EMH/data mining), Ch 3 (vol forecasting),
Ch 4 (variance premium), Ch 5 (edge taxonomy), Ch 6 (volatility positions),
Ch 7 (directional strikes), Ch 8 (strategy selection), Ch 9 (trade sizing), Ch 10 (meta risks).

---

## Chapter 1 — Options: A Summary (BSM / Theta-Gamma Mechanics)

### Core mechanism: where P&L actually comes from
BSM PDE collapses to: **theta + (1/2) * Gamma * S^2 * (sigma_realized^2 - sigma_implied^2) = 0**

The profit of *any* option position (hedged or not) is proportional to **(sigma_implied - sigma_realized)**.
This is the ONLY source of systematic edge. Structure choice (straddle vs condor vs fly) cannot change
the *average* P&L — it only changes the *shape* of the distribution.

*Project implication*: Our 0DTE short straddle's edge lives entirely in whether NIFTY IV > subsequent
realized vol on expiry days. The variance premium (Ch 4) is the mechanism. Stop calibration, structure
choice, and sizing shape the distribution around that mean but cannot manufacture edge that isn't there.

### Hedging costs vs variance reduction
Daily delta-hedging tightens the P&L distribution dramatically (std dev shrinks ~4x for a 1-year straddle
going from 0 to 252 hedges). But each hedge costs money. Sinclair's table: $0.10/share hedging costs
in an ATM straddle eroded average P&L by **$115** (from -$6 to -$122) — equivalent to misestimating
vol by 2 full points.

*Project implication*: 0DTE straddle with no delta hedge is correct. Intraday hedging at our size
(1 lot, Rs 600 round-trip F&O cost) would be catastrophic. The unhedged distribution's wide left tail
IS the business model — we collect that fat theta and accept path risk.

---

## Chapter 2 — EMH and Data Mining

### Critical rules for backtest validity
- Best-performing rule out of N tested rules is positively biased; bias shrinks with more data, grows with N.
- Apply Bonferroni correction: if best rule selected from 100, required t-score is 2.916 (not 1.96) for 95% significance.
- White's Reality Check (bootstrapped distribution of the maximum of N rule returns) is the rigorous alternative.
- Distinguish phenomenon from parameterization: "NIFTY IV > realized vol is persistent" is a phenomenon;
  "25% stop, 09:20 entry" is a parameterization of that phenomenon.

*Project implication*: Our deflated Sharpe (de Prado, pass-1) accounts for the family count N.
Sinclair's framework aligns: use Bonferroni-level skepticism. The phenomenon (variance premium exists in
index options) is pass-3-confirmed; the parameterization (25% stop, specific entry time) is testable.

---

## Chapter 3 — Forecasting Volatility

### GARCH vs EWMA — practical verdict
Sinclair's verdict: GARCH variants are all roughly equivalent and generally no better than EWMA. All require
~1000 data points for stable MLE, which means 4 years of daily data. Parameters are unstable week to week.

**Practical recommendation**: Choose GARCH(1,1) with fixed α ≈ 0.90-0.94, β ≈ 0.02-0.04 (without MLE fitting),
or simply use EWMA(0.9) or EWMA(0.95). The arbitrary-but-fixed model is better than MLE-refitted GARCH
because it develops intuition and avoids overfitting.

### Ensemble forecasting
Averaging multiple vol models (even similar ones) reduces forecast SD slightly without biasing the mean.
5-model ensemble (30-day HV, EWMA 0.9, EWMA 0.95, VIX, GARCH 1,1) had R^2 = 0.65 vs best individual 0.64.
The gain is modest but free.

*Project implication*: For straddle sizing, use IV(ATM) - median_variance_premium as realized vol forecast.
Averaging EWMA(0.94) with ATM IV is a quick ensemble improvement. For BANKNIFTY India-VIX equivalent,
use VIX INDIA as the implied input, subtract ~3-4 vol pts (India VRP is structurally similar to S&P).

### Implied vol as vol forecast
ATM IV minus the regime-conditioned variance premium (Table 4.3 equivalent for NIFTY) is the best
single realized vol predictor. Applying India-VIX quintile conditioning to shrink vol forecast by the
appropriate premium is implementable with existing data.

---

## Chapter 4 — The Variance Premium

### Evidence base
- S&P 500 (1990-2018): IV averages 4.08 vol pts above realized, positive **85%** of the time.
- Other major indices (Dow, NASDAQ 100, Russell 2000): similar results, mean VP 3.2-3.5 pts.
- VP increases with VIX level. At VIX < 13: mean VP 2.61. VIX > 24: mean VP 5.87.
- Effect documented in bonds, currencies, commodities, VIX options.

**Important for 0DTE**: Lo & Zhang (2005) found VP *increases as options get closer to expiration*.
Tosi & Ziegler (2017) on SPX: premium concentrated in the *last few days before expiry*. This is the
foundational support for 0DTE as the highest VP capture strategy.

### VP vs VIX regime — calibration table (S&P analogy)
| VIX quintile | Mean VP | SD of VP | 10th pct | 90th pct |
|---|---|---|---|---|
| < 13 | 2.61 | 3.54 | -0.81 | 5.16 |
| 13-16 | 3.37 | 3.60 | -0.80 | 7.21 |
| 16-19 | 4.35 | 4.58 | -0.74 | 8.73 |
| 19-24 | 4.19 | 6.53 | -3.15 | 10.45 |
| > 24 | 5.87 | 9.06 | -2.80 | 13.81 |

*Project implication*: Run same quintile table on India-VIX vs NIFTY 30-day realized.
Use VP quintile regime to gate straddle entry. At India-VIX < 13 (rare but 2024 reality),
VP mean is only 2.61 pts — verify theta-capture still exceeds F&O costs at 1 lot.

### Why VP exists (structural — why it won't disappear)
1. Insurance demand: long-stock holders buy puts structurally, driving put VP.
2. Jump risk: options provide non-replicable jump protection, making buyers willing to overpay.
3. Trading restrictions: many retail accounts can only be long options, not short.
4. Market-maker inventory: MMs systematically stay net long teeny options for business insurance.
5. Path preference psychology: people prefer down-then-up paths ("snatching victory"), creating
   demand for long options — hence systematic overpricing.

These are *independent* reasons. They would all have to disappear simultaneously for VP to vanish.
Most are structural features of market microstructure, not arbitrageable away.

### Weekend / overnight premium
All of the index VP is realized **overnight** (delta-hedged: -1% overnight, +0.3% intraday).
Options decay more than expected over weekends (Jones & Shemesh, 2017: weekend return -0.62% vs
other days +0.18%).

*Project implication*: Our 0DTE straddle, sold at 09:20 and closed at 15:10, captures the intraday
VP only (+0.3%). Most of the premium accumulation is overnight. But: (a) NIFTY 0DTE expires same
day so there is no next overnight, and (b) on expiry day the gamma acceleration of the final hours
is the real driver — this IS captured. The point is to be aware that Monday-entering a weekly straddle
would capture the weekend premium too; worth testing.

---

## Chapter 5 — Finding Edges (Confidence Taxonomy)

### Confidence Level 3 edges (strongest)

**1. Implied Volatility Term Structure (contango = sell)**
Sell short-dated options when term structure is in contango (front < back). This is a structural
risk premium, not an inefficiency — likely to persist.

*NIFTY application*: When near-term VIX (NIFTY 1W IV) < far-term VIX (NIFTY 1M IV), the term
structure is in contango. Flag this as a go/no-go filter for 0DTE entry. A flat or inverted term
structure (backwardation) is a warning to reduce size.

**2. Volatility-of-Volatility premium**
High VVIX (India equivalent: India-VIX of VIX, if available, or high vol-of-IV) predicts IV mean
reversion. At extremely high VVIX, sell vol. At extremely low VVIX, reduce short vol.
VVIX above 1-year 90th percentile: 27/31 trades profitable historically (Sinclair data).

### Confidence Level 2 edges

**3. Overnight / Weekend effect**
Options decay more than theta-predicted over non-trading periods. For short sellers:
time entries to capture maximum overnight/weekend decay.

**4. Event-based IV collapse (FOMC / RBI policy / earnings)**
IV collapses 1-3% after scheduled announcements. VIX/India-VIX drops ~3% on announcement days.
Sell straddles before RBI MPC releases; flat before announcement or use futures/VIX products if available.

*Indian market specific*: Shaikh & Padhi (2013) confirmed the effect in Indian markets.

**5. Post-Earnings Announcement Drift (PEAD)**
Stocks continue drifting in direction of surprise for 3-6 months. Not directly applicable to
index options, but informs our index trend-follower (Option-C) that post-budget/post-event
directional momentum is real and persistent in individual stocks.

### Data mining discipline (reapplied here)
- Start with phenomenon ("IV > realized on expiry day"), not rule ("25% stop, 09:20 entry").
- Never optimize parameters after seeing results — pre-register or use out-of-sample holdout.
- Crowded convergence trades (e.g., selling straddles) are stabilizing — crowding helps, not hurts,
  per Baltas (2019) for mean-reverting strategies. Unlike trend-following, which becomes unstable
  when crowded.
- February 2018 VIX spike lesson: ETN forced-liquidation feedback loops are what kills short vol.
  Our NIFTY 0DTE has no such feedback risk. We have no ETN structure, no forced rebalancing.

---

## Chapter 6 — Volatility Positions (Structure Comparison)

### Straddle vs Strangle (same vega exposure)
For equal vega, scaled to the same initial delta exposure:

| Metric | Short Straddle | Short Strangle |
|---|---|---|
| Win % (fairly priced, GBM) | 57% | 78% |
| Skewness | -1.83 | -4.8 |
| Excess kurtosis | 6.41 | 24.2 |
| Worst decile | -$2,274 | -$1,994 |
| Worst case | -$15,321 | -$27,683 |

**Key insight**: Strangle wins more often but has **much worse tail risk**. Straddle has lower
win rate but lower skewness and far less extreme downside. When badly wrong (realized vol 70%
vs implied 30%), straddle 10th pct = -$7,069 vs strangle -$10,085.

*Project implication for 0DTE*: We currently run the ATM short straddle. This is CORRECT
relative to a strangle — lower tail risk, better feedback on whether we have genuine vol edge
(straddle win rate maps more cleanly to whether IV > RV). A strangle on NIFTY 0DTE day would
create false confidence (high win % even without edge) and worse blowup risk.

### Iron Fly vs Naked Straddle — the key question

The book frames this precisely:
- Butterfly = straddle with long strangle as wings (capped downside, higher cost, symmetric P&L).
- Condor = strangle with wings (lower premium collected, worst case hits more often).

**Summary statistics fairly-priced butterfly** (long 70/130 wings, short 100 straddle):
- Win %: 46% vs 57% for naked straddle
- Standard deviation: $2,460 (slightly worse than straddle's $1,882)
- Minimum: capped at -$2,756 vs -$15,321 for naked
- Worst decile: -$2,756 (the maximum loss!)

**For a badly-priced butterfly** (realized vol 2.33x implied):
- Win %: only 20% (vs 25% for naked straddle)
- Worst decile = worst case = -$2,756 (30% of trades hit the max loss)

**Key Sinclair conclusion**: "Using a butterfly tames the extremely bad results of a straddle but
this comes at the expense of incurring the maximum possible loss 26-30% of the time."

*Project implication — iron fly on 0DTE*:
The iron fly eliminates the catastrophic tail (relevant for NIFTY gap opens, sudden circuit breaks).
The cost: (a) you pay bid-ask on 2 extra legs, (b) you hit max loss 26-30% of expiry days even when
vol is fairly priced. At Rs 600 F&O costs per full round-trip and India's F&O STT of 0.05% post-2026,
adding 2 wing legs means ~2x the transaction drag. For a strategy with ~40-60 trades/year, this is
meaningful.

**Quantitative framework for wing decision**:
Ask: Does the incremental insurance cost (wing premium + extra legs cost) exceed the expected saving
from capping tail losses?
- Our 25% stop fires 57% of days — this already acts as a behavioral stop, not a structural cap.
- If stop is correctly placed (stops us at the right time), wings add cost with little benefit.
- If stop is mis-placed (fires too early or too late), wings provide structural protection.
- Recommendation: test iron fly with 1-standard-deviation OTM wings at expiry on same historical
  data. Compare net premium collected per day vs naked straddle adjusted for stop hit cost.

### Strike choice (for strangles/condors — reference if we ever widen)
- The highest % skew premium is in the farthest OTM puts (highest implied vol), but selling these
  has catastrophic tail (Table 6.19: max loss -$1,463,400 per $1,000 vega sold).
- The maximum *dollar premium over ATM-priced value* peaks at ~10-delta put (260 strike in SPY example).
- Best practice: sell at the strike with maximum dollar premium over fair value, not maximum IV %.
- For NIFTY: if we ever expand to iron condor, short the put strike at maximum |IV_strike - IV_ATM| * price,
  not the furthest OTM strike.

### Expiration choice
Short-dated options have the highest VP. Premium is concentrated in the *last days before expiry*.
The evidence from Tosi & Ziegler (2017), van Binsbergen & Koijen (2015), Israelov & Tummala (2017) all
confirm: front-month > back-month VP. 0DTE is the apex of this.

"Vega wounds but gamma kills." Short-dated risk is gamma-dominated; long-dated risk is vega-dominated.
0DTE gamma sellers are compensated most because they take the most risk.

### Calendar spread (if ever using weekly 0DTE + next-week position)
Calendar spread = buy longer-dated + sell shorter-dated; net effect like a butterfly in PL.
Lower variance than naked short, but: (a) long vega — if IV drops, underperforms naked short;
(b) cost of two transaction legs. Not directly applicable to 0DTE single-expiry, but if we ever
run both Tuesday and Thursday weekly expirations, this concept applies.

---

## Chapter 7 & 8 — Directional Options (Brief — Option-C relevance)

### Core principle
Even directional option trades depend on the variance premium. If you pay too much for options,
a correct directional bet is worse than trading the underlying directly.

For the breadth-gated index trend rider (Option-C debit leg):
- At ATM call debit, you pay ATM IV. If ATM IV > realized, this drag compounds with theta.
- Sinclair's simulation: ATM long call (20% expected return, 30% IV = RV) earns $1,516 average
  vs $2,640 for 100 shares — the option "pays" for the stop-loss equivalent but foregoes expected
  drift income.
- Backtest prediction that debit option underperforms futures is confirmed here analytically.

### Short put spread — the defined-risk bullish position
For the debit call equivalent in Option-C, a short put spread is synthetically similar with:
- Higher win % (78%) vs long call (58%)
- Lower SD
- But you pay the skew premium on the long OTM put (that put has higher IV than ATM)
At current Indian skew levels (OTM puts typically 1.2-1.4x ATM IV), buying the "wing" put for
protection costs approximately 20-40% extra on the protection leg.

---

## Chapter 9 — Trade Sizing (Kelly + Skewness + Uncertainty)

### Kelly criterion basics
- Kelly maximizes long-term growth rate. It is mathematically optimal.
- Problems: (a) positions can be uncomfortably large; (b) high drawdowns; (c) compounding means
  equal wins/losses leaves you net negative.

### Non-normal outcomes (critical for option sellers)
Standard Kelly: f = mu / sigma^2

With negative skewness correction (Sinclair equation 9.25):
f_skew = (mu / sigma^2) * [1 - (skew / 6) * (mu / sigma^2)]

For negatively skewed strategies (ALL short option strategies), optimal f is **LOWER** than
standard Kelly. The more negative the skewness, the smaller the bet.

For a representative short vol strategy (mean=0.059, SD=1.137, skew=-6.2):
- Raw Kelly f = 0.046
- Standard deviation of f estimate = 0.031
- Probability that true Kelly < 0: 7%
- Probability we are currently over-betting (true f < half our estimate): 25%

**To get only 10% chance of over-betting: scale Kelly by 0.048 (Table 9.2) — effectively
trade at ~5% of raw Kelly.**

### Fractional Kelly + account subdivision
Sinclair's preferred method:
1. Split account into "safe" (untraded, cash) and "risky" (full Kelly on this sub-account).
2. Apply trailing % stop to the total account value.

Comparison table (μ=5%, σ=30%):
| Method | Mean | 90th pct | Max drawdown |
|---|---|---|---|
| Quarter Kelly | $101.70 | $111.10 | 26% |
| Sub-account (fixed max loss) | $102.20 | $120.90 | 38% |
| Sub-account (trailing %) | $117.13 | $153.40 | 30% |

Trailing % stop on total account wins on both average AND upside tail.

*Project implication*: At Rs 5L capital, 1-lot NIFTY 0DTE straddle per day:
- Margin requirement ~Rs 80-100K per straddle (1 lot).
- This is naturally ~20% of capital — close to the "sub-account at full Kelly" concept.
- The 2% daily breaker we already have approximates the trailing % stop at the daily level.
- We should NOT increase to 2 lots until the sub-account logic is formally modeled.

### Stops — the honest analysis
Sinclair is blunt: **stops reduce average return and change distribution shape; they do not
eliminate large losses in aggregate**.

A stop's effect:
- Eliminates the worst trades (good).
- Also eliminates trades that would have recovered (bad — often more of these than real big losers).
- Net: mean return drops (by ~0.4% in his simulation with a 15% stop on a 10% mean return strategy).

**When stops make sense for short options**:
"A position should be exited when we are wrong. Sometimes this coincides with losing money."
For 0DTE: if underlying moves sharply intraday, we ARE losing on delta + gamma simultaneously.
Exiting when the straddle is 25% in the money approximates "we are wrong about RV today."

The 25% stop firing 57% of days:
- If NIFTY moves >25% of straddle value intraday on >57% of expiry days, this is a signal that
  EITHER (a) the stop is too tight, OR (b) expiry-day realized vol is structurally high.
- Sinclair's framework: test stop placement empirically on your specific strategy. The game-theory
  argument (avoid obvious levels) is less relevant in electronic markets.
- Statistical placement: set stop where: E[loss if continued] > E[recovery gain]. For 0DTE
  with 15:10 close-out, the "recovery time" shrinks as the day progresses — a time-varying
  stop (tighter as day progresses) is theoretically superior to a fixed 25%.

---

## Chapter 10 — Meta Risks (Applicable Notes)

### Contract specification changes (India relevance)
SEBI has changed NSE F&O contracts multiple times (lot size, STT rules, expiry day cycle, weekly
expiry consolidation). The 2026 STT regime change (0.05% post-2026) is exactly this risk. Our
cost model must be re-parameterized whenever SEBI changes rules.

*Lesson*: Monitor SEBI circulars as a source of external parameter change. Never assume contract
specs are stable — the EuroSTOXX example (dividend yield changed 50% from index restructuring)
shows how non-market risk can cause a 1-day maximum loss.

### Leverage and forced exit risk
The XIV/VIX lesson: "The day you say you have to do something, you're screwed."
For us: as long as we trade 1 lot and maintain > Rs 4L buffer, we have no forced exit risk.
The 2% daily breaker at the account level is the structural protection.

---

## Summary: What This Book Changes in Our Methodology

### Confirmed correct
1. 0DTE ATM short straddle is the highest VP-capturing structure (short-dated, ATM, unhedged).
2. No delta hedging is correct for our cost structure.
3. ATM straddle > OTM strangle in terms of tail risk management.
4. 2% daily account breaker approximates the optimal Kelly sub-account stop.
5. The straddle stop-loss fires when we ARE wrong (underlying moved too far) — the philosophical
   basis of a stop is sound here.

### Should upgrade

**1. Regime-gate using VP quintile (India-VIX level)**
Before entry, check: India-VIX quintile -> expected VP. If India-VIX < 13 (lowest quintile),
mean VP ≈ 2.6 vol pts. With NIFTY straddle worth ~1-1.5% of index (0DTE ATM, say Rs 200-250 on
a Rs 25,000 index), 2.6 vol pts of VP needs to exceed transaction costs.
Effort: small (add India-VIX quintile lookup at signal time). Impact: high (regime conditioning).

**2. Term structure contango filter**
Check: NIFTY 0DTE IV vs NIFTY 1-week IV. If front < back (contango): proceed.
If inverted (backwardation/elevated front): reduce size or skip.
Effort: small (add IV term structure ratio as go/no-go filter). Impact: medium.

**3. Time-varying stop (tighten as expiry approaches)**
Fixed 25% stop through the day is suboptimal. As 15:10 approaches, the gamma profile
accelerates. A stop that starts at 30% and tightens to 20% by 14:00 is theoretically better.
Effort: medium (requires intraday stop parameterization). Impact: medium.

**4. Kelly sizing with skewness correction**
Our current 1-lot fixed size is conservative and correct at Rs 5L. When capital scales:
apply f = mu/sigma^2 * (1 - skew_correction). For negatively skewed short-vol returns,
this will keep us well below full Kelly.
Effort: small (formula application to backtest stats). Impact: high when scaling.

**5. VP ensemble forecast for sizing**
Use average of EWMA(0.94) and ATM IV - VP_quintile as realized vol forecast. This better-calibrated
forecast improves both the go/no-go decision and the fractional Kelly denominator.
Effort: small. Impact: medium.

**6. Iron fly A/B test (paper)**
Run iron fly (1 lot short straddle + buy 1-SD OTM call + 1-SD OTM put) in paper alongside naked
straddle for one full quarterly cycle. Compare net premium after transaction costs.
From Sinclair's framework: iron fly max loss hits 26-30% of days for a fairly-priced position.
At Rs 600 per extra 2 legs, the cost is real. Only adopt iron fly if:
  (a) tail losses in naked straddle backtest exceed iron fly premium drag, OR
  (b) a Kobe-earthquake-type gap open makes us psychologically unable to hold overnight risk.
Effort: medium. Impact: depends on empirical result.

### Contradictions and cautions

1. **Stop fires 57% of days — Sinclair says stops reduce average return**. If our 25% stop is the
   main "protective feature" but also fires most days, we may be trading a "wins when market is
   calm, stops when market moves" pattern — essentially the wrong side of the convex payoff.
   Counter: for 0DTE, every stop-out IS a day when RV > IV. That IS the loss case. The stop
   prevents the path going further negative, which is correct for an unhedged short-gamma position
   that has no time to recover.

2. **VP most pronounced in OTM puts — we sell ATM**. We are not capturing the maximum skew premium
   by selling ATM. ATM is the right choice for *risk management* (straddle tail behavior) not
   maximum premium capture. If premium capture is the goal, we'd sell a put-heavy strangle.
   Sinclair resolves this: "best to sell the option with highest dollar premium over fair value,
   not highest IV." For NIFTY, calculate ATM price vs fair value at each expiry to confirm ATM
   is still the best risk-adjusted choice.

3. **Transaction costs at 1-lot scale vs institutional**. Sinclair's framework applies to positions
   sized in $10,000 notional lots. Our Rs 600 F&O cost on a Rs 200 premium (30%!) is punishing.
   The book's stop analysis, strike optimization, and calendar spread analysis all assume much
   lower proportional transaction costs. Every recommendation involving "add legs" or "roll"
   must be stress-tested against Indian F&O friction.

4. **Vol forecasting section does not address 0DTE-specific dynamics**. Sinclair's GARCH/EWMA
   discussion is about 30-day vol. 0DTE realized vol is dominated by intraday microstructure
   (opening auction, news at 10:30, European open correlation, RBI/SEBI announcements).
   Standard EWMA may be a poor predictor of *same-day* vol. The intraday VWAP-vol proxy or
   ATR-based daily range estimator may outperform.

5. **India-VIX vs actual NIFTY option IV divergence**. India-VIX is model-free 30-day implied
   vol. Our 0DTE ATM straddle premium reflects 1-day IV, which is structurally different
   (gamma-dominated, not vega-dominated). Term structure relationship between India-VIX and
   0DTE IV is not directly analogous to S&P VIX-to-spot-option relationship. Must empirically
   calibrate the VP relationship for NIFTY 0DTE specifically.

---

## Quick-Reference: Sinclair's Key Numbers Applicable to Our Strategy

| Metric | S&P 500 data | NIFTY implication |
|---|---|---|
| Variance premium, mean | 4.08 vol pts | Estimate India ~3-5 pts (calibrate) |
| VP positive % of time | 85% | Structural base rate for short vol |
| 0DTE premium concentration | Last days > back-month | 0DTE is VP apex |
| Short straddle win % (fairly priced) | 57% | Target benchmark for our strategy |
| Short straddle skewness | -1.83 | Implies Kelly < standard Kelly |
| Iron fly max loss frequency (fairly priced) | 26-30% of trades | Cost of wings is high |
| Overnight VP as % of total daily VP | ~130% (total overnight > total intraday) | Expiry-day dynamics differ |
| EWMA vol forecast R^2 | 0.60-0.65 | Reasonable; ensemble marginally better |
| Half-Kelly still has 25% chance of over-betting | — | Trade at 1/4 Kelly or less for sparse-stat strategy |

---

*File written by pass-2 book distillation agent, 2026-06-13.*
*Source: /tmp/books/sinclair_pos.txt (Positional Option Trading, Euan Sinclair).*
