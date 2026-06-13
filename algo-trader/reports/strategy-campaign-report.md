# Intraday Strategy Campaign — Final Report (Generations 1–6)

*algo-trader · 2026-06-12 · ~1,100 registered trials · paper-only throughout*

## Executive summary

After six pre-registered generations of walk-forward research on 5 years of
1-minute Dhan data (24M+ bars, full Dhan cost + slippage modeling), **one
strategy survived the complete gauntlet** — fenced evaluation, parameter-plateau
check, 2× slippage stress, and a single untouched holdout that doubled as a
regime-change test:

### ✅ SURVIVOR: NIFTY 0DTE expiry-day ATM short straddle
*Sell 1 lot ATM CE + PE at 09:20 on weekly expiry day; buy back on a 25%
straddle-value stop or at 15:10. No profit target. 1 lot (~₹2L margin), ~52
trades/yr.*

| Phase | n | Net P&L | PF | Win% | posWindows | Worst trade | Max DD |
|---|---|---|---|---|---|---|---|
| Fenced 2021-06→2025-08 (Thursday era) | 218 | **+₹66,056** | **1.466** | 39% | 69% | −₹5,106 | −₹13,433 |
| Stress (2× all slippage) | 218 | +₹40,648 | 1.251 | 39% | 54% | — | −₹16,510 |
| **Holdout** 2025-09→2026-06 (Tuesday era, untouched) | **40** | **+₹28,359** | **1.534** | 48% | **80% of months** | −₹4,431 | −₹13,522 |

Holdout annualized ≈ ₹37k/yr ≈ **7.4% on ₹5L** at 1 lot; per-trade Sharpe
annualized ≈ **1.25**. The entire pre-registered 8-config grid was profitable
(PF 1.20–1.49) — a plateau, not a lucky spike. The holdout era uses a different
expiry weekday (NSE's Thursday→Tuesday migration, empirically mapped to
2025-08-28/2025-09-09) and a different market vintage; the edge improved.

**Honest caveats:** (1) intra-day option values between the real entry premiums
and exit are modeled (Black-Scholes per leg on the real 1-min spot path, the
contract's own minute-level IV, time-to-expiry implied from the real entry
straddle) because Dhan's rollingoption series re-anchors strikes and cannot
represent a held contract — model risk is real, concentrated in stop-fill
prices on violent expiry days (modeled at stop level +2.5% per leg; stress
doubles it). (2) Short straddles carry structural tail risk beyond any stop on
gap moves; position is capped at 1 lot and the 2% daily breaker applies.
(3) Capacity: 1 lot at ₹5L; scaling needs proportional capital. **Next
validation step: live paper trading with real option quotes on upcoming expiry
Tuesdays.**

### 🟡 Paper-stage (not confirmed): breadth-gated index trend rider
50-stock internals (% above own session VWAP ≥0.72 at 10:15) → ride
NIFTY/BANKNIFTY with ATR stops. Fenced +₹149k / PF 1.32 / posW 58% and
slippage-stress-proof — but its edge concentrates in low-ATR signal days, and
that subset's holdout was flat (PF 1.025 / 20 trades; the 0.75% risk budget
can rarely size 1 futures lot at 2025-26 index levels). Runs forward in paper
(`paper` account) as the live laboratory; no profitability claim is made.

## ❌ Killed (with reasons — the graveyard is the methodology)

| Family | Generations | Kill evidence |
|---|---|---|
| Trend-continuation (5m equities) | 1 | PF 0.62, 13k trades — structural overtrading |
| VWAP mean-reversion (equities) | 1 | 23% win at 2σ anchors; premise fails post-2021 |
| ORB v1 (equities + indices) | 1 | PF 0.85–0.89 gross of friction across 13k trades |
| ORB v2 (compression+gap-align+BE/trail) | 2–3 | Peak PF 1.01 = breakeven; **vol-rank hypothesis rejected** (Spearman p=0.76, all-50 universe) — TATAMOTORS's repeated wins were luck |
| Breadth × ORB gate stack | 3 | PF 0.83–0.87 < ungated 1.01 — compounded false precision |
| Breadth rider, wider entry windows | 4 | Dilutes edge monotonically (PF 1.32→1.01); signal is the 10:15 state, not crossings |
| Breadth rider @ min-1-lot sizing | 5 | Fenced PF collapses to 1.01; holdout −₹72.5k — the 0.75% budget was a hidden volatility filter |
| Breadth signal via ATM weekly debit options | 5 | Theta+friction (≈₹0.8–1.3k/lot/5h) exceeds the ≈₹490/trade edge; best PF 0.69 |

## Methodology (what makes the survivor credible)

- **Pre-registration**: every grid committed to git before any OOS evaluation;
  trial registry (`reports/trial_registry.jsonl`) logs all ~1,100 evaluations;
  protocol amendments registered *before* holdout runs.
- **Fences**: holdout periods frozen at campaign start; each consumed exactly
  once per candidate; contaminated reuse explicitly labeled informational.
- **Realism**: event-driven engine, next-bar-open fills, two-branch stop
  semantics, date-banded Dhan costs (incl. Budget-2026 F&O STT hikes),
  causal ATR slippage, mandatory 15:19:30 square-off, point-in-time lot sizes
  (NIFTY 25/75/65; BANKNIFTY 15/30/35/30), 2% daily breaker.
- **Artifact catches** (each would have silently corrupted results): Dhan
  end-stamps bars (1-min look-ahead averted); rollingoption strike re-anchoring
  (proved via corr(spot,premium)=0.08 — also taints finAgent's old strangle
  validation); corporate-action-unadjusted series (flagged); time-stop field
  silently unwired (fixed + regression test); slippage double-count (fixed).
- **Stats discipline**: bootstrap PF CIs, per-window consistency (no aggregate
  masking), plateau checks over spike-picking, trial-count reporting.

## Forward plan

1. **Paper-validate the 0DTE straddle** with real option quotes (ws feed) on
   upcoming expiry Tuesdays; reconcile real vs modeled fills weekly.
2. Keep the breadth rider in paper; promote only on forward evidence.
3. Daily EOD P&L report (per-strategy + aggregate) publishes every session
   from the paper accounts (first live session: today).
4. Research continues (Gen-7+): VIX-regime conditioning, deep-ITM expression
   of trend signals, cross-sectional RS pairs — same protocol.
5. **No live capital without explicit per-action operator approval** — paper
   evidence first, then the operator's call.

*Repo: /home/ubuntu/projects/algo-trader (commits through this report). Evals:
reports/sweeps/. Holdout artifacts: reports/gen6_holdout_result.json,
reports/holdout_result.json. 532 tests green.*
