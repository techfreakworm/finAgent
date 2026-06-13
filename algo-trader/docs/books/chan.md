# Chan (2013) — Algorithmic Trading: Winning Strategies and Their Rationale
## Distillation for NIFTY/BANKNIFTY Intraday Algo System

**Source:** /tmp/books/chan.txt (plain-text extraction)
**Chapters read:** All 8 chapters + Preface + Conclusion
**Distilled:** 2026-06-13
**Capital context:** Rs5L, Indian F&O markets, paper-only, event-driven 1m/5m backtest engine

---

## 1. Core Framework: Mean Reversion vs Momentum

### 1.1 The Regime Map (Ch. 6 pp. 151-153, Ch. 8 pp. 183-184)

Chan's central empirical claim, backed by out-of-sample evidence:

| Regime | Best strategy type | Why |
|---|---|---|
| Normal (steady vol, no crisis) | Mean reversion | High consistent volatility with no directional shock |
| Post-crisis grind (low vol, recovering) | Mean reversion continues to dominate | Momentum crashes hard post-crisis; MR thrives |
| Genuine trend / breakout | Intraday momentum (breakout) | Stop cascade, forced institutional flows |
| Crisis / shock | Momentum long-term; intraday MR may still work | Tail events feed momentum; HF reversion works on dislocations |

**Key finding (Ch. 6, pp. 151-152):** After the 2008 financial crisis, interday cross-sectional stock momentum suffered -30% APR. After the 1929 crash, representative momentum strategies did not return to prior high-watermark for **30+ years**. Mean reversion replaced momentum as the dominant stock regime throughout that period.

**Key finding (Ch. 1, pp. 22-24):** Post-2008, lower average volatility but higher-frequency volatility spikes caused "a general decrease in profits for mean-reverting strategies, which thrive on a high but constant level of volatility." This is the "grind" problem.

### 1.2 Mapping to Our System

**Our breadth-gated index trend rider** is an intraday momentum strategy. Chan's framework says:
- Intraday momentum does NOT suffer the interday "momentum crash" problem (Ch. 7, p. 155).
- The mechanism (stop cascade, news diffusion, forced institutional flows) still operates intraday.
- However, the edge concentrating on low-ATR signal days (our finding) is consistent with Chan's observation that momentum duration shortens as awareness grows — the signal must be selectiver.

**Our 0DTE straddle** is a mean-reversion strategy (selling elevated premium, expecting decay). Chan explicitly warns (Ch. 2, pp. 60-61): "the rare loss is often very painful and sometimes catastrophic" because stop losses logically contradict mean reversion. Our 25% straddle stop is positioned per his guidance: set above backtest intraday max-drawdown so it never triggers in normal operation but guards against regime shift / black swan.

---

## 2. Statistical Tests for Mean Reversion (Ch. 2, pp. 39-62)

### 2.1 ADF Test
Equation 2.1: Regress `Δy(t)` on `y(t-1)`. If coefficient λ is significantly negative, series is mean-reverting. Critical value at 90%: λ/SE(λ) < -2.594 (for our sample sizes this is the practical bar).

**Practical note (p. 44):** "Sampling the data at intraday frequency will not increase the statistical significance of the ADF test." — Use daily closes for stationarity tests even if trading intraday.

### 2.2 Hurst Exponent
- H < 0.5 → mean-reverting
- H = 0.5 → random walk
- H > 0.5 → trending

**For our index:** Run Hurst on NIFTY daily closes. If H > 0.5 consistently, momentum is justified. If H drops below 0.5 in grind periods, our breadth filter is doing correct work by gatekeeping entries.

### 2.3 Half-Life — The Practical Tool (pp. 46-48)
`half_life = -log(2) / lambda` where lambda from OLS regression of `Δy` on `y_lag`.

This is the most actionable number. It tells you:
1. Natural lookback for moving average in the strategy.
2. Expected holding period per trade.
3. Whether the series is tradeable at your time horizon.

