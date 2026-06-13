# O'Hara — Market Microstructure Theory (1995)
## Distillation for Intraday Algo: NIFTY/BANKNIFTY, Rs5L, Paper-Only
### Pass-2 Reading (Chs 1, 3, 4, 8 only; pass-1 already applied)

---

## 1. Why This Book and What to Extract

O'Hara lays the mathematical foundations of *how prices form* when traders
have heterogeneous information. For our project the first-order questions are:
(a) why the open is informationally special, (b) why spreads exist and how
large they are, (c) what informed order flow looks like vs noise order flow,
and (d) how liquidity and index-derivative markets interact. Everything below
maps to concrete backtest or execution implications.

---

## 2. Chapter 1 — Markets and Market Making

### 2.1 Call Auction vs Continuous Auction (pp. 10-11)

The NYSE opens with a **call auction**: all pre-market orders (market-on-open
and limit) accumulate, the specialist sets a single market-clearing price, and
every eligible order trades at that one price. Only after the open does the
market revert to a continuous quote-driven mechanism.

**Why this matters for us (09:15 NSE pre-open):**  
NSE's Ihab pre-open session (09:00–09:15) is structurally identical to the
NYSE call auction. Orders pile in with no continuous price; the exchange
computer finds a single equilibrium price and opens trading there. This means:

- The 09:15 opening print is a *batched-belief summary*, not a sequential
  price discovery outcome. It is more informationally efficient than any
  individual trade in the first seconds of continuous trading.
- The specialist/exchange can delay the open (provisionally announce a price)
  if order imbalance is too large. When NSE shows a large pre-open imbalance,
  slippage on the first 09:15 fill will be worse than our ATR-based model
  assumes.
- **Mechanism to implement:** Do not treat the 09:15 open bar as a "normal"
  bar. For the straddle, the 09:20 sell entry is in continuous-auction mode
  (5 minutes post-open). This is correct — the call-auction price is already
  settled by then. For breadth-gated strategies that use the 09:15 bar, treat
  the O/H/L as a single-point estimate (call-auction output), not a 5-minute
  OHLC from sequential flow.

### 2.2 Demsetz: Price of Immediacy and the Spread

Spread = cost of immediacy. Even without information asymmetry, buyers who
want to transact *now* pay a premium over those who can wait. Two supply and
demand curves exist at any moment — one for immediate traders, one for
patient traders.

**Implication for our Rs600 equity round-trip cost:**  
The Rs600 figure already encompasses the bid-ask and brokerage. For F&O the
quoted spread on ATM NIFTY options is the dominant cost and at 1-lot scale
(75 contracts) that spread is roughly Rs5-15 per option depending on time
of day. Our synthetic BS path fills at midpoint — we underestimate actual
cost when the real fill will be at or near the offer. This is a consistent
upward bias in our PF estimates for the debit-spread Option-C leg.

---

## 3. Chapter 3 — Information-Based Models

### 3.1 Bagehot / Copeland-Galai: The Informed-Trader Option (pp. 54-57)

The dealer writes a free option to every incoming order. An informed trader
simply exercises the in-the-money option; an uninformed trader may or may
not trade. The spread compensates the dealer for expected losses to informed
traders, paid for by gains from uninformed traders.

**Option framing for our straddle:**  
When we sell a 0DTE straddle at 09:20, the option-pricing microstructure
analog is: we are the market maker. Every retail punter who exercises against
us is a "noise trader"; every institution with a directional view is the
"informed trader." The spread we earn (IV vs realised vol) is analogous to
the dealer's bid-ask. This is not just metaphor — it directly implies:

1. Our short straddle PF must be positive *after* the expected loss to
   informed flow. On NSE expiry day, large institutions routinely hedge
   directional books near expiry, which constitutes informed flow.
2. The 25% straddle-value stop is our "market maker's max inventory loss"
   ceiling. Whether 25% is well-calibrated is a separate question (see §6
   below).

### 3.2 Glosten-Milgrom (GM) Sequential Trade Model (pp. 58-66)

Prices adjust via Bayesian updating after each trade. After a buy order the
market maker raises bid and ask; after a sell he lowers them. The spread at
any moment is:

    Ask = E[V | trader wants to buy from me]
    Bid  = E[V | trader wants to sell to me]

So prices are "regret-free" conditional expectations. Key results:

