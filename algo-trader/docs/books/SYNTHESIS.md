# Books Synthesis — Pass 1 (de Prado · Chan · Kaufman)

*2026-06-13. Full distillations: [deprado.md](deprado.md) · [chan.md](chan.md) ·
[kaufman.md](kaufman.md). More books arrive — this synthesis is NOT final.*

## Applied immediately (code)

1. **DSR family-count audit** (de Prado Ch 11/14): parameter variants within one
   strategy family are correlated, not independent trials. `TrialRegistry` now
   documents family tagging; `count_families()` gives the honest N for
   `deflated_sharpe()` (currently 6 families, not ~1,100 grid points). Selection
   ACROSS families uses family count; over-counting deflates too hard,
   under-counting flatters.
2. **Per-window consistency guard** (de Prado Ch 12): `wf_eval` flags configs
   with any window PF < 0.8 (`n_windows_pf_below_0p8`) — aggregate PF must not
   mask a catastrophic window.
3. **C-comparison prediction registered**: Kaufman Ch 16 debit math + our own
   Gen-5b backtest both predict the options leg underperforms on theta+friction.
   Logged before paper data arrives; paper arbitrates.

## Queued for the paper lab (observables only — no trading changes)

- **SADF trend gauge at 10:10** (de Prado Ch 17) — log per signal day; after
  60+ samples, test win-rate split. Candidate AND-gate for the breadth rider.
- **Hurst(60d) + CUSUM regime flags** (Chan Ch 2 / de Prado Ch 17) — daily
  pre-open log; tests the 2022-vol vs 2023-25-grind regime dependence honestly.
- **Straddle-stop audit** (Chan Ch 8 doctrine + Kaufman): our 25% stop fired on
  57% of fenced days — it is a strategy component, not tail insurance. Paper
  fills will show whether real stop executions degrade the modeled PF; also log
  intraday max-premium excursion per trade to evaluate looser-stop variants
  WITHOUT re-tuning the burned holdout.
- **Trail-vs-target geometry check** (de Prado Ch 13): for O-U-like reversion,
  optimal is often big-stop/small-target — opposite of the rider's
  2.0-stop/3.5-trail momentum geometry. Paper data will show whether wins
  actually reach the trail or die early.

## Strategy backlog (Gen-7+, pre-register before testing)

- Kaufman breadth variants: TRIN-style volume-weighted gate; McClellan-style
  continuous breadth; %-above-prev-close as robustness cross-check of our
  %-above-VWAP (which Kaufman does NOT validate — our 0.72 threshold has no
  analog in his data; treat as bespoke).
- Chan: opening-gap momentum and 09:15→10:15 gap-fill MR on NIFTY futures —
  both friction-challenged post-2026 STT (₹850-950/lot round trip); only worth
  testing with the strictest filters.
- Kaufman NR4/inside-day + range-vs-ATR(20) compression as ORB revival filters —
  LOW priority: his own friction math (1-5% friction-to-move vs our 30-40%)
  *confirms* our ORB kill at this capital.
- de Prado meta-labeling on breadth-rider signal quality — DEFERRED until 200+
  labeled paper trades exist; until then manual SFI on (SADF, ATR percentile,
  gap size, breadth margin).

## What the books validated about current practice

Pre-registration + trial registry; frozen single-shot holdouts; bootstrap PF
CIs; plateau-over-spike; honest cost modeling (Kaufman's friction tables
independently reproduce our equity-intraday kill); walk-forward as approximation
of CPCV (upgrade candidate); skepticism of weekday/seasonal effects at our
sample sizes; accepting 1-lot lumpy sizing (Kaufman: no workaround exists).

## Standing cautions adopted

- DSR assumes stationary SR — regime dependence means DSR is necessary, not
  sufficient. Pair with per-window checks + CUSUM flags.
- WF SR point estimates at 40-300 trades carry ±0.5-ish uncertainty — always
  report with CIs (we do) and resist celebrating small OOS wins.
- Ehlers-via-Kaufman: intraday non-stationarity argues for shorter WF windows
  (2m IS / 1w OOS) than our 8m/2m — evaluate in Gen-7 (more windows = better
  consistency statistics for sparse strategies, at higher compute).
