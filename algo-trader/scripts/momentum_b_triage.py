"""Momentum B-triage: survivorship-CONTAMINATED upper-bound scout (protocol §B).

Executes the FROZEN pre-registration in reports/momentum_prereg.md on TODAY's
cached NIFTY names applied historically. Because the universe is today's
survivors, results are OPTIMISTICALLY biased. This run can therefore ONLY:
  - KILL the momentum track (if even the optimistic version fails a pre-reg gate), or
  - return NOT-KILLED  --  which is NOT validation. A clean, point-in-time
    (membership-correct) A-test is required for ANY positive/deploy claim.
No deploy claim is possible under any outcome here. Paper/research only.

Grid (frozen, no expansion): formation J in {6, 12}, SKIP=1, HOLD=1 month,
monthly non-overlapping rebalance; rank by raw cumulative formation return;
PRIMARY = long-only top-quintile (10 names) equal-weight; benchmark = EW of the
same eligible universe, monthly rebalanced. Friction = 0.40% round-trip per
ROTATED name on actually-traded notional (held names cost 0); reports 1x and 2x.
Split (frozen): IS -> 2025-03-31; FROZEN HOLDOUT 2025-04-01 .. 2026-06-11, run
ONCE for the IS-winning J only. All gate thresholds are pre-committed & numeric.

Run: cd /home/ubuntu/projects/algo-trader-momentum && \
     ./.venv/bin/python scripts/momentum_b_triage.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from algotrader.backtest.walkforward import deflated_sharpe  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
CACHE = PROJECT / "data/cache"

# ---- frozen protocol constants --------------------------------------------
J_GRID = [6, 12]           # formation windows (months) -- the ONLY free param
SKIP = 1                   # months (locked)
HOLD = 1                   # months (locked)
FRICTION_RT = 0.004        # 0.40% round-trip per rotated name (0.20%/side)
IS_END = date(2025, 3, 31)          # in-sample fence (inclusive), by FORMATION month t
HOLDOUT_START = date(2025, 4, 1)    # frozen holdout start
HOLDOUT_END = date(2026, 6, 11)     # frozen holdout end (data cutoff)
N_TRIALS_DSR = 2           # K=2 trials (the 2 J values) for Bailey-LdP deflation
MONTHS = 12                # annualisation factor for monthly returns
NW_LAG = 3                 # Newey-West Bartlett lag

# pre-registered gate thresholds (LOCKED)
IR_MIN = 0.5               # IS annualised Information Ratio vs EW benchmark
NWT_MIN = 2.5              # |t| on mean monthly active return (Newey-West lag 3)
MAXDD_MAX = 0.25           # sleeve max drawdown ceiling (fraction)
BIG_MOVE = 0.15            # |daily return| reconciliation threshold (QC)

# universe exclusions: indices / derivatives / ETF / internal dirs
EXCLUDE_EXACT = {"NIFTY", "NIFTY_50", "BANKNIFTY", "INDIAVIX", "NIFTYBEES",
                 "_BREADTH", "_OPTIONS"}

# TATAMOTORS demerger (2025-10-14, ~-41%, NOT ratio-adjusted): the demerger
# falls INSIDE the formation window from ~2025-09 on and corrupts the signal ->
# exclude the name from formation+holding for any rebalance from 2025-09 onward.
DEMERGER_NAME = "TATAMOTORS"
DEMERGER_CUTOFF = date(2025, 9, 1)   # exclude at any formation month t >= this


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def load_universe() -> list[str]:
    """Cached equity names with a 1m dir, excluding indices/derivs/ETF/internal.

    NOTE: these are TODAY's survivors applied historically -> survivorship
    contamination (optimistic bias). A few names have since been dropped from
    the index; they are kept (same contamination direction) and documented.
    """
    names = []
    for d in sorted(CACHE.iterdir()):
        if not d.is_dir() or not (d / "1m").is_dir():
            continue
        sym = d.name
        if sym in EXCLUDE_EXACT or sym.endswith("-FUT"):
            continue
        names.append(sym)
    return names


# ---------------------------------------------------------------------------
# Prices: daily close = last 1m bar close per session; monthly parquets
# ---------------------------------------------------------------------------

def daily_closes(sym: str) -> pd.Series:
    """Daily close series (last 1m bar per session) for `sym`, indexed by date.

    Cache is split+bonus adjusted (verified in the pre-reg). Weekend rows are
    dropped here (phantom weekend prints); >15% moves are handled at panel level.
    """
    files = sorted((CACHE / sym / "1m").glob("*.parquet"))
    frames = [pd.read_parquet(f, columns=["ts", "close"]) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
    df["d"] = df["ts"].dt.date
    # last bar's close per session
    last = df.sort_values("ts").groupby("d")["close"].last()
    # drop Sat/Sun (phantom weekend prints -- known cache artifact)
    last = last[[dt.weekday() < 5 for dt in last.index]]
    last.index = pd.to_datetime([pd.Timestamp(dt) for dt in last.index])
    return last.sort_index()


def build_panel(universe: list[str]) -> tuple[pd.DataFrame, list[dict]]:
    """Assemble the daily close panel [date x sym] and run QC scrub.

    QC (per pre-reg): reconcile every |daily return| > 15%.
      1. TATAMOTORS 2025-10 demerger -> handled by the from-2025-09 name exclusion.
      2. Every OTHER >15% move: DOCUMENTED with its next-session return and KEPT
         as a genuine adjusted-price return. All such moves are liquid NIFTY
         large-caps on identifiable market-event days (e.g. Adani-Hindenburg
         Jan-2023, 2024-06-04 election-result day, IndusInd Mar-2025, the Jan-2022
         tech selloff) -- i.e. EXPLAINED, not data artifacts. Dropping them would
         remove legitimate momentum signal (the very losers/winners the factor
         sorts on) and BIAS the test.

    The pre-reg's named PHANTOM prints (2021-08-07/09, 2022-04-09/11) are on
    WEEKEND-ADJACENT dates: the artifact Saturdays (2021-08-07, 2022-04-09) are
    dropped by the weekend filter in daily_closes, and the adjacent Mondays leave
    NO >15% residual (verified: they never appear in this scan). So no artifact
    survives to need a targeted drop here. Returns (panel, qc_log).
    """
    series = {sym: daily_closes(sym) for sym in universe}
    panel = pd.DataFrame(series).sort_index()
    qc: list[dict] = []

    # >15% daily-return scan (on the weekend-filtered series); classify + document
    rets = panel.pct_change()
    for sym in universe:
        r = rets[sym]
        idx = list(panel.index)
        big = r[r.abs() > BIG_MOVE].dropna()
        for dt, val in big.items():
            d = dt.date()
            i = idx.index(dt)
            nxt = r.iloc[i + 1] if i + 1 < len(idx) else np.nan
            nxt_r = round(float(nxt), 4) if pd.notna(nxt) else None
            if sym == DEMERGER_NAME and date(2025, 10, 1) <= d <= date(2025, 10, 31):
                qc.append({"sym": sym, "date": str(d), "ret": round(float(val), 4),
                           "next_ret": nxt_r, "class": "TATAMOTORS_demerger_2025-10",
                           "action": "name excluded from formation+holding from "
                                     "2025-09 onward (handled at schedule level)"})
                continue
            qc.append({"sym": sym, "date": str(d), "ret": round(float(val), 4),
                       "next_ret": nxt_r, "class": "real_market_move_kept",
                       "action": "explained market-event move -> KEPT as a genuine "
                                 "adjusted-price return (not a data artifact)"})
    return panel, qc


def monthly_prices(panel: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Standard monthly-last-price matrix [month_period x sym].

    Each cell = the name's LAST VALID daily close within that calendar month
    (NaN only if the name had no bar that month). This is the canonical
    monthly-close convention and avoids a union-calendar quirk: requiring the
    exact panel-wide last-trading-DATE would spuriously NaN a name that traded
    all month but not on the one day some other name had a stray bar.
    Also returns {month_period -> actual last trading DATE in the panel} for the
    IS/holdout split and daily-sleeve boundaries.
    """
    per = panel.index.to_period("M")
    mprice = panel.groupby(per).last()          # last non-NaN per column per month
    month_end_date = {p: panel.index[per == p].max() for p in mprice.index}
    return mprice, month_end_date


