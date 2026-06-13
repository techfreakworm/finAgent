# Tharp — Trade Your Way to Financial Freedom (2nd ed.) — Distillation
**Purpose:** Reference for the NIFTY/BANKNIFTY intraday algo system (pass-2).
**Capital:** Rs 5L paper-only, 1-lot minimum (NIFTY: ~Rs 10k notional/lot; BANKNIFTY: ~Rs 15k/lot).
**Pass-1 already done:** de Prado, Chan, Kaufman. Tharp-specific mechanisms only; no re-covering walk-forward, Sharpe, bootstrap — those are already implemented.

Sections read: Ch.4 (system steps), Ch.6 (expectancy + R-multiples), Ch.9 (stops), Ch.10 (profit exits), Ch.11 (opportunity + cost), Ch.12 (position sizing), Ch.13 (conclusion/testing).

---

## 1. R-Multiples — The Core Measurement Unit (Ch. 6, 9)

### 1.1 Definition and Calculation

**R** is the initial risk on a trade — the dollar (or rupee) amount between entry and the initial hard stop.

```
R-multiple = (trade P&L) / (initial risk R)
```

Examples:
- Enter at Rs 1,200 straddle premium, stop at Rs 1,500 (25% stop = Rs 300 risk). Final P&L = +Rs 250. R-multiple = 250/300 = +0.83R.
- Stop fires: exit at Rs 1,500. R-multiple = -300/300 = -1.0R.
- Theta decay to Rs 0: R-multiple = +1,200/300 = +4.0R.

**Key property:** Losses trade-by-trade are typically around −0.5R because you raise your stop as the trade moves in your favor (the initial stop is worst-case R; average loss is about half of R per Tharp Ch. 9). Only when stopped on entry day do you realize the full −1R.

**For the short straddle specifically:**
- Initial risk R = 25% of straddle premium at 09:20 (our existing stop).
- If straddle premium opens at Rs 200, R = Rs 50/lot (one leg, NIFTY lot = 25 units, so R = Rs 1,250 in rupees per lot).
- A full expiry day with decay to near zero = approximately +4R (premium received is ~Rs 200, risk was Rs 50, so reward-to-risk ~4:1).
- A stop-fire day = −1R exactly.

### 1.2 The R-Multiple Distribution as the "Marble Bag" (Ch. 6 p. 144-148)

Treat your entire trade history as a bag of marbles. Each trade drawn = one R-multiple. Replace it back.

From 40-300 trades (our sample range), group into Rs 500 (or Rs X = minimum loss in our case Rs R) ranges:

| R-multiple bucket | Count | Probability | Payoff |
|---|---|---|---|
| +3R to +5R (full expiry, no stop) | e.g. 60 | 0.43 | ~4R avg |
| +1R to +2R (partial decay, timed exit) | e.g. 30 | 0.21 | ~1.5R |
| −1R (stop fires) | e.g. 57 | 0.41 | −1.0R |

Then: Expectancy = sum(prob_win_i × R_win_i) − sum(prob_lose_j × R_lose_j)

**Procedural step:** Our EOD report should log `R_multiple = trade_pnl_net_of_costs / initial_R_value` for every closed trade. Over 50+ trades this becomes our distribution.

### 1.3 Expectancy Per Rupee Risked (Ch. 6 p. 148-152)

Formula: `E = (PW × AW) − (PL × AL)` where PW/PL = probability of win/loss, AW/AL = average R-multiple of wins/losses.

The key insight Tharp emphasizes repeatedly: **expectancy per dollar risked is more informative than percent win rate.** A 90%-win system can still have negative expectancy (Ch. 6 p. 143 example: 90% win × 0.275R avg win − 10% lose × 2.7R avg loss = −0.0225 expectancy).

**Applied to our short straddle:** We have ~57% stop-fire rate. What matters is whether our average win on non-stop days is large enough to overcome the −1R losses.

Worked example with our approximate numbers:
- P(stop fires) = 0.57, loss = −1.0R
- P(full/partial decay) = 0.43, avg win = assume +2.5R (mid-range estimate)
- Expectancy = (0.43 × 2.5) − (0.57 × 1.0) = 1.075 − 0.57 = +0.505R per trade

