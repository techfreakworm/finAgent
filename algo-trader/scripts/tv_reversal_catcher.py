"""TV-mining validation: 'Reversal-Catcher' (RSI+Bollinger mean-reversion) on NIFTY.

Source: TradingView community script (RSI crossover from OB/OS + close back inside
Bollinger + EMA21/50 trend filter; "71% accuracy, ~1 trade/month on NIFTY"). This
is a FAITHFUL APPROXIMATION screen (not a byte-exact Pine port) — proportionate to
its low prior: it's (1) mean-reversion (gen-1 VWAP-rev KILLED), (2) directional on
the index (friction-hard), (3) a SWING strategy (out of our intraday mandate), and
(4) "~1 trade/month" (tiny sample). Expected = KILL; run for the honest record.

Pre-registered rules (frozen before looking at the holdout):
- 15-min bars from NIFTY 1m. RSI(14, Wilder), Bollinger(20, 1.5sd), EMA21, EMA50.
- SHORT (fade up): EMA21>EMA50 AND RSI crosses DOWN through 70 AND close<upperBB.
- LONG  (fade down): EMA21<EMA50 AND RSI crosses UP through 30 AND close>lowerBB.
- Enter next bar open; one position at a time.
- Exit: first of {RSI crosses 50 (reversion done) | adverse 2*ATR14 stop | 25-bar
  max-hold (~1 session)}. Gross pts = (exit-entry)*dir.
- Friction = 13.6 index pts round-trip (our established NIFTY-futures figure).
- Fence: entries < 2025-09-01 = fenced; >= = holdout (reported separately).

KILL if net expectancy <= 0 or PF < 1.0 (after friction) on the fenced set.

Run: cd /home/ubuntu/projects/algo-trader-strategies && \
     ./.venv/bin/python scripts/tv_reversal_catcher.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
NIFTY_DIR = PROJECT / "data/cache/NIFTY/1m"
FENCE = pd.Timestamp("2025-09-01", tz="Asia/Kolkata")
FRICTION_PTS = 13.6
RSI_N, BB_N, BB_K, EMA_F, EMA_S, ATR_N = 14, 20, 1.5, 21, 50, 14
MAX_HOLD = 25


def load_15m() -> pd.DataFrame:
    df = pd.concat([pd.read_parquet(f) for f in sorted(NIFTY_DIR.glob("*.parquet"))])
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert("Asia/Kolkata")
    df = df.set_index("ts").sort_index()
    o = df["open"].resample("15min").first()
    h = df["high"].resample("15min").max()
    l = df["low"].resample("15min").min()
    c = df["close"].resample("15min").last()
    v = df["volume"].resample("15min").sum()
    b = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()
    return b


def wilder_rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0.0); dn = (-d).clip(lower=0.0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / dn.ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([(df["high"] - df["low"]).abs(),
                    (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def main():
    b = load_15m()
    c = b["close"]
    rsi = wilder_rsi(c, RSI_N)
    ma = c.rolling(BB_N).mean(); sd = c.rolling(BB_N).std()
    upper, lower = ma + BB_K * sd, ma - BB_K * sd
    ema_f, ema_s = c.ewm(span=EMA_F, adjust=False).mean(), c.ewm(span=EMA_S, adjust=False).mean()
    a = atr(b, ATR_N)

    rsi_p = rsi.shift()
    cross_dn70 = (rsi_p >= 70) & (rsi < 70)
    cross_up30 = (rsi_p <= 30) & (rsi > 30)
    short_sig = cross_dn70 & (ema_f > ema_s) & (c < upper)
    long_sig = cross_up30 & (ema_f < ema_s) & (c > lower)

    idx = b.index
    o = b["open"].values; cl = c.values; rs = rsi.values; av = a.values
    n = len(b)
    short_arr, long_arr = short_sig.values, long_sig.values
    trades = []
    i = 0
    while i < n - 1:
        d = 1 if long_arr[i] else (-1 if short_arr[i] else 0)
        if d == 0:
            i += 1; continue
        entry = o[i + 1]                      # next-bar open
        if not np.isfinite(entry) or not np.isfinite(av[i]) or av[i] <= 0:
            i += 1; continue
        stop_dist = 2 * av[i]
        exit_px = None; j = i + 1
        end = min(i + 1 + MAX_HOLD, n - 1)
        while j <= end:
            # adverse stop (intrabar via close-proxy: use close move)
            adverse = (entry - cl[j]) if d == 1 else (cl[j] - entry)
            if adverse >= stop_dist:
                exit_px = cl[j]; break
            # reversion done: RSI back through 50
            if (d == 1 and rs[j] >= 50) or (d == -1 and rs[j] <= 50):
                exit_px = cl[j]; break
            j += 1
        if exit_px is None:
            exit_px = cl[end]; j = end
        gross = (exit_px - entry) * d
        trades.append({"ts": idx[i + 1], "dir": d, "gross": gross,
                       "net": gross - FRICTION_PTS})
        i = j + 1                              # no overlapping positions

    tdf = pd.DataFrame(trades)
    if tdf.empty:
        print("no trades"); return

    def stats(sub, label):
        if sub.empty:
            print(f"  {label:<10} n=0"); return
        net = sub["net"]; g = sub["gross"]
        wins = (net > 0).sum()
        pf_num = net[net > 0].sum(); pf_den = -net[net < 0].sum()
        pf = pf_num / pf_den if pf_den else float("inf")
        print(f"  {label:<10} n={len(sub):>4} grossAvg={g.mean():+.1f}pt netAvg={net.mean():+.1f}pt "
              f"win%={wins/len(sub):.0%} PF={pf:.2f} netSum={net.sum():+.0f}pt")

    print(f"Reversal-Catcher MR on NIFTY 15m | {len(tdf)} trades "
          f"{tdf['ts'].min().date()}→{tdf['ts'].max().date()} | friction {FRICTION_PTS}pt RT\n")
    fenced = tdf[tdf["ts"] < FENCE]; holdout = tdf[tdf["ts"] >= FENCE]
    print("BEFORE friction vs AFTER (net) — decision rests on net/PF, fenced set:")
    stats(fenced, "FENCED"); stats(holdout, "HOLDOUT"); stats(tdf, "ALL")
    longs = tdf[tdf.dir == 1]; shorts = tdf[tdf.dir == -1]
    print("\nby side (all):"); stats(longs, "LONG"); stats(shorts, "SHORT")
    f = fenced
    verdict = "KILL" if (f.empty or f["net"].mean() <= 0 or
                         (f["net"][f["net"] > 0].sum() / max(1e-9, -f["net"][f["net"] < 0].sum())) < 1.0) \
              else "SURVIVES-FENCED (check holdout)"
    print(f"\nPRE-REG VERDICT (fenced): {verdict}")


if __name__ == "__main__":
    main()