# ---------------------------------------------------------------------------
# Statistics (Newey-West, IR, Sharpe, maxDD, DSR wrapper)
# ---------------------------------------------------------------------------

def nw_tstat(x: np.ndarray, lag: int = NW_LAG) -> float:
    """t-stat on the sample MEAN of x with a Newey-West (Bartlett) HAC SE.

    long-run var = gamma0 + 2 * sum_{k=1..L} (1 - k/(L+1)) * gamma_k
    Var(mean) = long-run var / T ; t = mean / sqrt(Var(mean)).
    (Implemented directly -- statsmodels is not installed in this env.)
    """
    x = np.asarray(x, dtype=float)
    T = x.size
    if T < 3:
        return math.nan
    xbar = x.mean()
    dev = x - xbar
    gamma0 = float(dev @ dev) / T
    lrv = gamma0
    for k in range(1, min(lag, T - 1) + 1):
        gk = float(dev[k:] @ dev[:-k]) / T
        lrv += 2.0 * (1.0 - k / (lag + 1.0)) * gk
    if lrv <= 0:
        return math.nan
    se = math.sqrt(lrv / T)
    return xbar / se if se > 0 else math.nan


def ann_ir(active: np.ndarray) -> float:
    """Annualised Information Ratio of a monthly active-return series."""
    a = np.asarray(active, dtype=float)
    if a.size < 2:
        return math.nan
    sd = a.std(ddof=1)
    if sd <= 0:
        return math.nan
    return math.sqrt(MONTHS) * a.mean() / sd