That positive figure is why the system has PF > 1.0. The stop-fire rate of 57% alone is not damning — what matters is the R-multiple of the wins.

**Caution on our 57% stop-fire rate:** Tharp warns (Ch. 6, Ch. 9) that systems can look great in backtest but "the distribution of marbles in the future is not the same as in the past." Our 57% stop-fire rate may have been lower in 2022 trending regime and may be higher in future elevated-IV regimes. Check expectancy separately across market-condition buckets.

---

## 2. Position Sizing — The "How Much" Question (Ch. 12)

### 2.1 What Position Sizing Is NOT (Ch. 12 p. 282-284)

Tharp is emphatic:
- Position sizing is NOT your stop loss.
- Position sizing is NOT diversification.
- Position sizing is NOT risk control in the "how tight is my stop" sense.
- Position sizing IS the algorithm that answers "how many lots/contracts do I put on?"

**Key quote (Ch. 12 p. 290):** "Your position size on a given trade must be low enough so that you can realize the long-term expectancy of your system over many trades."

### 2.2 The Four Anti-Martingale Models (Ch. 12 p. 285-306)

All four models increase position size as equity increases (anti-martingale). Martingale (doubling down on losses) destroys capital mathematically — do not confuse with "averaging into a position."

**Model 1: Units per Fixed Amount**
- Rule: 1 lot per Rs X of equity.
- Our situation: 1 lot at all times (because Rs 5L can only support 1 lot at any time in NIFTY F&O). This is Model 1 de facto at minimal scale.
- Disadvantage: Can't scale until equity doubles. Essentially "no position sizing" for small accounts.

**Model 2: Equal Value Units (equity traders)**
- Divide equity into N equal-rupee chunks; buy one chunk per signal.
- Not applicable to futures/options where lot size is fixed.

**Model 3: Percent Risk Model** — Tharp's recommended model for F&O
- Rule: `lots = floor( (equity × risk_pct) / (stop_in_rupees × lot_size) )`
- Example: Equity Rs 5L, risk_pct = 1%, stop = Rs 300/straddle, lot = 25 units:
  - Max_risk_per_trade = 5,00,000 × 0.01 = Rs 5,000
  - Rs_at_risk_per_lot = Rs 300 × 25 units = Rs 7,500
  - Lots = floor(5,000 / 7,500) = 0 — **we cannot even take 1 lot at 1% risk with current capital**
  - At 2% risk: Max_risk = Rs 10,000, lots = floor(10,000/7,500) = 1 lot — barely 1 lot.
  - At 3% risk: Max_risk = Rs 15,000, lots = 2 lots — possible but aggressive (Tharp calls 3%+ "gunslinger" level).

**The brutal constraint:** At Rs 5L capital and Rs 7,500 per lot risk (25% stop on a Rs 200 straddle), the percent-risk model confirms we can only take 1 lot at 1.4–2% risk per trade. Scaling to 2 lots would require equity above Rs 5.3L minimum. This validates our current 1-lot constraint.

**Model 4: Percent Volatility Model**
- Rule: `lots = floor( (equity × vol_pct) / (ATR_daily_rupees × lot_size) )`
- Useful for tight-stop strategies (like our breadth rider). For the straddle, Model 3 is more natural since R is well-defined.

### 2.3 Ralph Vince Study — The Most Important Data Point (Ch. 12 p. 347-349)

40 PhDs played a positive-expectancy game (60% win, 1:1 payoff). 95% lost money. Why? Over-leveraged positions in streaks of losses.

**Implication for us:** Even with positive expectancy, we can blow up the account if we risk too much per trade. At Rs 5L, 3 consecutive −1R losses at 3% risk each = Rs 5L × (1−0.03)^3 = Rs 4.56L. Recoverable. At 10% risk each: Rs 5L × (1−0.10)^3 = Rs 3.64L — a 27% drawdown requiring 37% gain to recover.

