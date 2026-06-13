# Trading and Exchanges: Market Microstructure for Practitioners
### Larry Harris (Oxford, 2003) — Pass-2 Distillation
*Algo-trader project: NIFTY/BANKNIFTY intraday, Rs5L, paper-only*
*Focus: fill/slippage realism, spread decomposition, adverse selection, auction mechanics, options microstructure*

---

## Sections Read

- Ch 2 (§2.7): Options market trade walkthrough
- Ch 4 (§4.3–4.8): Order types — market, limit, stop, MIT, tick-sensitive
- Ch 5 (§5.2–5.3): Call vs continuous markets; quote-driven vs order-driven
- Ch 6 (§6.3–6.4): Single-price auction and discriminatory-price continuous markets
- Ch 13 (§13.1–13.9): Dealers — quotations, inventory risk, adverse selection responses
- Ch 14 (§14.1–14.6): Bid/ask spread decomposition (transaction cost + adverse selection)
- Ch 15 (§15.2): Block trading and the four block-trader problems
- Ch 18 (§18.1–18.5): Buy-side traders — market vs limit order choice, exposure costs
- Ch 19 (§19.1–19.5): Liquidity dimensions (immediacy, width, depth) and supplier types
- Ch 20 (§20.1–20.3): Fundamental vs transitory volatility; Roll's covariance estimator
- Ch 21 (§21.2–21.5): Transaction cost measurement — effective spread, realized spread, VWAP, implementation shortfall
- Ch 23 (§23.4–23.5): Index markets, package trading, index derivative products
- Ch 24 (§24.5.7, 24.5.9): Specialist opening auction and dealer profit mechanics
- Ch 28 (headers): Bubbles, crashes, circuit breakers (skimmed)

---

## Part I — Order Types and Execution Mechanics

### Ch 4: Order Types

**Market orders** demand immediacy; the buyer pays the ask, the seller receives the bid. The cost per trade is half the bid/ask spread. Harris calls the spread "the price of immediacy." [§4.3.1]

**Execution price uncertainty** is the primary risk of market orders: quotes can change between order submission and fill. For large orders the price concession needed to attract the other side adds further cost beyond the quoted spread. [§4.3.4]

**Limit orders are free trading options granted to the market.** A standing sell limit order is a call option; a standing buy limit order is a put option; the strike is the limit price. Whoever submits a market order may exercise the option at any time. The limit order writer receives no premium — only the possibility of a better fill price. This asymmetry is fundamental: volatile markets make these free options more valuable to takers, so limit order writers post wider spreads in volatile conditions. [§4.4.2]

**Stop orders accelerate price changes.** When a stop is triggered, the resulting market order demands liquidity precisely when liquidity is thinnest. Stops add momentum — they buy when prices rise and sell when prices fall. [§4.5.2]

**Market-if-touched (MIT)** orders become market orders when price touches a pre-set level, but they trigger in the *contrarian* direction (buy when price falls to touch level). They stabilize prices but are uncommon. [§4.6]

---

## Part II — Auction Mechanics and Market Structure

### Ch 5–6: Call vs Continuous Markets; Pricing Rules

**Opening call auctions** (single-price) concentrate all accumulated orders and find the volume-maximizing clearing price. All matched orders trade at a single price. Any excess supply or demand at the clearing price is handled by secondary precedence rules (typically time). [§6.3]

**Continuous markets** use the discriminatory pricing rule: each trade executes at the limit price of the resting order. This gives market order traders better average prices than a call would in illiquid conditions, but at the cost of giving up the timing option to standing limit order traders. [§6.4]

**The specialist's opening option (NYSE):** The specialist sees all pre-open order flow, then decides how much to participate. This look-back timing option allows specialists to choose the clearing price after all others have committed. In imbalanced opens, specialists supply liquidity on the weak side — and prices often reverse afterward. [§24.5.7]

