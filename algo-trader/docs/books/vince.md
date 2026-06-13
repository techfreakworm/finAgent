# The Mathematics of Money Management — Vince (1992)
## Distillation for Intraday Algo Project (Pass-2)
### Indian markets · Rs5L capital · NIFTY/BN 0DTE straddle + breadth trend rider

---

## What this book is (and is not)

Vince formalises the optimal-f framework — the fraction of capital to allocate
per trade that maximises the geometric (compound) growth rate — and extends it
to multi-market portfolios and options positions.  It is a book about **sizing
mathematics**, not about trade selection.  Everything that follows assumes a
positive mathematical expectation already exists (Chapter 1 warning: no sizing
rule can turn a losing strategy into a winner).

Pass-1 already covered Kelly/half-Kelly via de Prado/Chan/Kaufman; this pass
extracts the mechanisms that go beyond those summaries.

---

## Chapter 1 — The Empirical Techniques

### Core: optimal-f derivation

Optimal f is **not** the simple Kelly fraction.  Kelly assumes a Bernoulli
process (fixed win size, fixed loss size).  Real trade P&L streams have varying
win and loss amounts, making the Kelly formula give a wrong (usually lower)
answer when plugged in naively.

The correct empirical optimal f is found by iteration:

```
for f in 0.01 to 1.00 step 0.01:
    HPR_i = 1 + f * (-Trade_i / Biggest_Loss)   # per Eq 1.12
    TWR   = product of all HPR_i
keep the f that yields the highest TWR
```

This is the **Vince f**, not the Kelly fraction derived from average win / average loss.  
The Kelly formula underestimates optimal f whenever the win or loss distribution
is non-uniform — which is always the case in trading.

### The f-curve shape and overbetting

The TWR curve vs f is smooth with a single peak.  Key property (Figure 1-1,
Figure 1-6):

- Going 15% above the optimal f drops TWR roughly as much as going 15% below.
- Going 25% above optimal f can reduce TWR to **below 1** (net loss), approaching
  certain ruin as trades accumulate.
- This means overbetting is **asymmetrically worse** than underbetting.

### Kelly understatement vs optimal f (practical example, Chapter 1)

Kelly formula for the worked example gives f = 0.16.  True optimal f = 0.24.
After 900 trades, optimal f produces **267% more wealth** than Kelly.  After
999 trades, Kelly is at only 33.6% of optimal f's wealth.

**Implication for us**: our backtest currently caps risk per trade at fixed
Rs-amounts.  Computing the Vince optimal f on the trade P&L stream (even
empirically via iteration) would give a different — likely higher — number than
naive Kelly.  However, at 1-lot lumpy scale (see below) this is mostly academic.

### TWR and the geometric mean

```
TWR           = product(HPR_i for all trades)    # Eq 1.04
Geometric Mean = TWR^(1/N)                       # Eq 1.05
HPR_i          = 1 + f * (-Trade_i / Biggest_Loss)
```

The geometric mean is the "growth factor per play" — the single number that
determines long-run compound growth.  Comparing systems: prefer the one with
the **highest geometric mean at the fraction of f you will actually trade**.

---

## Chapter 2 — Characteristics of Fixed Fractional Trading

### Drawdown is bounded below by f

"The drawdown you can expect … as a percentage retracement of your account
equity, historically would have been at least as much as f percent." (Ch. 2)

If optimal f = 0.55 → expect at least 55% drawdown historically.  This is a
floor, not a ceiling.  Better systems have higher f, hence higher minimum
drawdown.  This is the **Vince paradox**: good systems hurt more.

### The integer-contract problem (small accounts)

TWR calculations assume infinitely divisible position sizes.  In real life you
trade whole lots.  Two consequences:

1. **Small accounts deviate most from theoretical optimal**: a Rs5L account
   with a lot requiring Rs60,000–90,000 margin can only adjust position size in
   large discrete jumps (0 or 1 lot, rarely 2).