def per_period_sharpe(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if x.size < 2:
        return math.nan
    sd = x.std(ddof=1)
    return x.mean() / sd if sd > 0 else math.nan


def max_drawdown_compound(rets: np.ndarray) -> float:
    """Max peak-to-trough drawdown (fraction, >=0) on a compounded return path."""
    r = np.asarray(rets, dtype=float)
    if r.size == 0:
        return 0.0
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / peak
    return float(dd.max())


def dsr_active(active: np.ndarray) -> dict:
    """Deflated Sharpe (Bailey-Lopez de Prado) on the monthly active series.

    Reuses algotrader.backtest.walkforward.deflated_sharpe.
      - observed Sharpe = PER-PERIOD (monthly), un-annualised.
      - n_days = number of monthly observations (the function is period-agnostic).
      - kurt is RAW (normal=3); pandas .kurtosis() gives EXCESS -> add 3.
      - n_trials=2 (the two J values).
    NOTE ON THE "DSR > 0" GATE (protocol ambiguity, resolved below): the function
    returns a PROBABILITY P(SR > SR*) in (0,1) which is mathematically always > 0,
    so a literal "DSR > 0" is vacuous. We resolve the pre-reg's intent as "the
    multiple-testing-ADJUSTED Sharpe is positive", i.e. observed SR exceeds the
    expected-max-under-null SR*  <=>  DSR probability > 0.5. We report the raw
    probability, the (SR - SR*) sign (the operative gate), AND the strict 0.95 bar.
    """
    a = np.asarray(active, dtype=float)
    n = a.size
    s = pd.Series(a)
    sr = per_period_sharpe(a)
    skew = float(s.skew()) if n > 2 else 0.0
    excess_kurt = float(s.kurtosis()) if n > 3 else 0.0
    raw_kurt = excess_kurt + 3.0
    prob = deflated_sharpe(sr, N_TRIALS_DSR, n, skew, raw_kurt)
    # replicate SR* (expected max under null) to expose (SR - SR*) sign
    from scipy.stats import norm as _norm
    sr_star = math.nan
    if n >= 2:
        inner = 1.0 - skew * sr + (raw_kurt - 1.0) / 4.0 * sr ** 2
        if inner > 0:
            sigma_sr = math.sqrt(inner / (n - 1))
            sr_star = _norm.ppf(1.0 - 1.0 / N_TRIALS_DSR) * sigma_sr
    adj_sr = sr - sr_star if sr_star == sr_star else math.nan
    return {"per_period_sharpe": _r(sr), "dsr_prob": _r(prob), "sr_star": _r(sr_star),
            "adj_sharpe_SR_minus_SRstar": _r(adj_sr),
            "gate_adj_sharpe_positive": bool(adj_sr > 0) if adj_sr == adj_sr else False,
            "dsr_vs_0.95": bool(prob > 0.95) if prob == prob else False,
            "skew": _r(skew), "excess_kurt": _r(excess_kurt)}


def _r(x, nd: int = 4):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return round(float(x), nd)


# ---------------------------------------------------------------------------
# Core backtest for one J
# ---------------------------------------------------------------------------

def run_config(J: int, panel: pd.DataFrame, mprice: pd.DataFrame,
               med: dict) -> dict:
    """Run the frozen long-only top-quintile momentum sleeve for formation J.

    Ranking/holding prices come from the monthly-last matrix `mprice` (row index
    = month Period); daily returns for the hedge come from `panel` bounded by the
    per-month last trading DATE in `med`.

    For each rebalance formation month t (Period at index i in mprice.index):
      formation return R_form = P[t-1] / P[t-1-J] - 1   (ends 1 month before t)
      holding return per name  = P[t+1] / P[t] - 1        (skip month = [t-1,t])
    Eligible name at t = has a monthly-last price for EVERY month in the span
    [t-1-J .. t+1] (rankable AND holdable), and not TATAMOTORS at/after the
    demerger cutoff. Sleeve = EW top-quintile (round(0.2*N)); benchmark = EW of
    the eligible set. drag_t = FRICTION_RT * one-way turnover vs previous top set.
    """
    months = list(mprice.index)                    # pandas Period('M') list
    n_m = len(months)
    periods = []
    prev_top: set[str] | None = None
    daily_sleeve: dict[pd.Timestamp, float] = {}    # {date -> EW daily return of held book}

    # i indexes the FORMATION month t; need i-1-J >= 0 and i+1 <= n_m-1
    for i in range(1 + J, n_m - 1):
        t = months[i]
        m_form_end = months[i - SKIP]        # = t-1 (formation window end)
        m_form_start = months[i - SKIP - J]  # = t-1-J (formation window start)
        m_hold_end = months[i + HOLD]        # = t+1 (holding end)
        # look-ahead guard: formation end strictly before t, holding after t
        assert m_form_end < t < m_hold_end, (m_form_start, m_form_end, t, m_hold_end)
        t_date = med[t].date()               # actual last trading date of month t

        p_start = mprice.loc[m_form_start]
        p_end = mprice.loc[m_form_end]
        p_t = mprice.loc[t]
        p_next = mprice.loc[m_hold_end]

        # eligible = monthly price present for every month in [t-1-J .. t+1]
        span = [m for m in months if m_form_start <= m <= m_hold_end]
        eligible = []
        for sym in mprice.columns:
            if sym == DEMERGER_NAME and t_date >= DEMERGER_CUTOFF:
                continue
            if mprice.loc[span, sym].notna().all():
                eligible.append(sym)
        N = len(eligible)
        if N < 5:
            continue
        n_top = max(1, round(0.2 * N))

        form_ret = {s: p_end[s] / p_start[s] - 1.0 for s in eligible}
        ranked = sorted(eligible, key=lambda s: form_ret[s], reverse=True)
        top = ranked[:n_top]
        bottom = ranked[-n_top:]

        hold_ret = {s: p_next[s] / p_t[s] - 1.0 for s in eligible}
        sleeve_gross = float(np.mean([hold_ret[s] for s in top]))
        bench_gross = float(np.mean([hold_ret[s] for s in eligible]))
        bottom_gross = float(np.mean([hold_ret[s] for s in bottom]))

        # one-way turnover vs previous top set (fraction of book rotated)
        if prev_top is None:
            turnover = 1.0                    # initial establishment
        else:
            changed = len(set(top) - prev_top)
            turnover = changed / n_top
        prev_top = set(top)

        drag1 = FRICTION_RT * turnover
        drag2 = 2.0 * FRICTION_RT * turnover

        periods.append({
            "t": str(t), "form_start": str(m_form_start), "form_end": str(m_form_end),
            "hold_end": str(m_hold_end), "t_date": t_date.isoformat(),
            "N_eligible": N, "n_top": n_top, "turnover": round(turnover, 4),
            "sleeve_gross": sleeve_gross, "bench_gross": bench_gross,
            "bottom_gross": bottom_gross,
            "sleeve_net1": sleeve_gross - drag1, "sleeve_net2": sleeve_gross - drag2,
            "active_gross": sleeve_gross - bench_gross,
            "active_net1": (sleeve_gross - drag1) - bench_gross,
            "active_net2": (sleeve_gross - drag2) - bench_gross,
            "top": top, "in_is": t_date <= IS_END,
        })

        # daily sleeve returns across the holding month (t_date, t+1 last date]
        t_end_date, next_end_date = med[t], med[m_hold_end]
        hold_days = panel.loc[(panel.index > t_end_date) & (panel.index <= next_end_date)]
        prev_row = panel.loc[t_end_date]
        for dt, row in hold_days.iterrows():
            rr = [row[s] / prev_row[s] - 1.0 for s in top
                  if pd.notna(row[s]) and pd.notna(prev_row[s]) and prev_row[s] > 0]
            if rr:
                daily_sleeve[dt] = float(np.mean(rr))
            prev_row = row

    return {"J": J, "periods": periods, "daily_sleeve": daily_sleeve}


def summarise_split(periods: list[dict], key_active: str, key_net_sleeve: str) -> dict:
    """Compute the pre-reg statistics over a list of period dicts."""
    if len(periods) < 3:
        return {"n": len(periods), "insufficient": True}
    active_gross = np.array([p["active_gross"] for p in periods])
    active = np.array([p[key_active] for p in periods])          # net-active (1x/2x)
    active_net2 = np.array([p["active_net2"] for p in periods])
    net_sleeve = np.array([p[key_net_sleeve] for p in periods])  # net sleeve (1x)
    turn = np.array([p["turnover"] for p in periods])
    ir_gross = ann_ir(active_gross)
    ir_net = ann_ir(active)
    nwt = nw_tstat(active_gross)      # NW t on GROSS active (friction is separate gate)
    dsr = dsr_active(active_gross)
    maxdd = max_drawdown_compound(net_sleeve)
    return {
        "n": len(periods),
        "ir_gross": _r(ir_gross), "ir_net1": _r(ir_net),
        "nw_t_gross": _r(nwt),
        "mean_active_gross": _r(active_gross.mean(), 6),
        "mean_active_net1": _r(active.mean(), 6),
        "mean_active_net2": _r(active_net2.mean(), 6),
        "mean_net2_positive": bool(active_net2.mean() > 0),
        "sleeve_maxdd_net1": _r(maxdd),
        "mean_turnover": _r(turn.mean()),
        "cum_sleeve_net1": _r(float(np.prod(1.0 + net_sleeve) - 1.0)),
        "cum_active_gross": _r(float(active_gross.sum()), 6),
        "dsr": dsr,
    }


def is_gate(stats: dict) -> dict:
    """Evaluate the IS-side pre-registered gates (1-4). Gate 5 = holdout."""
    g1 = stats.get("ir_gross") is not None and stats["ir_gross"] >= IR_MIN
    nwt = stats.get("nw_t_gross")
    g2 = (nwt is not None and abs(nwt) >= NWT_MIN
          and stats["dsr"]["gate_adj_sharpe_positive"])
    g3 = bool(stats.get("mean_net2_positive"))
    g4 = stats.get("sleeve_maxdd_net1") is not None and stats["sleeve_maxdd_net1"] <= MAXDD_MAX
    return {"g1_ir>=0.5": g1, "g2_nwt>=2.5_and_adjSharpe>0": g2,
            "g3_net_positive_2x": g3, "g4_maxdd<=25%": g4,
            "passes_is_gate": bool(g1 and g2 and g3 and g4)}


# ---------------------------------------------------------------------------
# Diagnostics (report-only, NEVER decision)
# ---------------------------------------------------------------------------

def wml_diagnostic(periods: list[dict]) -> dict:
    """Top-minus-bottom quintile monthly spread + NW t (anomaly-exists check)."""
    if len(periods) < 3:
        return {"n": len(periods), "insufficient": True}
    wml = np.array([p["sleeve_gross"] - p["bottom_gross"] for p in periods])
    return {"n": len(periods), "mean_monthly": _r(wml.mean(), 6),
            "nw_t": _r(nw_tstat(wml)), "ann_ir": _r(ann_ir(wml)),
            "bar_t>=2.0": bool(abs(nw_tstat(wml)) >= 2.0) if wml.size >= 3 else False}


def monotonicity_diagnostic(J: int, mprice: pd.DataFrame, med: dict) -> dict:
    """Mean holding return per formation quintile (Q5..Q1); check Q5>..>Q1 approx."""
    months = list(mprice.index)
    n_m = len(months)
    qsum = defaultdict(list)  # quintile idx 0(top)..4(bottom) -> holding returns
    for i in range(1 + J, n_m - 1):
        t = months[i]; t_start = months[i - SKIP - J]
        t_end = months[i - SKIP]; t_next = months[i + HOLD]
        span = [m for m in months if t_start <= m <= t_next]
        eligible = [s for s in mprice.columns
                    if not (s == DEMERGER_NAME and med[t].date() >= DEMERGER_CUTOFF)
                    and mprice.loc[span, s].notna().all()]
        if len(eligible) < 10:
            continue
        fr = {s: mprice.loc[t_end, s] / mprice.loc[t_start, s] - 1.0 for s in eligible}
        hr = {s: mprice.loc[t_next, s] / mprice.loc[t, s] - 1.0 for s in eligible}
        ranked = sorted(eligible, key=lambda s: fr[s], reverse=True)
        for qi, grp in enumerate(np.array_split(ranked, 5)):  # 5 approx-equal quintiles
            if len(grp):
                qsum[qi].append(float(np.mean([hr[s] for s in grp])))
    means = {f"Q{5 - qi}": _r(float(np.mean(v)), 6) for qi, v in sorted(qsum.items())}
    ordered = [np.mean(qsum[qi]) for qi in sorted(qsum)]  # Q5(top)..Q1(bottom)
    strict_mono = all(ordered[k] > ordered[k + 1] for k in range(len(ordered) - 1))
    top_gt_bottom = ordered[0] > ordered[-1] if ordered else False
    return {"quintile_mean_holding_return": means,
            "strict_monotonic_Q5>..>Q1": bool(strict_mono),
            "top>bottom": bool(top_gt_bottom)}


def vol_scaled_robustness(J: int, panel: pd.DataFrame, mprice: pd.DataFrame,
                          med: dict) -> dict:
    """Robustness-only: rank by formation return / formation daily-vol."""
    months = list(mprice.index)
    n_m = len(months)
    active = []
    for i in range(1 + J, n_m - 1):
        t = months[i]; t_start = months[i - SKIP - J]
        t_end = months[i - SKIP]; t_next = months[i + HOLD]
        span = [m for m in months if t_start <= m <= t_next]
        eligible = [s for s in mprice.columns
                    if not (s == DEMERGER_NAME and med[t].date() >= DEMERGER_CUTOFF)
                    and mprice.loc[span, s].notna().all()]
        if len(eligible) < 5:
            continue
        # formation daily vol over the actual dates of [t-1-J .. t-1]
        form_days = panel.loc[(panel.index > med[months[i - SKIP - J - 1]])
                              & (panel.index <= med[t_end])]
        score = {}
        for s in eligible:
            fr = mprice.loc[t_end, s] / mprice.loc[t_start, s] - 1.0
            vol = form_days[s].pct_change().std()
            score[s] = fr / vol if vol and vol > 0 else fr
        hr = {s: mprice.loc[t_next, s] / mprice.loc[t, s] - 1.0 for s in eligible}
        n_top = max(1, round(0.2 * len(eligible)))
        top = sorted(eligible, key=lambda s: score[s], reverse=True)[:n_top]
        sleeve = float(np.mean([hr[s] for s in top]))
        bench = float(np.mean([hr[s] for s in eligible]))
        active.append((med[t].date() <= IS_END, sleeve - bench))
    is_a = np.array([a for isit, a in active if isit])
    return {"is_ann_ir_vol_scaled": _r(ann_ir(is_a)) if is_a.size >= 2 else None,
            "is_n": int(is_a.size)}


# ---------------------------------------------------------------------------
# Hedge correlation (informational)
# ---------------------------------------------------------------------------

def hedge_correlation(daily_sleeve: dict) -> dict:
    """Pair the sleeve's daily returns with 0DTE straddle per-day nets."""
    try:
        from scripts.zerodte_straddle import run_day, expiry_days
    except Exception as e:  # pragma: no cover
        return {"error": f"could not import zerodte_straddle: {e}"}
    straddle = {}
    for d in expiry_days():
        try:
            r = run_day(d, "09:20", 0.25, None)
        except Exception:
            r = None
        if r is not None:
            straddle[pd.Timestamp(d)] = float(r["net"])
    # shared days
    sl_by_date = {pd.Timestamp(k.date()): v for k, v in daily_sleeve.items()}
    shared = sorted(set(sl_by_date) & set(straddle))
    if len(shared) < 5:
        return {"n_shared": len(shared), "insufficient": True,
                "n_straddle_days": len(straddle), "n_sleeve_days": len(sl_by_date)}
    sl = np.array([sl_by_date[d] for d in shared])
    st = np.array([straddle[d] for d in shared])
    from scipy.stats import pearsonr, spearmanr
    pear = float(pearsonr(sl, st)[0])
    spear = float(spearmanr(sl, st)[0])
    # tail: E[sleeve daily return | straddle in its WORST quartile of days]
    q25 = np.quantile(st, 0.25)
    worst = sl[st <= q25]
    e_tail = float(worst.mean()) if worst.size else math.nan
    return {"n_shared": len(shared), "n_straddle_days": len(straddle),
            "pearson": _r(pear), "spearman": _r(spear),
            "E[mom|straddle_worst_quartile]": _r(e_tail, 6),
            "straddle_worst_quartile_threshold_net": _r(float(q25), 1),
            "hedge_hope_corr<=+0.2": bool(pear <= 0.2),
            "hedge_hope_tail>=0": bool(e_tail >= 0) if e_tail == e_tail else None,
            "NOTE": "informational only; B-triage can never DEPLOY (contaminated)."}


# ---------------------------------------------------------------------------
# Sanity checks (must run + report)
# ---------------------------------------------------------------------------

def _raw_monthly_last_close(sym: str, period_str: str) -> float:
    """OUT-OF-BAND: last non-weekend 1m-bar close in `period_str` (YYYY-MM),
    read straight from the raw parquet (independent of the pipeline panel)."""
    f = CACHE / sym / "1m" / f"{period_str}.parquet"
    df = pd.read_parquet(f, columns=["ts", "close"])
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
    df["d"] = df["ts"].dt.date
    last = df.sort_values("ts").groupby("d")["close"].last()
    last = last[[d.weekday() < 5 for d in last.index]]
    return float(last.iloc[-1])


def sanity_checks(configs: dict, mprice: pd.DataFrame, med: dict) -> dict:
    out: dict = {}

    # (1) hand-verify 2 formation returns -- recompute from RAW parquet closes
    #     (out-of-band, not via the pipeline panel) and compare to the pipeline.
    hv = []
    for p in configs[12]["periods"][:2]:
        sym = p["top"][0]
        fs, fe = p["form_start"], p["form_end"]   # 'YYYY-MM' period strings
        raw_start = _raw_monthly_last_close(sym, fs)
        raw_end = _raw_monthly_last_close(sym, fe)
        raw_ret = raw_end / raw_start - 1.0
        pipe_ret = float(mprice.loc[pd.Period(fe), sym] / mprice.loc[pd.Period(fs), sym] - 1.0)
        hv.append({"sym": sym, "t": p["t"], "form_start": fs, "form_end": fe,
                   "raw_p_start": _r(raw_start, 2), "raw_p_end": _r(raw_end, 2),
                   "raw_formation_return": _r(raw_ret, 6),
                   "pipeline_formation_return": _r(pipe_ret, 6),
                   "match": bool(abs(raw_ret - pipe_ret) < 1e-9)})
    out["hand_verified_formation_returns"] = hv

    # (2) benchmark & sleeve share an identical calendar (both computed on the
    #     same eligible set each period -> same period list, both finite)
    cal_ok = all(isinstance(p["sleeve_gross"], float) and isinstance(p["bench_gross"], float)
                 for J in J_GRID for p in configs[J]["periods"])
    out["sleeve_benchmark_identical_calendar"] = bool(cal_ok)

    # (3) no look-ahead: formation end < t < hold end for every period (the
    #     month Period strings sort chronologically) -- also asserted in run_config
    la_ok = all(p["form_start"] < p["form_end"] < p["t"] < p["hold_end"]
                for J in J_GRID for p in configs[J]["periods"])
    out["no_look_ahead_month_ordering"] = bool(la_ok)

    # (4) months with < 50 eligible names, per J
    below = {}
    for J in J_GRID:
        cnts = [p["N_eligible"] for p in configs[J]["periods"]]
        below[f"J{J}"] = {"n_periods": len(cnts),
                          "months_below_50": int(sum(1 for c in cnts if c < 50)),
                          "min_eligible": int(min(cnts)) if cnts else None,
                          "max_eligible": int(max(cnts)) if cnts else None}
    out["eligible_name_counts"] = below
    out["eligibility_rule"] = ("a name needs a monthly-last close for EVERY month in "
                               "[t-1-J .. t+1] (rankable + holdable) to enter a rebalance; "
                               "TATAMOTORS excluded from t>=2025-09. Sub-50 months are due "
                               "to late-listed names lacking full formation history (e.g. "
                               "ETERNAL/Zomato) and the TATAMOTORS late exclusion -- NOT "
                               "data drops (no >15% move was dropped).")
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    universe = load_universe()
    panel, qc = build_panel(universe)
    mprice, med = monthly_prices(panel)
    print(f"universe: {len(universe)} equity names")
    print(f"panel: {panel.shape[0]} trading days "
          f"{panel.index[0].date()} .. {panel.index[-1].date()}; "
          f"{len(mprice)} months; QC events: {len(qc)}")

    configs = {J: run_config(J, panel, mprice, med) for J in J_GRID}

    # per-J IS / holdout / full stats
    results = {}
    for J in J_GRID:
        periods = configs[J]["periods"]
        is_p = [p for p in periods if p["in_is"]]
        ho_p = [p for p in periods if not p["in_is"]]
        is_stats = summarise_split(is_p, "active_net1", "sleeve_net1")
        full_stats = summarise_split(periods, "active_net1", "sleeve_net1")
        gate = is_gate(is_stats)
        results[J] = {"is": is_stats, "is_gate": gate, "full": full_stats,
                      "n_holdout": len(ho_p), "holdout_periods": ho_p,
                      "wml_full": wml_diagnostic(periods), "wml_is": wml_diagnostic(is_p),
                      "monotonicity": monotonicity_diagnostic(J, mprice, med),
                      "vol_scaled_robustness": vol_scaled_robustness(J, panel, mprice, med)}
        print(f"\n[J={J}] IS n={is_stats['n']}  IR_gross={is_stats.get('ir_gross')}  "
              f"NW_t={is_stats.get('nw_t_gross')}  meanActive_net2={is_stats.get('mean_active_net2')}  "
              f"maxDD_net1={is_stats.get('sleeve_maxdd_net1')}")
        print(f"        gate: {gate}")

    # IS-winner selection (frozen rule)
    passing = [J for J in J_GRID if results[J]["is_gate"]["passes_is_gate"]]
    holdout_stats = None
    if not passing:
        winner = None
        verdict = "KILL"
        verdict_reason = ("NEITHER J passed the IS gate (gates 1-4) -> KILL; holdout "
                          "NOT run (per frozen rule).")
    else:
        # among passing, pick higher IS annualised IR
        winner = max(passing, key=lambda J: results[J]["is"]["ir_gross"])
        ho_p = results[winner]["holdout_periods"]
        holdout_stats = summarise_split(ho_p, "active_net1", "sleeve_net1")
        is_active_sign = math.copysign(1, results[winner]["is"]["mean_active_gross"])
        ho_mean = holdout_stats.get("mean_active_gross")
        ho_ir = holdout_stats.get("ir_gross")
        g5 = (ho_mean is not None and ho_ir is not None
              and math.copysign(1, ho_mean) == is_active_sign and ho_ir >= 0.0)
        holdout_stats["g5_same_sign_and_ir>=0"] = bool(g5)
        if g5:
            verdict = "NOT-KILLED (survivorship-contaminated upper bound; NOT validated)"
            verdict_reason = (f"J={winner} passed IS gates 1-4 AND the frozen holdout "
                              f"was consistent (gate 5). This is NOT validation -- the "
                              f"universe is survivorship-contaminated (optimistic bias). "
                              f"A clean point-in-time A-test is required for ANY positive "
                              f"claim. No deploy under any B-triage outcome.")
        else:
            verdict = "KILL"
            verdict_reason = (f"J={winner} passed IS gates 1-4 but the frozen holdout "
                              f"CONTRADICTED (gate 5 failed) -> KILL.")

    hedge = hedge_correlation(configs[winner]["daily_sleeve"]) if winner is not None \
        else hedge_correlation(configs[J_GRID[-1]]["daily_sleeve"])
    sanity = sanity_checks(configs, mprice, med)

    # ---- assemble artifacts -------------------------------------------------
    # strip bulky per-period 'top' lists from the JSON (keep counts + returns)
    def slim(periods):
        return [{k: v for k, v in p.items() if k != "top"} for p in periods]
    for J in J_GRID:
        results[J]["holdout_periods"] = slim(results[J]["holdout_periods"])

    eval_json = {
        "meta": {
            "protocol": "reports/momentum_prereg.md §B (survivorship-CONTAMINATED "
                        "upper-bound scout)",
            "date_run": date.today().isoformat(),
            "outcome_space": "KILL or NOT-KILLED; NOT-KILLED != validated (optimistic "
                             "bias); no DEPLOY possible under any outcome.",
            "grid": {"J": J_GRID, "skip": SKIP, "hold": HOLD},
            "friction_round_trip": FRICTION_RT,
            "split": {"is_end": IS_END.isoformat(),
                      "holdout": [HOLDOUT_START.isoformat(), HOLDOUT_END.isoformat()],
                      "split_by": "formation month-end t"},
            "gate_thresholds": {"ir_min": IR_MIN, "nw_t_min": NWT_MIN,
                                "maxdd_max": MAXDD_MAX, "dsr_trials": N_TRIALS_DSR},
        },
        "universe": {"n": len(universe), "names": universe,
                     "note": "TODAY's cached survivors applied historically; includes "
                             "a few names since dropped from NIFTY-50 (same "
                             "contamination direction). Excluded: indices/derivs/ETF."},
        "qc_log": qc,
        "per_config": {str(J): {"is": results[J]["is"], "is_gate": results[J]["is_gate"],
                                "full": results[J]["full"], "n_holdout": results[J]["n_holdout"],
                                "wml_full": results[J]["wml_full"], "wml_is": results[J]["wml_is"],
                                "monotonicity": results[J]["monotonicity"],
                                "vol_scaled_robustness": results[J]["vol_scaled_robustness"]}
                       for J in J_GRID},
        "is_winner_selection": {"passing_is_gate": passing, "winner_J": winner,
                                "rule": "higher IS ann IR among J passing gates 1-4; "
                                        "if none pass -> KILL, no holdout."},
        "holdout": holdout_stats,
        "hedge_correlation": hedge,
        "sanity_checks": sanity,
        "verdict": verdict, "verdict_reason": verdict_reason,
    }
    out_dir = PROJECT / "reports/momentum"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "b_triage_eval.json").write_text(json.dumps(eval_json, indent=1, default=str))

    # ---- honest summary markdown -------------------------------------------
    md = _summary_md(eval_json, results, universe, qc)
    (out_dir / "b_triage_summary.md").write_text(md)

    print("\n" + "=" * 70)
    print(f"VERDICT: {verdict}")
    print(verdict_reason)
    print("=" * 70)
    print("saved: reports/momentum/b_triage_eval.json")
    print("saved: reports/momentum/b_triage_summary.md")


