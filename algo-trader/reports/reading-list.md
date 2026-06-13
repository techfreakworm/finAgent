# Intraday Algo-Trader Reading List
*Curated for: Python intraday backtester + paper trader, Indian markets (NIFTY/BANKNIFTY, DhanHQ), event-driven engine, 0DTE short-straddle theta strategy, breadth-gated trend, walk-forward validation.*

---

## 1. Quant / Algo Strategy Design & Validation

### Advances in Financial Machine Learning
**Marcos Lopez de Prado** — Wiley, 2018  
The book most directly aligned with what this project already does. Introduces Purged K-Fold and Combinatorial Purged cross-validation (prevents leakage from overlapping bars), fractionally-differentiated features (stationarity without memory-loss), and the Triple-Barrier labelling framework. The deflated Sharpe ratio — already in use here — is fully derived. Read as the canonical antidote to naive in-sample validation.  
**Tag: MUST-READ**

---

### Algorithmic Trading: Winning Strategies and Their Rationale
**Ernest P. Chan** — Wiley, 2nd ed. 2013  
Chan's second book (not the first) is where the practitioner gold lives: mean-reversion, momentum, and pairs strategies dissected with realistic cost assumptions, Kelly sizing, and explicit attention to what survives transaction costs. The walk-forward and out-of-sample discipline mirrors what this project already does; the book gives it conceptual grounding. Skip "Machine Trading" (2017) — community consensus is that it is noticeably weaker and more exploratory.  
**Tag: MUST-READ**

---

### Evidence-Based Technical Analysis
**David Aronson** — Wiley, 2006  
Methodological bedrock for anyone building signal libraries. Aronson applies the scientific method to trading signals, addressing the multiple-comparison problem, data-mining bias, bootstrap resampling, and walk-forward validation in rigorous statistical language. Directly relevant to knowing *when to stop testing* a signal family and when apparent edges in the intraday equity signal graveyard were simply noise.  
**Tag: MUST-READ**

---

## 2. Backtesting & Statistical Rigor / Avoiding Overfitting

### Advances in Financial Machine Learning
*(listed above — cross-reference; the backtesting rigor chapters are its core)*

---

