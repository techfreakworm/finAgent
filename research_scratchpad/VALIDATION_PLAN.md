# FinAgent — Minimal Validation Plan (Do This FIRST)

## Philosophy: Prove the Edge Before Building the System

Don't build 30 days of infrastructure only to discover the strategies don't work. Instead, validate each hypothesis in **Jupyter notebooks** with minimal code. Each validation is independent and takes 1-2 days max.

**Kill criteria:** If a hypothesis fails validation, STOP and pivot. Don't proceed to the full build.

---

## Validation 1: Can we get the data? (Day 1)

### Goal
Prove that all critical data sources are accessible and return clean, usable data.

### What to do
One Jupyter notebook: `01_data_access.ipynb`

```
Test each source — just fetch and display, nothing fancy:

1. Price data (pick ONE that works):
   - yfinance: `yf.download("RELIANCE.NS", period="3y")`
   - jugaad-data: `stock_df("RELIANCE", "01-01-2023", "28-03-2026")`
   - nselib: historical data for NIFTY 50

2. Option chain:
   - nselib: `nse_live_option_chain("NIFTY")`
   - NSE website direct: fetch from nseindia.com/api/option-chain-indices?symbol=NIFTY

3. FII/DII data:
   - NSE reports page: download CSV from nseindia.com/reports/fii-dii
   - Check: do we get daily buy/sell/net for cash + F&O?

4. News:
   - MoneyControl RSS: parse with feedparser
   - Check: do we get headlines + dates?

5. India VIX:
   - nselib or yfinance: `yf.download("^INDIAVIX")`
```

### Pass/Fail
- **Pass:** Can fetch 3+ years of daily OHLCV, live option chain, FII/DII data, and news
- **Fail:** Key data sources blocked/broken → find alternatives before proceeding

---

## Validation 2: Does FII/DII data predict NIFTY direction? (Day 2)

### Goal
Test the single most important hypothesis: **FII positioning in F&O has predictive power for NIFTY**.

### What to do
One notebook: `02_fii_dii_signal.ipynb`

```python
# 1. Get 1-2 years of daily FII/DII data + NIFTY daily closes
# 2. Calculate:
#    - FII net cash flow (buy - sell)
#    - FII net F&O flow
#    - 5-day cumulative FII flow
#    - FII Index Long-Short Ratio (if available)
# 3. Calculate NIFTY next-day / next-5-day return
# 4. Correlation analysis:
#    - Pearson correlation between FII 5d flow and NIFTY 5d forward return
#    - Rank correlation (Spearman)
# 5. Simple strategy test:
#    - Go long when FII 5d cumulative > 0 (buying)
#    - Go flat/short when FII 5d cumulative < 0 (selling)
#    - Calculate: cumulative return, Sharpe, max drawdown
#    - Compare vs buy-and-hold NIFTY
# 6. Visualize: scatter plot of FII flow vs NIFTY returns
```

### Pass/Fail
- **Pass:** Statistically significant correlation (p < 0.05) AND simple FII-based strategy beats buy-and-hold
- **Fail:** No predictive power → drop FII as core signal, look for other edges

---

## Validation 3: Does PCR / Max Pain predict weekly NIFTY expiry? (Day 3)

### Goal
Test if options data (Put-Call Ratio, Max Pain) predicts where NIFTY settles on weekly expiry.

### What to do
One notebook: `03_options_edge.ipynb`

```python
# 1. Get historical option chain snapshots (or reconstruct from OI data)
#    - For each weekly expiry over past 1 year
# 2. Calculate:
#    - Max Pain for each expiry (strike with min total pain for option sellers)
#    - PCR (OI) on Monday/Tuesday of expiry week
#    - NIFTY close on expiry day
# 3. Analysis:
#    - How often does NIFTY settle within 1% of Max Pain? (research says ~60-70%)
#    - Correlation between PCR extremes and NIFTY direction
#    - Simple strategy: if NIFTY is far from Max Pain on Tuesday,
#      bet on convergence by Thursday
# 4. Backtest this simple convergence strategy:
#    - Entry: Tuesday close, if NIFTY > Max Pain + 100 → sell, < Max Pain - 100 → buy
#    - Exit: Thursday expiry close
#    - Calculate: win rate, avg P&L, Sharpe
```