**Application to us:** For the 0DTE straddle, the mean-reversion time scale is intraday (minutes to hours). Running the half-life calculation on intraday premium time series would tell us optimal adjustment time. For the breadth rider, half-life on the index itself is irrelevant — we are momentum trading, not mean-reverting.

---

## 3. Implementing Mean-Reverting Strategies (Ch. 3, pp. 63-86)

### 3.1 Bollinger Bands vs Linear Scaling
Chan shows the linear strategy (position size = -Z-score of price) is cleaner for detecting statistically significant profit because it has zero free parameters beyond lookback (set to half-life). Bollinger bands with fixed entry/exit thresholds introduce optimizable parameters.

**For our straddle:** Our 25% stop is a binary Bollinger band (one threshold). This is fine for an options strategy where we cannot continuously scale — the discrete lot-size constraint (Indian index options are 1-lot minimum) forces binary entry/exit anyway.

### 3.2 Scaling-In Warning (pp. 63-64, 2979-2985)
Chan discusses pros/cons of scaling-in (adding to positions as price moves against you). He notes it requires "unlimited buying power." At Rs5L with 1-lot minimums (NIFTY lots = 75 units, BANKNIFTY lots = 35 units), meaningful scaling-in is not feasible. This is a binding constraint we already recognise.

### 3.3 Kalman Filter for Dynamic Hedge Ratio (Ch. 3)
For dynamic pairs (non-constant cointegration), Kalman filter estimates the hedge ratio in real-time without look-ahead. Relevant if we ever build a spread strategy between NIFTY and BANKNIFTY.

---

## 4. Intraday Momentum Strategies (Ch. 7, pp. 155-168)

### 4.1 Key Mechanisms That Operate Intraday (p. 155-156)
From Chan's enumeration of causes that DO work intraday:
1. **Slow news diffusion** — earnings, macro data. In Indian context: RBI announcements, budget, global cues at open.
2. **Forced institutional flows** — index rebalancing, FII flows. In India: Nifty50 index rebalancing moves stocks strongly.
3. **Stop order cascades** — after breach of support/resistance. Our breadth rider's entry at 10:15 captures the first directional cascade after opening volatility settles.
4. **Leveraged ETF rebalancing** — less relevant for Indian F&O but the principle applies to index futures: a big market move early day forces hedgers to adjust, creating afternoon momentum.

**The one mechanism that does NOT work intraday:** Roll return persistence. Not relevant to us (we are not holding futures overnight).

### 4.2 Opening Gap / Breakout Strategy (pp. 156-157)
Chan tested opening-gap momentum on FSTX (Eurostoxx 50 futures): buy if open > yesterday's high (scaled by 90-day vol), short if open < yesterday's low. APR 13%, Sharpe 1.4.

**Indian analog:** Our breadth gate (10:15 entry after the first 45 minutes of price discovery) is structurally similar — we wait for initial direction to declare, then ride the continuation. The mechanism (stop cascade from overnight positioning) is the same. Chan's FSTX result validates the index futures opening-breakout archetype. Our use of ATR stops is more sophisticated than his simple exit-at-close.

### 4.3 Post-Earnings Drift Shortening (p. 162)
"Price momentum driven by earnings announcements used to last several days. Now it lasts barely until the market closes." This is the general pattern: as more traders exploit a momentum signal, the duration compresses. We should assume our breadth rider's edge horizon is also compressing — the backtest used 2019-2024 data; live edge horizon on low-ATR signal days may already be shorter than the backtest holding period.

### 4.4 High-Frequency Order Flow (pp. 164-167)
Not directly applicable at our timescale, but the key insight is: bid/ask imbalance predicts near-term direction. If we ever add Level-2 data from Dhan, directional order-flow imbalance at 10:00-10:15 would be a useful pre-filter for the breadth gate.

---

## 5. Risk Management / Kelly / Sizing (Ch. 8, pp. 169-186)

### 5.1 The Kelly Formula (pp. 171-174)
```
f = m / s^2
```
where `m` = mean excess return, `s^2` = variance of excess returns (both annualised).