2. Vince recommends that for a small account starting at 1 lot, the dollar
   amount to allocate **before switching to 2 lots** is the **threshold to the
   geometric** (Eq 2.02):

   ```
   T = (AAT / GAT) * (Biggest_Loss / -f)
   ```
   where AAT = arithmetic average trade, GAT = geometric average trade.

   Switch to 2 lots only when equity reaches T, not merely when it reaches
   `2 * (Biggest_Loss / -f)`.  T is always ≥ the naive "2-contract" threshold
   because GAT ≤ AAT.

### Fractional f strategies — what they cost you

At half-f (FRAC = 0.5):

```
FAHPR  = (AHPR - 1) * 0.5 + 1       # Eq 2.06
FSD    = SD * 0.5                    # Eq 2.07
FGHPR  = sqrt(FAHPR^2 - FSD^2)      # Eq 2.08
```

In a 2:1 coin-toss example with optimal f = 0.25 and geometric mean 1.06066:
- Half-f gives geometric mean 1.04583.
- Expected trades to double: 11.8 (full f) vs 15.5 (half f).
- After many trades, full-f leads to **infinitely more wealth** asymptotically.

**But**: half-f has half the drawdown.  The trade-off is explicit and
quantifiable.  We currently run at a fixed lot, which is effectively some
fractional f.

### Arc-sine law and drawdown duration

When trading at optimal f, the **longest drawdown** typically consumes 35–55%
of the total elapsed trading time.  This is not a bug — it is mathematically
expected.  Shorter look-back windows than this will misclassify regime
degradation as strategy failure.

**Implication**: with 40–300 trades in our sparse backtest windows, a 35–55%
drawdown streak is a statistical certainty, not evidence of strategy breakdown.

### Adding market systems — correlation effects

Adding a market system to the portfolio with correlation < +1.0 and positive
expectation **always improves** the geometric mean of the portfolio (never
reduces it).  Benefit is marginally decreasing.  There is also a small
"efficiency loss" from simultaneous rather than sequential outcomes (cannot
recapitalise after every leg).

**Confirmed**: our Option-C paper comparison (futures + option debit) is a
two-system portfolio.  Per Vince, the combined system should have a higher
geometric mean than either leg alone, but there is a small efficiency penalty
vs trading each leg sequentially.

---

## Chapter 3 — Parametric Optimal f (Normal Distribution)

### When to use parametric vs empirical

Empirical optimal f uses the actual trade P&L stream directly.  Parametric
optimal f fits a probability distribution to the stream and computes f from
that distribution.  The parametric approach allows "what-if" stress testing:

```
Shrink = 0.5  →  avg trade cut in half
Stretch = 1.6 →  std dev grows 60%
```

Running the parametric model with these inputs gives the new optimal f and
geometric mean **before the degraded performance is realised**.

Vince acknowledges: "the Normal is often regarded as a poor model for the
distribution of trade profits and losses."  He notes option-pricing theory
assumes log-normal price changes (equivalent to Normal log-returns), and that
this is baked into implied volatility.

**Implication**: for our short straddle, the P&L distribution is strongly
non-Normal (capped gains, fat left tail).  Parametric methods assuming
Normality will understate the risk.  Use the empirical iteration method on the
actual trade P&L stream.

### Stress-testing f with shrink/stretch

Even with only a few hundred trades, we can run:

1. Fit the empirical distribution to the trade P&L stream.
2. Shrink mean by 50%, stretch SD by 60%.
3. Re-derive optimal f and geometric mean.
4. If geometric mean falls below 1.0, the strategy is not viable under that
   stress scenario.

This is a practical procedure we can implement in the backtest engine today.

---

## Chapter 4 — Parametric Techniques on Other Distributions

### Goodness-of-fit: Kolmogorov-Smirnov test

Before fitting any distribution, verify the fit using the K-S statistic:

```
SIG = sum over j of: (-1)^(j+1) * 4 * exp(-2 * j^2 * (N^0.5 * D)^2)
```

Lower D = better fit.  This gives a significance level for "could this sample
have been drawn from the candidate distribution?"