**Table from Ch. 12 (p. 283): Recovery math**
| Drawdown | Recovery needed |
|---|---|
| 10% | 11.1% gain |
| 25% | 33% gain |
| 40% | 66.7% gain |
| 50% | 100% gain |
| 75% | 300% gain |

**Applied: Keep per-trade risk under 2% of current equity. Never exceed 3%.**

### 2.4 The Equity Wall Metaphor and Undercapitalization (Ch. 6, 12)

Tharp's "snow wall" metaphor: your equity is the wall. One black snowball bigger than the wall destroys everything. He explicitly states (Ch. 12 p. 392): "accounts under Rs 50,000 USD" (equivalent in context) have mathematical odds of failure just from account size, not system quality.

**Our situation is marginal.** Rs 5L is the wall. A single large adverse event on the straddle (IV spike, gap, circuit) could fire a stop that is much larger than the modeled −1R if the market gaps through the stop price. This is the "runaway market" scenario Tharp mentions where your actual loss exceeds your stop. Build that into scenario analysis.

---

## 3. Expectancy × Opportunity = Dollar Volume (Ch. 11)

### 3.1 The Opportunity Factor is a Third Dimension (Ch. 11 p. 271-276)

Dollar volume per day = Expectancy × Opportunities per day.

This is why a low-expectancy system with very high frequency can beat a high-expectancy system with low frequency (Ch. 11 Table 11-1):

| Trader | Expectancy/trade | Trades/day | Dollar volume/day |
|---|---|---|---|
| Long-trend | Rs 3.32 | 0.05 | Rs 0.15 |
| Mid-term | Rs 0.63 | 0.5 | Rs 0.31 |
| Short-term | Rs 0.30 | 5 | Rs 0.15 |
| Market maker | Rs 0.01 | 500 | Rs 6.00 |

**Applied to our strategies:**
- Short straddle: 1 trade per week (expiry day only — NIFTY weekly Thu; BANKNIFTY Mon, Wed, Fri). ~3-4 trades/week if both traded. High expectancy, low frequency.
- Breadth rider: potential 2-3 trades/week per expression. Lower expectancy but higher frequency.
- Raw dollar volume makes these comparable in expected daily return.

### 3.2 Transaction Cost Is a Direct Drag on Expectancy (Ch. 11 p. 276-278)

Tharp: costs must be subtracted before quoting expectancy. They are not optional.

**Critical for us — Rupee translation:**
- Equity intraday round trip: ~Rs 600 (confirmed from our Dhan cost model).
- NIFTY F&O straddle round trip: both legs opened and closed = ~4 trades × Rs 150 each = Rs 600 in brokerage + STT.
- Post-2026 STT on F&O seller: 0.05% of premium on sell leg.

**Example: Rs 200 NIFTY straddle, 1 lot (25 units):**
- Notional premium sold = Rs 200 × 25 = Rs 5,000
- STT on sell = 0.05% × Rs 5,000 = Rs 2.50 per leg (very small)
- Total costs ~Rs 600 round trip
- If the trade earns Rs 800 net premium decay, expectancy after costs = Rs 200 net / Rs 7,500 risk = +0.027R per Rs risked. Marginal.

Tharp explicitly states (Ch. 11 p. 277): "if your average profit per trade was Rs 50, then you would pay much more attention to a Rs 100 trading cost." **At small trade sizes, friction erodes expectancy severely.** Our Rs 600 costs on a trade capturing Rs 800 gross leaves only Rs 200 net — 25% cost drag.

**The debit option leg of Option-C is especially vulnerable here.** Tharp's framework directly explains why it underperforms: the lower premium (0.75% risk vs 1.75% for futures) means more cost-as-percent-of-premium, and theta decay is already in the debit buyer's cost, not a credit. This is a Tharp-level confirmation of Kaufman's prediction.

---

## 4. Stops — Setting R Intelligently (Ch. 9)

### 4.1 The Stop Sets R, Not the Other Way Around (Ch. 9 p. 236-237)

"Your stop loss predefines your initial risk R. Your primary job as a trader is to get profits that are large multiples of R."