- **Prices follow a Martingale** w.r.t. the market maker's information set.
  Expected return from trade-to-trade is zero by construction.
- **Prices converge to full-information value** in the limit — but this can
  be slow and depends on the fraction of informed traders.
- **Implication for our OHLC bars:** A 5-minute bar contains many sequential
  trades. The close of the bar already reflects the market maker's posterior.
  Our 5-min bar close is a nearly fully-updated belief, not a stale signal.
  This supports using bar-close prices rather than bar-open prices for signal
  construction.

### 3.3 Easley-O'Hara (EO) Trade-Size Model (pp. 66-72)

Key innovation: large trades carry more information than small trades.
Market maker sets *zero spread for small trades* (no informed interest there)
but a positive spread for large trades (information-based adverse selection).

**Separating equilibrium condition:**  
If the ratio of large to small trade size exceeds a threshold function of
informed-trading probability (ap) and uninformed large-trade fraction (X),
the market is in a separating equilibrium where informed trade only large and
the market maker sets no spread on small sizes.

**Two-type order flow uncertainty:**  
EO introduce a dual uncertainty — the market maker does not know:
1. Whether an information event has occurred today.
2. If yes, whether it is good or bad news.

This means prices are NOT Markov — the effect of a trade depends on the
*sequence* of past trades, not just current price. After a block sale followed
by a small trade, price partially recovers because the small trade signals that
maybe no information event occurred.

**Mechanism for our engine:**  
The non-Markov result is the theoretical basis for momentum/reversal
microstructure strategies. For our 1-min/5-min engine it means:
- The first large-lot print of the day (NIFTY futures) carries maximum
  information content. Subsequent prints of the same sign are less
  informative (market maker has already partially updated).
- If our breadth-gated signal fires, the first big directional bar is
  informationally distinct. This is a theoretical backing for the 10:15
  "breadth confirmation at 1 hour in" timing — we are waiting for the
  market maker's posterior to settle, not just for volume.
- Price-non-Markov property: two consecutive large sells are more bearish
  than a large sell followed by a small sell of equal total size. We do not
  currently exploit this sequence structure.

### 3.4 Implication: IV as Signal of Informed Probability

The Copeland-Galai result (footnote 3.3, pp. 56-57) connects the dealer's
spread to an option pricing framework: bid-ask spread = straddle value
written by dealer against informed traders. If IV is high, the "option premium
paid" by the market maker is large, meaning the market expects a high fraction
of informed flow. **High IV before 09:20 is a signal of high adverse selection
risk for our short straddle.** This is not just a vol-mean-reversion argument;
it is structural.

---

## 4. Chapter 4 — Strategic Trader Models I: Kyle Framework

### 4.1 Kyle Lambda — Price Impact as Liquidity Measure (pp. 91-99)

Kyle's central result: in a batch-clearing market,

    Price = prior + λ · (net order flow)

where λ = (σ_v / σ_u) · (1/2). Here σ_v is the volatility of the asset's
true value, σ_u is the volatility of uninformed (noise) order flow.

**λ is the price impact per unit of order flow.** It is the inverse of
liquidity: higher λ means less liquid, greater impact per lot.

Key properties:
- λ is decreasing in uninformed volume — more noise = lower impact per unit.
- Informed trader sizes his order proportional to (v - prior) and inversely
  proportional to λ. He hides in the noise flow.
- In a single auction, posterior variance = prior variance / 2. The informed
  trader releases exactly half his information in one round.
- If uninformed volume doubles, the informed trader doubles his order size.
  Net effect on λ: zero. Prices do not change on an *ex ante* basis.

**What this tells us about NIFTY options open:**  
NIFTY ATM options have a single dominant informed-flow event daily: the
expiry settlement. Informed traders (hedgers, large DII desks) know their
directional exposure. Their optimal strategy is to hide in the noise of retail
flow. The model predicts they will size their orders proportionally to how
much retail noise they can hide in.

- In high-volume NSE sessions (large noise flow), informed traders can take
  bigger positions without moving IV drastically. Their hidden orders are
  proportional to noise volume.
- On low-volume expiry days, noise flow is thin and λ is higher. Any
  institutional hedge is more visible and moves the market more. Our 25%
  straddle stop is more likely to fire on low-volume expiry days not because
  real volatility is higher but because *price impact* is higher.