**Implication**: run a K-S test to confirm whether our straddle P&L stream is
closer to Normal, a Student-t with fat tails, or a custom empirical distribution.
Given negative skew, a Gumbel or Johnson S_B distribution may fit better.

### Scenario planning — the practitioner version of parametric optimal f

Rather than fitting a continuous distribution, enumerate discrete scenarios:

```
For each scenario k:
    probability[k]  = P_k
    P&L outcome[k]  = L_k
    HPR[k]          = 1 + f * (L_k / |Biggest_Loss|)
    
TWR(f) = product over k of: HPR[k]^P_k
Geometric Mean(f) = TWR(f)^(1/N)  where N = sum(P_k)
```

Maximise over f to get the parametric optimal f under the scenario assumptions.
This works for any distribution including highly skewed ones.

**For the short straddle**: define scenarios as (e.g.):
- Day expires in 80% of max-gain band: +6,000, prob 0.45
- Stop triggered (25% straddle value): -8,000, prob 0.57 (our observed stop rate)
- Day expires benign: +4,000, prob 0.20
- Gap/tail event: -25,000, prob 0.08

Run the f-iteration over these scenarios to find the empirical optimal fraction.

---

## Chapter 5 — Multiple Simultaneous Positions: Options Under Parametric Approach

### The single short option — HPR equation (Eq 5.20)

For a short option position where S = premium received:

```
HPR(T, U) = (1 + f * (1 - Z(T, U-Y) / S))^P(T, U)
```

where:
- T = time remaining to expiration at the mandated exit
- U = price of underlying at exit
- Z(T, U-Y) = theoretical option price at that underlying price and time
- S = option price at entry (premium received)
- P(T, U) = probability of underlying reaching U by time T
- Y = arithmetic expected move in underlying

The optimal f for the net straddle position is found by combining the call and
put legs into a single position:

- S becomes the **net premium** (sum of both legs)
- Z(T, U-Y) becomes the **net theoretical price** of both legs at the
  given underlying price

This is the rigorous framework for sizing the short straddle.  The key insight
from Vince's worked example (Ch. 5): the mathematical expectation of a long
option decays each day after entry.  For a short option, the expectation is
greatest immediately after entry and decays as time passes — which confirms
theta capture as the economic rationale.

### Short straddle: unlimited liability and ruin

Vince states explicitly (Ch. 5, p. 62 equivalent):

> "Being short the underlying instrument is analogous to being short a call
> option with infinite time remaining till expiration, and liability is truly
> unlimited in such a situation."

For an **unhedged short straddle on a futures instrument** (NIFTY futures-based
straddle):

> "If you play a game with unlimited liability, you will go broke with a
> probability that approaches certainty as the length of the game approaches
> infinity."

The practical mitigation Vince endorses: limited-liability instruments (long
options where the position is initiated at a debit).  For credit positions, he
recommends defining a hard stop (our 25% straddle-value stop) and treating it
as the "biggest loss" in the f calculation.  But he also notes that price can
"make very large leaps" that bypass the stop entirely — the empirical biggest
loss understates the true tail.

**Implication for our straddle**: the 25% stop defines the reference worst case
for sizing purposes, but we must reserve capital for the realistic tail loss
(gap open, circuit breaker, news shock).  Vince would say: treat the stop as
the sizing denominator, but size as if the stop will fail at least occasionally.
At 1-lot, this is self-enforcing.

### Straddle sizing with Vince f

At Rs5L capital, with a typical NIFTY ATM straddle premium of ~Rs 250 × 50
(BN 100):

- Net premium received (straddle) ≈ Rs 12,500–25,000 per lot
- 25% stop → biggest loss reference ≈ Rs 3,125–6,250
- If optimal f from scenario planning = 0.15–0.25 (typical for short-vol
  strategies with fat left tails)
- Dollar per contract = Biggest_Loss / (-f) ≈ Rs 6,000 / 0.20 = Rs 30,000 per lot

Rs5L / Rs30,000 ≈ 16 lots — but margin requirement constrains this to 2–3 lots.
This means we are operating well below optimal f from a capital-allocation
standpoint (i.e., on the left side of the f-curve), which is actually safer
but slower-growing.  This is fine: at 1-lot paper stage, the exact f is irrelevant.