**Half-Kelly is standard practice.** Chan explicitly: "Many traders justifiably prefer [half-Kelly] and they routinely deploy a leverage equal to half of what the Kelly formula recommends."

**Why full Kelly is dangerous:** Estimation error in m (which is highly uncertain for strategies with 40-300 trades) leads to overestimated f, which can eventually cause ruin. With ~150 trades in a 2-year walk-forward window, our confidence interval on the true Sharpe is wide; using half-Kelly or less is mandatory.

### 5.2 Multi-Strategy Capital Allocation Under Leverage Constraint (pp. 173-175)
Formula for optimal allocation with multiple strategies:
```
F = C^{-1} * M
```
where C is covariance matrix of strategy returns, M is vector of mean excess returns.

**Critical finding for our 2-strategy system (Example 8.2, pp. 174-175):**
When total Kelly gross leverage >> broker-allowed max leverage, it is often **optimal to put all buying power into the single highest-growth-rate strategy** rather than splitting proportionally. This directly addresses our situation: with Rs5L and 1-lot minimums creating a binding constraint, we should not try to run both straddle AND trend rider simultaneously at sub-optimal sizes for each. Run the confirmed strategy (straddle, holdout PF 1.53) at full Kelly and treat the rider as paper-only until it confirms.

### 5.3 Lumpy Lots Problem
Chan does not address minimum lot sizes explicitly, but his math makes the issue clear: Kelly gives a continuous optimal leverage. At Rs5L, NIFTY futures require ~Rs5.5L margin for 1 lot (75 units × ~7300 per lot as of mid-2026). So futures are inherently at ~110% of capital — leaving no room for the breadth rider at 0.75% risk-per-trade sizing. This mathematically confirms our finding that futures are unsizeable at current index levels with Rs5L.

**Chan's formula confirms the ATM option debit hypothesis is worth testing:** An ATM call debit of ~0.75% capital = ~Rs3750 per trade. At NIFTY ~22000, a 50-point ATM option costs ~Rs150-200 (75 lots), total outlay ~Rs11,250-15,000. That is 2.25-3% of capital per trade — above the 0.75% target but less catastrophic than 1-lot futures. The backtest said theta+friction kills option debit; the paper comparison will arbitrate.

### 5.4 Constant Proportion Portfolio Insurance (CPPI) (pp. 180-182)
Structure: allocate only a fraction D of total equity to the trading subaccount; apply full Kelly leverage there. If subaccount goes to zero, strategy is shut down — graceful wind-down.

**Application for us:** We can formalise the 2% daily breaker as a CPPI-adjacent rule. Set D = 0.02 (2% daily drawdown limit). Losing 2% in one day triggers exit for the day. Over a multi-day drawdown, we should scale back position sizes (currently we do not — this is a gap).

### 5.5 Stop Loss Doctrine for Mean-Reverting vs Momentum (pp. 182-183)

Chan's definitive statements:
- **Mean-reverting strategies:** "I have never backtested any mean-reverting strategy whose APR or Sharpe ratio is increased by imposing a stop loss." However, stop loss prevents black-swan ruin when the strategy regime-changes.
- **Momentum strategies:** Stop loss is logically consistent with momentum. If momentum reverses, exit is correct.
- **Recommended practice for MR stops:** "Set stop loss greater than the backtest maximum intraday drawdown. In this case, the stop loss would never have been triggered in the backtest period and could not have affected the backtest performance."

**Our straddle stop at 25% is per this doctrine.** We should verify that 25% was never hit during the holdout period. If it was hit on any holdout day, recalibrate upward.

**Our breadth rider ATR trailing stop is correctly typed** as a momentum exit — it is consistent with the strategy logic (if price reverses, momentum is gone, exit is correct).

### 5.6 Risk Indicators (pp. 183-186)
Leading indicators: VIX (India equivalent: India VIX), TED spread, credit spreads.

