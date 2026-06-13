# Kaufman — Trading Systems and Methods (6th ed.) — Distillation
**Purpose:** Reference for the NIFTY/BANKNIFTY intraday algo system.
**Capital:** Rs 5L, paper-only, 1 lot min.
**Reader note:** This is a 1,200-page reference. Sections were read selectively via the ToC.
Chapters read: Ch.12 (Volume/Breadth), Ch.15 (Short-Term Patterns), Ch.16 (Day Trading),
Ch.17 partial (Adaptive), Ch.20 partial (Volatility), Ch.21 (System Testing), Ch.22 (Adding Reality), Ch.23 (Risk Control).

---

## 1. Intraday Breakout Systems (Ch.16, pp. 551-560 in ToC form)

### 1.1 Opening Range Breakout (ORB) — What Kaufman Actually Tested

The 1st-Hour Breakout (N-Bar ORB) is the canonical form:
- Set high/low over first N bars; buy break above, sell break below.
- Exit on close or next open.
- ATR-based threshold variant: use `open ± f × ATR(n)` where f is optimized (found ~0.5–0.9 optimal for 30-min S&P).
- Previous-close variant: center bands on prior close instead of today's open. This adds embedded trend bias. S&P tests showed this variant produced ~$800K vs ~$450K for the open-based variant over 2010–2017 on 30-min data. **Mechanically: `prev_close ± f × ATR(n)` — more directional signals, better for trending days.**

**Key filters NOT in our killed ORB-v2 that Kaufman showed to help:**
1. **Preceding inside day / NR4:** Crabel's data shows win rate jumps from ~55% to ~70%+ after an inside day. Raschke's NR4 (4th day has smallest range of 4 days) is the easiest implementation.
2. **Compression filter:** If the opening range is very small relative to ATR, skip — noise-driven breakouts fail. Minimum range = `c × ATR` where c ~0.2–0.4.
3. **ATR volatility minimum for the day:** High-volatility opening bar predicts more volatile day (U-shaped intraday pattern). Kaufman describes a cumulative intraday volatility histogram: if today's early bars are above the 5-day average histogram, day is likely to be more volatile and breakout trades have more headroom.
4. **Mean-reversion alternative:** At `f_MR × ATR` extension from open, *sell* instead of buy. S&P results showed 14% lower absolute profit but 2× average trade size and 64% win rate vs 56% directional. For our low-vol signal days (breadth rider fires on ATR<threshold), mean-reversion ORB on the index itself may be worth testing.
5. **No signal within 2 hours of open:** Cancel order. Reduces whipsaws.
6. **Cancel new entries within 1 hour of close.** (We do this with 15:19 flat.)

**Filters we can add to ORB-v2 backtest immediately:** inside-day flag and ATR-compression filter. Both are signals, not look-ahead.

### 1.2 Mark Fisher's ORB (Logical Trader Method)