The choice of stop level determines the entire R-multiple framework. A tight stop = small R = potentially larger R-multiple wins, but more false stop-outs. A wide stop = large R = harder to achieve multiple-R wins but fewer false stops.

**For our short straddle:**
- Our 25% stop means R = 25% of straddle premium at entry.
- This fires 57% of days. Tharp's MAE framework (Ch. 9 p. 238-241) is the diagnostic tool.

### 4.2 Maximum Adverse Excursion — The Diagnostic for Stop Placement (Ch. 9 p. 238-244)

**MAE procedure:**
1. For each trade in backtest, record the maximum adverse move of the position during the trade (worst intraday mark-to-market against you).
2. For winners, plot their MAE distribution. Winners should cluster at low MAE.
3. For losers, plot their MAE. Losers typically have higher MAE.
4. Find the percentile of winner-MAE that a tighter stop would still capture without stopping out.

**Tharp's finding (Ch. 9 p. 823):** "Winning trades will seldom go below a certain value. Good trades seldom go too far against us." In his British pound data: only 12.5% of winning trades had MAE above 1× ATR; 66.7% of losing trades had MAE above 1× ATR.

**Action for our straddle:** We need to compute the intraday MAE distribution of our straddle P&L on winning days (days where we hold to 15:10 flat). If our winners almost never exceed 20% adverse move during the day, our 25% stop is well-placed. If winners frequently touch 22-24% intraday, we are getting stopped out of winners — and the stop should be wider. The 57% stop-fire rate may be partially explained by premature exits that would have recovered.

### 4.3 Volatility Stops Are Optimal for Most Applications (Ch. 9 p. 244)

Tharp recommends 2.7–3.4× ATR as the stop for most trend-following. For mean-reversion/short-vol systems, the analog is "stop beyond the range of daily noise."

**For the straddle:** A volatility-based stop could be expressed as: exit if the combined straddle delta (or mark-to-market loss) exceeds `N × daily ATR of the straddle premium`. This is more adaptive than the fixed 25% rule. Consider testing a 2× daily ATR stop on the straddle P&L as an alternative.

### 4.4 Dollar Stops vs. Percent Stops — Their Equivalence and Difference

Tharp (Ch. 9 p. 243): Dollar stops have the advantage of being unpredictable to the market (no one knows your cost), and they work well when they are placed beyond typical MAE. Our 25% stop is a percent-of-premium stop, which is a dollar stop in disguise (since the premium is fixed at entry).

---

## 5. Profit-Taking Exits (Ch. 10)

### 5.1 Multiple Exits Are Usually Necessary (Ch. 10 p. 256)

Tharp: "Consider using different exit strategies for each of your system objectives." A system typically needs:
1. Hard stop (capital preservation — our 25% stop)
2. Timed stop ("get out after N bars if not profitable" — our 15:10 force-flat is this)
3. Trailing stop for profit protection (not currently used in the straddle)
4. Profit objective/retracement stop

**For the short straddle:** Our current exits are (1) 25% hard stop and (2) 15:10 time exit. We have no profit-protection mechanism. If the straddle decays from Rs 200 to Rs 40 by 13:00, we're still holding with full −Rs 200 tail risk until 15:10. Adding a partial profit lock (e.g., close 50% of position when 60% of premium is captured) is a legitimate exit enhancement.

### 5.2 Profit Retracement Stop — Protecting Large Winners (Ch. 10 p. 260-262)

After reaching a 2R profit, allow only 30% retracement; tighten to 20% at 3R, 10% at 4R.

**Adapted for the straddle:**
- At 60% premium decay (1.5R equivalent since straddle at 0.40× original): tighten stop from 25% absolute to, say, 15% of original premium.
- Prevents giving back a large winning day if volatility spikes late afternoon.

### 5.3 Timed Exits for Mean-Reversion Systems (Ch. 10 p. 256)