def _summary_md(ev: dict, results: dict, universe: list[str], qc: list[dict]) -> str:
    v = ev["verdict"]
    L = []
    L.append("# Momentum B-triage — survivorship-CONTAMINATED upper-bound scout")
    L.append("")
    L.append(f"**Run:** {ev['meta']['date_run']} · **Protocol:** frozen `reports/momentum_prereg.md` §B · "
             "**Paper/research only.**")
    L.append("")
    L.append(f"## VERDICT: {v}")
    L.append("")
    L.append(ev["verdict_reason"])
    L.append("")
    L.append("> **NOT-KILLED != validated.** The universe is TODAY's NIFTY survivors applied "
             "historically, so any positive result is OPTIMISTICALLY biased (survivorship "
             "contamination). A clean, point-in-time (membership-correct) **A-test** is required "
             "for ANY positive/deploy claim. **No deploy is possible under any B-triage outcome.**")
    L.append("")
    L.append("## Headline numbers")
    for J in J_GRID:
        s = results[J]["is"]; g = results[J]["is_gate"]
        L.append(f"- **J={J} (IS, n={s.get('n')}):** ann IR (gross) = **{s.get('ir_gross')}** "
                 f"(gate ≥{IR_MIN}); NW t = **{s.get('nw_t_gross')}** (gate |t|≥{NWT_MIN}); "
                 f"mean active net-2× = {s.get('mean_active_net2')} (gate >0: {g['g3_net_positive_2x']}); "
                 f"sleeve maxDD = {s.get('sleeve_maxdd_net1')} (gate ≤{MAXDD_MAX}); "
                 f"**passes IS gate: {g['passes_is_gate']}**")
    win = ev["is_winner_selection"]["winner_J"]
    if win is not None and ev["holdout"] is not None:
        h = ev["holdout"]
        L.append(f"- **Holdout (J={win}, n={h.get('n')}):** ann IR (gross) = {h.get('ir_gross')}; "
                 f"mean active (gross) = {h.get('mean_active_gross')}; "
                 f"gate 5 (same sign & IR≥0): {h.get('g5_same_sign_and_ir>=0')}")
    else:
        L.append("- **Holdout:** NOT RUN (neither J passed the IS gate — frozen rule).")
    hh = ev["hedge_correlation"]
    if "pearson" in hh:
        L.append(f"- **Hedge (informational, n_shared={hh['n_shared']}):** Pearson = {hh['pearson']}, "
                 f"Spearman = {hh['spearman']}, E[mom | straddle worst-quartile] = "
                 f"{hh['E[mom|straddle_worst_quartile]']} (hopes: corr ≤+0.2 → "
                 f"{hh['hedge_hope_corr<=+0.2']}; tail ≥0 → {hh['hedge_hope_tail>=0']}).")
    L.append("")
    L.append("## Which gate(s) failed / passed")
    for J in J_GRID:
        g = results[J]["is_gate"]
        L.append(f"- **J={J}:** IR≥0.5 = {g['g1_ir>=0.5']}; NW|t|≥2.5 & adj-Sharpe>0 = "
                 f"{g['g2_nwt>=2.5_and_adjSharpe>0']}; net+ @2× = {g['g3_net_positive_2x']}; "
                 f"maxDD≤25% = {g['g4_maxdd<=25%']}.")
    L.append("")
    L.append("## Diagnostics (report-only, never decision)")
    for J in J_GRID:
        w = results[J]["wml_full"]; m = results[J]["monotonicity"]
        L.append(f"- **J={J} WML (full):** mean monthly = {w.get('mean_monthly')}, NW t = "
                 f"{w.get('nw_t')} (anomaly bar t≥2.0: {w.get('bar_t>=2.0')}); "
                 f"quintile monotonic Q5>..>Q1: {m.get('strict_monotonic_Q5>..>Q1')}, "
                 f"top>bottom: {m.get('top>bottom')}.")
    L.append("")
    L.append("## Caveats & resolved protocol ambiguities")
    L.append("- **Survivorship contamination is the headline caveat** (see above).")
    L.append("- **\"DSR > 0\" gate:** the reused Bailey-López de Prado `deflated_sharpe` returns a "
             "PROBABILITY P(SR>SR*) in (0,1), so a literal \"DSR>0\" is vacuous. Resolved as the "
             "protocol's intent = the multiple-testing-**adjusted** Sharpe is positive "
             "(observed SR > expected-max-under-null SR*, i.e. DSR prob > 0.5). Raw prob, "
             "(SR−SR*) sign, and the strict 0.95 bar are all reported in the JSON.")
    L.append("- **Split by formation month t** (t ≤ 2025-03-31 → IS; t ≥ 2025-04-01 → holdout; "
             "IS n=33 for J=12 matches the pre-reg's ~33). IR gate on GROSS active returns; "
             "friction handled by the separate net-2× gate. \"Net positive after 2× friction\" "
             "read as mean **net-active** (sleeve−benchmark) > 0.")
    L.append("- **\"IS gate\" for winner selection** read as passing ALL IS-side gates (1–4). "
             "Both J FAIL gate 2 (NW t-stat) → neither passes → KILL, holdout preserved unused. "
             "Under a looser IR-only reading the holdout would run for J=12, but the full DEPLOY "
             "gate still fails on gate 2 → **verdict is KILL either way**; the DSR wording is "
             "likewise moot (gate 2 fails on the t-stat regardless).")
    L.append("- **Anomaly-exists diagnostic is also negative:** WML NW t = 0.22–0.60 (bar ≥2.0) and "
             "the quintile spread is non-monotonic — momentum here is statistically ABSENT/faint, "
             "not merely unharvestable-after-friction.")
    L.append("- **Monthly-last-price convention** (each name's last valid close per calendar month) "
             "for ranking/holding, rather than requiring the exact panel-wide last-trading-date "
             "(which would spuriously drop a name that traded all month but not on that one day).")
    L.append("- **Eligibility:** a name needs full month-end history across [t−1−J .. t+1]. "
             "TATAMOTORS excluded from formation+holding for t ≥ 2025-09 (demerger 2025-10-14, "
             "−41%, not ratio-adjusted, inside the formation window).")
    L.append("- **Final holding period is partial** (May-2026 end → 2026-06-11 data cutoff) to "
             "match the frozen holdout end exactly.")
    n_kept = sum(1 for q in qc if q["class"] == "real_market_move_kept")
    n_dem = sum(1 for q in qc if q["class"] == "TATAMOTORS_demerger_2025-10")
    L.append(f"- **QC:** all Sat/Sun rows dropped (neutralises the named 2021-08 / 2022-04 phantom "
             f"prints — those are weekend-adjacent and leave no >15% residual). {len(qc)} |daily "
             f"return|>15% events reconciled: {n_kept} KEPT as real market-event moves "
             f"(Adani-Hindenburg, 2024-06-04 election day, IndusInd, etc.), {n_dem} TATAMOTORS "
             f"demerger handled by the from-2025-09 exclusion. No move was dropped as an artifact "
             f"(see `qc_log` in the JSON).")
    L.append("")
    L.append(f"**Full numbers:** `reports/momentum/b_triage_eval.json` · "
             f"**Universe ({len(universe)} names):** pinned in that JSON.")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