**Price clustering:** Traders use round numbers disproportionately. Clever limit order traders place orders just ahead of or behind round numbers to exploit this clustering. [§5.2.2]

---

## Part III — Spread Decomposition and Dealers

### Ch 13–14: Spread Components, Adverse Selection, Equilibrium Spreads

**The bid/ask spread has two components:**

1. **Transaction cost component** (also: transitory spread component): compensates dealers for normal costs of doing business — overhead, inventory financing, risk bearing. Causes bid/ask bounce (prices oscillating between bid and ask) which is transitory and mean-reverting.

2. **Adverse selection component** (also: permanent spread component): compensates dealers for losses to informed traders. When an informed trader buys, the dealer loses the mispricing; to recover this from uninformed traders, the dealer widens the spread. Price changes from adverse selection are permanent — they do not systematically reverse. [§14.2]

**The Glosten-Milgrom theorem** (information perspective = accounting perspective): The adverse selection component equals the probability of trading with an informed trader × expected mispricing. Both the information-update derivation and the accounting loss derivation yield identical estimates. [§14.2.3]

**Empirical finding:** In most markets the adverse selection component exceeds the transaction cost component. This matters: if you think you are paying only the bid/ask bounce, you are underestimating your true cost. [§14.2.4]

**Uninformed traders lose regardless of order type** (Harris calls this the most important lesson in the book):
- With *limit orders*: informed traders cherry-pick the stale limit orders when they become advantageous; when not advantageous, the limit order fails to fill.
- With *market orders*: the dealer widens the spread to recover from informed traders; the uninformed market order trader pays this wider spread.
The message: uninformed traders lose simply because they trade. To minimize losses, minimize trading. [§14.3]

**Equilibrium spread determinants** — the spread settles where traders are indifferent between limit and market order strategies. Key factors that widen spreads:
- Information asymmetry (informed traders in the market)
- Volatility (timing option on standing limit orders is more valuable)
- Slow limit order cancellation (gives market orders more timing option value)
- Infrequent trading (fixed costs spread over fewer trades)
- Risk aversion of limit order traders

**Index futures/contracts have narrow spreads** because few traders have material information advantage about macroeconomic variables. Diversified index portfolios have less adverse selection risk than individual stocks. [§14.6.2.1]

**Dealers respond to informed flow** by immediately adjusting quotes: lower both bid and ask if they sold to an informed buyer; raise both if they bought from an informed seller. They also rapidly try to lay off the position before prices move. A realized spread smaller than the quoted spread signals adverse selection losses. [§13.8.1]