### Pass/Fail
- **Pass:** Max Pain convergence works >55% of the time AND PCR extremes are contrarian indicators
- **Fail:** Options data has no predictive power → de-prioritize Layer 2 in full system

---

## Validation 4: Does FinBERT sentiment predict stock moves? (Day 4)

### Goal
Test if financial news sentiment (scored by FinBERT) has predictive value for next-day returns.

### What to do
One notebook: `04_sentiment_signal.ipynb`

```python
# 1. Collect 3-6 months of financial news headlines (MoneyControl RSS backfill)
# 2. Run FinBERT (ProsusAI/finbert) on each headline
#    - pip install transformers torch
#    - Score each headline: positive/negative/neutral + confidence
# 3. Per stock per day: average sentiment score
# 4. Analysis:
#    - Correlation between daily sentiment and next-day return
#    - Event study: what happens after extreme sentiment days (>0.8 or <-0.8)?
#    - Does sentiment MOMENTUM (3d change) predict better than level?
# 5. Simple strategy:
#    - Buy stocks with top 10% daily sentiment improvement
#    - Sell/avoid stocks with bottom 10% sentiment
#    - Calculate: return spread, Sharpe
```

### Pass/Fail
- **Pass:** Sentiment extremes predict direction >55% AND event study shows clear impact
- **Fail:** Sentiment is noise → reduce weight of Layer 3, rely more on quantitative signals

---

## Validation 5: Does regime detection work on Indian markets? (Day 5)

### Goal
Test if HMM can reliably detect market regimes AND if regime-aware trading improves results.

### What to do
One notebook: `05_regime_detection.ipynb`

```python
# 1. Get 3 years of daily data:
#    - NIFTY returns, India VIX, Advance-Decline ratio
# 2. Fit HMM with 3-4 states (hmmlearn library)
#    - pip install hmmlearn
#    - Use GaussianHMM with features: [returns, vix, adv_decline]
# 3. Label each day with regime
# 4. Sanity check:
#    - Mar 2020 crash → should be "High-Vol" or "Bear"
#    - Oct 2021-Jan 2022 rally → should be "Bull"
#    - Range-bound periods → should be "Neutral"
# 5. Strategy comparison:
#    - Strategy A: Simple moving average crossover (always on)
#    - Strategy B: Same strategy BUT only trades in "Bull" regime, cash in others
#    - Compare: returns, Sharpe, max drawdown
# 6. Key question: Does sitting out in Bear/HighVol regimes improve Sharpe?
```

### Pass/Fail
- **Pass:** Regimes match known market periods AND regime-filtered strategy has higher Sharpe
- **Fail:** HMM produces random/noisy regimes → use simpler VIX-based regime (VIX < 15 = bull, > 25 = bear)

---

## Validation 6: Does the core ML model beat buy-and-hold? (Day 6-7)

### Goal
Test the ACTUAL money-making hypothesis: can XGBoost with multi-source features generate profitable signals?

### What to do
One notebook: `06_ml_backtest.ipynb`

