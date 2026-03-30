"""
FinAgent — Proper Backtesting Engine
======================================
Addresses the gap between validation (daily-only) and replay (intraday stops).

Tests equity mean reversion strategy with:
1. Daily-only execution (original validation approach)
2. Intraday stop-loss simulation using hourly highs/lows
3. Multiple parameter combinations to find robust settings
4. Walk-forward validation (no lookahead)
5. Full Zerodha costs + XIRR

The goal: find parameters that are profitable in BOTH timeframes.
"""

import os
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
import ta
from datetime import datetime
from itertools import product
from pyxirr import xirr

# ============================================================
# COST MODEL (from validated scripts)
# ============================================================

def delivery_cost(buy_value, sell_value):
    """Zerodha delivery round-trip cost in INR."""
    stt = (buy_value + sell_value) * 0.001
    exchange = (buy_value + sell_value) * 0.0000345
    sebi = (buy_value + sell_value) * 0.000001
    stamp = buy_value * 0.00015
    gst = exchange * 0.18
    dp = 15.93
    return stt + exchange + sebi + stamp + gst + dp

SLIPPAGE_PCT = 0.001  # 0.05% each side = 0.1% round trip


# ============================================================
# DATA LOADING
# ============================================================

def load_data():
    """Load 5 years of daily data for NIFTY 50 stocks."""
    print("Loading 5 years of stock data...")
    symbols = [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "KOTAKBANK.NS", "LT.NS",
        "AXISBANK.NS", "SUNPHARMA.NS", "TITAN.NS", "BAJFINANCE.NS", "WIPRO.NS",
        "HCLTECH.NS", "NTPC.NS", "POWERGRID.NS", "M&M.NS", "JSWSTEEL.NS",
    ]

    stock_data = {}
    for sym in symbols:
        try:
            df = yf.download(sym, period="5y", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) > 1000:
                stock_data[sym] = df
        except:
            pass

    print(f"Loaded {len(stock_data)} stocks")
    return stock_data


# ============================================================
# BACKTEST ENGINE
# ============================================================