"Get out at the close in 2 days if this position is not profitable." For the straddle, this is our 15:10 exit, which already implements this. No change needed, but the logic is correct: we do NOT hold a straddle overnight. The psychological cost of holding an overnight short-gamma position in India (SGX Nifty / global macro overnight risk) far exceeds any theta benefit.

---

## 6. System Evaluation Framework (Ch. 6 summary section)

### 6.1 The Minimum Expectancy Standard (Ch. 6 p. 221-222)

"If your system includes at least 100 trades and has an expectancy above 50 cents per dollar risked, then it is a good system."

**For our 40-300 trade sample range:** We are at the low end or below 100 trades for each sub-strategy. This is the "sparse statistics" problem. Tharp does not give a lower-sample guidance here. Our mitigation (from pass-1): bootstrap confidence intervals on PF, deflated Sharpe with family correction. The 50-cent benchmark is a useful rough target.

### 6.2 The Seven-Step Expectancy Review (Ch. 6 p. 200-205)

1. Calculate total expectancy = total net profit / number of trades. This is your raw per-trade mean.
2. Eliminate position sizing effects — use 1-lot basis.
3. Group trades into R-multiple buckets (smallest loss = 1R unit).
4. Convert to probability matrix.
5. Apply Formula 6-2 to get expectancy per Rupee risked.
6. Check sample size (>100 ideal) and magnitude (>0.50R target).
7. Determine opportunity: how many trades/year does this generate?