**Dealer inventory risk has two types:**
1. Diversifiable risk — random price moves uncorrelated with inventory position; averages to zero; can be diversified across many instruments.
2. Adverse selection risk — price moves *inversely correlated* with inventory position (informed traders made the dealer's inventory go the wrong direction); this is not diversifiable and is the more dangerous type. [§13.7]

---

## Part IV — Liquidity

### Ch 19: Liquidity Dimensions

**Liquidity = immediacy × width × depth** — three correlated but distinct dimensions:
- *Immediacy*: how fast you can trade a given size at a given cost
- *Width*: the bid/ask spread; cost of a small immediate trade
- *Depth*: how much size can be traded at a given spread; width and depth are mathematical duals [§19.2]

**Five types of liquidity suppliers and their niches:**
1. Market makers — immediacy to small anonymous traders; narrow spreads; no fundamental value advantage
2. Block dealers — depth to large uninformed clients; they know their clients well
3. Value traders — ultimate depth suppliers; confident in value estimates; trade against momentum
4. Precommitted traders — supply immediacy at narrow spreads because they intend to trade anyway (e.g., index rebalancers)
5. Arbitrageurs — move liquidity across markets; ensure depth is accessible wherever you trade [§19.3]

**Liquidity dries up when traders are absent** — near holidays, in thinly traded strikes, around lunch. Block trading is hardest to arrange when responsive traders are not paying attention. [§19.1.2]

**Value traders make markets resilient.** After an uninformed price impact, value traders gradually enter on the other side, pushing prices back. In the absence of value traders, transitory volatility persists. [§19.5 example]

---

## Part V — Volatility

### Ch 20: Fundamental vs Transitory Volatility

**Two types of volatility:**
1. *Fundamental volatility*: price changes due to unexpected changes in instrument value. Random walk process — does not systematically revert. Generated by new information, informed trading, or news.
2. *Transitory volatility*: price changes due to uninformed demand for immediacy. Prices diverge from fundamental value then revert. Measured by negative serial correlation in price changes (bid/ask bounce is the simplest form; large uninformed orders create longer-horizon transitory volatility). [§20.1–20.2]

**Options traders must distinguish the two.** Option values depend on total volatility, but the components have very different mean-reversion properties. Transitory volatility in the underlying inflates short-term realized vol above long-term fundamental vol. A short straddle at elevated IV around expiry benefits if the vol was mostly transitory. A wrong-direction gap open (fundamental shock) is not mean-reverting.

**Perishable instruments** (like 0DTE options) are the extreme case of high-storage-cost assets: any surplus of supply (option premium) collapses to zero rapidly as expiry approaches. The theta/decay is the option equivalent of spoilage. [§20.1.3 analogy]

**Roll's serial covariance spread estimator** extracts the transitory spread component from price change serial covariance: Spread ≈ 2×sqrt(−Cov(ΔP_t, ΔP_{t-1})). This is an empirically useful lower-bound estimate when tick data is unavailable. [§20.3, §27.5]

---

## Part VI — Transaction Cost Measurement

### Ch 21: Measuring Costs

**Three types of transaction costs:**
1. Explicit: commissions, taxes, exchange fees
2. Implicit: spread cost, price impact (market impact of demand)
3. Missed trade opportunity: cost of limit orders that did not fill [§21.1]

**Benchmark methods:**
- *Effective spread*: 2 × |trade_price − midpoint at time of trade|. Measures spread paid. Unbiased for small single-trade retail orders. Cannot detect cumulative impact of split orders.
- *Realized spread*: 2 × |trade_price − midpoint 5/15/60 min post trade|. Measures dealer's actual profit; the difference between effective and realized spread is the adverse selection loss.
- *VWAP benchmark*: compares trade price to day's volume-weighted average. Commonly gamed by brokers; zero-cost if you were the only participant. Poor for large orders.
- *Implementation shortfall* (Perold): trade_price − midpoint at *decision time*. Best overall estimator; immune to split-order bias and gaming; requires knowing decision time. [§21.3]

**Implementation shortfall captures the full cost of a trading decision**, including market impact of all legs. For our purpose: the decision-time midpoint of the straddle is the sum of the bid/ask midpoints of the call and put at 09:20. Actual fill prices above that midpoint are implementation cost.

**VWAP is unreliable for strategy evaluation** when the strategy itself influences prices (a straddle sale at 09:20 may move IV). [§21.4.4.1]

**All estimates are noisy on single trades.** Average over many transactions. [§21.4.5]

---

## Part VII — Index Products and Expiry Mechanics

### Ch 23: Index Markets

**Index futures and options have structurally lower adverse-selection spreads** because no trader has material private information about broad macro variables. The information playing field is more level; dealers quote tighter. This is a structural advantage for trading NIFTY/BANKNIFTY index derivatives versus single-stock derivatives.

**Index product liquidity concentrates.** Volume in index products is much higher than in the underlying because traders no longer need to replicate the portfolio. NIFTY weekly options benefit from this concentration effect — liquidity in front-week ATM strikes is typically higher than in individual NIFTY-50 names.

**Rollover transaction costs** matter for futures strategies. When index futures expire, traders roll into the next contract; the roll spread is a recurring friction. Our engine correctly handles point-in-time lots and needs to account for this at expiry. [§23.5]

---

## Application Analysis for Our System

### Fill Realism: Next-Bar-Open Assumption

**Harris's framework makes the following clear about "next bar open" fills:**

1. The next-bar-open price is a *call auction clearing price in miniature*. Pre-bar orders execute at the first traded price when the bar opens. In Indian markets, the opening is set by an opening call auction at 09:00–09:07 for equities, and 09:15 for NSE F&O (pre-open session). The 09:20 bar-open is post-auction continuous trading.

2. **The 09:20 straddle sale occurs in continuous trading.** The "next-bar-open" fill assumes execution at the first 1-minute bar's opening price. This is a *market order at the open*, which Harris classifies as paying the spread for immediacy. For a straddle (two legs), we pay half the spread twice — once on the call and once on the put. With Rs5L capital and 1 lot, the absolute spread cost is small, but the relative cost per trade is material.

3. **Our ATR-fraction slippage model** is a reasonable proxy for: (a) the transaction cost spread component (bid/ask bounce), and (b) small market impact. Harris shows that for small liquid instruments, the cost is close to half the bid/ask spread. NSE F&O is a centralised order-driven market; the ATR-fraction approach should be calibrated to the observed NIFTY options spread at open (~0.5–1.0% of premium in front-week ATM strikes, widening to 2–5% in deep OTM or thin strikes).

4. **Systematic bias risk in next-bar-open:** If our strategy entry signals are triggered by the prior bar's close, and the next bar opens with a gap, we experience *execution price uncertainty*. This is the key argument for using next-bar-open (not signal-bar-close) fills — it is the more conservative and causally correct model. Harris confirms: market order traders must accept the price at the time of execution.

### Stop Calibration for the 0DTE Short Straddle

Our 25% straddle-value stop fires on 57% of expiry days. Harris provides a microstructure lens:

1. **Stop orders accelerate price moves.** If many straddle sellers have identical 25% stops, a coordinated gamma-squeeze or momentum move triggers clustered stop orders simultaneously, demanding liquidity when it is thinnest. The stop's execution price will be materially worse than the trigger level. This is the *execution price uncertainty* of stop orders compounded by *adverse selection* — informed buyers in the option market know straddle-seller stops exist at round levels.

2. **The realized stop price is worse than the theoretical stop price.** Harris explicitly notes that stops execute at the best price then available, which may be far from the stop trigger price if the market is moving fast. Our 25% trigger will have a realized cost of 25% + slippage. The higher the stop-day frequency (57%), the more we should assume the average realized cost per stop event is higher than modeled.

3. **Calibration implication:** Consider whether a *stop limit* (not a stop market) on the weaker leg reduces execution slippage at the cost of occasionally not filling. For a short straddle stop, the operationally sensible stop is: when straddle value > 1.25× entry premium, close both legs at market. Harris's framework suggests the market order execution cost on a stop day is non-trivial. Budget an extra 0.5–1.0% of notional for realized vs. trigger stop slippage.

4. **Why 57% stop rate may indicate a misplaced stop:** Harris's equilibrium spread model: when uninformed traders lose simply by trading, position limits that minimize the number of entries (fewer, higher-quality trades) dominate. A 57% stop rate means the strategy is entering on days when IV is so bid-up that the collected premium is insufficient to absorb the typical intraday move. This is consistent with the adverse selection framework: the days we collect the most premium are the days informed traders (who know a big move is coming) are on the other side.

### Straddle Entry: Limit vs Market

For the 09:20 straddle entry:

- **Market order** (our current approach via next-bar-open): guarantees fill, pays the spread, subject to opening price uncertainty from overnight events.
- **Limit order near the bid for the buyer/offer for the seller**: Harris shows limit order strategies get better prices on average but risk non-execution. For a short straddle (we are the seller), we *receive* the bid for both legs. Placing limit sells slightly above the bid improves received premium but risks partial fill if the market moves against us.

**Practical recommendation:** For the short straddle entry, a *marketable limit order* (limit price = best bid − tick) offers price protection against extreme opening moves while almost guaranteeing fill in liquid NIFTY options. Track effective spread (received_premium − mid at entry) to measure implementation quality.

### Option Spread Width on Expiry Day

Harris's cross-sectional spread predictions [§14.6]:

- **Expiry day spread dynamics:** On expiry, deep OTM options become nearly worthless and their relative spreads explode (5–20%+ of premium). ATM options near expiry maintain reasonable absolute spreads but the relative spread (spread/mid-price) widens as the mid-price itself falls intraday. By 14:00 on expiry day, a strike that was ATM at 09:20 may be 100 points away, becoming OTM with a wide relative spread.
- **Implication for stop execution:** If the stop triggers on a leg that has moved OTM, the bid/ask spread on that leg will be wide. Actual closing cost may be above the theoretical stop-level mark. Our backtest needs to model widening spreads for OTM legs, not just ATR slippage on the still-ATM leg.

- **Volatility and spread width:** High VIX / pre-announcement periods produce wider spreads (the timing option on limit orders is more valuable; market makers widen quotes to compensate). Our strategy's entry on high-IV days implicitly faces higher transaction costs as well as higher collected premium. The net is uncertain — but the Harris framework says the adverse selection component is highest exactly when IV is highest (informed traders are more active on high-IV days).

### Debit Option Leg (Option-C comparison)

For the breadth-gated trend rider (ATM index debit call/put buy):

- **Buy side pays the ask.** In our paper comparison, the debit option leg buys ATM options at open. Harris: market order buyers pay the ask = mid + half spread. With index option spreads at 0.5–1% for front-week ATM, this is a fixed drag on every entry.
- **Theta compound with spread:** The debit buyer not only pays theta decay every day the trade is on, but also paid the spread at entry and will pay the spread at exit. Round-trip spread cost on a debit option is ≈ full spread (not half). Combined with theta, the debit leg's friction is systematically higher than the futures leg — Harris's framework confirms the backtest prediction.
- **Limit order strategy for the debit leg:** If we are not committed to entering immediately, placing a limit order at the mid-price (splitting the spread) could save 0.25–0.50% per entry. With only 40–100 trades per year, the aggregate saving is modest but worth programming into the paper loop as a comparison.

---

## Methodology Upgrades

### 1. Model Two-Component Slippage Explicitly
Current: single ATR-fraction slippage applied uniformly.
Upgrade: separate (a) half-spread cost (function of bid/ask width; proxy = recent realized spread from tick data, or 0.15–0.30% of notional for NIFTY F&O ATM) from (b) market impact (ATR-fraction, scales with size). Apply both additively. This maps directly to Harris's transaction cost + adverse selection decomposition.

### 2. Calibrate Stop Slippage Separately from Entry Slippage
Stop exits on bad days are market orders into one-sided flow. Harris shows realized stop prices are materially worse than trigger prices during momentum moves. Add a separate stop_slippage parameter (e.g., +1.5× the base slippage multiplier) to model this cost asymmetry. Test sensitivity of straddle PF to this parameter.

### 3. Expiry-Day Spread Widening Model for OTM Legs
When a straddle leg moves OTM by >0.5×ATR by stop-time, widen the slippage multiplier for that leg. A simple rule: if strike is now >50 points OTM on NIFTY, increase slippage on that leg by 1.5–2×. This captures the increasing relative spread of OTM options.

### 4. Implementation Shortfall Tracking in Paper Loop
Record for every paper trade: (a) decision-time mid-price, (b) actual fill price, (c) difference = implementation shortfall per leg. Aggregate by strategy. This is the cleanest way to measure whether our slippage model is realistic. Harris: averaging over many trades gives reliable cost estimates; single-trade estimates are too noisy.

### 5. Market Open as Single-Price Auction — Pre-Open Data
The NSE pre-open session (09:00–09:07) is a call market. The indicative price visible during pre-open is the equilibrium of the auction. Harris shows specialists/market makers at the open see the full order book and can choose clearing prices. In our context: the 09:20 bar-open may gap from the 09:15 indicative if the pre-open order book was thin. Capturing the pre-open indicative price provides a pre-trade benchmark for implementation shortfall calculation.

### 6. Volume-Time Slippage Scaling
Harris: price impact scales with order size relative to available liquidity. For 1-lot NIFTY options, our impact is negligible. But a regime check: if NIFTY ATM open-interest on expiry is below some threshold (e.g., below 5,000 contracts in the front-month), widen the slippage assumption. This guards against low-liquidity expiry dates.

---

## Sizing and Risk Insights

- **Harris's zero-sum framework:** At 1-lot scale, we are always a small uninformed-looking trader from the market maker's perspective. Market makers will fill our orders at the ask without adverse selection concerns. This is good news — we are unlikely to be systematically front-run. The bad news is we are the counterparty to market makers who profit from our spread payments.

- **Stop orders and lumpy sizing:** At 1-lot scale, stops are binary (full close vs. remain). Harris notes stop orders are especially dangerous in thin markets because they add to one-sided flow. Intraday NIFTY expiry options near the ATM strike are typically liquid enough that a 1-lot stop is not price-impacting. But the *aggregate* of all retail straddle sellers stopping at the same 25% level could cluster and move the market.

- **Patient vs impatient capital:** Harris repeatedly contrasts patient traders (use limit orders, supply liquidity, earn the spread) against impatient traders (use market orders, pay the spread). Our short straddle strategy is structurally a *patient* strategy — we are the liquidity supplier receiving premium. But our *management* of the position (stops, flat at 15:10) involves impatient market orders. The cost asymmetry: we collected premium as a patient supplier but pay spread as an impatient closer. Minimize the number of stop events to minimize this cost asymmetry.

- **Daily PF at Rs5L:** With ~Rs600 round-trip cost on equity intraday and F&O STT at 0.05% post-2026, total explicit costs per straddle trade are approximately: 2 legs × (brokerage + STT + exchange fees) ≈ Rs800–1,200. On a typical NIFTY weekly straddle collecting Rs150–250 premium per lot per side, explicit costs alone are ~0.5–1.5% of notional collected. Combined with implicit costs (spread), the total friction is material at this scale.

---

## Execution Realism Insights

1. **Next-bar-open is a market order at the first traded price.** Correct. Pays the spread. In continuous trading post-09:15, this is the ask for buys and bid for sells (for the option seller: receives the bid). Our next-bar-open model correctly handles direction.

2. **The 5-minute bar open is not guaranteed at the OHLC open.** NIFTY options are thinly traded in the first 1 second of a new 5-min bar if the prior bar closed at an extreme. The first trade establishing the bar's open price may be at a price worse than the midpoint. In practice, slippage within the first bar is captured by our ATR-fraction model if the ATR calibration uses the opening hour period.

3. **For the 15:10 flat-exit:** the market becomes thin near close on expiry days as open interest compresses. Harris's model predicts spreads widen as participation drops. Our 15:19:30 force-flat uses a market order into potentially declining liquidity. Widen slippage for the close-leg exit by 1.2× the base rate.

4. **Synthetic BS paths between real entry/exit premiums:** Our methodology of generating synthetic intraday option price paths using BS pricing (from backtested option chains) correctly captures the option's theoretical value trajectory, but the *realized* bid/ask spread around each path point is not modeled. Harris's framework: the spread is the cost of deviating from the theoretical midpoint. To stress-test: apply 0.5× spread at every simulated intraday check point to see how straddle value would differ if we used midpoints vs. tradeable prices.

5. **Adverse selection at entry on high-IV days:** NIFTY options market makers widen spreads on pre-announcement days (budget, RBI policy, US Fed) because informed flow is more likely. We enter the short straddle on every Thursday expiry day without an IV filter. Harris would predict that entry on days when IV is unusually bid up (informed buyers present) is entry against adverse selection. An IV percentile filter (e.g., avoid entry when IV > 85th pct of trailing 60 expiry days) could reduce adverse selection exposure while giving up some premium collection days.

---

## What We Already Do Right

- Causal ATR slippage (applied at next bar, not signal bar) correctly models execution price uncertainty.
- Next-bar-open fills avoid look-ahead bias and correctly model that market orders execute at prevailing prices.
- Explicit Rs600 round-trip cost for equity intraday and 0.05% STT for F&O post-2026 correctly separate the transaction cost component.
- 15:19:30 force-flat prevents holding into auction close uncertainty.
- 2% daily loss breaker is structurally sound — Harris would classify the 2% limit as a pre-committed trader constraint that reduces the cost of holding positions when information flow turns adverse.
- Walk-forward 8m/2m structure with single-shot holdouts avoids the mining bias that Harris's discussion of transaction cost gaming warns against (§21.4.4.4 analogy).
- Bootstrap PF confidence intervals correctly account for small-N strategy statistics — Harris's point that single-trade cost estimates are noisy applies equally to single-strategy-window estimates.

---

## Contradictions and Cautions

1. **"Minimize trading" advice conflicts with systematic trading.** Harris's most important lesson is that uninformed traders lose simply by trading. A systematic algo is an uninformed trader by definition (no private fundamental information). The implication is not to stop trading, but to (a) confirm positive expected value before each strategy deployment, (b) minimize unnecessary in-strategy trading (e.g., avoid re-entry after a stopped straddle on the same expiry day), and (c) keep explicit + implicit costs below the strategy's gross edge.

2. **Institutional-only concepts:** Harris's block trading chapter (§15), specialist privileges (§24), and payment-for-order-flow discussions (§25) describe mechanisms available only to large institutional participants. These are not actionable at Rs5L / 1-lot scale. Skipped for distillation.

3. **Book is US-market-centric (2003).** NSE's F&O market structure differs: it is fully electronic, order-driven (no specialists), with a pre-open call auction. The core microstructure principles (spread decomposition, adverse selection, timing options) are universal. The specific institutional mechanisms (NYSE specialist, NASDAQ wholesalers) do not apply.

4. **Spread is not the only cost.** Harris focuses heavily on the bid/ask spread, but for NSE F&O the STT asymmetry is equally significant: post-2026, sell-side STT of 0.05% applies on option premium (not notional), making the short straddle exit (which closes both short legs by buying) carry STT only on the buy-close transactions. This is already modeled in our engine but Harris's framework does not cover it.

5. **Informed-trader framing for crypto/equity vs. options:** Harris's adverse selection model was developed for equities. In index options, the dominant informed flow is from delta hedgers and vol traders, not fundamental value traders. The adverse selection spread component in NIFTY options is driven by realized-vs-implied vol mismatch (short gamma risk) not by fundamental company information. The mechanism is the same (limit order traders get picked off when wrong), but the trigger is different.

6. **Stop manipulation risk (§11.4):** Harris discusses manipulation of stop orders — sophisticated traders push prices to known stop levels to trigger stops, then reverse. For our 25% straddle stop, if this level is predictable (e.g., widely known as the retail straddle-seller stop), intraday gamma-squeeze strategies by large participants could systematically trigger our stops before reversal. This is a real risk on expiry day. Mitigation: make the stop level less predictable (range 22–28% per strategy, randomized within our risk bounds) or use a time-of-day condition to avoid the first-hour volatility window.

---

*Distilled from targeted reads of ~8,000 lines. All section references are to the Harris (2003) first edition chapter.section notation.*