### 4.2 Informed Trader Strategy: "Information Monopolist"

The informed trader is rational about price impact. He does not dump all his
position at once because doing so would move prices too far too fast. He
bleeds his information slowly. This implies:

- Trend-following on the open will *front-run the eventual information
  revelation* but the information is released gradually. This supports our
  trend-rider model that waits until 10:15 for breadth confirmation: we are
  waiting for the informed trader to have revealed enough information.
- The informed trader's order intensity is NOT front-loaded at the open. He
  is indifferent between trading periods in the continuous Kyle model. Volume
  and price impact do not peak at the open under pure information-monopolist
  behavior.

### 4.3 Admati-Pfleiderer: Trade Concentration (Ch.5, but key for timing)

Discretionary uninformed traders cluster their trades at times when others
are clustering (positive feedback in timing). This creates endogenous "thick"
trading periods. With short-lived information (expires in one period), the
equilibrium involves concentrated trading.

**Result: Volume peaks are endogenous.** Both informed and uninformed cluster
in the same periods. Uninformed do it to reduce their per-unit adverse
selection cost (they are safer when noise is thicker). Informed do it to hide
better. This explains empirical U-shaped intraday volume patterns.

**For us:** The NSE open (09:15-09:30) is peak concentration. The volume
is high, spreads are wide, and price movement per unit of real news is
large. Our engine's 09:20 straddle entry is *deliberately* in this high-noise
window, which is correct for a short-vol strategy: we collect the elevated
spread.

---

## 5. Chapter 8 — Liquidity and the Relationships Between Markets

### 5.1 Grossman-Miller: Liquidity as Price of Immediacy (pp. 216-222)

Speculators absorb order imbalances for a return. The return is:
    r_speculator = (inventory · variance_of_return)

Without risk aversion, price movements = 0 (perfectly liquid). With risk
aversion and a fixed number M of speculators:

    Liquidity shock absorbed = M / (1+M) of total imbalance

More speculators → more liquidity → smaller price impact from order flow.

**For our straddle:**  
When few speculators are in the market (low open interest, thin order book),
a single large institutional hedge causes a large price dislocation. This is
the regime where our straddle stop fires most often. The 57% stop-fire rate
suggests that either (a) our stop is too tight, or (b) thin liquidity amplifies
normal noise into stop-triggering moves. The G-M model says: on days where
market maker count is low (e.g., expiry holidays, reduced DII participation),
normal liquidity shocks can cause disproportionate moves.

**Actionable:** Log NSE total-market open interest on each expiry morning.
If OI is low versus the trailing 4-week average, treat that expiry day as a
"high λ, low M" day and either skip or widen the stop.

### 5.2 Pagano: Endogenous Liquidity and Market Fragmentation (pp. 223-227)

Liquidity concentrates in one market because liquidity begets more liquidity.
Multiple markets can coexist only if they serve different trader clienteles
(different endowment variances). The presence of high transaction costs can
sustain a smaller illiquid market alongside the dominant market.

**For us (NIFTY futures vs NIFTY options):**  
Our Option-C paper comparison pits two markets — NIFTY futures (deep, low
spread) vs ATM option debit (shallower, wider spread). Pagano's result
says the debit option market serves different clientele (defined-risk buyers)
and can coexist with the futures market because transaction costs (STT,
spread) deter some from the futures market. But for our *1-lot* strategy:
- The futures leg has Rs600 round-trip friction.
- The option debit leg has the STT asymmetry (0.05% post-2026 on selling leg
  at expiry), plus the market maker's adverse selection spread.
- Pagano implies that in thin markets (our scale), the option debit leg gets
  worse fills than the futures leg because its clientele is smaller and the
  market maker charges more adverse-selection premium.

This is theoretical backing for our backtest prediction that the debit-option
leg underperforms. Paper trading arbitrates whether this persists in practice.

### 5.3 Subrahmanyam: Index Basket vs Individual Securities (pp. 244-248)

When informed traders can trade both individual securities AND an index
basket, they prefer the basket because it diversifies their idiosyncratic
risk. This reduces the adverse selection premium on the basket vs the
component.

**For NIFTY (index) vs individual components:**  
The NIFTY index product (futures, options) is the basket. Our straddle is on
the basket. Informed traders with index-level views (macro, FII flows) trade
the NIFTY future or option first. Individual stock flow follows. This implies:

- NIFTY price discovery *leads* the Nifty-50 component stocks in the morning.
  Our breadth indicator (advance/decline of Nifty-50 at 10:15) is a lagged
  confirmer of what the index has already moved. This is fine for trend-rider
  confirmation but means breadth is *never* an early signal.
- The adverse selection component of the NIFTY basket is lower than for any
  single stock. This makes the NIFTY short straddle systematically safer than
  a single-stock short straddle. Diversification of informed idiosyncratic
  flows is built into the product.

### 5.4 Kumar-Seppi: Futures-Spot Information Lag (pp. 248-251)

Futures traders observe index-level signals; spot specialists observe
individual-stock signals. There is a k-period lag in cross-market price
observation. This lag generates a price gap between futures and spot that is:
- Normally distributed.
- Converges to zero as periods increase (no long-run divergence).
- Does NOT converge immediately — short-term gaps are real.

**For our engine:**  
The price gap between NIFTY spot and NIFTY futures (basis) at the open is
informational, not just arbitrage. The direction of the basis in the first
5 minutes post-open is a noisy signal of which market (institutional futures
vs retail equity) has more information flow that morning. We do not currently
use basis direction as a filter. This is a potential future observable.

### 5.5 Block Trades and Upstairs Market (pp. 233-242)

Large uninformed traders prefer block mechanisms (no anonymity) because they
can signal to the dealer that they are NOT informed, reducing their adverse
selection cost. The "no bagging the street" constraint (Seppi) enforces this
credibility.

**For us:**  
Not directly applicable (we are 1-lot, retail-sized). However, the conceptual
point translates: institutions that *need* to hedge their expiry risk use
large block-type mechanisms (basket/EFT swaps), not retail NSE single-lot
orders. Their expiry hedging does NOT appear directly in the NSE order flow
that our straddle faces. We see the *secondary* price impact, not the primary
block. This means the straddle's stop fires in response to secondary effects,
which are more random and less persistent than primary informed flow.

---

## 6. Calibrating the 25% Straddle Stop

The GM model and EO model together imply:

1. **Information is released gradually (Kyle):** A single bad event does not
   cause the full move in one bar. The informed trader bleeds his order.
   A 25% straddle move in one go likely signals a large *liquidity* shock
   (inventory imbalance, circuit-breaker fear) rather than pure information.

2. **Post-event partial reversal (EO):** After a large move (block sale in
   the index), a small trade sequence partially reverses the price (existence
   uncertainty in EO). If our 25% stop fires on a large but transient spike,
   we are stopping out of a position that would partially recover.

3. **Time-of-day spread pattern (Ho-Stoll, Ch.2):** Spreads are widest at
   the open and narrow steadily through the day. Our straddle captures the
   open-period wide spreads when we enter at 09:20. But the first hour is
   also the highest adverse selection risk (most informed flow). The 57%
   stop-fire rate at 25% suggests the stop is calibrated in a regime where
   first-hour straddle value swings are structurally large, not just volatile.

**Potential fix:** Rather than a fixed 25% stop on straddle value, consider
a *time-decayed stop* — tighter at open (when real information flow is
highest) and looser into the afternoon (when most information has been
revealed and remaining moves are mostly noise/liquidity). E.g., 20% stop
before 11:00, 30% stop 11:00-13:00, 25% stop 13:00+. This aligns with
the microstructure prediction of declining adverse selection cost through
the day.

---

## 7. Opening Range and Pre-Open Bar Handling

### 7.1 What the Call Auction Tells Us

The pre-open equilibrium price (09:15 NSE) is a single point where all
expressed order flow clears. Key properties derived from the model:

- It contains *all* pre-market information (overnight news, global futures)
  in one price.
- It is NOT the result of sequential learning — there is no Bayesian updating
  during the call. All orders are submitted simultaneously.
- Any deviation from the call price in the first continuous bar (09:15–09:20)
  is a *price discovery* process beginning from a non-sequential starting point.

**What we currently do:** We use the 09:15 bar open as the reference for the
straddle ATM strike selection. This is correct — the call auction price is the
best available estimate of the market's aggregate belief at that instant.