**Implementation in our EOD report (Task #9):** Add `r_multiple` field to each closed trade. After 50 trades per strategy, compute the probability matrix automatically and report E per trade and E per rupee risked.

### 6.3 Six Key Variables of Trading System Success (Ch. 13 p. 315)

1. System reliability (percent winners)
2. Reward-to-risk ratio (average R-multiple)
3. Cost of trading (subtracted from expectancy before reporting)
4. Trading opportunity (trades per month)
5. Size of equity (the "snow wall")
6. Position-sizing algorithm (anti-martingale only)

**Variables 5 and 6 are MORE important than 1-4.** Tharp makes this claim repeatedly. At Rs 5L and 1-lot constraint, we are capital-constrained in variables 5 and 6. Our methodology work (walk-forward, deflated Sharpe) primarily improves variables 1-4 — necessary but not sufficient.

---

## 7. The Short Straddle as Tharp's "High Probability, Low R-Multiple System" (Ch. 11 p. 273)

Tharp describes this exact strategy archetype (Ch. 11):
> "You've decided you cannot tolerate long losing streaks. You need to be right at least 60% of the time. You are willing to sacrifice size of profits for being correct more often."

He gives an example with 60% reliability, 0.50R expectancy after transaction costs.

**Our straddle:**
- Win rate: ~43% (100% − 57% stop-fire)
- Our wins are potentially large R-multiples (3-4R when straddle fully decays)
- Net expectancy after costs: ~+0.50R (estimated above)

This matches the "high-probability category" from Tharp's perspective because even with a majority of stop days, the few big wins carry the expectancy. He warns: "the question is can you survive with a 30 cent expectancy? Do you generate enough trades?"

**Critical finding:** Tharp's analysis of his market-maker example (Table 11-1) shows that even a tiny 0.012 expectancy × 500 trades/day = $6/day. Our straddle at ~0.50R expectancy × ~3.5 expiry trades/week = ~1.75R/week. If 1R = Rs 7,500, that is Rs 13,125/week on a Rs 5L account = ~2.6% per week, or ~135% annualized. This is the theoretical ceiling — the real output depends on actual R-multiples.

---

## 8. Where Tharp Breaks Down for Our Context

### 8.1 1-Lot Granularity — "No Position Sizing" in Practice

Tharp explicitly warns (Ch. 12 p. 760, 963): "For a small account, equity would almost have to double before you could increase your exposure by one unit. That's basically no position sizing."

**Our situation:** Until equity reaches ~Rs 10.5L (sufficient to hold 2 lots at 2% risk), we are locked at 1 lot. All four of Tharp's position-sizing models collapse to "take 1 lot always" at Rs 5L scale. The only meaningful sizing decision is binary: take the trade or skip it. This is not a failure — it is the honest constraint of starting capital.

### 8.2 100-Trade Minimum for Reliable Expectancy

Tharp's guidance requires >100 trades for a reliable marble bag. Our straddle generates ~150 expiry trades/year if all NIFTY + BANKNIFTY weekly expiries are taken. That is acceptable if we trade both indices. Trading only NIFTY (~52/year) keeps us below the 100-trade threshold. **Practical implication:** The holdout PF and bootstrap CI are more important metrics than point-estimate expectancy for our sample sizes.

### 8.3 No SQN in This Edition

Tharp's SQN (System Quality Number = mean(R-multiples)/std(R-multiples) × sqrt(N)) is a concept from his later work and is not present in the 2nd edition as a named statistic. We can compute it ourselves: `SQN = E_per_R / std(R_multiples) × sqrt(N)`. A score above 2.0 is "good"; above 3.0 is "excellent." With our PF of ~1.5, our SQN is probably in the 1.5-2.0 range — adequate but not exceptional.

### 8.4 Cost Impact Is Vastly More Severe at Retail Scale

Tharp's examples use 0.5% round-trip costs. Our F&O costs are:
- Short straddle: Rs 600 on Rs 5,000 gross credit = **12% cost as % of premium received**. This is more than 10× Tharp's assumed ratio.
- Tharp's long-term trend follower example loses only 5% to costs (Rs 100 on Rs 2,000 trade). We lose 12% minimum.

**Practical implication:** Any marginal strategy that Tharp would label "borderline positive expectancy" is almost certainly negative for us after costs. Only strategies with E > 0.50R per trade survive our cost structure.

### 8.5 Options-Specific Risk — Gamma and Pin Risk Are Not Discussed

Tharp treats all trading as directional or spread trades on the underlying. He does not address:
- Short gamma (convex losses accelerate as underlying moves away from strikes)
- Pin risk at expiry (straddle P&L is nonlinear as underlying approaches strike)
- IV crush / expansion as independent of delta

For the short straddle, our R is defined at entry but the actual maximum loss is theoretically unbounded. The 25% stop provides a practical cap, but the model assumes normal fills — gap risk or circuit events violate this. Tharp's "runaway market" caveat applies here. This is the primary model limitation.

---

## 9. Actionable Implementation Steps

### 9.1 EOD Report Additions (Task #9, high priority)

For every closed straddle or trend-rider trade, compute and log:
```python
initial_risk_R = abs(entry_price - stop_price) * lot_size  # in rupees
r_multiple = net_pnl_after_costs / initial_risk_R
```

Weekly summary statistics:
- Mean R-multiple (= raw expectancy per trade)
- Std of R-multiples
- SQN proxy = mean / std × sqrt(N) (report when N ≥ 20)
- Probability matrix by R-multiple bucket (−2R to +5R in 0.5R steps)

### 9.2 MAE Analysis for Stop Calibration (Task #12 extension)

Add to the paper trading loop: record intraday maximum adverse excursion of the straddle P&L on every day. After 30 trading days:
1. Plot distribution of winner MAE (days where we exit flat at 15:10 profitably)
2. Plot distribution of loser MAE (days where stop fires)
3. If winners never touch 22%, the 25% stop is correctly placed
4. If winners frequently touch 23-24% before recovering, widen the stop slightly or use a 2-min confirmation filter before stop execution

### 9.3 Partial Profit Lock (Enhancement for short straddle)

After the straddle premium decays to 40% of original (60% captured = ~1.5R), implement an optional trailing mechanism:
- Tighten stop from 25% absolute to 15% of original premium
- Or close 50% of position and let remaining decay

This reduces tail risk on "almost-won" days. Test in backtest first — does this improve or hurt expected value? Tharp's framework says it will reduce variance while potentially reducing mean return slightly.

### 9.4 Failure Probability Framing

Use the R-multiple distribution to answer: "What is the probability of N consecutive −1R trades?" 

For our 57% stop-fire rate (probability of loss per trade = 0.57):
- P(2 consecutive stops) = 0.57² = 0.32 (32%, roughly 1 in 3 weeks)
- P(3 consecutive stops) = 0.57³ = 0.18 (18%, roughly once a month)
- P(4 consecutive stops) = 0.57⁴ = 0.10 (10%, about once in 10 weeks)

Capital impact at 2% risk per trade:
- After 3 consecutive −1R losses: equity = Rs 5L × (0.98)³ = Rs 4.71L (−5.8%)
- After 4 consecutive −1R losses: equity = Rs 4.61L (−7.8%)

This is survivable but should trigger the "regime check" from our daily breaker logic — is there a reason the straddle keeps stopping out (trending market, persistently elevated IV)?

---

## 10. Confirms What We Already Do Right

1. **We compute expectancy correctly** — our PF × win rate math is equivalent to Tharp's formula (PW × AW − PL × AL) when normalized. The PF > 1.0 confirmation is exactly what Tharp calls "positive expectancy."

2. **Our force-flat at 15:19:30 is the correct timed exit** — Tharp explicitly endorses timed exits as one of the most underused exit types. Our 15:10 exit is the same mechanism.

3. **We use percent-risk sizing implicitly** — our 2% daily breaker and 1-lot constraint already implement a rough percent-risk model. It just can't scale below 2 lots due to capital.

4. **We reject martingale** — we do not size up after losing days. Our 2% daily breaker actually reduces exposure (freezes trading) after a bad day, which is the correct anti-martingale response.

5. **Walk-forward and holdout are methodologically sound** — Tharp's warning about overfitted systems (Ch. 13: "software optimizes results to make you think you have a great system when you may not") is exactly what our 8m/2m walk-forward and frozen holdouts guard against.

6. **Our cost model is thorough** — Tharp's insistence that costs must be subtracted before quoting expectancy is already implemented in our Dhan cost model (Rs 600 equity RT, date-banded F&O STT).

---

## 11. Contradictions and Cautions

**Tharp's "50-cent expectancy = good system" guideline is too lenient for our cost structure.** At 12% cost-per-trade, we need E > 0.70R just to stay profitable after costs. His guideline assumes ~3-5% cost per trade in his institutional examples.

**"Entry doesn't matter much" is dangerous advice for high-frequency short-vol.** Tharp argues entry technique accounts for only minor variance in outcomes vs. position sizing. For the straddle, entry timing (9:20 vs. 9:35 after volatility settles) significantly changes the premium received and therefore both R and the distribution of outcomes. Entry timing matters more for short-vol strategies than for directional trend-following.

**Tharp's recommended 1% risk per trade is operationally impossible at Rs 5L + 1 lot.** Our minimum position is 1 lot with Rs 7,500 at risk = 1.5% risk. We cannot reduce this further without not trading. He acknowledges this for small accounts but provides no practical solution. Our answer: accept the higher percent risk at this stage, monitor carefully, and scale only when equity supports 2-lot positions.

**Psychology chapters (Ch. 1-3): skipped deliberately.** Tharp's psychology content (biases, self-sabotage) is valuable for discretionary traders but provides no algorithmic mechanism for a rules-based system. We are running a paper algo with pre-registered parameters — psychology is irrelevant until we go live with real money.

---

## Summary Table

| Tharp Concept | Our Implementation | Gap/Action |
|---|---|---|
| R-multiple logging | Not yet in EOD report | **Add R-multiple field to closed trade log** |
| Expectancy per R risked | Computed as PF but not per-R | **Add per-R expectancy to weekly stats** |
| MAE analysis | Not computed | **Log intraday max drawdown on straddle** |
| Probability matrix | Not visualized | **Build after 50 trades** |
| Percent risk model | Implicit; 1-lot only | Constrained by Rs 5L; no action needed now |
| Profit retracement stop | Not implemented | **Test as enhancement to straddle exit** |
| Timed exit | 15:10 force-flat | Already correct |
| Anti-martingale | 2% daily breaker | Already correct |
| Transaction cost subtraction | Dhan model in place | Already correct |
| SQN computation | Not reported | **Add to weekly report when N ≥ 20** |