---

## Chapter 8 — Risk Management: Asset Allocation

### Static vs dynamic fractional f

**Static fractional f**: trade a fixed fraction (e.g., half-f) forever.  The
fraction of optimal f used remains constant, so variance is constant.  Simple.

**Dynamic fractional f (split-equity technique)**:
- Split capital into "active" and "inactive" portions.
- Trade at full optimal f on the active portion only.
- As active equity grows, it approaches full capital; as it shrinks, the
  fraction of total capital at risk drops automatically.
- This provides implicit portfolio insurance: ruin of the active portion does
  not ruin the inactive portion.

For a 20% initial active equity allocation:
- Static .2f doubles in ~139 trades.
- Dynamic .2f doubles in ~125 trades (faster, because active equity grows
  using full-f internally).
- Asymptotically, dynamic f produces infinitely more wealth than static f.

**For us**: our Rs5L capital with a 1-lot minimum is effectively a static
fractional-f constraint.  The dynamic technique requires sufficient capital to
trade multiple lots so that the active/inactive split has meaning.  Below Rs10L,
the split-equity technique is theoretical.

### Investor utility / gut-feel active percentage

The crude but practical version: allocate as active equity the **maximum
percentage you are willing to lose**.  If 25% drawdown is tolerable → 25%
active.

For our 2% daily breaker: if we're willing to lose 2% of Rs5L (Rs10,000) in a
day, and our biggest single-trade loss is Rs 6,000–8,000, we are effectively
running at ~2 lots maximum under this constraint.

### Portfolio insurance interpretation

Long a call option on the portfolio = being long the portfolio + long a put.
This is the payoff profile of a portfolio with a floor.  The cost of this
insurance is the option premium.  Vince shows this is mathematically equivalent
to the split-equity technique: the inactive portion is the "put premium," and
the active portion is the leveraged portfolio.

**For our debit-option leg** (ATM index debit spread in Option-C): Vince's
framework explicitly validates debit options as a risk-limiting tool.  By buying
the debit spread, we are paying for portfolio insurance.  The friction (theta
decay on the debit) is the insurance premium.  Vince would say this is the
correct structure for a limited-capital trader who cannot survive a tail event
without a floor.

---

## Critical Limitations for Our Setup

### 1. Optimal f overbets at small sample sizes

"At short time horizons, the difference between optimal f and any other
strategy may not appear impressive."  With 40–300 trades per strategy per
window, we are squarely in the regime where empirical optimal f is **unstable**:

- The "biggest loss" in a 40-trade sample is not the true biggest loss.
- The optimal f derived from 40 trades may be 2–4x the true optimal f if the
  empirical sample happens to avoid tail events.
- Running optimal f naively on a 40-trade sample will overbet.

**Mitigation**: use the scenario-planning parametric approach (Ch. 4/5) to
bound the tail, rather than relying purely on the empirical biggest-loss.
Alternatively, use the worst-case loss from the **entire** combined data set
(not just the current walk-forward window) as the biggest-loss reference.

### 2. Optimal f is too aggressive for negative-skew payoffs

The short straddle has a negative-skew P&L distribution: many small wins,
occasional large losses.  Optimal f on a negative-skew stream will be **lower**
than for a symmetric stream with the same expectation, because the left tail
dominates.

- If we use the 25% stop as the biggest loss: f calculation is stable but
  ignores gap risk.
- If we use a realistic gap loss (e.g., 3x the stop value): f will be much
  lower, correctly restricting leverage.

Vince's honest conclusion: trading strategies with unlimited liability on the
negative tail should be sized with **extreme caution**, using the parametric
scenario approach and including a tail-event scenario with sufficient probability.

### 3. Transaction costs absorb most of the fractional-f advantage at small scale

At Rs600 round-trip cost on an equity trade and 0.05% STT on F&O post-2026,
the arithmetic average trade must be positive **after costs** before any optimal
f sizing is meaningful.  Vince assumes frictionless reinvestment in all derivations.