**What we should add:** Check whether the pre-open order imbalance (if
available from Dhan's API) correlates with first-bar direction. A large bid
imbalance in pre-open → opening gap up → straddle sold at inflated IV is
better than gap down → straddle sold at deflated IV.

### 7.2 Why the First 5 Minutes Are Special (and Dangerous)

GM model: The market maker has the widest uncertainty in the first trade of
the day. He does not know today's information event probability or direction.
He quotes a wide spread. Sequential updating narrows it.

EO model: After the first few trades reveal the existence/direction of
information events, the market maker's spread narrows and his prices are
more stable.

**Implication:** The 09:15–09:20 window (our "waiting period" before straddle
entry) is the single most volatile and informationally unstable period. Our
decision to enter at 09:20 rather than at 09:15 is microstructurally sound:
we let 5 minutes of continuous trading resolve the open call-auction
uncertainty.

However, 5 minutes may not be enough. On high-information-flow days (RBI
policy, earnings of NIFTY-50 heavyweights), the informational cascade can
last 15-30 minutes. The 10:15 breadth gate is our second layer, and the
theory supports making it mandatory on high-IV days.

---

## 8. Microstructure Realism for Our Backtest

### 8.1 Fill Model

Our engine uses next-bar-open fills with ATR-based slippage. What O'Hara's
models imply about fill realism:

| Situation | Model Prediction | Our Model |
|-----------|-----------------|-----------|
| First bar of day (post-open) | Wide spread, high adverse selection | We add ATR slippage — partially correct |
| Mid-day (low informed flow) | Narrow spread, GM liquidity | ATR slippage constant — OVERESTIMATES cost |
| Near close (15:10-15:19) | Spread widening again (settlement demand) | ATR slippage — partially correct |
| Expiry day AM | Very wide spread (max informed flow) | We do not widen slippage on expiry — likely UNDERESTIMATES cost |

**Fix:** Apply a time-of-day multiplier to slippage. Widest at 09:15-10:00
(1.5x base ATR slippage), narrowest 11:00-14:00 (0.8x), wider again at
15:00-15:20 (1.2x). On expiry days, apply an additional 1.3x multiplier.

### 8.2 Options Synthetic Path Fill

When we construct synthetic BS option prices between real entry/exit premiums,
we are essentially assuming zero bid-ask spread within the bar. O'Hara's
models say:

- The bid-ask spread on the option IS the market maker's adverse selection
  premium. For ATM NIFTY options on expiry day, this is 5-20 points (Rs375-
  Rs1500 per lot at 75 multiplier).
- Our fills at BS mid-price understate the actual cost by half the spread.
- For a 1-lot position with entry + exit, this is approximately Rs750-Rs3000
  systematic understatement per trade.
- At 60-80 straddle trades per year, that is Rs45,000-Rs240,000 of overstated
  P&L — material at our Rs5L capital scale (9-48% of capital).

**Fix:** In our synthetic path, add half-spread to all option buys and subtract
half-spread from all option sells. Use 10 points as a conservative ATM spread
estimate on expiry day (vs 5 points midday).

---

## 9. Summary: Mechanisms to Implement / Test

| Mechanism | Source | What to do | Effort |
|-----------|--------|------------|--------|
| Pre-open imbalance filter | Ch.1, call auction | Check if Dhan provides pre-open bid/offer quantity; use as IV-confirmation signal | Medium |
| Time-of-day slippage multiplier | Ch.2 Ho-Stoll, Ch.3 GM | Implement 0.8x-1.5x intraday slippage scaling | Small |
| Straddle stop time-decay | Ch.3 EO, Ch.4 Kyle | Tighter stop first hour (20%), looser afternoon (30%) | Small |
| Sequence-aware price signal | Ch.3 EO | Log if last 3 bars are same direction: signal informationally stronger | Medium |
| Basis direction as open filter | Ch.8 Kumar-Seppi | Compute futures-spot basis at 09:20; skip or resize straddle if basis directional | Medium |
| Open OI as λ proxy | Ch.4 Kyle, Ch.8 GM | Log total expiry-day OI vs 4wk avg; flag high-λ days for stop adjustment | Small |
| Expiry-day spread cost adder | Ch.8 Subrahmanyam | Add 10-point per-leg spread to all option fills in synthetic path | Small |
| Half-spread deduction from all option fills | Ch.3 Copeland-Galai | Deduct 5 points from all option exits and add 5 points to all option entries | Small |

---

## 10. What the Book Does NOT Help With (Institutional-Only or Friction-Fatal)

| Book suggestion | Our constraint | Verdict |
|----------------|---------------|---------|
| Kyle informed-trader order sizing | We are 1-lot, cannot size optimally | Irrelevant as execution strategy |
| Upstairs block-trader logic | We are retail-sized, no block access | Irrelevant |
| Multimarket strategic routing (Chowdhry-Nanda) | Single market, single lot | Irrelevant |
| Competition among market makers | NSE is effectively single market maker per option strike | Theory does not map |
| Endogenous speculator entry (Grossman-Miller) | We cannot attract more speculators | Irrelevant |
| Optimal limit order submission (Rock) | Our fills are market orders | Irrelevant for now |

---

## 11. Contradictions and Cautions

1. **Martingale prices vs momentum strategies:** GM model says prices are
   Martingales. Our trend-rider strategy assumes non-zero expected return.
   These are reconciled because: (a) the model assumes competitive, risk-
   neutral market makers — NSE has frictions and market power; (b) our
   breadth gate selects a *conditioning event* (regime) where the Martingale
   property does not hold (momentum after information event). But the
   theoretical foundation is weak. The trend signal is a regime-conditional
   departure, not a robust structural edge.

2. **EO non-Markov prices and our independence assumption:** Our bootstrap
   PF confidence intervals assume independence of trades. EO says prices are
   NOT Markov — returns depend on the sequence. Our bootstrap overstates PF
   CI precision if trades are sequentially correlated. For sparse strategies
   (40-300 trades), the bias from sequential correlation in returns may be
   material. We should check autocorrelation of daily P&L.

3. **Admati-Pfleiderer trade clustering and time independence:** A&P says
   informed and uninformed cluster together. Our strategy entry times are
   fixed (09:20 straddle, 10:15 trend gate). If cluster timing shifts (e.g.,
   informed flow moves to 10:00-10:30 range post-SEBI rule changes), our
   fixed entry time may fall outside the liquidity cluster. We should
   periodically test whether different entry times produce different PF.

4. **Separating vs pooling equilibrium (EO):** The model predicts that large
   trades carry information. In our context, NSE lot sizes are standardised —
   institutional players cannot easily separate themselves by size in the
   retail option market. Lot-size limitations reduce the separating
   equilibrium's relevance. But large OI buildup over the day still functions
   as a "large trade" signal for informed intent.

5. **Kyle linear pricing is an approximation:** The model's linear price-
   impact function implies a negative price is possible for large enough
   negative order flow. In practice, prices have floors (zero for options, ATM
   circuit limits for futures). Our straddle stop should never be modeled as
   linear in order flow — it fires at a discrete trigger, which is closer to
   a nonlinear equilibrium.

---

## Chapter References Quick Index

| Topic | Chapter / Section | Page |
|-------|------------------|------|
| Call auction mechanics | Ch.1, §1.2 | pp. 10-11 |
| Price of immediacy / spread | Ch.1, §1.1 | pp. 3-6 |
| Bagehot / Copeland-Galai informed option | Ch.3, §3.1 | pp. 53-57 |
| Glosten-Milgrom sequential model | Ch.3, §3.3 | pp. 58-65 |
| Easley-O'Hara trade-size model | Ch.3, §3.4 | pp. 66-72 |
| Non-Markov price sequences | Ch.3, §3.5 | pp. 73-76 |
| Kyle batch model / lambda | Ch.4, §4.1 | pp. 91-99 |
| Admati-Pfleiderer trade clustering | Ch.5, §5.1 | pp. 131-143 |
| Call market vs continuous stability | Ch.7, §7.1 | pp. 188-191 |
| Grossman-Miller liquidity / immediacy | Ch.8, §8.1 | pp. 216-222 |
| Pagano endogenous liquidity | Ch.8, §8.2 | pp. 223-228 |
| Block trades / upstairs market | Ch.8, §8.3 | pp. 233-243 |
| Index basket vs security liquidity | Ch.8, §8.4 | pp. 244-248 |
| Futures-spot information lag | Ch.8, §8.4 | pp. 248-251 |

---

*Pass-2 distillation complete. Build on pass-1 (de Prado / Chan / Kaufman) findings. Next books in pass-2 queue should address Indian market-specific microstructure and volatility term-structure.*