**For our system:**
- India VIX as a daily pre-filter: Chan showed that VIX > 35 was a leading indicator that the FSTX opening gap strategy would fail (Sharpe 0.16 vs 1.4 baseline). High India VIX may similarly impair our breadth rider (which we found has edge concentrated on LOW-ATR signal days — consistent with Chan's VIX finding).
- Operationalise: if India VIX > 20 (elevated), apply a tighter gate or skip the breadth rider for the day.

---

## 6. Backtesting Pitfalls (Ch. 1, pp. 1-38)

### 6.1 Data-Snooping and Walk-Forward (pp. 4-9, 589-593)
Chan's hierarchy of out-of-sample evidence (weakest to strongest):
1. Cross-validation on in-sample subsets
2. Fixed hold-out period
3. Walk-forward test
4. Paper trading
5. Live trading with real money (smallest allocation)

"Most traders would be happy to find that live trading generates a Sharpe ratio better than half of its backtest value."

**We already have:** Pre-registered grids, 1100 trials with deflated Sharpe (Bailey-LdP), plateau-over-spike selection, 8m/2m walk-forward, frozen holdouts. This is stronger than what Chan describes as standard practice. We are doing this correctly.

### 6.2 Number of Trades for Statistical Significance (pp. 16-20)
Test statistic: `mean(ret) / std(ret) * sqrt(N)` where N = number of trading days.

For 95% confidence (p-value < 0.05), need this > 1.645.

If daily Sharpe = 0.05 (modest), need N > (1.645/0.05)^2 = 1082 trading days = ~4.3 years.
If daily Sharpe = 0.10, need N > 270 days = ~1 year.

**Our straddle has 40-300 trades** (expiry-day only). At ~50 expiry trades per year, 4 years = 200 trades. For 90% confidence, need mean(ret)/std(ret)*sqrt(200) > 1.282, so need daily Sharpe of the strategy > 0.091. If our holdout PF 1.53 corresponds to a Sharpe above this threshold, we are borderline significant. This quantifies why the straddle's statistical base is still thin.

**Our breadth rider** at ~80 trades/year is similarly constrained. Both strategies need 3-4 more years of paper/live data to achieve conventional 95% significance.

### 6.3 Look-Ahead Bias
Chan's recommended safeguard: "If your backtesting and live trading programs are one and the same, and the only difference is what kind of data you are feeding in, then there can be no look-ahead bias."

**Our system:** Event-driven next-bar-open fills. Look-ahead bias is structurally prevented. Signals computed at bar close; fills at next open. This is the correct architecture.

### 6.4 Transaction Costs Must Be Included (pp. 2989-2994)
Chan explicitly notes that backtest examples omit transaction costs to simplify code — but warns readers to add them. Our engine already includes Dhan costs with 2026 F&O STT hikes and causal ATR slippage. This is a meaningful advantage over naive implementations.

### 6.5 Regime Shift Is the Unsolvable Problem (pp. 1237-1283)
No amount of backtesting protects against regime shifts. Chan's examples:
- 2001 decimalization killed many stat-arb strategies.
- 2007 RegNMS changed market microstructure.
- 2008 crisis killed interday momentum for years.

For Indian markets, the analogous risks:
- SEBI F&O regulatory changes (lot size hikes, STT hikes already in 2026).
- RBI macro tightening cycles.
- NSE market structure changes (market-making rules, tick size changes).

**Implication:** Keep our model set simple (few parameters per Chan's Occam's razor). Complex multi-parameter models break in regime shifts faster than simple ones.

---

## 7. Momentum vs Mean Reversion — Definitive Comparison (Ch. 6, pp. 151-153)

| Dimension | Mean Reversion | Momentum (intraday) |
|---|---|---|
| Sharpe ratio | Higher (more frequent signals) | Lower (fewer independent signals at interday) |
| Stop loss | Contradictory; use only as black-swan guard | Natural and consistent; rolling stop = de facto exit |
| Tail risk / black swans | High (unlimited downside, capped upside) | Low (limited downside, unlimited upside) |
| Post-crisis performance | Survives better (replaces momentum) | Crashes for years (interday); intraday less affected |
| Risk management | Hardest (can't stop-loss without contradicting logic) | Easiest (stop = signal reversal) |
| Edge duration | Long (fundamental reason to persist) | Shrinking (awareness arbitrages it away) |

**Chan's synthesis (conclusion, p. 188):** "Adding momentum strategies to a portfolio of mean-reverting strategies allows us to achieve higher Sharpe ratios and smaller drawdowns than either type alone." — This directly justifies our portfolio of straddle (MR) + breadth rider (momentum).

---

## 8. What the Book Does Not Cover (Gaps vs Our System)

1. **Options theta decay in the context of MR strategies.** Chan's MR chapter covers price-spread pairs, not option premium. His stop-loss doctrine for MR is directionally applicable but does not address the asymmetric payoff of short straddles.

2. **Indian market-specific costs.** 2026 F&O STT hike (0.1% on options exercise, 0.05% on futures buy/sell) are India-specific. Chan's transaction cost discussion uses U.S. commissions. Our cost engine is more accurate than anything in this book.

3. **Intraday index breadth as a signal.** Chan does not discuss stock breadth / %-above-VWAP as a filter for index directional trades. Our innovation here is original.

4. **Lot-size constraints for small capital.** No explicit treatment of the mathematical problem of 1-lot minima at Rs5L.

5. **0DTE / same-day expiry options.** Chan's option discussion (brief references) is about holding-period options, not 0DTE.

---

## 9. Methodology Upgrades Recommended

### 9.1 Half-Life Calculation on Index Spread (Priority: Medium)
Run OLS regression `Δy(t) = λ*y(t-1) + μ + ε` on NIFTY intraday prices (5-min bars) to compute half-life of intraday mean reversion. This gives us the natural lookback for any intraday MR signal and confirms/refutes our ATR-based parameter choices.

**Code:** `halflife = -log(2) / lambda` where lambda from regression of `diff(prices)` on `lag(prices)`.

### 9.2 Hurst Exponent as Regime Detector (Priority: High)
Compute rolling Hurst (e.g., 60-day rolling window) on NIFTY daily closes. Plot alongside strategy PnL. If H < 0.5 during MR outperformance and H > 0.5 during momentum periods, this becomes a regime switch signal. Invert strategy weights accordingly.

**Specific action:** Add `compute_hurst(prices, window=60)` to our daily regime monitor.

### 9.3 India VIX as Leading Risk Indicator (Priority: High)
Per Chan's empirical finding (pp. 183-184): high VIX predicted failure of opening-gap momentum strategies. Test whether India VIX > 18 (elevated, not crisis) on previous day predicts poor breadth-rider performance on signal days. If confirmed, add VIX gate to the rider.

### 9.4 Monte Carlo Growth Rate Optimization for Sizing (Priority: Low)
Chan's Pearson-system Monte Carlo (pp. 176-178) to find optimal leverage using fat-tailed empirical distribution. This is more appropriate than plain Kelly for our short-return-series (40-300 trades). Implement after we have 1 year of paper data as the empirical distribution input.

### 9.5 Kelly Multi-Strategy Allocation Under Constraint (Priority: Medium)
Formally apply Equation 8.2 (`F = C^{-1} * M`) when we have enough live/paper data for both straddle and rider. Until then, per Example 8.2: under tight capital constraints, concentrate on the highest-growth-rate confirmed strategy (straddle at holdout PF 1.53).

---

## 10. Strategy Ideas from the Book

### 10.1 Volatility Futures vs Index Futures Arbitrage (Ch. 6, pp. 142-144)
VX vs ES: buy VX front contract if in backwardation (roll return positive), short ES simultaneously. Sharpe ~1, APR 6.9%.

**Indian analog:** India VIX futures vs NIFTY futures. VIX futures on NSE exist but are illiquid. Not directly applicable with our capital and liquidity constraints. Flag for future.

### 10.2 Linear Cross-Sectional Mean Reversion on NIFTY-50 Stocks (Ch. 4)
Chan's strategy: each day, buy stocks in bottom decile of 20-day return, short stocks in top decile, hold 1 day. Sharpe 4.7 pre-2008.

**Indian applicability:** Very high in theory. In practice: (a) shorting individual stocks on NSE requires F&O position (no direct equity short for retail intraday without BTST), (b) we have 50 stocks — enough for decile ranking, (c) Indian friction ~Rs600/equity round trip makes this viable only if the per-position alpha is >>600 per round trip. Needs separate feasibility study.

### 10.3 Seasonal/Conditional MR at Indian Market Open (Ch. 3, pp. 2970-2974)
"Seasonal mean reversion means that a price series will mean-revert only during specific periods of the day or under specific conditions." First 30 minutes of Indian market (09:15-09:45) are volatile gap-fill. A buy-the-gap (buy when price opens below previous close) intraday strategy on NIFTY is testable. Chan showed this worked on stocks; index behavior may differ.

---

## 11. Key Numbers and Rules of Thumb from Chan

| Rule | Source | Value |
|---|---|---|
| Minimum independent trades for 90% significance | Ch. 1, p. 17 | Sharpe_daily * sqrt(N) > 1.282 |
| Target live Sharpe vs backtest Sharpe | Ch. 1, p. 593 | Live > 0.5 * backtest |
| Half-Kelly as standard practice | Ch. 8, p. 287 | Leverage = 0.5 * Kelly_f |
| Lookback = half-life for MR strategies | Ch. 2, p. 219 | halflife = -log(2)/lambda |
| Stop loss for MR: set above backtest max intraday DD | Ch. 8, p. 723 | Never triggered in backtest |
| Stop loss logically consistent with momentum | Ch. 8, p. 727 | Stop = signal reversal |
| Pre-crisis Sharpe 4.7 (high freq MR stocks) | Ch. 6, p. 456 | Benchmark for comparison |

---

## 12. Contradictions and Cautions vs Our Practice

1. **Chan says stop losses hurt MR strategy backtests; we have a 25% straddle stop.** Chan's resolution: the stop is correct IF it guards against regime change. It should never trigger in normal backtests. We must verify this against holdout data. If it triggered in holdout, our stop is too tight.

2. **Chan says momentum crashes post-crisis for years.** Our breadth rider is an intraday momentum strategy. Chan explicitly says intraday momentum does NOT suffer the interday crash phenomenon (Ch. 7, p. 155). So this is NOT a contradiction, but we should track whether intraday index momentum in India showed similar post-crisis compression after March 2020.

3. **Chan advocates linear simple models; our system uses a composite gate (breadth + ATR).** The two-factor gate (breadth >= 0.72 AND ATR < threshold) is still simple by Chan's standards (2 parameters, both economically motivated). No contradiction.

4. **Chan warns that strategy performance depends sensitively on implementation details.** Our next-bar-open fill model may differ from the optimal implementation of the breadth rider. Paper trading will identify any implementation-to-backtest divergence.

---

## Appendix: Equations

**ADF regression:**
```
Δy(t) = λ*y(t-1) + μ + β*t + α1*Δy(t-1) + ... + αk*Δy(t-k) + ε
```
λ < 0 → mean-reverting; λ/SE(λ) < -2.594 → reject unit root at 90%

**Half-life:**
```
half_life = -log(2) / λ
```

**Kelly leverage:**
```
f = m / s^2   (single strategy)
F = C^{-1} * M  (multiple strategies, covariance matrix C, means vector M)
```

**Hurst exponent:**
```
Var(z(t+τ) - z(t)) ~ τ^{2H}
H < 0.5: mean-reverting
H = 0.5: random walk
H > 0.5: trending
```

**Minimum trades for significance (Gaussian assumption):**
```
SR_daily * sqrt(N) > 1.282 (90%)
SR_daily * sqrt(N) > 1.645 (95%)
SR_daily * sqrt(N) > 2.326 (99%)
```

**CPPI growth with drawdown constraint D:**
```
g_cppi = sum(log(1 + R(t) * D * f_optimal * (1 + drawdown(t-1))))
```
Set aside D fraction of equity as trading sub-account; full Kelly on sub-account; cash stays in reserve.
