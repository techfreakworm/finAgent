"""
FinAgent Strategy Validation v3 — With Realistic Indian Market Costs
=====================================================================
Accounts for:
  - Brokerage (Zerodha: ₹0 delivery, ₹20/order intraday)
  - STT (Securities Transaction Tax): 0.1% buy+sell delivery, 0.025% sell intraday
  - Exchange Transaction Charges (NSE): 0.00345%
  - SEBI Turnover Charges: ₹10/crore (0.0001%)
  - Stamp Duty: 0.015% on buy (delivery), 0.003% (intraday)
  - GST: 18% on (brokerage + exchange charges)
  - Slippage: 0.05% per trade (conservative for NIFTY 50 stocks)

Starting capital: ₹5,00,000
Position sizing: equal-weight, max 20% per stock
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

# ============================================================
# INDIAN MARKET COST MODEL
# ============================================================

class IndianMarketCosts:
    """Realistic cost model for Indian equity markets (Zerodha-like broker)"""

    def __init__(self, trade_type="delivery"):
        self.trade_type = trade_type

    def calculate_round_trip_cost_pct(self, buy_value, sell_value):
        """
        Returns total cost as a fraction of trade value for a round trip (buy + sell).
        All values in INR.
        """
        if self.trade_type == "delivery":
            return self._delivery_costs(buy_value, sell_value)
        else:
            return self._intraday_costs(buy_value, sell_value)

    def _delivery_costs(self, buy_value, sell_value):
        """Delivery (CNC) trade costs — Zerodha"""
        # Brokerage: ₹0 for delivery on Zerodha
        brokerage_buy = 0
        brokerage_sell = 0

        # STT: 0.1% on both buy and sell for delivery
        stt_buy = buy_value * 0.001
        stt_sell = sell_value * 0.001

        # Exchange Transaction Charges (NSE): 0.00345% on turnover
        etc_buy = buy_value * 0.0000345
        etc_sell = sell_value * 0.0000345

        # SEBI Charges: ₹10 per crore = 0.0001%
        sebi_buy = buy_value * 0.000001
        sebi_sell = sell_value * 0.000001

        # Stamp Duty: 0.015% on buy side only (delivery)
        stamp = buy_value * 0.00015

        # GST: 18% on (brokerage + exchange charges)
        gst_buy = (brokerage_buy + etc_buy) * 0.18
        gst_sell = (brokerage_sell + etc_sell) * 0.18

        # DP charges: ₹15.93 per sell transaction (Zerodha)
        dp_charges = 15.93

        total = (brokerage_buy + brokerage_sell +
                 stt_buy + stt_sell +
                 etc_buy + etc_sell +
                 sebi_buy + sebi_sell +
                 stamp +
                 gst_buy + gst_sell +
                 dp_charges)

        avg_value = (buy_value + sell_value) / 2
        return total / avg_value if avg_value > 0 else 0

    def _intraday_costs(self, buy_value, sell_value):
        """Intraday (MIS) trade costs — Zerodha"""
        # Brokerage: ₹20 per order or 0.03% whichever is lower
        brokerage_buy = min(20, buy_value * 0.0003)
        brokerage_sell = min(20, sell_value * 0.0003)

        # STT: 0.025% on sell side only for intraday
        stt_sell = sell_value * 0.00025

        # Exchange Transaction Charges: 0.00345%
        etc_buy = buy_value * 0.0000345
        etc_sell = sell_value * 0.0000345

        # SEBI: 0.0001%
        sebi_buy = buy_value * 0.000001
        sebi_sell = sell_value * 0.000001

        # Stamp Duty: 0.003% on buy side (intraday)
        stamp = buy_value * 0.00003

        # GST: 18% on (brokerage + exchange charges)
        gst_buy = (brokerage_buy + etc_buy) * 0.18
        gst_sell = (brokerage_sell + etc_sell) * 0.18

        total = (brokerage_buy + brokerage_sell +
                 stt_sell +
                 etc_buy + etc_sell +
                 sebi_buy + sebi_sell +
                 stamp +
                 gst_buy + gst_sell)

        avg_value = (buy_value + sell_value) / 2
        return total / avg_value if avg_value > 0 else 0


SLIPPAGE_PCT = 0.0005  # 0.05% slippage per side (conservative for liquid NIFTY 50 stocks)
STARTING_CAPITAL = 500000  # ₹5 lakh

# ============================================================
# UTILITIES
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
                if abs(v) > 1000:
                    print(f"    {k}: ₹{v:,.0f}")
                else:
                    print(f"    {k}: {v:.4f}")
            else:
                print(f"    {k}: {v}")
    print()

def sharpe_ratio(returns, risk_free_rate=0.06):
    if len(returns) == 0 or returns.std() == 0:
        return 0.0
    excess = returns - risk_free_rate / 252
    return float(np.sqrt(252) * excess.mean() / excess.std())

def max_drawdown(equity_curve):
    peak = equity_curve.cummax()
    dd = (equity_curve - peak) / peak
    return float(dd.min())

def cagr(equity_curve):
    total_days = len(equity_curve)
    if total_days < 2 or equity_curve.iloc[0] <= 0:
        return 0.0
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1
    years = total_days / 252
    if years <= 0:
        return 0.0
    return float((1 + total_return) ** (1 / years) - 1)


# ============================================================
# DATA LOADING
# ============================================================

def load_data():
    print_header("LOADING DATA (5 years)")

    nifty50_symbols = [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "HINDUNILVR.NS", "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "KOTAKBANK.NS",
        "LT.NS", "AXISBANK.NS", "ASIANPAINT.NS", "MARUTI.NS", "SUNPHARMA.NS",
        "TITAN.NS", "ULTRACEMCO.NS", "BAJFINANCE.NS", "WIPRO.NS", "HCLTECH.NS",
        "NTPC.NS", "POWERGRID.NS", "M&M.NS", "ADANIPORTS.NS",
        "NESTLEIND.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "TECHM.NS", "INDUSINDBK.NS",
    ]

    print(f"  Downloading {len(nifty50_symbols)} stocks (5y)...")
    stock_data = {}
    for sym in nifty50_symbols:
        try:
            df = yf.download(sym, period="5y", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) > 1000:
                stock_data[sym] = df
        except:
            pass
    print(f"  Loaded: {len(stock_data)} stocks")

    print("  Downloading NIFTY 50 index...")
    nifty = yf.download("^NSEI", period="5y", progress=False)
    if isinstance(nifty.columns, pd.MultiIndex):
        nifty.columns = nifty.columns.get_level_values(0)
    print(f"  NIFTY rows: {len(nifty)}")

    # Print cost model for reference
    costs = IndianMarketCosts("delivery")
    sample_cost = costs.calculate_round_trip_cost_pct(100000, 101000)
    print(f"\n  Cost model (₹1L delivery round-trip): {sample_cost*100:.3f}%")
    print(f"  Slippage per side: {SLIPPAGE_PCT*100:.2f}%")
    print(f"  Total round-trip friction (cost + slippage): ~{(sample_cost + 2*SLIPPAGE_PCT)*100:.3f}%")
    print(f"  Starting capital: ₹{STARTING_CAPITAL:,}")

    return stock_data, nifty


# ============================================================
# STRATEGY 1: MONTHLY MOMENTUM (Delivery, with full costs)
# ============================================================

def strategy_1_momentum(stock_data, nifty):
    print_header("STRATEGY 1: Cross-Sectional Momentum (Monthly Rebalance)")
    print("  Buy top 5 stocks by 12-month momentum")
    print("  Delivery trades, equal-weight, monthly rebalance\n")

    costs = IndianMarketCosts("delivery")

    # Build monthly close prices
    monthly_prices = {}
    for sym, df in stock_data.items():
        monthly = df["Close"].resample("ME").last()
        if len(monthly) > 36:
            monthly_prices[sym] = monthly

    prices_df = pd.DataFrame(monthly_prices).dropna(axis=1, how="any")
    n_stocks = len(prices_df.columns)
    print(f"  Universe: {n_stocks} stocks, {len(prices_df)} months")

    lookback = 12
    skip = 1
    top_n = 5

    capital = STARTING_CAPITAL
    equity_curve = [capital]
    dates = []
    total_costs_paid = 0
    total_slippage_paid = 0
    n_rebalances = 0
    prev_holdings = {}  # symbol -> weight

    nifty_monthly = nifty["Close"].resample("ME").last()

    for i in range(lookback + skip, len(prices_df)):
        date = prices_df.index[i]
        dates.append(date)

        # Calculate momentum
        momentum = prices_df.iloc[i - skip] / prices_df.iloc[i - lookback - skip] - 1
        ranked = momentum.sort_values(ascending=False)
        top_stocks = ranked.head(top_n).index.tolist()

        # Current month returns (for each stock)
        current_returns = prices_df.iloc[i] / prices_df.iloc[i-1] - 1

        # Portfolio return (equal weight among top stocks)
        gross_return = current_returns[top_stocks].mean()

        # Calculate turnover (how many positions changed)
        new_holdings = set(top_stocks)
        old_holdings = set(prev_holdings.keys())
        sells = old_holdings - new_holdings
        buys = new_holdings - old_holdings
        turnover_pct = (len(sells) + len(buys)) / (2 * top_n) if top_n > 0 else 0

        # Cost per stock position (buy_value ≈ capital / top_n)
        position_value = capital / top_n

        # Costs for sells
        sell_cost = 0
        for _ in sells:
            sell_value = position_value * (1 + gross_return)  # approximate
            round_trip_cost = costs.calculate_round_trip_cost_pct(position_value, sell_value)
            sell_cost += position_value * round_trip_cost
            sell_cost += position_value * SLIPPAGE_PCT * 2  # slippage both sides

        # Costs for buys (new positions)
        buy_cost = 0
        for _ in buys:
            round_trip_frac = costs.calculate_round_trip_cost_pct(position_value, position_value)
            # Only entry cost for new buys (exit cost will be charged when selling)
            buy_cost += position_value * (round_trip_frac / 2)
            buy_cost += position_value * SLIPPAGE_PCT

        total_trade_cost = sell_cost + buy_cost
        total_costs_paid += total_trade_cost
        total_slippage_paid += (len(sells) * position_value * SLIPPAGE_PCT * 2 +
                                 len(buys) * position_value * SLIPPAGE_PCT)

        # Net return
        net_return = gross_return - (total_trade_cost / capital)
        capital = capital * (1 + net_return)
        equity_curve.append(capital)
        n_rebalances += 1

        prev_holdings = {s: 1/top_n for s in top_stocks}

    equity_series = pd.Series(equity_curve[1:], index=dates)

    # NIFTY benchmark (buy-and-hold from same start)
    nifty_aligned = nifty_monthly.reindex(dates).dropna()
    common = equity_series.index.intersection(nifty_aligned.index)
    nifty_aligned = nifty_aligned.loc[common]
    nifty_cum = (nifty_aligned / nifty_aligned.iloc[0]) * STARTING_CAPITAL

    # Daily-equivalent returns for Sharpe
    monthly_returns = equity_series.pct_change().dropna()
    nifty_monthly_ret = nifty_aligned.pct_change().dropna()

    strat_sharpe = float(np.sqrt(12) * (monthly_returns.mean() - 0.06/12) / monthly_returns.std()) if monthly_returns.std() > 0 else 0
    nifty_sharpe = float(np.sqrt(12) * (nifty_monthly_ret.mean() - 0.06/12) / nifty_monthly_ret.std()) if nifty_monthly_ret.std() > 0 else 0

    net_profit = capital - STARTING_CAPITAL

    metrics = {
        "starting_capital": float(STARTING_CAPITAL),
        "final_capital": float(capital),
        "net_profit": float(net_profit),
        "total_costs_paid": float(total_costs_paid),
        "total_slippage_paid": float(total_slippage_paid),
        "costs_as_pct_of_starting": float(total_costs_paid / STARTING_CAPITAL),
        "strategy_cagr": cagr(equity_series),
        "nifty_cagr": cagr(nifty_cum) if len(nifty_cum) > 2 else 0,
        "strategy_sharpe": strat_sharpe,
        "nifty_sharpe": nifty_sharpe,
        "strategy_max_dd": max_drawdown(equity_series),
        "nifty_max_dd": max_drawdown(nifty_cum) if len(nifty_cum) > 2 else 0,
        "n_rebalances": n_rebalances,
        "n_months": len(equity_series),
    }

    # Pass: net profit > 0 AND Sharpe > 0.3 after costs
    passed = net_profit > 0 and strat_sharpe > 0.3

    print_result("Momentum (after costs)", passed, metrics)
    return passed


# ============================================================
# STRATEGY 2: MEAN REVERSION RSI (Delivery, with full costs)
# ============================================================

def strategy_2_mean_reversion(stock_data):
    print_header("STRATEGY 2: Mean Reversion RSI Extremes (with full costs)")
    print("  Buy when RSI < 30, sell when RSI > 50 or stop/timeout")
    print("  Delivery trades, ₹5L capital, max 20% per position\n")

    costs = IndianMarketCosts("delivery")
    capital = STARTING_CAPITAL
    max_position_pct = 0.20  # 20% max per stock
    equity_curve = [capital]
    dates = [datetime(2021, 4, 1)]  # approximate start

    all_trades = []

    for sym, df in stock_data.items():
        df = df.copy()
        df["returns"] = df["Close"].pct_change()
        df["rsi"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
        df = df.dropna()

        in_trade = False
        entry_price = 0
        entry_date = None

        for idx, row in df.iterrows():
            if not in_trade and row["rsi"] < 30:
                in_trade = True
                entry_price = row["Close"]
                entry_date = idx

            elif in_trade:
                days_held = (idx - entry_date).days
                pnl_pct_gross = (row["Close"] - entry_price) / entry_price

                if row["rsi"] > 50 or pnl_pct_gross < -0.05 or days_held > 20:
                    # Calculate position value
                    position_value = capital * max_position_pct
                    buy_value = position_value
                    sell_value = position_value * (1 + pnl_pct_gross)

                    # Round-trip costs
                    cost_pct = costs.calculate_round_trip_cost_pct(buy_value, sell_value)
                    slippage_pct = SLIPPAGE_PCT * 2  # both sides

                    total_friction = cost_pct + slippage_pct
                    pnl_pct_net = pnl_pct_gross - total_friction

                    # Absolute P&L
                    pnl_inr = position_value * pnl_pct_net
                    cost_inr = position_value * cost_pct
                    slippage_inr = position_value * slippage_pct

                    all_trades.append({
                        "symbol": sym,
                        "entry_date": entry_date,
                        "exit_date": idx,
                        "entry_price": entry_price,
                        "exit_price": row["Close"],
                        "pnl_gross_pct": pnl_pct_gross,
                        "cost_pct": cost_pct,
                        "slippage_pct": slippage_pct,
                        "pnl_net_pct": pnl_pct_net,
                        "pnl_net_inr": pnl_inr,
                        "cost_inr": cost_inr,
                        "slippage_inr": slippage_inr,
                        "position_value": position_value,
                        "days_held": days_held,
                        "exit_reason": "target" if row["rsi"] > 50 else ("stop" if pnl_pct_gross < -0.05 else "timeout"),
                    })
                    in_trade = False

    if not all_trades:
        print("  No trades")
        print_result("Mean Reversion (after costs)", False, {})
        return False

    trades_df = pd.DataFrame(all_trades)
    trades_df = trades_df.sort_values("entry_date")

    # Simulate sequential capital growth
    capital = STARTING_CAPITAL
    equity_points = []

    for _, trade in trades_df.iterrows():
        position_value = capital * max_position_pct
        pnl = position_value * trade["pnl_net_pct"]
        capital += pnl
        equity_points.append({"date": trade["exit_date"], "capital": capital})

    equity_df = pd.DataFrame(equity_points)

    total_trades = len(trades_df)
    gross_winners = (trades_df["pnl_gross_pct"] > 0).sum()
    net_winners = (trades_df["pnl_net_pct"] > 0).sum()

    gross_win_rate = float(gross_winners / total_trades)
    net_win_rate = float(net_winners / total_trades)

    # Trades that were profitable gross but turned negative after costs
    cost_killed = ((trades_df["pnl_gross_pct"] > 0) & (trades_df["pnl_net_pct"] <= 0)).sum()

    avg_gross = float(trades_df["pnl_gross_pct"].mean())
    avg_net = float(trades_df["pnl_net_pct"].mean())
    avg_cost = float(trades_df["cost_pct"].mean())

    total_costs = float(trades_df["cost_inr"].sum())
    total_slippage = float(trades_df["slippage_inr"].sum())
    total_gross_pnl = float((trades_df["pnl_gross_pct"] * trades_df["position_value"]).sum())
    total_net_pnl = float(trades_df["pnl_net_inr"].sum())

    # Net profit factor
    net_profits = trades_df[trades_df["pnl_net_pct"] > 0]["pnl_net_inr"].sum()
    net_losses = abs(trades_df[trades_df["pnl_net_pct"] <= 0]["pnl_net_inr"].sum())
    net_profit_factor = float(net_profits / net_losses) if net_losses > 0 else float("inf")

    # Net expectancy
    net_expectancy = float(trades_df["pnl_net_pct"].mean())

    metrics = {
        "total_trades": total_trades,
        "gross_win_rate": gross_win_rate,
        "net_win_rate (after costs)": net_win_rate,
        "trades_killed_by_costs": int(cost_killed),
        "avg_gross_pnl_pct": avg_gross,
        "avg_cost_pct (per round trip)": avg_cost,
        "avg_net_pnl_pct": avg_net,
        "net_expectancy_per_trade": net_expectancy,
        "net_profit_factor": net_profit_factor,
        "total_gross_pnl_inr": float(total_gross_pnl),
        "total_costs_inr": float(total_costs),
        "total_slippage_inr": float(total_slippage),
        "total_net_pnl_inr": float(total_net_pnl),
        "final_capital": float(capital),
        "net_return_on_capital": float((capital - STARTING_CAPITAL) / STARTING_CAPITAL),
        "avg_days_held": float(trades_df["days_held"].mean()),
        "stocks_traded": len(trades_df["symbol"].unique()),
    }

    # Pass: net expectancy > 0 AND net profit factor > 1.0 AND net positive P&L
    passed = net_expectancy > 0 and net_profit_factor > 1.0 and total_net_pnl > 0

    print_result("Mean Reversion (after costs)", passed, metrics)
    return passed


# ============================================================
# STRATEGY 3: EMA TREND (Delivery, with full costs)
# ============================================================

def strategy_3_ema_trend(stock_data):
    print_header("STRATEGY 3: EMA 9/21 Crossover + ADX Filter (with full costs)")
    print("  Entry: EMA9 > EMA21 crossover, ADX > 20")
    print("  Exit: 3% target, 1.5% stop, or EMA cross back\n")

    costs = IndianMarketCosts("delivery")
    capital = STARTING_CAPITAL
    max_position_pct = 0.20

    all_trades = []

    for sym, df in stock_data.items():
        df = df.copy()
        df["ema9"] = df["Close"].ewm(span=9).mean()
        df["ema21"] = df["Close"].ewm(span=21).mean()
        df["adx"] = ta.trend.ADXIndicator(df["High"], df["Low"], df["Close"]).adx()
        df = df.dropna()

        in_trade = False
        entry_price = 0
        entry_date = None
        prev_ema_above = False

        for idx, row in df.iterrows():
            ema_above = row["ema9"] > row["ema21"]

            if not in_trade:
                if ema_above and not prev_ema_above and row["adx"] > 20:
                    in_trade = True
                    entry_price = row["Close"]
                    entry_date = idx
            else:
                pnl_gross = (row["Close"] - entry_price) / entry_price
                days_held = (idx - entry_date).days

                # Wider targets for delivery
                if pnl_gross >= 0.03 or pnl_gross <= -0.015 or not ema_above or days_held > 20:
                    position_value = capital * max_position_pct
                    buy_value = position_value
                    sell_value = position_value * (1 + pnl_gross)

                    cost_pct = costs.calculate_round_trip_cost_pct(buy_value, sell_value)
                    slippage_pct = SLIPPAGE_PCT * 2
                    pnl_net = pnl_gross - cost_pct - slippage_pct

                    all_trades.append({
                        "symbol": sym,
                        "pnl_gross_pct": pnl_gross,
                        "cost_pct": cost_pct,
                        "pnl_net_pct": pnl_net,
                        "pnl_net_inr": position_value * pnl_net,
                        "cost_inr": position_value * cost_pct,
                        "days_held": days_held,
                        "exit_reason": "target" if pnl_gross >= 0.03 else (
                            "stop" if pnl_gross <= -0.015 else (
                            "ema_cross" if not ema_above else "timeout")),
                    })
                    in_trade = False

            prev_ema_above = ema_above

    if not all_trades:
        print("  No trades")
        print_result("EMA Trend (after costs)", False, {})
        return False

    trades_df = pd.DataFrame(all_trades)

    total = len(trades_df)
    net_win_rate = float((trades_df["pnl_net_pct"] > 0).mean())
    gross_win_rate = float((trades_df["pnl_gross_pct"] > 0).mean())
    cost_killed = int(((trades_df["pnl_gross_pct"] > 0) & (trades_df["pnl_net_pct"] <= 0)).sum())

    avg_cost = float(trades_df["cost_pct"].mean())
    net_expectancy = float(trades_df["pnl_net_pct"].mean())

    net_profits = trades_df[trades_df["pnl_net_pct"] > 0]["pnl_net_inr"].sum()
    net_losses = abs(trades_df[trades_df["pnl_net_pct"] <= 0]["pnl_net_inr"].sum())
    net_pf = float(net_profits / net_losses) if net_losses > 0 else float("inf")

    total_net_pnl = float(trades_df["pnl_net_inr"].sum())
    total_costs = float(trades_df["cost_inr"].sum())

    metrics = {
        "total_trades": total,
        "gross_win_rate": gross_win_rate,
        "net_win_rate": net_win_rate,
        "trades_killed_by_costs": cost_killed,
        "avg_cost_per_trade_pct": avg_cost,
        "net_expectancy_per_trade": net_expectancy,
        "net_profit_factor": net_pf,
        "total_net_pnl_inr": float(total_net_pnl),
        "total_costs_inr": float(total_costs),
        "avg_days_held": float(trades_df["days_held"].mean()),
    }

    passed = net_expectancy > 0 and net_pf > 1.0 and total_net_pnl > 0

    print_result("EMA Trend (after costs)", passed, metrics)
    return passed


# ============================================================
# COST BREAKDOWN ANALYSIS
# ============================================================

def cost_analysis():
    print_header("REFERENCE: Indian Market Cost Breakdown")

    costs_delivery = IndianMarketCosts("delivery")
    costs_intraday = IndianMarketCosts("intraday")

    trade_sizes = [25000, 50000, 100000, 200000]

    print("  DELIVERY (CNC) — Round-trip costs:")
    print(f"  {'Trade Size':>12} | {'Cost %':>8} | {'Cost ₹':>8} | {'+ Slippage':>10} | {'Total %':>8}")
    print(f"  {'-'*12}-+-{'-'*8}-+-{'-'*8}-+-{'-'*10}-+-{'-'*8}")
    for size in trade_sizes:
        cost_pct = costs_delivery.calculate_round_trip_cost_pct(size, size * 1.01)
        slip = SLIPPAGE_PCT * 2
        total = cost_pct + slip
        print(f"  ₹{size:>10,} | {cost_pct*100:>7.3f}% | ₹{size*cost_pct:>7.0f} | {slip*100:>9.3f}% | {total*100:>7.3f}%")

    print(f"\n  INTRADAY (MIS) — Round-trip costs:")
    print(f"  {'Trade Size':>12} | {'Cost %':>8} | {'Cost ₹':>8} | {'+ Slippage':>10} | {'Total %':>8}")
    print(f"  {'-'*12}-+-{'-'*8}-+-{'-'*8}-+-{'-'*10}-+-{'-'*8}")
    for size in trade_sizes:
        cost_pct = costs_intraday.calculate_round_trip_cost_pct(size, size * 1.005)
        slip = SLIPPAGE_PCT * 2
        total = cost_pct + slip
        print(f"  ₹{size:>10,} | {cost_pct*100:>7.3f}% | ₹{size*cost_pct:>7.0f} | {slip*100:>9.3f}% | {total*100:>7.3f}%")

    print(f"\n  Key insight: Delivery costs ~0.25-0.35% round-trip")
    print(f"  A trade needs >0.35% gross profit just to break even on delivery")
    print(f"  This kills small-edge, high-frequency strategies on Indian markets")


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "="*70)
    print("  FINAGENT STRATEGY VALIDATION v3 — WITH REAL COSTS")
    print("  " + "="*66)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Capital: ₹{STARTING_CAPITAL:,} | Slippage: {SLIPPAGE_PCT*100:.2f}%/side")
    print(f"  Costs: STT + Exchange + SEBI + Stamp + GST + DP charges")
    print("="*70)

    # Show cost model first
    cost_analysis()

    # Load data
    stock_data, nifty = load_data()

    results = {}

    # S1: Momentum with costs
    results["S1_momentum_net"] = strategy_1_momentum(stock_data, nifty)

    # S2: Mean reversion with costs
    results["S2_mean_reversion_net"] = strategy_2_mean_reversion(stock_data)

    # S3: EMA Trend with costs (wider targets)
    results["S3_ema_trend_net"] = strategy_3_ema_trend(stock_data)

    # ---- FINAL SUMMARY ----
    print_header("FINAL SUMMARY — After All Indian Market Costs")

    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    total_pass = sum(1 for v in results.values() if v)
    print(f"\n  Strategies surviving costs: {total_pass}/{len(results)}")

    if total_pass >= 2:
        print("\n  >>> VERDICT: GO — Strategies survive real-world costs.")
    elif total_pass == 1:
        print("\n  >>> VERDICT: CONDITIONAL — Only 1 strategy survives. Needs more work.")
    else:
        print("\n  >>> VERDICT: NO-GO — No strategy profitable after costs.")

    output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation_v3_results.json")
    with open(output, "w") as f:
        json.dump({"timestamp": datetime.now().isoformat(),
                    "results": {k: bool(v) for k, v in results.items()},
                    "capital": STARTING_CAPITAL, "slippage": SLIPPAGE_PCT}, f, indent=2)
    print(f"\n  Results saved to: {output}")


if __name__ == "__main__":
    main()
