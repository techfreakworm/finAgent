# Books Synthesis — Pass 2 (9 titles) + applied 0DTE audit

*2026-06-13. Distillations: natenberg, sinclair_vol, sinclair_pos, taleb,
aronson, harris, ohara, vince, tharp (all in docs/books/). Builds on pass-1
(de Prado/Chan/Kaufman). Pairs with reports/gen7_0dte_audit.json.*

## The convergent finding (4 books): our 0DTE PF is overstated by fill realism

Harris, O'Hara, Taleb, and Natenberg independently converge: our backtest
filled the short straddle at synthetic Black-Scholes **mid** with ~1%/leg
slippage. A 1-lot **retail** seller actually pays **half the bid-ask per leg**
(ATM 0DTE spread 5-20 pts) and **1.5-2x more on stop-triggered exits** (the
short-gamma crowd all buying back at once — Taleb's liquidity-hole). O'Hara
calls this "the single largest execution realism gap." Natenberg adds that
model deltas/paths are unreliable at 0DTE because BS ignores jumps.

### Applied: adversarial fill re-audit (scripts/zerodte_audit.py, fenced only)

| slippage | PF | net | posWindows |
|---|---|---|---|
| 1x (original) | 1.466 | +₹66,056 | 69% |
| 2x | 1.251 | +₹40,648 | 54% |
| **3x** | **1.084** | **+₹15,239** | 38% |

**Verdict: the edge is REAL but THIN.** It survives 2x but nearly vanishes at
3x. The headline PF 1.47/holdout 1.53 carried an optimistic fill assumption.
The true multiplier is unknown until we trade real quotes — **this is the
single most important reason the strategy stays in paper.** Paper must log
realized fill vs modeled mid (implementation shortfall) every trade.

## Sinclair's stop challenge — tested, resolved in our favour

Sinclair (direct quote): "adding arbitrary price-based stops is a poor idea …
high volatility is transient." Our 25% stop fires 57% of days. Audit (B), 2x
fills:

| stop | PF | net | maxDD |
|---|---|---|---|
| none | 0.875 | −₹37,087 | −₹53,665 |
| **25%** | **1.251** | **+₹40,648** | **−₹16,510** |
| 40% | 1.118 | +₹23,811 | −₹22,255 |
| 60% | 0.973 | −₹6,454 | −₹31,910 |

Removing the stop is **net-negative**. For 0DTE the no-recovery-time exception
(which Sinclair himself grants) dominates: the stop pays small premiums to cut
the gamma tail. The 57% fire rate is the strategy working, not a flaw. Keep 25%.

## VIX-regime split → a clean new filter (audit C)

Stop-fires are ~uniform across VIX terciles, but **P&L is not**: low-VIX expiry
days are net-negative (−₹45/trade), mid/high-VIX strongly positive
(+₹559 / +₹494). Premium on calm expiry days is too thin to clear friction —
exactly Sinclair's VRP-cone / Natenberg's vol-cone logic. **Gen-8 candidate
(pre-registered): skip 0DTE entry when India VIX is in its bottom tercile.**
NOT a claim — needs its own holdout on the accumulating Tuesday-era days.

## Applied / queued elsewhere

- **Stop-event slippage uplift** (Harris/Taleb): backtest should apply ~1.5-2x
  slippage on stop-triggered exits vs scheduled 15:10 exits. QUEUED (engine
  change; touches all tests — do carefully in its own checkpoint).
- **Half-spread option fills** (O'Hara/Harris): model option entry at bid /
  exit at ask, spread ≈ % of premium calibrated from paper. QUEUED.
- **MCP permutation test** (Aronson): honest p-values for sparse strategies
  where bootstrap CIs are weak — encode the stop as one rule, test vs no-stop
  null. QUEUED (Masters' public-domain MCP, ~50 lines).
- **Newey-West / block bootstrap** (O'Hara): our PF CIs assume iid trades;
  returns are serially correlated → CIs too tight. Widen them. QUEUED.
- **R-multiple + MAE logging** (Tharp): per-trade R and max-adverse-excursion
  in the EOD log — folds into task #12 paper observables.
- **VRP pre-trade filter** (Sinclair/Natenberg): straddle premium percentile vs
  trailing-20 expiries / IV-RV spread — Gen-8 alongside the VIX gate.
- **Defined-risk iron-fly** (Sinclair pos.): buy wings to cap the tail — but
  India put-skew (1.3-1.5x ATM IV) makes wings expensive; net-of-friction at
  1 lot likely marginal. Backtest before adopting; low priority.

## Cautions adopted

Friction is 2-5x institutional in every book's examples → only the highest-VRP
trades survive at 1-lot scale. Short-straddle is negative-skew: Vince/optimal-f
and naive Kelly overbet it; size by tail scenario, keep 1 lot. Taleb: gap risk
bypasses the stop entirely on circuit/event days — the modeled 25% is a floor,
not a guarantee; the 2% daily breaker is the real backstop.