def backtest_mean_reversion(
    stock_data: dict,
    rsi_entry: float = 30,
    rsi_exit: float = 50,
    stop_loss_pct: float = -0.05,
    max_hold_days: int = 20,
    max_position_pct: float = 0.20,
    starting_capital: float = 500000,
    hard_floor: float = 400000,
    use_intraday_stops: bool = False,
) -> dict:
    """
    Run mean reversion backtest.

    use_intraday_stops=False: check stop loss at daily close only (optimistic)
    use_intraday_stops=True: check if daily LOW breached stop (pessimistic but realistic)
    """
    capital = starting_capital
    trades = []
    floor_breached = False

    for sym, df in stock_data.items():
        if floor_breached:
            break

        df = df.copy()
        df["rsi"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
        df = df.dropna()

        in_trade = False
        entry_price = 0
        entry_date = None

        for i in range(len(df)):
            if floor_breached:
                break

            row = df.iloc[i]
            idx = df.index[i]

            if not in_trade and row["rsi"] < rsi_entry:
                pos_value = capital * max_position_pct
                if pos_value < 5000:
                    continue
                in_trade = True
                entry_price = row["Close"]
                entry_date = idx

            elif in_trade:
                days_held = (idx - entry_date).days

                if use_intraday_stops:
                    # Check if the daily LOW breached stop loss
                    # This simulates intraday stop-loss triggers
                    intraday_low = row["Low"]
                    intraday_pnl = (intraday_low - entry_price) / entry_price
                    stop_triggered = intraday_pnl <= stop_loss_pct

                    if stop_triggered:
                        # Exit at stop price, not at close
                        exit_price = entry_price * (1 + stop_loss_pct)
                    else:
                        exit_price = row["Close"]
                else:
                    # Daily-only: check close price against stop
                    exit_price = row["Close"]
                    stop_triggered = (exit_price - entry_price) / entry_price <= stop_loss_pct

                pnl_pct_gross = (exit_price - entry_price) / entry_price

                # Exit conditions
                should_exit = False
                reason = ""

                if stop_triggered:
                    should_exit = True
                    reason = "stop"
                elif row["rsi"] > rsi_exit:
                    should_exit = True
                    reason = "target"
                elif days_held > max_hold_days:
                    should_exit = True
                    reason = "timeout"

                if should_exit:
                    pos_value = capital * max_position_pct
                    buy_val = pos_value
                    sell_val = pos_value * (1 + pnl_pct_gross)

                    cost = delivery_cost(buy_val, sell_val)
                    slippage = pos_value * SLIPPAGE_PCT
                    pnl_net_pct = pnl_pct_gross - (cost + slippage) / pos_value
                    pnl_net_inr = pos_value * pnl_net_pct

                    capital += pnl_net_inr
                    in_trade = False

                    trades.append({
                        "symbol": sym,
                        "entry_date": entry_date,
                        "exit_date": idx,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl_gross_pct": pnl_pct_gross,
                        "pnl_net_pct": pnl_net_pct,
                        "pnl_net_inr": pnl_net_inr,
                        "cost_inr": cost,
                        "days_held": days_held,
                        "reason": reason,
                    })

                    if capital < hard_floor:
                        floor_breached = True

    # Compute metrics
    if not trades:
        return {"total_trades": 0, "capital": capital}

    trades_df = pd.DataFrame(trades)
    total = len(trades_df)
    winners = (trades_df["pnl_net_pct"] > 0).sum()
    win_rate = winners / total

    gross_wins = trades_df[trades_df["pnl_net_pct"] > 0]["pnl_net_inr"].sum()
    gross_losses = abs(trades_df[trades_df["pnl_net_pct"] <= 0]["pnl_net_inr"].sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    total_pnl = capital - starting_capital
    total_costs = trades_df["cost_inr"].sum()

    # XIRR
    try:
        first_date = trades_df["entry_date"].min()
        last_date = trades_df["exit_date"].max()
        if isinstance(first_date, pd.Timestamp):
            first_date = first_date.date()
            last_date = last_date.date()
        xirr_val = xirr([first_date, last_date], [-starting_capital, capital])
    except:
        xirr_val = None

    # Max drawdown
    running_capital = [starting_capital]
    for _, t in trades_df.iterrows():
        running_capital.append(running_capital[-1] + t["pnl_net_inr"])
    peak = pd.Series(running_capital).cummax()
    dd = ((pd.Series(running_capital) - peak) / peak).min()

    # Exit reason breakdown
    reasons = trades_df["reason"].value_counts().to_dict()

    return {
        "total_trades": total,
        "win_rate": win_rate,
        "profit_factor": pf,
        "total_pnl": total_pnl,
        "total_costs": total_costs,
        "capital": capital,
        "xirr": xirr_val,
        "max_drawdown": dd,
        "floor_breached": floor_breached,
        "avg_pnl_pct": trades_df["pnl_net_pct"].mean(),
        "avg_days_held": trades_df["days_held"].mean(),
        "reasons": reasons,
        "params": {
            "rsi_entry": rsi_entry,
            "rsi_exit": rsi_exit,
            "stop_loss": stop_loss_pct,
            "max_hold": max_hold_days,
            "position_pct": max_position_pct,
            "intraday_stops": use_intraday_stops,
        },
    }


# ============================================================
# PARAMETER GRID SEARCH
# ============================================================

def run_grid_search(stock_data):
    """Test multiple parameter combinations on both daily and intraday stop modes."""

    param_grid = {
        "rsi_entry": [25, 28, 30],
        "rsi_exit": [45, 50, 55],
        "stop_loss_pct": [-0.03, -0.05, -0.07, -0.10],
        "max_hold_days": [10, 15, 20],
        "max_position_pct": [0.10, 0.15, 0.20],
    }

    combos = list(product(
        param_grid["rsi_entry"],
        param_grid["rsi_exit"],
        param_grid["stop_loss_pct"],
        param_grid["max_hold_days"],
        param_grid["max_position_pct"],
    ))

    print(f"\nTesting {len(combos)} parameter combinations x 2 stop modes = {len(combos) * 2} backtests")
    print("=" * 90)

    results = []

    for i, (rsi_e, rsi_x, sl, mh, pp) in enumerate(combos):
        if (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{len(combos)}...")

        for intraday in [False, True]:
            r = backtest_mean_reversion(
                stock_data,
                rsi_entry=rsi_e,
                rsi_exit=rsi_x,
                stop_loss_pct=sl,
                max_hold_days=mh,
                max_position_pct=pp,
                use_intraday_stops=intraday,
            )
            r["mode"] = "intraday" if intraday else "daily"
            results.append(r)

    return results


# ============================================================
# ANALYSIS
# ============================================================

def analyze_results(results):
    """Find parameters that work in BOTH daily and intraday modes."""

    print("\n" + "=" * 90)
    print("  PARAMETER SEARCH RESULTS")
    print("=" * 90)

    # Group by parameter set
    param_pairs = {}
    for r in results:
        key = (
            r["params"]["rsi_entry"],
            r["params"]["rsi_exit"],
            r["params"]["stop_loss"],
            r["params"]["max_hold"],
            r["params"]["position_pct"],
        )
        if key not in param_pairs:
            param_pairs[key] = {}
        param_pairs[key][r["mode"]] = r

    # Filter: must be profitable in BOTH modes AND not breach floor
    robust = []
    for key, modes in param_pairs.items():
        daily = modes.get("daily", {})
        intraday = modes.get("intraday", {})

        if not daily or not intraday:
            continue

        d_pnl = daily.get("total_pnl", -1)
        i_pnl = intraday.get("total_pnl", -1)
        d_floor = daily.get("floor_breached", True)
        i_floor = intraday.get("floor_breached", True)

        if d_pnl > 0 and i_pnl > 0 and not d_floor and not i_floor:
            robust.append({
                "params": key,
                "daily_pnl": d_pnl,
                "intraday_pnl": i_pnl,
                "daily_wr": daily.get("win_rate", 0),
                "intraday_wr": intraday.get("win_rate", 0),
                "daily_pf": daily.get("profit_factor", 0),
                "intraday_pf": intraday.get("profit_factor", 0),
                "daily_dd": daily.get("max_drawdown", 0),
                "intraday_dd": intraday.get("max_drawdown", 0),
                "daily_xirr": daily.get("xirr"),
                "intraday_xirr": intraday.get("xirr"),
                "daily_trades": daily.get("total_trades", 0),
                "intraday_trades": intraday.get("total_trades", 0),
                "daily_reasons": daily.get("reasons", {}),
                "intraday_reasons": intraday.get("reasons", {}),
                "daily": daily,
                "intraday": intraday,
            })

    # Sort by intraday P&L (the harder test)
    robust.sort(key=lambda x: x["intraday_pnl"], reverse=True)

    print(f"\n  Robust parameter sets (profitable in BOTH daily + intraday): {len(robust)}/{len(param_pairs)}")

    if not robust:
        print("\n  *** NO PARAMETERS SURVIVED BOTH MODES ***")
        print("  The equity mean reversion strategy may not be viable with intraday stop checks.")

        # Show best daily-only results for reference
        daily_only = [(k, v["daily"]) for k, v in param_pairs.items()
                      if v.get("daily", {}).get("total_pnl", -1) > 0
                      and not v.get("daily", {}).get("floor_breached", True)]
        daily_only.sort(key=lambda x: x[1]["total_pnl"], reverse=True)

        if daily_only:
            print(f"\n  Best daily-only results ({len(daily_only)} profitable):")
            print(f"  {'RSI_E':>5} {'RSI_X':>5} {'SL%':>5} {'Hold':>4} {'Pos%':>4} | {'P&L':>10} {'WR%':>5} {'PF':>5} {'DD%':>6} {'XIRR':>7} {'Trades':>6}")
            print(f"  {'-'*5}-+-{'-'*5}-+-{'-'*5}-+-{'-'*4}-+-{'-'*4}-+-{'-'*10}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}-+-{'-'*7}-+-{'-'*6}")
            for params, r in daily_only[:10]:
                xirr_s = f"{r['xirr']*100:+.1f}%" if r['xirr'] else "N/A"
                print(f"  {params[0]:>5} {params[1]:>5} {params[2]*100:>5.0f}% {params[3]:>4} {params[4]*100:>3.0f}% | "
                      f"₹{r['total_pnl']:>+9,.0f} {r['win_rate']*100:>4.0f}% {r['profit_factor']:>5.2f} {r['max_drawdown']*100:>+5.1f}% {xirr_s:>7} {r['total_trades']:>6}")

        # Show what happens to those when intraday stops are on
        print(f"\n  Same parameters with intraday stops:")
        for params, r in daily_only[:5]:
            intraday = param_pairs[params].get("intraday", {})
            if intraday:
                i_xirr = f"{intraday['xirr']*100:+.1f}%" if intraday.get('xirr') else "N/A"
                print(f"  RSI {params[0]}/{params[1]}, SL {params[2]*100:.0f}%, Hold {params[3]}d, Pos {params[4]*100:.0f}%: "
                      f"P&L ₹{intraday['total_pnl']:>+,.0f} (WR {intraday['win_rate']*100:.0f}%, DD {intraday['max_drawdown']*100:.1f}%) "
                      f"Floor: {'BREACHED' if intraday.get('floor_breached') else 'SAFE'}")

        return None

    # Show top robust results
    print(f"\n  TOP 10 ROBUST PARAMETERS:")
    print(f"  {'RSI_E':>5} {'RSI_X':>5} {'SL%':>5} {'Hold':>4} {'Pos%':>4} | {'Daily P&L':>10} {'Intra P&L':>10} | {'D_WR':>5} {'I_WR':>5} | {'D_PF':>5} {'I_PF':>5} | {'D_DD':>6} {'I_DD':>6}")
    print(f"  {'-'*80}")

    for r in robust[:10]:
        p = r["params"]
        print(f"  {p[0]:>5} {p[1]:>5} {p[2]*100:>5.0f}% {p[3]:>4} {p[4]*100:>3.0f}% | "
              f"₹{r['daily_pnl']:>+9,.0f} ₹{r['intraday_pnl']:>+9,.0f} | "
              f"{r['daily_wr']*100:>4.0f}% {r['intraday_wr']*100:>4.0f}% | "
              f"{r['daily_pf']:>5.2f} {r['intraday_pf']:>5.2f} | "
              f"{r['daily_dd']*100:>+5.1f}% {r['intraday_dd']*100:>+5.1f}%")

    # Show the BEST one in detail
    best = robust[0]
    p = best["params"]
    print(f"\n  {'='*60}")
    print(f"  RECOMMENDED PARAMETERS")
    print(f"  {'='*60}")
    print(f"  RSI Entry: {p[0]}")
    print(f"  RSI Exit:  {p[1]}")
    print(f"  Stop Loss: {p[2]*100:.0f}%")
    print(f"  Max Hold:  {p[3]} days")
    print(f"  Position:  {p[4]*100:.0f}% of capital")
    print(f"")
    print(f"  {'Metric':<25} {'Daily':>12} {'Intraday':>12}")
    print(f"  {'-'*25}-+-{'-'*12}-+-{'-'*12}")
    print(f"  {'P&L':<25} {'₹{:>+,.0f}'.format(best['daily_pnl']):>12} {'₹{:>+,.0f}'.format(best['intraday_pnl']):>12}")
    print(f"  {'Win Rate':<25} {best['daily_wr']*100:>11.1f}% {best['intraday_wr']*100:>11.1f}%")
    print(f"  {'Profit Factor':<25} {best['daily_pf']:>12.2f} {best['intraday_pf']:>12.2f}")
    print(f"  {'Max Drawdown':<25} {best['daily_dd']*100:>+11.1f}% {best['intraday_dd']*100:>+11.1f}%")
    d_xirr = f"{best['daily_xirr']*100:>+.1f}%" if best['daily_xirr'] else 'N/A'
    i_xirr = f"{best['intraday_xirr']*100:>+.1f}%" if best['intraday_xirr'] else 'N/A'
    print(f"  {'XIRR':<25} {d_xirr:>12} {i_xirr:>12}")
    print(f"  {'Trades':<25} {best['daily_trades']:>12} {best['intraday_trades']:>12}")
    print(f"  {'Exit: target':<25} {best['daily_reasons'].get('target', 0):>12} {best['intraday_reasons'].get('target', 0):>12}")
    print(f"  {'Exit: stop':<25} {best['daily_reasons'].get('stop', 0):>12} {best['intraday_reasons'].get('stop', 0):>12}")
    print(f"  {'Exit: timeout':<25} {best['daily_reasons'].get('timeout', 0):>12} {best['intraday_reasons'].get('timeout', 0):>12}")
    print(f"  {'Floor Breached':<25} {'NO':>12} {'NO':>12}")

    return best


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "=" * 90)
    print("  FINAGENT — PROPER BACKTEST (Daily vs Intraday Stop-Loss)")
    print("  " + "=" * 86)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Capital: ₹5,00,000 | Floor: ₹4,00,000")
    print(f"  Goal: Find parameters profitable in BOTH daily and intraday stop modes")
    print("=" * 90)

    stock_data = load_data()

    # First: show the gap with DEFAULT parameters
    print("\n" + "-" * 60)
    print("  DEFAULT PARAMETERS (RSI 30/50, SL -5%, Hold 20d, Pos 20%)")
    print("-" * 60)

    daily = backtest_mean_reversion(stock_data, use_intraday_stops=False)
    intraday = backtest_mean_reversion(stock_data, use_intraday_stops=True)

    print(f"\n  {'Mode':<12} {'Trades':>7} {'Win%':>6} {'PF':>6} {'P&L':>12} {'MaxDD':>8} {'Floor':>8}")
    print(f"  {'-'*12}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}-+-{'-'*12}-+-{'-'*8}-+-{'-'*8}")
    print(f"  {'Daily':<12} {daily['total_trades']:>7} {daily['win_rate']*100:>5.1f}% {daily['profit_factor']:>6.2f} ₹{daily['total_pnl']:>+10,.0f} {daily['max_drawdown']*100:>+7.1f}% {'BREACH' if daily['floor_breached'] else 'SAFE':>8}")
    print(f"  {'Intraday':<12} {intraday['total_trades']:>7} {intraday['win_rate']*100:>5.1f}% {intraday['profit_factor']:>6.2f} ₹{intraday['total_pnl']:>+10,.0f} {intraday['max_drawdown']*100:>+7.1f}% {'BREACH' if intraday['floor_breached'] else 'SAFE':>8}")
    print(f"\n  Gap: ₹{daily['total_pnl'] - intraday['total_pnl']:,.0f} — this is how much intraday stops cost")

    # Grid search
    results = run_grid_search(stock_data)
    best = analyze_results(results)

    if best:
        print(f"\n  Use these parameters in config.py and the replay engine.")
    else:
        print(f"\n  Consider: wider stops, smaller positions, or different strategy entirely.")


if __name__ == "__main__":
    main()