For our straddle, costs per lot ≈ Rs 500–1,500 (broking + STT + exchange).
Straddle premium revenue (net P&L expectation) ≈ Rs 1,500–4,000 per trade
(rough, from 2024-25 data).  Cost-to-expectation ratio is 30–50%.  This
dramatically reduces the geometric mean and shifts the optimal f downward.

A system with geometric mean < 1.005 per trade (very thin edge) has little
benefit from f-optimisation — the compounding benefit is negligible, but the
drawdown cost is unchanged.

### 4. The integer-lot problem is binding at Rs5L

We cannot trade 0.4 lots.  This means:
- Below threshold to geometric T: 1 lot.
- Above T: 2 lots.
- The equity jump from 1 to 2 lots doubles variance instantly.

For the straddle at Rs5L:
- 1-lot margin required: ~Rs 60,000–90,000 (SPAN + exposure)
- Threshold to switch to 2 lots: T = (AAT / GAT) × (Biggest_Loss / -f)
  If AAT = Rs2,500, GAT = Rs1,800, Biggest_Loss = Rs6,000, f = 0.15:
  T = (2500/1800) × (6000/0.15) = 1.389 × 40,000 = Rs 55,556 active equity needed
  This is plausible at Rs5L, but the 2-lot jump doubles margin commitment.

### 5. Risk of ruin with unlimited liability is theoretically 1

Vince is explicit: any strategy with unlimited downside risk will eventually
experience ruin given sufficient time.  The short straddle (even with a stop)
has tail risk from gap opens that exceed the stop.  This is not a reason not to
trade it, but it means:

- Paper-trade only until edge is confirmed over multiple market regimes.
- Never size up to the point where a 3-sigma overnight gap would breach the 
  Rs5L capital floor.
- The stop is not a guarantee; it is a risk limiter.

---

## Procedures We Can Implement

### Procedure A: Empirical optimal f on trade stream

```python
def empirical_optimal_f(trades, step=0.01):
    """trades: list of P&L values in Rs"""
    biggest_loss = min(trades)
    best_f, best_twr = 0.0, 0.0
    for f in np.arange(step, 1.0 + step, step):
        hprs = [1 + f * (-t / biggest_loss) for t in trades]
        twr = np.prod(hprs)
        if twr > best_twr:
            best_twr, best_f = twr, f
    return best_f, best_twr
```

Run on the full backtest P&L stream (not per window) to get a stable estimate.
Report: optimal f, f-in-dollars (biggest_loss / -f), geometric mean.

### Procedure B: Scenario planning parametric f (for the straddle)

Define 4–6 scenarios based on observed empirical frequencies plus a tail:

| Scenario | P&L (Rs) | Probability |
|---|---|---|
| Full theta capture (range-bound) | +8,000 | 0.25 |
| Partial capture, move near stop | +3,000 | 0.20 |
| Stop triggered, clean fill | -6,500 | 0.40 |
| Gap open, stop slip 2x | -13,000 | 0.10 |
| Black-swan gap 5x | -30,000 | 0.05 |

```python
def scenario_optimal_f(scenarios, step=0.01):
    """scenarios: list of (pnl, prob) tuples"""
    biggest_loss = min(p for p, prob in scenarios)
    best_f, best_ghpr = 0.0, 0.0
    for f in np.arange(step, 1.0 + step, step):
        ghpr = np.prod(
            (1 + f * (p / abs(biggest_loss))) ** prob
            for p, prob in scenarios
        )
        if ghpr > best_ghpr:
            best_ghpr, best_f = ghpr, f
    return best_f, best_ghpr
```

### Procedure C: Threshold to geometric (when to move from 1 to 2 lots)

```python
def threshold_to_geometric(aat, gat, biggest_loss, f):
    """All in Rs. Returns equity level at which to double lot size."""
    return (aat / gat) * (biggest_loss / -f)
```

If threshold > current capital: stay at 1 lot.  Track this daily.

### Procedure D: Fractional f stress test

For any f fraction FRAC (e.g., 0.25, 0.50):

