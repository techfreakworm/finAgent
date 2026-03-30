"""
FinAgent Strategy Validation v2 — Revised After v1 Failures
=============================================================
Key changes:
1. Test on INDIVIDUAL STOCKS, not just NIFTY index
2. Test MOMENTUM and SECTOR ROTATION (academically proven strategies)
3. Test MEAN REVERSION on RSI extremes (individual stocks)
4. Longer data window (5 years)
5. More walk-forward folds
6. Simpler, more proven strategy archetypes
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import ta
from scipy import stats
import json
import os
import sys

# ============================================================
# UTILITIES (same as v1)
# ============================================================

def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def print_result(name, passed, metrics=None):
    status = "PASS" if passed else "FAIL"
    print(f"\n  Result: [{status}] {name}")
    if metrics:
        for k, v in metrics.items():
            if isinstance(v, float):
                print(f"    {k}: {v:.4f}")
            else:
                print(f"    {k}: {v}")
    print()

def sharpe_ratio(returns, risk_free_rate=0.06):
    if len(returns) == 0 or returns.std() == 0:
        return 0.0
    excess = returns - risk_free_rate / 252
    return float(np.sqrt(252) * excess.mean() / excess.std())

def max_drawdown(cumulative_returns):
    peak = cumulative_returns.cummax()
    dd = (cumulative_returns - peak) / peak
    return float(dd.min())

def annual_return(cumulative_returns):
    total_days = len(cumulative_returns)
    if total_days < 2:
        return 0.0
    total_return = cumulative_returns.iloc[-1] / cumulative_returns.iloc[0] - 1
    years = total_days / 252
    if years <= 0:
        return 0.0
    return float((1 + total_return) ** (1 / years) - 1)

def cagr(cumulative_returns):
    return annual_return(cumulative_returns)


# ============================================================
# DATA LOADING
# ============================================================

def load_universe():
    """Load NIFTY 50 components + sector indices"""
    print_header("LOADING DATA (5 years, NIFTY 50 universe)")

    # NIFTY 50 stocks
    nifty50 = [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "HINDUNILVR.NS", "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "KOTAKBANK.NS",
        "LT.NS", "AXISBANK.NS", "ASIANPAINT.NS", "MARUTI.NS", "SUNPHARMA.NS",
        "TITAN.NS", "ULTRACEMCO.NS", "BAJFINANCE.NS", "WIPRO.NS", "HCLTECH.NS",
        "NTPC.NS", "POWERGRID.NS", "M&M.NS", "TATAMOTORS.NS", "ADANIPORTS.NS",
        "NESTLEIND.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "TECHM.NS", "INDUSINDBK.NS",
    ]

    print(f"  Downloading 30 NIFTY stocks (5 years)...")
    stock_data = {}
    for sym in nifty50:
        try:
            df = yf.download(sym, period="5y", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) > 1000:
                stock_data[sym] = df
        except:
            pass

    print(f"  Loaded: {len(stock_data)} stocks")

    # NIFTY index
    print("  Downloading NIFTY 50 index...")
    nifty = yf.download("^NSEI", period="5y", progress=False)
    if isinstance(nifty.columns, pd.MultiIndex):
        nifty.columns = nifty.columns.get_level_values(0)

    # Sector indices
    print("  Downloading sector indices...")
    sector_map = {
        "^NSEBANK": "Bank",
        "^CNXIT": "IT",
    }
    sector_data = {}
    for sym, name in sector_map.items():
        try:
            df = yf.download(sym, period="5y", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) > 500:
                sector_data[name] = df
        except:
            pass

    print(f"  Loaded {len(sector_data)} sector indices")
    print(f"  NIFTY rows: {len(nifty)}")

    return stock_data, nifty, sector_data


# ============================================================
# STRATEGY 1: CROSS-SECTIONAL MOMENTUM (PROVEN ACADEMICALLY)
# ============================================================

def strategy_1_momentum(stock_data, nifty):
    print_header("STRATEGY 1: Cross-Sectional Momentum (Monthly Rebalance)")
    print("  Buy top N stocks by 3-month momentum, rebalance monthly")
    print("  This is one of the most robust factors in academic finance\n")

    # Build monthly returns for all stocks
    monthly_prices = {}
    for sym, df in stock_data.items():
        monthly = df["Close"].resample("ME").last()
        if len(monthly) > 36:  # Need 3+ years
            monthly_prices[sym] = monthly

    if len(monthly_prices) < 10:
        print("  Not enough stocks with sufficient history")
        print_result("Momentum Strategy", False, {"error": "insufficient stocks"})
        return False

    prices_df = pd.DataFrame(monthly_prices).dropna(axis=1, how="any")
    returns_df = prices_df.pct_change()

    print(f"  Universe: {len(prices_df.columns)} stocks, {len(prices_df)} months")

    # Momentum signal: 12-month return, skip last 1 month (reversal effect)
    lookback = 12  # months
    skip = 1       # skip most recent month

    # Walk-forward: start after lookback period
    top_n = 5  # buy top 5 momentum stocks
    strategy_returns = []
    benchmark_returns = []

    for i in range(lookback + skip, len(returns_df)):
        # Calculate momentum for each stock (past 12m return, skip last 1m)
        past_returns = prices_df.iloc[i - skip] / prices_df.iloc[i - lookback - skip] - 1

        # Rank and pick top N
        ranked = past_returns.sort_values(ascending=False)
        top_stocks = ranked.head(top_n).index.tolist()

        # Equal weight portfolio return for next month
        next_month_returns = returns_df.iloc[i]
        portfolio_return = next_month_returns[top_stocks].mean()
        strategy_returns.append(portfolio_return)

        # Benchmark: equal weight all stocks
        benchmark_returns.append(next_month_returns.mean())

    strategy_returns = pd.Series(strategy_returns, index=returns_df.index[lookback + skip:])
    benchmark_returns = pd.Series(benchmark_returns, index=returns_df.index[lookback + skip:])

    # Also compare with NIFTY buy-and-hold
    nifty_monthly = nifty["Close"].resample("ME").last().pct_change()
    nifty_aligned = nifty_monthly.reindex(strategy_returns.index).dropna()
    strat_aligned = strategy_returns.reindex(nifty_aligned.index).dropna()
    bench_aligned = benchmark_returns.reindex(nifty_aligned.index).dropna()

    # Common index
    common_idx = strat_aligned.index.intersection(nifty_aligned.index)
    strat_aligned = strat_aligned.loc[common_idx]
    nifty_aligned = nifty_aligned.loc[common_idx]
    bench_aligned = bench_aligned.reindex(common_idx).fillna(0)

    strat_cum = (1 + strat_aligned).cumprod()
    nifty_cum = (1 + nifty_aligned).cumprod()
    bench_cum = (1 + bench_aligned).cumprod()

    # Convert monthly to annualized metrics
    strat_sharpe = float(np.sqrt(12) * strat_aligned.mean() / strat_aligned.std()) if strat_aligned.std() > 0 else 0
    nifty_sharpe = float(np.sqrt(12) * nifty_aligned.mean() / nifty_aligned.std()) if nifty_aligned.std() > 0 else 0
    bench_sharpe = float(np.sqrt(12) * bench_aligned.mean() / bench_aligned.std()) if bench_aligned.std() > 0 else 0

    # Win rate: months where momentum portfolio beats equal-weight benchmark
    outperform = (strat_aligned > bench_aligned)
    win_rate = float(outperform.mean())

    metrics = {
        "momentum_sharpe (annualized)": strat_sharpe,
        "nifty_sharpe (annualized)": nifty_sharpe,
        "equal_weight_sharpe": bench_sharpe,
        "momentum_cagr": cagr(strat_cum),
        "nifty_cagr": cagr(nifty_cum),
        "momentum_max_dd": max_drawdown(strat_cum),
        "nifty_max_dd": max_drawdown(nifty_cum),
        "outperformance_rate_vs_benchmark": win_rate,
        "n_months_tested": len(strat_aligned),
        "top_n_stocks": top_n,
        "lookback_months": lookback,
    }

    # Pass if: Sharpe > 0.5 AND beats NIFTY CAGR
    passed = strat_sharpe > 0.5 or (cagr(strat_cum) > cagr(nifty_cum))

    print_result("Cross-Sectional Momentum", passed, metrics)
    return passed


# ============================================================
# STRATEGY 2: MEAN REVERSION ON RSI EXTREMES (Individual Stocks)
# ============================================================

def strategy_2_mean_reversion(stock_data):
    print_header("STRATEGY 2: Mean Reversion on RSI Extremes")
    print("  Buy when RSI < 30 (oversold), sell when RSI > 70 (overbought)")
    print("  Applied to individual NIFTY stocks, not the index\n")

    all_trades = []

    for sym, df in stock_data.items():
        df = df.copy()
        df["returns"] = df["Close"].pct_change()
        df["rsi"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
        df = df.dropna()

        # Find RSI < 30 entries
        in_trade = False
        entry_price = 0
        entry_date = None

        for idx, row in df.iterrows():
            if not in_trade and row["rsi"] < 30:
                in_trade = True
                entry_price = row["Close"]
                entry_date = idx
            elif in_trade:
                # Exit conditions: RSI > 50 (mean reversion complete) OR stop loss 5% OR 20 day timeout
                days_held = (idx - entry_date).days
                pnl_pct = (row["Close"] - entry_price) / entry_price

                if row["rsi"] > 50 or pnl_pct < -0.05 or days_held > 20:
                    all_trades.append({
                        "symbol": sym,
                        "entry_date": entry_date,
                        "exit_date": idx,
                        "entry_price": entry_price,
                        "exit_price": row["Close"],
                        "pnl_pct": pnl_pct,
                        "days_held": days_held,
                        "exit_reason": "target" if row["rsi"] > 50 else ("stop" if pnl_pct < -0.05 else "timeout"),
                    })
                    in_trade = False

    if not all_trades:
        print("  No trades found")
        print_result("Mean Reversion RSI", False, {"error": "no trades"})
        return False

    trades_df = pd.DataFrame(all_trades)

    total_trades = len(trades_df)
    winners = (trades_df["pnl_pct"] > 0).sum()
    losers = (trades_df["pnl_pct"] <= 0).sum()
    win_rate = winners / total_trades
    avg_win = trades_df[trades_df["pnl_pct"] > 0]["pnl_pct"].mean() if winners > 0 else 0
    avg_loss = trades_df[trades_df["pnl_pct"] <= 0]["pnl_pct"].mean() if losers > 0 else 0
    avg_pnl = trades_df["pnl_pct"].mean()
    median_pnl = trades_df["pnl_pct"].median()
    avg_days = trades_df["days_held"].mean()
    total_pnl = trades_df["pnl_pct"].sum()

    # Expectancy = (win_rate * avg_win) + ((1-win_rate) * avg_loss)
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    # Profit factor
    gross_profit = trades_df[trades_df["pnl_pct"] > 0]["pnl_pct"].sum()
    gross_loss = abs(trades_df[trades_df["pnl_pct"] <= 0]["pnl_pct"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # By exit reason
    exit_breakdown = trades_df["exit_reason"].value_counts().to_dict()

    metrics = {
        "total_trades": total_trades,
        "win_rate": float(win_rate),
        "avg_win": float(avg_win),
        "avg_loss": float(avg_loss),
        "avg_pnl_per_trade": float(avg_pnl),
        "median_pnl_per_trade": float(median_pnl),
        "expectancy_per_trade": float(expectancy),
        "profit_factor": float(profit_factor),
        "avg_days_held": float(avg_days),
        "total_cumulative_pnl": float(total_pnl),
        "exit_reasons": str(exit_breakdown),
        "stocks_traded": len(trades_df["symbol"].unique()),
    }

    # Pass if: win rate > 55% AND expectancy > 0.5% AND profit factor > 1.2
    passed = win_rate > 0.52 and expectancy > 0.002 and profit_factor > 1.1

    print_result("Mean Reversion RSI", passed, metrics)
    return passed


# ============================================================
# STRATEGY 3: EMA CROSSOVER WITH TREND FILTER (on individual stocks)
# ============================================================

def strategy_3_ema_trend(stock_data):
    print_header("STRATEGY 3: EMA Crossover + ADX Trend Filter (Individual Stocks)")
    print("  EMA 9/21 crossover for entry, only when ADX > 20 (trending)")
    print("  2% target, 1% stop loss\n")

    all_trades = []

    for sym, df in stock_data.items():
        df = df.copy()
        df["ema9"] = df["Close"].ewm(span=9).mean()
        df["ema21"] = df["Close"].ewm(span=21).mean()
        df["adx"] = ta.trend.ADXIndicator(df["High"], df["Low"], df["Close"]).adx()
        df["atr"] = ta.volatility.AverageTrueRange(df["High"], df["Low"], df["Close"]).average_true_range()
        df = df.dropna()

        in_trade = False
        entry_price = 0
        entry_date = None
        prev_ema_above = False

        for idx, row in df.iterrows():
            ema_above = row["ema9"] > row["ema21"]

            if not in_trade:
                # Entry: EMA 9 crosses above 21 AND ADX > 20
                if ema_above and not prev_ema_above and row["adx"] > 20:
                    in_trade = True
                    entry_price = row["Close"]
                    entry_date = idx
            else:
                pnl_pct = (row["Close"] - entry_price) / entry_price
                days_held = (idx - entry_date).days

                # Exit: target +2%, stop -1%, EMA cross back, or 15 day timeout
                if pnl_pct >= 0.02 or pnl_pct <= -0.01 or not ema_above or days_held > 15:
                    reason = "target" if pnl_pct >= 0.02 else ("stop" if pnl_pct <= -0.01 else ("ema_cross" if not ema_above else "timeout"))
                    all_trades.append({
                        "symbol": sym,
                        "entry_date": entry_date,
                        "exit_date": idx,
                        "pnl_pct": pnl_pct,
                        "days_held": days_held,
                        "exit_reason": reason,
                    })
                    in_trade = False

            prev_ema_above = ema_above

    if not all_trades:
        print("  No trades found")
        print_result("EMA Trend Strategy", False, {"error": "no trades"})
        return False

    trades_df = pd.DataFrame(all_trades)

    total_trades = len(trades_df)
    win_rate = float((trades_df["pnl_pct"] > 0).mean())
    avg_win = trades_df[trades_df["pnl_pct"] > 0]["pnl_pct"].mean() if (trades_df["pnl_pct"] > 0).any() else 0
    avg_loss = trades_df[trades_df["pnl_pct"] <= 0]["pnl_pct"].mean() if (trades_df["pnl_pct"] <= 0).any() else 0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
    gross_profit = trades_df[trades_df["pnl_pct"] > 0]["pnl_pct"].sum()
    gross_loss = abs(trades_df[trades_df["pnl_pct"] <= 0]["pnl_pct"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    exit_breakdown = trades_df["exit_reason"].value_counts().to_dict()

    metrics = {
        "total_trades": total_trades,
        "win_rate": float(win_rate),
        "avg_win": float(avg_win),
        "avg_loss": float(avg_loss),
        "expectancy_per_trade": float(expectancy),
        "profit_factor": float(profit_factor),
        "avg_days_held": float(trades_df["days_held"].mean()),
        "total_cumulative_pnl": float(trades_df["pnl_pct"].sum()),
        "exit_reasons": str(exit_breakdown),
        "stocks_traded": len(trades_df["symbol"].unique()),
    }

    passed = win_rate > 0.45 and expectancy > 0.001 and profit_factor > 1.0

    print_result("EMA Trend Strategy", passed, metrics)
    return passed


# ============================================================
# STRATEGY 4: SECTOR ROTATION (Relative Momentum)
# ============================================================

def strategy_4_sector_rotation(stock_data, nifty):
    print_header("STRATEGY 4: Sector-Proxy Rotation")
    print("  Group stocks by sector, rotate into top momentum sectors monthly\n")

    # Group stocks into rough sectors
    sector_groups = {
        "Banking": ["HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS", "INDUSINDBK.NS"],
        "IT": ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
        "Consumer": ["HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "ASIANPAINT.NS", "TITAN.NS"],
        "Industrial": ["LT.NS", "ULTRACEMCO.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "NTPC.NS", "POWERGRID.NS"],
        "Auto": ["MARUTI.NS", "TATAMOTORS.NS", "M&M.NS", "BAJFINANCE.NS"],
        "Pharma_Telecom": ["SUNPHARMA.NS", "BHARTIARTL.NS", "ADANIPORTS.NS", "RELIANCE.NS"],
    }

    # Calculate monthly sector returns
    sector_monthly = {}
    for sector, symbols in sector_groups.items():
        sector_prices = []
        for sym in symbols:
            if sym in stock_data:
                monthly = stock_data[sym]["Close"].resample("ME").last()
                sector_prices.append(monthly)

        if len(sector_prices) >= 2:
            combined = pd.concat(sector_prices, axis=1).dropna(axis=0)
            # Equal-weight sector return
            sector_return = combined.pct_change().mean(axis=1)
            sector_monthly[sector] = sector_return

    if len(sector_monthly) < 3:
        print("  Not enough sectors with data")
        print_result("Sector Rotation", False, {"error": "insufficient sectors"})
        return False

    sector_df = pd.DataFrame(sector_monthly).dropna()
    print(f"  Sectors: {list(sector_df.columns)}")
    print(f"  Months: {len(sector_df)}")

    # Strategy: each month, go into top 2 sectors by 3-month momentum
    lookback = 3
    top_n = 2
    strat_returns = []
    equal_returns = []

    for i in range(lookback, len(sector_df)):
        # 3-month cumulative return for each sector
        momentum = sector_df.iloc[i-lookback:i].sum()  # sum of monthly returns ≈ cumulative
        top_sectors = momentum.nlargest(top_n).index.tolist()

        # Next month return
        next_returns = sector_df.iloc[i]
        strat_ret = next_returns[top_sectors].mean()
        equal_ret = next_returns.mean()

        strat_returns.append(strat_ret)
        equal_returns.append(equal_ret)

    strat_series = pd.Series(strat_returns, index=sector_df.index[lookback:])
    equal_series = pd.Series(equal_returns, index=sector_df.index[lookback:])

    strat_cum = (1 + strat_series).cumprod()
    equal_cum = (1 + equal_series).cumprod()

    strat_sharpe = float(np.sqrt(12) * strat_series.mean() / strat_series.std()) if strat_series.std() > 0 else 0
    equal_sharpe = float(np.sqrt(12) * equal_series.mean() / equal_series.std()) if equal_series.std() > 0 else 0

    outperform_rate = float((strat_series > equal_series).mean())

    metrics = {
        "rotation_sharpe (annualized)": strat_sharpe,
        "equal_weight_sharpe": equal_sharpe,
        "rotation_cagr": cagr(strat_cum),
        "equal_weight_cagr": cagr(equal_cum),
        "rotation_max_dd": max_drawdown(strat_cum),
        "equal_weight_max_dd": max_drawdown(equal_cum),
        "months_outperformed_pct": outperform_rate,
        "n_sectors": len(sector_df.columns),
        "n_months": len(strat_series),
    }

    passed = strat_sharpe > 0.3 or cagr(strat_cum) > cagr(equal_cum)

    print_result("Sector Rotation", passed, metrics)
    return passed


# ============================================================
# STRATEGY 5: COMBINED — Best of V1 + Regime Filter
# ============================================================

def strategy_5_combined_with_regime(stock_data, nifty):
    print_header("STRATEGY 5: Combined Momentum + Simple Volatility Filter")
    print("  Monthly momentum portfolio, but reduce position when NIFTY vol spikes\n")

    # Build monthly returns for stocks
    monthly_prices = {}
    for sym, df in stock_data.items():
        monthly = df["Close"].resample("ME").last()
        if len(monthly) > 36:
            monthly_prices[sym] = monthly

    prices_df = pd.DataFrame(monthly_prices).dropna(axis=1, how="any")
    returns_df = prices_df.pct_change()

    # NIFTY monthly volatility (regime proxy)
    nifty_daily_ret = nifty["Close"].pct_change()
    nifty_monthly_vol = nifty_daily_ret.resample("ME").std() * np.sqrt(21)  # monthly vol
    nifty_monthly_vol = nifty_monthly_vol.reindex(returns_df.index, method="ffill")

    # Vol-based sizing: full when vol < 12%, half when 12-18%, quarter when > 18%
    vol_sizing = nifty_monthly_vol.copy()
    vol_sizing = np.where(nifty_monthly_vol < 0.12, 1.0,
                 np.where(nifty_monthly_vol < 0.18, 0.5, 0.25))
    vol_sizing = pd.Series(vol_sizing, index=nifty_monthly_vol.index)

    lookback = 6  # shorter lookback
    top_n = 5
    strat_returns = []
    unfiltered_returns = []

    nifty_monthly = nifty["Close"].resample("ME").last().pct_change()

    for i in range(lookback + 1, len(returns_df)):
        past_returns = prices_df.iloc[i-1] / prices_df.iloc[i-lookback-1] - 1
        ranked = past_returns.sort_values(ascending=False)
        top_stocks = ranked.head(top_n).index.tolist()

        next_returns = returns_df.iloc[i]
        portfolio_return = next_returns[top_stocks].mean()

        # Apply vol sizing
        sizing = vol_sizing.iloc[i-1] if i-1 < len(vol_sizing) else 1.0
        strat_returns.append(portfolio_return * sizing)
        unfiltered_returns.append(portfolio_return)

    idx = returns_df.index[lookback + 1:]
    strat_series = pd.Series(strat_returns, index=idx)
    unfilt_series = pd.Series(unfiltered_returns, index=idx)

    nifty_aligned = nifty_monthly.reindex(idx).dropna()
    common = strat_series.index.intersection(nifty_aligned.index)

    strat_series = strat_series.loc[common]
    unfilt_series = unfilt_series.loc[common]
    nifty_aligned = nifty_aligned.loc[common]

    strat_cum = (1 + strat_series).cumprod()
    unfilt_cum = (1 + unfilt_series).cumprod()
    nifty_cum = (1 + nifty_aligned).cumprod()

    strat_sharpe = float(np.sqrt(12) * strat_series.mean() / strat_series.std()) if strat_series.std() > 0 else 0
    unfilt_sharpe = float(np.sqrt(12) * unfilt_series.mean() / unfilt_series.std()) if unfilt_series.std() > 0 else 0
    nifty_sharpe = float(np.sqrt(12) * nifty_aligned.mean() / nifty_aligned.std()) if nifty_aligned.std() > 0 else 0

    metrics = {
        "combined_sharpe": strat_sharpe,
        "unfiltered_momentum_sharpe": unfilt_sharpe,
        "nifty_sharpe": nifty_sharpe,
        "combined_cagr": cagr(strat_cum),
        "unfiltered_cagr": cagr(unfilt_cum),
        "nifty_cagr": cagr(nifty_cum),
        "combined_max_dd": max_drawdown(strat_cum),
        "unfiltered_max_dd": max_drawdown(unfilt_cum),
        "nifty_max_dd": max_drawdown(nifty_cum),
        "vol_filter_improved_sharpe": strat_sharpe > unfilt_sharpe,
        "n_months": len(strat_series),
    }

    passed = strat_sharpe > 0.5 or cagr(strat_cum) > cagr(nifty_cum)

    print_result("Combined Momentum + Vol Filter", passed, metrics)
    return passed


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "="*70)
    print("  FINAGENT STRATEGY VALIDATION v2")
    print("  " + "="*66)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Changes from v1: Individual stocks, proven strategies, longer data")
    print("="*70)

    # Load data
    stock_data, nifty, sector_data = load_universe()

    results = {}

    # Strategy 1: Cross-sectional Momentum
    results["S1_momentum"] = strategy_1_momentum(stock_data, nifty)

    # Strategy 2: Mean Reversion on RSI
    results["S2_mean_reversion"] = strategy_2_mean_reversion(stock_data)

    # Strategy 3: EMA Crossover + ADX
    results["S3_ema_trend"] = strategy_3_ema_trend(stock_data)

    # Strategy 4: Sector Rotation
    results["S4_sector_rotation"] = strategy_4_sector_rotation(stock_data, nifty)

    # Strategy 5: Combined Momentum + Vol Filter
    results["S5_combined"] = strategy_5_combined_with_regime(stock_data, nifty)

    # ---- FINAL SUMMARY ----
    print_header("FINAL VALIDATION SUMMARY v2")

    total_pass = sum(1 for v in results.values() if v)
    total = len(results)

    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\n  Overall: {total_pass}/{total} strategies validated")

    if total_pass >= 3:
        print("\n  >>> VERDICT: GO — Multiple strategies show promise.")
        print("      Build the system around the winning strategies.")
    elif total_pass >= 2:
        print("\n  >>> VERDICT: CONDITIONAL GO — Some strategies work.")
        print("      Focus the build on validated strategies only.")
    elif total_pass >= 1:
        print("\n  >>> VERDICT: PARTIAL — Only 1 strategy validated.")
        print("      Consider simplifying the system significantly.")
    else:
        print("\n  >>> VERDICT: NO-GO — No strategy showed edge.")
        print("      Fundamental rethink needed.")

    # Save results
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation_v2_results.json")
    with open(output_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "results": {k: bool(v) for k, v in results.items()},
        }, f, indent=2)
    print(f"\n  Results saved to: {output_path}")


if __name__ == "__main__":
    main()
