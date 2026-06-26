# FG-3 — Measured Slippage / Implementation-Shortfall Model (PRE-DRAFT)

**Status:** pre-draft (algo-brain, 2026-06-21), FORWARD-DATA-GATED. Activate when the
forward `/optionchain` collector (`data/cache/_OPTIONS/NIFTY_CHAIN_FWD/`) has ~1–2
weeks of sessions (≈5–10 sessions, ≥2–3 expiry Tuesdays). Ping algo-brain then to
turn this into an implementation-ready spec against the actual stored schema.

**Purpose:** replace the GUESSED slips (`ENTRY_SLIP`=1% / `STOP_SLIP`=2.5% /
`EXIT_SLIP`=1%) with an EMPIRICAL fill model from real top-of-book, then re-run
gen6 (and later P3) under it. Directly attacks our #1 risk — fill-sensitivity
(gen7 synthetic curve PF 1.47→1.25→1.08 at 1x/2x/3x). Live 06-16 datum: ~₹650/leg
≈ a quarter of the idealized edge → reality likely sits inside the 1x–3x band.

## Model components (all from the collector's real bid/ask(+qty)/ts)
1. **Entry (we SELL):** fill ≈ bid. Empirical half-spread `(mid−bid)/mid` per leg
   over 09:16–09:25. Entry credit = `mid × (1 − measured_hs)`. Report measured
   half-spread vs the 1% assumption.
2. **Exit/stop (we BUY back):** fill ≈ ask. Measure `(ask−mid)/mid` **conditional
   on fast-move / high-vol minutes** → a STATE-DEPENDENT stop slip (the spread
   widens nonlinearly on exactly the days the stop fires). Biggest correction
   over gen7's uniform 2×.
3. **Depth/size:** how often 1 lot (75) exceeds top-of-book qty at the ATM legs/
   times → book-walk impact (likely small for ATM NIFTY weeklies; material later
   for P2 wings — check).
4. **Implementation shortfall** = decision-mid − achieved-fill (entry + exit), in
   ₹/leg — directly comparable to the live ₹650/leg datum.

## Re-run gen6 under it (sessions WITH chain data)
Swap synthetic-mid + flat-slip for: entry credit = real ATM **bid**; intraday mark
= real chain **mids**; stop fires on real combined premium ≥ 1.25×; exit = real
**ask** × (1 + depth-impact). Output the fill-realistic PF and locate **where
reality sits on the synthetic 1x/2x/3x curve.**

## Pre-registered decision (gives it teeth)
- real-fill PF ≥ ~1.10 → thin-but-real edge, continue 1-lot.
- real-fill PF < ~1.0 → the live edge does NOT survive real fills → STOP trading
  it / rethink. (Hypothesis: reality ≈ between 2x and 3x → ~1.10–1.25.)

## Folded-in tests (FG-3 hosts these)
- **(a) TN-1 fill-RELIABILITY sub-thesis** (the one synthetic data couldn't kill):
  with real bid/ask, does a spot-trigger exit achieve better fills than a
  premium-% exit when the option quote is wide/stale?
- **(b) Entry-timing re-test** 09:16/09:20/09:30 on REAL fills — no synthetic
  τ-inversion, so free of the opening-window artifact that produced the TN-2 dip.

## Timeline & guards
~1–2 weeks → first entry/exit half-spread estimate. State-dependent stop widening
needs several VOLATILE / stop-fire days → ~1–2 months; **pre-register: do NOT
overfit the stop-slip to <5 stop-days.** Cross-validate against the live paper
trade's own logged fills where available.

## Methodological lesson banked (TN-2 root cause)
Synthetic-BS with entry-implied τ0 (brentq from the entry straddle) is **fragile to
entry-premium noise**: a momentarily-off premium in the noisy 09:15–29 window
biases τ0, which mis-marks the WHOLE held path for that day → one large spurious
P&L. A few such days swung the TN-2 per-minute aggregate PF (the "09:20 dip").
Non-monotone + concentrated (top-5 days = 81%) + per-day coinflip = artifact, not
edge. → Prefer real fills; treat synthetic opening-window entries as unreliable;
this is the third confirmation (P2, TN-1, TN-2) that small/concentrated positives
are traps.