```python
def fractional_f_stats(ahpr, sd_hpr, frac):
    fahpr = (ahpr - 1) * frac + 1
    fsd   = sd_hpr * frac
    fghpr = (fahpr**2 - fsd**2)**0.5
    return fahpr, fsd, fghpr
```

Use to compare: "what does our effective sizing do versus theoretical optimal f?"

---

## What Our Project Already Does Right (Vince Confirmation)

1. **Stop = biggest loss reference**: our 25% straddle-value stop is the
   natural "biggest loss" for f calculations.  This is the correct denominator.

2. **Geometric mean as performance metric**: our bootstrap PF / Sharpe
   analysis implicitly optimises geometric returns.  Vince validates this.

3. **Walk-forward banding**: our 8m/2m splits avoid the small-sample overfit
   that Vince warns about for optimal f derivation.

4. **Fixed 1-lot sizing**: at Rs5L, we are naturally on the left side of the
   f-curve (under-betting), which Vince says is the safer error.  Drawdowns
   are bounded at 1-lot loss; we cannot blow up by sizing.

5. **Paper-only**: Vince's ruin argument confirms our paper-only stance is
   correct until edge is stable across multiple regime windows.

6. **Daily circuit breaker at 2%**: maps to the "investor utility" method of
   limiting active equity per session.

7. **Cost inclusion**: our Rs600 round-trip cost model is what Vince means by
   "the friction that must be overcome before any geometric benefit is realised."

---

## Contradictions and Honest Cautions

1. **Full optimal f is unworkable at 1-lot scale**: optimal f for a typical
   futures/options strategy is 10–40% of capital.  At Rs5L, this means
   Rs50,000–200,000 per trade.  With 1-lot margin at Rs60,000+, we are already
   at full or over optimal f by margin constraint alone.

2. **The debit-option leg will always underperform Vince's framework**: Vince
   shows (Ch. 5) that the mathematical expectation of a long option decays each
   day after entry.  If our debit trade is held 1+ days, the expectation
   decline is the cost of defined risk.  This is consistent with Kaufman's
   prediction that the debit leg underperforms futures — the paper arbiter will
   confirm this.

3. **Vince assumes independent trials**: trade-by-trade independence.  Our
   straddle has significant regime dependency (2022 high-vol vs 2023-25 grind).
   Optimal f derived from pooled data across regimes will be incorrect for any
   single regime.  The walk-forward per-window f is the right approach.

4. **Correlation assumptions in multi-system portfolios**: Vince warns (Ch. 8)
   never to adjust correlation coefficients lower than observed — "if you're
   going to err, err by moving them upward."  Our two systems (straddle +
   trend-rider) are likely positively correlated in high-vol days (both lose
   simultaneously).  We should use observed correlation, not assume
   diversification benefit.

5. **Risk of ruin is 1 for the short straddle in the limit**: Vince is clear
   that unlimited-liability trades (where the stop can be jumped by a gap) will
   eventually ruin any account given infinite time.  Our stop is the mitigation,
   but it is not a mathematical guarantee.

---

## Summary: Key Numbers for Our Reference

| Metric | Formula / Rule |
|---|---|
| Empirical optimal f | Iterate f in [0.01, 1.00]; max TWR |
| f-in-dollars (lot threshold) | abs(Biggest_Loss) / f |
| Threshold to 2nd lot | (AAT/GAT) × f-in-dollars |
| Drawdown floor at optimal f | equals f (as % of equity) |
| Fractional f geometric mean | sqrt((FAHPR^2 - FSD^2)) |
| Time to double at half-f | ln(2)/ln(FGHPR) trades |
| Scenario parametric f | Iterate GHPR product of HPR^P |
| Arc-sine law: expected drawdown duration | 35–55% of total trading horizon |

---

*Chapter citations: Ch1 (pp. 9–61 empirical), Ch2 (pp. 62–110 characteristics),
Ch3 (pp. 111–170 parametric Normal), Ch4 (pp. 171–230 other distributions),
Ch5 (pp. 231–300 options), Ch8 (pp. 301–390 risk management).*