```python
# 1. Build feature matrix (use data from validations 1-5):
#    - 15 technical features (EMA, MACD, RSI, BB, ATR, Volume)
#    - 5 options features (PCR, Max Pain distance, FII LR) — if validation 3 passed
#    - 3 sentiment features (FinBERT avg, news count, VIX) — if validation 4 passed
#    - 2 regime features (HMM state, regime prob) — if validation 5 passed
#    Total: ~25 features (start small!)
#
# 2. Label with Triple Barrier Method:
#    - Upper barrier: +2% (target)
#    - Lower barrier: -1% (stop)
#    - Time barrier: 5 trading days
#    - Label: 1 (hit target), -1 (hit stop), 0 (timeout)
#
# 3. Walk-Forward Backtest:
#    - Train on 18 months, test on 3 months, roll forward by 3 months
#    - Repeat across 2 years of data → get 4+ OOS test periods
#    - Use XGBoost with default params (don't overfit!)
#
# 4. Calculate on OOS periods ONLY:
#    - Accuracy, Precision, Recall
#    - Simulated P&L (with 0.05% transaction cost)
#    - Sharpe ratio
#    - Max drawdown
#    - Win rate
#    - Compare vs buy-and-hold NIFTY 50
#
# 5. Feature importance:
#    - Which features actually matter?
#    - Are options/sentiment features worth the effort?
```

### Pass/Fail Criteria (CRITICAL — this is the go/no-go for the full build)
- **Pass (proceed to full build):**
  - OOS Sharpe > 0.8
  - OOS win rate > 52%
  - OOS max drawdown < 20%
  - Beats buy-and-hold NIFTY by at least 3% annually
  - Results are CONSISTENT across multiple OOS windows (not just one lucky period)
- **Partial pass (proceed with modifications):**
  - Some OOS windows work, others don't → investigate which regimes work
  - Only certain feature groups add value → simplify the full system
- **Fail (pivot or stop):**
  - OOS Sharpe < 0.5
  - Doesn't beat buy-and-hold
  - High variance across OOS windows → overfitting detected

---

## Validation 7 (Optional): India VIX as a Simple Regime Filter (Day 7)

### Goal
Test the simplest possible regime filter as a baseline.

### What to do
One notebook: `07_vix_regime_filter.ipynb`

```python
# Simplest possible test:
# 1. When India VIX < 15: full position (aggressive)
# 2. When India VIX 15-20: half position
# 3. When India VIX 20-25: quarter position
# 4. When India VIX > 25: cash (no trading)
#
# Apply this filter to a simple EMA crossover strategy on NIFTY
# Compare filtered vs unfiltered performance
```

### Why this matters
If even this trivially simple filter improves performance, it validates that regime awareness is the real edge — and the HMM just refines it.

---

## Summary: 7 Notebooks, 7 Days

| Day | Notebook | Hypothesis | Kill if fails? |
|-----|----------|-----------|----------------|
| 1 | `01_data_access.ipynb` | Can we access the data? | YES — no data, no system |
| 2 | `02_fii_dii_signal.ipynb` | FII/DII flow predicts NIFTY | Modify — reduce derivatives weight |
| 3 | `03_options_edge.ipynb` | Max Pain + PCR predict expiry | Modify — simplify options layer |
| 4 | `04_sentiment_signal.ipynb` | FinBERT sentiment has edge | Modify — drop sentiment if no edge |
| 5 | `05_regime_detection.ipynb` | HMM regimes are real + useful | Modify — use simple VIX filter |
| 6-7 | `06_ml_backtest.ipynb` | XGBoost ensemble beats buy-and-hold | **YES — if this fails, don't build** |
| 7 | `07_vix_regime_filter.ipynb` | Simple VIX filter improves Sharpe | Informational |

## Decision Matrix After Validation

| Outcome | Action |
|---------|--------|
| All pass | Proceed with full 30-day build as planned |
| V6 passes, some others fail | Proceed but simplify: drop non-performing data layers |
| V6 partially passes | Investigate which features/regimes work, redesign accordingly |
| V6 fails | STOP. Go back to research. Test different approaches (RL, different features, different universe) |
| V1 fails (can't get data) | Find alternative data sources, or pivot to US markets with better API access |

## Tech Requirements (Minimal)

```
pip install jupyter pandas numpy yfinance nselib ta xgboost hmmlearn \
    transformers torch feedparser beautifulsoup4 requests matplotlib seaborn scikit-learn
```

No Docker, no databases, no APIs to set up. Just Jupyter + pip install. Pure validation.