More sophisticated. After establishing an OR over the first 5–30 min:
- Compute `+A = ORH + x% of OR`, `-A = ORL - x% of OR`.
- Also compute `+C` and `-C` at a larger multiplier.
- Buy signal only if price breaches `+A` AND remains there for `OR/2` time.
- Confirmation time requirement filters false breakouts at support/resistance levels.
- Pivot range (from previous day's range) as directional bias overlay.

**Applicability:** Fisher's confirmation time filter is implementable on 1-min bars. For our 10:15 breadth gate, a shorter OR (9:15–9:30 NSE time = first 15 min) with a confirmation dwell time could filter same-direction false breaks.

### 1.3 Intraday Time-of-Day Patterns

U-shaped volatility/volume: high at open, low at midday, high near close.
- **At midday (11:30–12:30 IST analog for NSE), volume drops.** Price reversals at midday range extremes are common.
- **Afternoon breakout:** If price breaks the morning high/low after midday with conviction, it tends to continue. Kaufman's "TSM Midday Support and Resistance" rules: define morning range, enter in direction of afternoon break, exit at close or next open. This is mechanically close to our breadth rider (which uses 10:15 signal — but could add a 12:30–13:30 confirmation gate for the index entry on non-signal days).
- **Day pattern taxonomy** (from Tubbs, updated): Strong open → reversal by 11 AM → support at 1 PM → strong close. This describes a common Indian session pattern too; mid-day lull around 12–1 PM.

---

## 2. Breadth Indicators (Ch.12, pp. 487-500 in ToC form)

### 2.1 Core Breadth Concepts

Kaufman: breadth confirms price. The relationship matrix:
| Breadth | Price | Signal |
|---------|-------|--------|
| Rising  | Rising | Bullish confirmation |
| Falling | Falling | Bearish confirmation |
| Falling | Rising | Divergence — bearish |
| Rising  | Falling | Divergence — bullish |

**Breadth must match the index being traded.** A large-cap breadth indicator (Dow-30 stocks) will not confirm small-cap moves. For NIFTY, our 50-stock %-above-VWAP is theoretically correct since the universe exactly matches the index components.

### 2.2 Variants Worth Testing Against Our %-Above-VWAP

**Our current signal:** %-above-VWAP at 10:15 >= 0.72 → long NIFTY with 2×ATR stop.

**Kaufman alternatives (adapting from US equity breadth to NSE 50 stocks):**

1. **McClellan Oscillator equivalent:** Fast EMA(net advancers) − Slow EMA(net advancers), using (advancing − declining) from our 50-stock universe at each 1-min bar. This gives a continuous intraday breadth flow rather than a single 10:15 snapshot. Signal: Enter when McClellan crosses above zero from negative territory; stay long while it is positive. This is a more dynamic version of our current binary gate.

2. **TRIN/Arms Index adapted to NIFTY 50:**
   `TRIN = (advancing_count / declining_count) / (advancing_volume / declining_volume)`
   TRIN < 0.5 is strongly bullish (per Bhandari's thresholds: buy < 0.5, sell > 2.0 as trend signal). We have per-stock volume available via Dhan — this is buildable.

3. **High-Low Ratio:** Count NIFTY-50 stocks making intraday new highs vs. new lows (relative to prior-day close). Gerry Appel uses a 10-day smoothed ratio with threshold 0.80 for buy signals. Intraday version: count stocks above prior-day high at 10:15 vs. below prior-day low. Simple to compute.

4. **Schultz Advancing Ratio:** `advancing_count / total_count` — simpler than %-above-VWAP (no VWAP calculation needed), but loses the VWAP anchor's information content. Less attractive.

5. **Breadth as countertrend:** Connors documented that extreme breadth values produce short-term mean-reversion trades in the index. If %-above-VWAP is > 0.92 (very high), consider fading the index rather than chasing. This is relevant to our edge-concentration finding: the 0.72 threshold may capture the "moderate-strong" regime; extreme values may not trend.

### 2.3 Volume Spike as Intraday Signal

Kaufman's rule for volume spikes (adapting to intraday):
- "Normal" volume = 60-day average for that bar time-of-day.
- Spike = today's bar volume > 2× normal for that bar.
- Trade: sell into spike if 5-day trend up; buy into spike if 5-day trend down (mean-reversion).
- Profit target: 3 × 20-day ATR.

For NSE, we have tick volume (number of trades) available intraday. Could implement: if NIFTY shows a volume spike (>2× 5-day average for that 5-min bar) after our breadth gate fires, reduce position size or skip entry — the move may already be exhausted.

---

## 3. System Testing Methodology (Ch.21)

### 3.1 What Kaufman Calls "Robustness"

A robust system satisfies ALL of:
1. Based on a sound fundamental premise (not data-mined).
2. Adapts to changing conditions — volatility-scaled stops, not fixed dollar amounts.
3. Fewest rules possible. Adding a rule that helps one case and hurts others (kurtosis of optimization surface increases) is overfitting.
4. Tested with maximum data including bear markets, price shocks, and regime changes.

His empirical finding (citing Futures Truth): **best-performing systems commonly have four or fewer variables.**

**Comparison to our methodology:**
- We preregister grids before testing — this aligns with his "do not discover via optimization."
- Our plateau-over-spike rule (prefer average of a region over the peak) aligns exactly with his "average results of neighbors" principle (pp. 584-602). He computes 5-bar rolling average of optimization profits and recommends choosing from the flat plateau, not the spike.
- Our +50% slippage stress test aligns with his recommendation to test conservative cost assumptions.

### 3.2 Walk-Forward (Step-Forward in Kaufman's Terminology)

His procedure is identical to our 8m/2m windows:
- In-sample (IS): 4 years. Out-of-sample (OOS): 1 year. Step forward 1 year at a time.
- **Intraday-specific guidance (citing Ehlers, p. 921):** "Intraday data is not stationary. Test window should be two months, walk-forward one week." This is more aggressive than our 8m/2m for intraday. We may be using too-long windows for intraday signal decay.

**Actionable:** Consider a supplementary 60-day IS / 10-day OOS walk-forward analysis for the breadth rider specifically, in addition to our existing 8m/2m WF. Both results should agree for confidence.

### 3.3 Common Testing Errors to Guard Against

1. **Price shock windfall profits:** If > 50% of largest single-day profits come from shocks that could have gone either way, the strategy is overfit. Review our backtest's largest 10 trade days — if concentrated near events (elections, budget, global crash), discount those returns.
2. **Short test bias in walk-forward:** If optimal parameter jumps between very fast and very slow across successive IS windows (e.g., 10-day MA in one window, 100-day in next), IS windows are too short.
3. **Out-of-sample cannot be reused:** Once the final holdout is used, you cannot fix and reuse it. We are correctly treating our holdout as frozen.
4. **Feedback loop:** Every time you review OOS results and adjust, you are consuming the OOS sample. We must document each holdout use strictly.

### 3.4 Optimization Surface Visualization

Kaufman uses heat maps and 3D surface charts. Our plateau rule is equivalent to his "choose the region of smooth high profits, not the isolated spike." Key tests:
- **Kurtosis of optimization result distribution:** High kurtosis (> 6) = overfitting signal.
- **Percentage of profitable parameter combinations:** If > 70% of all combinations are profitable, the system is robust. Below 50%, do not trade.

---

## 4. Risk Control and Position Sizing (Ch.23)

### 4.1 The "Rule of Small Risk" and Our Context

Kaufman: "Never risk more than 5% of invested capital on any one trade." More conservative practitioners: 1–2%.

For our system:
- Rs 5L capital.
- 1 lot NIFTY futures = ~Rs 5.75L notional at 23,000 NIFTY × 25 lot size. Margin ~Rs 1.1–1.3L.
- 1 lot BANKNIFTY = ~Rs 10L+ notional. Much worse.
- At 2% per-trade risk of Rs 5L = Rs 10,000 max loss per trade.
- 1 lot NIFTY with a 2×ATR stop at ATR=80 pts = 80×2×25 = Rs 4,000 loss. This is 0.8% risk — fine.
- At low-ATR days (ATR=50) the stop is tighter: 50×2×25 = Rs 2,500 = 0.5% — acceptable but small absolute P&L.

**Our unsizeable futures problem:** Kaufman confirms this: "Futures trading uses only 25% of investment for purchasing... reserves cover losses." At Rs 5L, 1 lot consumes 22–26% as margin and the full notional exposure is 115% of capital — extreme leverage. This is exactly why the backtest flagged futures as unsizeable at 0.75% risk.

### 4.2 ATR-Based Position Sizing (The Kaufman Standard)

His procedure (Table 23.3):
1. Equal allocation per market: `alloc = total_capital × portfolio_fraction`.
2. ATR dollar volatility: `vol_$ = ATR_20d × contract_multiplier × FX`.
3. Contracts: `contracts = alloc / vol_$`.

For a single-strategy system with 1 lot minimum:
- If `vol_$ > alloc`, you cannot size to 1 lot at target risk. You must either increase capital or increase per-trade risk %.
- **At Rs 5L, NIFTY 1 lot at 1.5% risk requires alloc = Rs 7,500 for a 100-pt ATR day (100 × 25 = Rs 2,500 per lot). Allocation available at 1.5% = Rs 7,500 — OK for 3 lots at Rs 2,500 each. But we have 1 lot minimum, so we simply accept the resulting 0.5% risk on low-ATR days.**

Kaufman's conclusion on this exact problem: "The magnitude of the risk should be handled by decreasing or increasing leverage." At 1-lot minimum, we are constrained and must accept variable per-trade risk as a function of ATR. This is documented and acceptable.

### 4.3 Stop Loss Design for Intraday Mean-Reversion vs. Trend

Kaufman (pp. 623–630):
- **Trend systems:** Stop-loss works. Place at 2–3× ATR. Trail the winner.
- **Mean-reversion systems:** Stop-loss *hurts* the strategy by limiting the big wins from reversals. Use a wide catastrophic-only stop (e.g., 5× ATR) to prevent blowup, not a tight one. Our 25% straddle stop on the 0DTE strategy falls into this bucket — mean-reversion payoff, wide stop acceptable.
- **Breadth rider:** This is a trend trade. Our 2×ATR entry stop and 3.5×ATR trail are appropriate per Kaufman's framework.

### 4.4 Managing Risk without Stops — Volatility Scaling

Kaufman: "Reduce position size as volatility increases; the greater the volatility, the smaller the position." This is our causal ATR slippage model extended to sizing. Practical implementation for our system:
- When NIFTY ATR(14) on 5-min bars is in the top quintile of the trailing 20-day distribution, reduce size to 0 (no trade). Our breadth rider already uses "low-ATR signal days" as the edge concentration source — this confirms the intuition.

### 4.5 Daily Risk Breaker Context

Our 2% daily breaker = Rs 10,000 max daily loss.
Kaufman: "1–5% of invested capital on any one trade." Our breaker covers the portfolio-level equivalent. He recommends 3× max drawdown as minimum capital for a futures account — our drawdown target implied by 2% breaker is ~Rs 10,000/day; we must have experienced drawdown of 2–3 weeks of bad days = Rs 100K–Rs 150K. At Rs 5L that is a 20–30% drawdown threshold before we would need to pause. This is acceptable for a paper system but aggressive for live.

---

## 5. Seasonality and Day-of-Week Effects (Ch.10, Ch.15)

### 5.1 Weekday Patterns — Directly Applicable

Kaufman's study (US markets, 2000–2018, ~823 weeks):
- **"Up on Monday, down on Tuesday":** Empirical study shows Tuesday reversal probability only 48–52% — marginally below coin flip in trending markets. Not tradeable by itself.
- **Friday reversal tendency:** About 62% of Friday closes move opposite to Monday's direction in equity index markets (S&P). In a trend context (filtered by 30/60/120-day MA), Friday still reverses ~57%. This suggests: **do not add new positions Friday afternoon; if in a profitable trade from Monday, consider Friday early-exit to lock gains.**
- **Monday morning re-entry effect:** Weekend news accumulates. Monday open often reverses Friday direction (mean-reversion). For our breadth rider, Friday is not an optimal entry day if the signal fires late.

**Adaptation for NSE weekly expiry structure:** India's NIFTY weekly options expire Thursday. On Thursday expiry days, the straddle strategy dominates. For the breadth rider, Thursday near-expiry tends to see gamma-driven moves in the underlying — elevated intraday volatility, which is actually good for our trend rider (high ATR → better stops → but also our edge concentration is low-ATR, so this is contradictory). Flag: Thursday expiry days may behave differently for the breadth rider. Backtest should separate expiry-Thursdays.

### 5.2 Expiry-Day Patterns (Extrapolated from Kaufman's Volume Chapter)

Kaufman (Ch.12): "Volume is higher on triple witching day... equity index futures, options on futures, and options on individual stocks all expire at the same time." He notes higher volatility but also higher liquidity near expiry.

For India:
- **Monthly NIFTY expiry (last Thursday):** Our confirmed 0DTE ATM straddle short strategy benefits from this elevated IV collapse. Kaufman's breadth chapter confirms that volume spikes on expiry days are not informational but mechanical. Our straddle is correctly positioned to harvest that.
- **Weekly expiry (every Thursday):** Same structure, smaller magnitude. The breadth rider on a weekly expiry Thursday may see false breakouts as HFT/MM positioning for expiry settlement causes index noise. Suggestion: mark weekly expiry Thursdays and test breadth rider performance separately on those days.

---

## 6. Volatility Systems and Regime Filters (Ch.20 partial)

### 6.1 ATR as the Universal Adapter

Kaufman consistently uses ATR (rather than percentage or fixed-point) for:
- Stop placement: 2–3× ATR.
- Breakout thresholds: 0.5–1.0× ATR from open.
- Position sizing (see Section 4).

Our causal ATR slippage model and ATR-based stops are confirmed as best practice.

### 6.2 VIX Trading Systems (India VIX Analog)

Kaufman describes VIX-based entry filters:
- If 2-day RSI of VIX > 90 AND S&P > 200-day MA: buy S&P, exit when RSI < 65.
- Another variant: VIX range expansion (10-day new high) + reversal → buy equity index.

**India VIX:** NVIX (India VIX) is available. For our 0DTE straddle: when NVIX > 20 (elevated), IV crush on expiry is larger — fatter credits. When NVIX < 12, straddle premium is thin, making the trade less attractive relative to friction. **Suggest adding NVIX filter to straddle entry: only trade when NVIX >= 14 to ensure premium covers friction (Rs ~600 round trip = roughly 0.12% of notional at current levels).**

### 6.3 Regime Filter for the Breadth Rider

Kaufman (pp. 582–586) on market regime and trend filters:
- The best filters are those that shift the performance surface UP across most parameter combinations (Fig. 21.17b), not those that create a spike at one value.
- For equity index intraday systems, a regime filter (trending/sideways) helps. He uses KAMA (Kaufman Adaptive Moving Average) as a regime indicator: if KAMA slope is nearly flat, market is sideways; if steep, trending.

**For our breadth rider:** The breadth gate itself IS a regime filter (0.72 threshold = "market is in a broad uptrend today"). Kaufman's insight adds: we should also verify that the NIFTY itself is in an intermediate uptrend (e.g., above 20-day SMA or KAMA slope positive) before taking the signal. On days when breadth is high but the index is still below a meaningful resistance level, signals may fail.

---

## 7. The Option-C Decision: Futures vs. Option Debit (Kaufman Framework)

Kaufman does not discuss Indian-specific F&O but his friction analysis directly informs the debate:

### 7.1 Cost Threshold for Day Trades

Key formula (paraphrasing Ch.16 cost analysis):
`Minimum average trade profit > 2 × (commission + slippage) per side`

For our futures: total friction Rs 600 round trip. Average NIFTY trend-day move (low-ATR signal day) = ~60–80 points. At 1 lot: 60 × 25 = Rs 1,500 gross. After friction: Rs 900 net. That's a 60% friction ratio — very high.

For option debit (ATM call, 0.75% risk):
- Buy ATM call for Rs 150; sell at Rs 180 if NIFTY moves 60 pts. Net = Rs 30 × 75 = Rs 2,250 gross.
- Friction: Rs 600 round trip is less punishing vs. premium value.
- BUT: theta burns Rs 20–40/day. On slow days (our breadth rider fires on low-ATR days = slow trending days), theta erosion during the 2–4 hour hold is material.
- **Kaufman's conclusion in Ch.16:** "Trend trading has only a 1/3 chance of profitable trade... profitable trades are 2× as large as losers; net result is a loss equal to total transaction costs." For options, if the underlying edge is 1/3 win rate, the option debit amplifies losses on the 2/3 losers (theta + premium loss) while capping wins.

**Kaufman framework verdict on Option-C (b):** The backtest finding that "theta + friction kills (b)" is consistent with Kaufman's cost analysis. Paper trading should confirm but the fundamental math is against option debit for our low-ATR signal days.

---

## 8. Sparse Statistics and Small Trade Counts (Our Key Pain)

Kaufman (p. 740, Ch.21): "A system that has more trades with the same returns has a better chance of performing up to expectations."

He specifically notes: "A system with only a few trades is statistically unreliable" even if profits are good. However, he does NOT provide a specific minimum. He cites that a fast day-trader may generate 2,500 trades in 5 years, but a long-term trend system may have very few trades — and a long-term trend system IS acceptable if it has been tested across bull, bear, and sideways markets.

**For our 40–300 trade count:**
- 40 trades: Statistically weak. A profit factor of 1.53 (our 0DTE straddle holdout result) based on 40 trades has wide confidence intervals. The bootstrap PF CIs we compute are the right tool — Kaufman does not go deeper on this.
- 300 trades: Borderline acceptable per Kaufman's informal standard.
- **His suggestion:** "Paper trade until there are enough trades to compare the out-of-sample profile with the expectations defined by your tests." This validates our paper-trading approach.

Separately, he notes that deflated Sharpe / multiple-testing adjustments are needed when many parameters were tested. Our Bailey-LdP deflated Sharpe computation is methodologically ahead of Kaufman's coverage (he only discusses information ratio and sensitivity testing for robustness; he does not cover Bailey-LdP or Haircut Sharpe directly).

---

## 9. Contradictions and Cautions

### 9.1 Kaufman on ORB Profit Potential vs. Our Friction

Ch.16 shows ORB working well on S&P emini with ~$16 slippage total round-trip. Our friction is Rs 600 on NIFTY futures (futures STT post-2026 = 0.05% of notional per sell side = ~Rs 290; brokerage Rs 40–100; SEBI/stamp ~Rs 20). For a 60-point NIFTY move (Rs 1,500 per lot), friction is 40% of gross. Kaufman's profitable ORB examples had friction at 1–5% of typical move. **Our friction is 8–40× higher relative to trade size than Kaufman's test conditions.** All ORB filters shown in this book improve win rate from 55% to 70%+ — at our friction ratio, we need win rate ≥ 75% to be consistently profitable with even a 2:1 payoff ratio.

### 9.2 Walk-Forward Window Size

Kaufman (citing Ehlers) recommends 2-month IS / 1-week OOS for intraday. Our 8m/2m is used for daily-data-analog of the breadth rider (even though it fires once per day intraday). If we treat the signal as an intraday strategy, Ehlers' recommendation applies: tighter windows, more frequent re-optimization. We should be cautious about whether 8m windows capture regime changes (2022 vol vs. 2023-25 grind).

### 9.3 Kaufman on %-Above-VWAP vs. Standard Breadth

Kaufman uses standard advancing/declining counts as breadth. Our %-above-VWAP is a custom variant. Kaufman does not validate VWAP-based breadth specifically. The theoretical justification (VWAP as fair-value anchor for each stock) is sound but not empirically validated in this book. This is an area where our system is ahead of Kaufman's coverage — we need to do that validation ourselves via the strategy's backtest.

### 9.4 Position Sizing with 1-Lot Constraint

Kaufman's volatility-parity sizing requires fractional lots. At Rs 5L with NIFTY 1 lot minimum, the resulting lot count is often 0 or 1 — no granularity. This is a real limitation. His framework says to undercapitalize and accept this. We do.

### 9.5 Stop Loss on Mean-Reversion (Straddle)

Kaufman explicitly states that tight stops hurt mean-reversion strategies by cutting winning reversals short. Our 25% straddle stop is a delta-equivalent protection stop, not a tight price stop. However, if implemented as a hard stop on straddle P&L (not delta), it may be triggering on normal vol fluctuations before the mean-reversion payoff arrives at expiry. Worth verifying stop trigger frequency vs. final P&L correlation in the straddle backtest.

---

## 10. Confirmed Best Practices (What We Already Do Right)

1. **Plateau-over-spike selection:** Kaufman's "average results of neighbors" / 5-bar rolling average of optimization profits — identical to our plateau rule. Confirmed.
2. **Bootstrap PF confidence intervals:** Kaufman endorses the concept (without Bailey-LdP specifically). Our implementation is more rigorous.
3. **Holdout freeze:** Kaufman: "Once the final out-of-sample data is used, you're done." We keep holdouts frozen and use exactly once.
4. **ATR-based stops:** Standard throughout Kaufman, confirmed for both entries and position sizing.
5. **Walk-forward testing:** Kaufman's step-forward is our 8m/2m WF. Structure confirmed.
6. **Causal slippage model:** Kaufman endorses volatility-adjusted slippage: "Whatever slippage you decided to use, increase in proportion to increased daily volatility." Our ATR-scaled slippage is consistent.
7. **15:10/15:19 force-flat:** Kaufman: "Cancel new entries within 1 hour of close." Our force-flat at 15:10 (straddle) and 15:19:30 (breadth rider) is consistent.
8. **2% daily breaker:** Consistent with Kaufman's "know your exit conditions in advance" and per-trade risk limits.
9. **Paper trading before live:** Kaufman: "Don't start trading a new system without monitoring its performance first. After that, start with a small amount." Our paper-only phase is correctly sequenced.
10. **Pre-registered grids:** Prevents post-hoc optimization. Kaufman's testing integrity chapter validates this.

---

## 11. Actionable Next-Step Ideas from This Reading

| Priority | Action | Source in Kaufman |
|----------|--------|------------------|
| High | Add inside-day and NR4 compression filters to ORB backtest | Ch.16, Crabel/Raschke sections |
| High | Test McClellan-equivalent (continuous breadth) vs. single-snapshot 10:15 gate | Ch.12 |
| High | Add NVIX >= 14 filter to 0DTE straddle entry | Ch.20 (VIX systems) |
| Medium | Separate Thursday expiry days in breadth rider backtest | Ch.12 (volume/expiry) |
| Medium | Supplementary 60d/10d walk-forward for breadth rider | Ch.21 (Ehlers on intraday WF) |
| Medium | TRIN equivalent (per-stock volume on advancing/declining) for NIFTY 50 | Ch.12, Arms Index |
| Medium | Test mean-reversion ORB (fade large early moves) on low-ATR signal days | Ch.16, mean-reversion section |
| Low | KAMA regime filter (NIFTY above KAMA = trend confirmed) for breadth rider | Ch.17, KAMA |
| Low | Day-of-week flag: backtest breadth rider separately on Mondays vs. other days | Ch.15 weekday patterns |

---

*Generated from selective reading: Ch.12, Ch.15 (selected), Ch.16, Ch.20 (partial), Ch.21, Ch.22, Ch.23. Not read: Ch.1–11, Ch.13–14, Ch.18–19, Ch.24. Pages cited are approximate based on ToC line offsets in the plain-text extraction.*