### Trading Systems and Methods
**Perry J. Kaufman** — Wiley, 6th ed. 2019  
The encyclopaedic reference for systematic strategy builders. Covers trend-following, momentum, mean-reversion, opening-range breakout (directly relevant to this project's intraday work), and breadth/internals — all with explicit in-sample vs. out-of-sample discipline and robustness checks. The sixth edition adds risk-profiling chapters. Use as a reference, not a cover-to-cover read; extremely dense but authoritative.  
**Tag: NICE-TO-HAVE** *(essential to own, selective reading)*

---

## 3. Options, Volatility & Greeks (incl. Practical Short-Vol Risk)

### Option Volatility and Pricing: Advanced Trading Strategies and Techniques
**Sheldon Natenberg** — McGraw-Hill, 2nd ed. 1994/2014 update  
The industry onboarding text at professional options desks worldwide. Builds intuition for the Greeks from first principles — especially Theta and Vega behaviour near expiry — which is exactly what a short-straddle ATM seller needs when managing a NIFTY weekly-expiry position. The theta-decay curve, pin-risk near expiry, and volatility skew chapters are foundational before moving to Sinclair. Does not require advanced mathematics.  
**Tag: MUST-READ**

---

### Volatility Trading
**Euan Sinclair** — Wiley, 2nd ed. 2013  
Purpose-built for the edge this project is exploiting: the persistent volatility risk premium (implied > realised). Sinclair shows how to measure forecast vs. implied vol, size short-vol positions, and manage the tail risk that comes with short-straddle positions. Includes a rigorous money-management chapter specifically for vol sellers. The math is heavy — treat this as the operational manual for the 0DTE strategy.  
**Tag: MUST-READ**

---

### Positional Option Trading: An Advanced Guide
**Euan Sinclair** — Wiley, 2020  
Sinclair's most recent and most directly actionable book. Covers identifying *where* the edge actually lives in options (variance premium, term-structure, earnings effects), how to find it systematically, and how to select structures (straddles vs. strangles vs. spreads) to exploit it with defined risk — critical for the tail-hedging component of the short-straddle trade. Written for practitioners running real books, not students.  
**Tag: MUST-READ**

---

### Dynamic Hedging: Managing Vanilla and Exotic Options
**Nassim Nicholas Taleb** — Wiley, 1997  
Polarising but irreplaceable for short-vol operators. Taleb writes from the market-maker/arbitrageur perspective: how Greeks behave off-model, vanna/vomma in stressed markets, the dangers of gamma exposure near expiry, and the practical management of tail events. The project is short gamma near expiry on NIFTY — Taleb's treatment of what happens when the model breaks is the essential stress-test framework. Dense; read selectively (Greeks taxonomy + risk chapters).  
**Tag: NICE-TO-HAVE** *(high-signal for gamma/tail-risk understanding)*

---

## 4. Risk Management & Position Sizing

### Trade Your Way to Financial Freedom
**Van K. Tharp** — McGraw-Hill, 2nd ed. 2006  
The clearest practitioner treatment of position sizing and expectancy-based thinking. Tharp's percent-risk model and R-multiple framework translate directly to the Rs 5L capital constraint with lot-size floors: how to size a short-straddle unit when the capital is small, lot sizes are lumpy, and ruin must be avoided. The psychology section is overlong but the sizing framework is rigorous. Prefer Chapter 10–14 for the quant content.  
**Tag: MUST-READ**

---

### The Mathematics of Money Management
**Ralph Vince** — Wiley, 1992  
The formal treatment of Optimal-f (the Kelly criterion generalised for variable payoff distributions). For a short-straddle strategy with fat left tails and asymmetric payoffs, Kelly-fraction estimation is non-trivial; Vince derives it properly. Also covers Monte Carlo simulation for portfolio-level ruin estimation. Dry and mathematical — read alongside Tharp, not instead of it.  
**Tag: NICE-TO-HAVE**

---

## 5. Market Microstructure & Execution

### Trading and Exchanges: Market Microstructure for Practitioners
**Larry Harris** — Oxford University Press, 2003  
Universally described as "the bible of market microstructure" by practitioners. Covers the full taxonomy: order types, adverse selection, bid-ask spread decomposition, dealer vs. auction markets, and — critically — the microstructure of index derivatives and expiry-day mechanics. The chapters on informed trading and liquidity directly inform the slippage and fill assumptions in the backtester. A practitioner-first textbook, not academic theory.  
**Tag: MUST-READ**

---

### Market Microstructure Theory
**Maureen O'Hara** — Blackwell, 1995  
The academic counterpart to Harris — more formal, model-heavy, but gives the *why* behind the stylised facts Harris describes. Relevant for understanding price impact models, information asymmetry around expiry opens and auction sessions, and the theoretical basis for why Indian market opens (call auction) behave as they do. Read Chapter 1–4 and Chapter 8; skip the inventory model derivations unless mathematically inclined.  
**Tag: NICE-TO-HAVE**

---

## 6. Indian-Market-Specific

**Honest assessment:** There is no English-language book specifically on Indian equity microstructure or quant algo-trading that meets the standard of the titles above. The NSE Knowledge Hub white papers on Indian market microstructure (freely available at nsearchives.nseindia.com) are more rigorous and current than any published book in this space. Ashwani Gujral's derivatives books are widely cited in retail circles but are pattern-and-opinion driven — not appropriate for a backtested quant system. The SEBI circular on weekly expiry restrictions (November 2024 — NIFTY 50 weekly retained; Bank Nifty et al. moved to monthly) is essential reading from primary sources, not a book.

**Practical recommendation:** Supplement the reading list above with (a) NSE Research Papers on intraday liquidity and microstructure, (b) SEBI's F&O framework circulars, and (c) Euan Sinclair's blog posts and QuantInsti lecture recordings, which explicitly use NSE/NIFTY examples.

---

## Suggested Reading Order (First 4 Books to Drop First)

These four, in sequence, build the conceptual stack from the ground up for the specific work this project does:

1. **Option Volatility and Pricing — Natenberg** — Before anything else: without solid Greeks intuition, neither the 0DTE trade management nor the risk books will land properly.
2. **Advances in Financial Machine Learning — Lopez de Prado** — Immediately after: re-examine every signal and backtest decision already made through the lens of purged CV, deflated Sharpe, and leakage control.
3. **Volatility Trading — Sinclair** — Now build the quantitative edge framework for the short-straddle: vol forecasting, variance premium measurement, position sizing for vol sellers.
4. **Trade Your Way to Financial Freedom — Tharp** — Lock in position sizing and expectancy logic before paper-trading goes live with real Rs 5L capital.

Books 5–12 (Harris, Positional Option Trading, Algorithmic Trading/Chan, Aronson, Kaufman, Vince, Taleb, O'Hara) can be read in parallel or topic-driven order based on which problem is most pressing.

---

## Verification Sources

- [Lopez de Prado — Amazon / Wiley page](https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086)
- [ResearchGate book review: Advances in Financial Machine Learning](https://www.researchgate.net/publication/336409053_Book_Review_Marcos_Lopez_de_Prado_Advances_in_Financial_Machine_Learning_Wiley_2018)
- [Deflated Sharpe Ratio — Bailey & Lopez de Prado, SSRN 2014](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- [Natenberg — Shell Capital review](https://shell-capital.com/asymmetric-insights-trading-and-investment-book-summaries/option-volatility-and-pricing-advanced-trading-strategies-and-techniques-2nd-edition-by-sheldon-natenberg)
- [Sinclair Volatility Trading — kriminiltrading review](https://kriminiltrading.com/blogs/must-read-economic-market-books/volatility-trading-by-euan-sinclair-book-review-summary)
- [Sinclair Positional Option Trading — RobotWealth review](https://robotwealth.com/positional-option-trading-by-euan-sinclair-a-review/)
- [Larry Harris Trading and Exchanges — TurtleTrader review](https://www.turtletrader.com/larry-harris-review/)
- [Ernest Chan Algorithmic Trading — Goodreads](https://www.goodreads.com/en/book/show/17848897-algorithmic-trading)
- [Ernest Chan Machine Trading — eranraviv.com review](https://eranraviv.com/machine-trading-book-review/)
- [Aronson Evidence-Based TA — earnforex review](https://www.earnforex.com/blog/review-evidence-based-technical-analysis-by-david-aronson/)
- [Kaufman Trading Systems and Methods — QuantifiedStrategies review](https://www.quantifiedstrategies.com/trading-systems-and-methods-book-by-perry-j-kaufman/)
- [Van Tharp — TraderLion review](https://traderlion.com/trading-books/trade-your-way-to-financial-freedom/)
- [Taleb Dynamic Hedging — Investor Bookshelf review](https://investbookshelf.com/book-review-dynamic-hedging-managing-vanilla-and-exotic-options/)
- [SEBI weekly expiry restriction — Zerodha Z-Connect](https://zerodha.com/z-connect/business-updates/sebis-new-rules-for-index-derivatives-heres-whats-changing)
- [NSE India market microstructure research paper](https://nsearchives.nseindia.com/content/research/comppaper128.pdf)
