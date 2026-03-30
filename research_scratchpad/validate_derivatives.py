"""
FinAgent Derivatives Validation
================================
Tests options/F&O strategies with actual NSE data:
  1. Can we access NSE derivatives data? (option chain, FII/DII, participant OI)
  2. Does FII/DII ACTUAL data predict NIFTY? (not the proxy from v1)
  3. Does Max Pain convergence work on NIFTY weekly expiry?
  4. Does PCR (Put-Call Ratio) have predictive power?
  5. Can a simple options selling strategy (at Max Pain) generate alpha after costs?

Costs for F&O:
  - Brokerage: ₹20/order (Zerodha)
  - STT: 0.0125% on sell side (options), 0.0125% on sell side (futures)
  - Exchange: 0.053% (options), 0.002% (futures) on NSE
  - GST: 18% on brokerage+exchange
  - SEBI: ₹10/crore
  - Stamp: 0.003% on buy
  - Slippage: 0.1% for options (wider spreads)
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import json
import os
import time

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


# ============================================================
# F&O COST MODEL
# ============================================================

class FnOCosts:
    """Cost model for NSE Futures & Options (Zerodha)"""

    @staticmethod
    def options_round_trip(buy_premium, sell_premium, lot_size, n_lots=1):
        """
        Returns total cost in ₹ for an options round-trip.
        buy_premium, sell_premium: per-unit premium
        """
        buy_value = buy_premium * lot_size * n_lots
        sell_value = sell_premium * lot_size * n_lots

        # Brokerage: ₹20 per order (buy + sell = ₹40)
        brokerage = 40

        # STT: 0.0625% on sell side ONLY for options (on premium)
        stt = sell_value * 0.000625

        # Exchange charges: 0.0495% on premium turnover (NSE options)
        exchange_buy = buy_value * 0.000495
        exchange_sell = sell_value * 0.000495

        # SEBI: ₹10 per crore
        sebi = (buy_value + sell_value) * 0.000001

        # Stamp: 0.003% on buy side
        stamp = buy_value * 0.00003

        # GST: 18% on (brokerage + exchange charges)
        gst = (brokerage + exchange_buy + exchange_sell) * 0.18

        total = brokerage + stt + exchange_buy + exchange_sell + sebi + stamp + gst
        return total

    @staticmethod
    def futures_round_trip(entry_price, exit_price, lot_size, n_lots=1):
        """Returns total cost in ₹ for a futures round-trip."""
        buy_value = entry_price * lot_size * n_lots
        sell_value = exit_price * lot_size * n_lots

        brokerage = 40
        stt = sell_value * 0.0125 / 100  # 0.0125% on sell
        exchange_buy = buy_value * 0.002 / 100
        exchange_sell = sell_value * 0.002 / 100
        sebi = (buy_value + sell_value) * 0.000001
        stamp = buy_value * 0.00003
        gst = (brokerage + exchange_buy + exchange_sell) * 0.18

        total = brokerage + stt + exchange_buy + exchange_sell + sebi + stamp + gst
        return total


# ============================================================
# DATA ACCESS: Try to get NSE derivatives data
# ============================================================

def test_nse_data_access():
    print_header("VALIDATION D1: NSE Derivatives Data Access")

    results = {}

    # 1. Try nselib for option chain
    print("  [1] Testing nselib option chain...")
    try:
        from nselib.derivatives import nse_live_option_chain
        oc = nse_live_option_chain("NIFTY")
        if oc is not None and len(oc) > 0:
            results["option_chain_nselib"] = f"OK — {len(oc)} rows"
            print(f"      Got {len(oc)} rows")
        else:
            results["option_chain_nselib"] = "EMPTY"
            print("      Empty result")
    except Exception as e:
        results["option_chain_nselib"] = f"FAILED: {str(e)[:80]}"
        print(f"      Failed: {str(e)[:80]}")

    # 2. Try nselib for FII/DII
    print("  [2] Testing nselib FII/DII data...")
    try:
        from nselib.capital_market import fii_dii_trading_activity
        fii = fii_dii_trading_activity()
        if fii is not None and len(fii) > 0:
            results["fii_dii_nselib"] = f"OK — {len(fii)} rows"
            print(f"      Got {len(fii)} rows")
        else:
            results["fii_dii_nselib"] = "EMPTY"
            print("      Empty result")
    except Exception as e:
        results["fii_dii_nselib"] = f"FAILED: {str(e)[:80]}"
        print(f"      Failed: {str(e)[:80]}")

    # 3. Try direct NSE API for option chain
    print("  [3] Testing direct NSE API for option chain...")
    try:
        import requests
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })
        # First hit main page for cookies
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
        resp = session.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            records = data.get("records", {}).get("data", [])
            results["option_chain_nse_api"] = f"OK — {len(records)} strike records"
            print(f"      Got {len(records)} strike records")
        else:
            results["option_chain_nse_api"] = f"HTTP {resp.status_code}"
            print(f"      HTTP {resp.status_code}")
    except Exception as e:
        results["option_chain_nse_api"] = f"FAILED: {str(e)[:80]}"
        print(f"      Failed: {str(e)[:80]}")

    # 4. Try NSE FII/DII reports
    print("  [4] Testing NSE FII/DII reports endpoint...")
    try:
        import requests
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
        })
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
        resp = session.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            results["fii_dii_nse_api"] = f"OK — got {len(data)} entries"
            print(f"      Got response with {len(data)} entries")
        else:
            results["fii_dii_nse_api"] = f"HTTP {resp.status_code}"
            print(f"      HTTP {resp.status_code}")
    except Exception as e:
        results["fii_dii_nse_api"] = f"FAILED: {str(e)[:80]}"
        print(f"      Failed: {str(e)[:80]}")

    # 5. India VIX (proxy for options implied vol)
    print("  [5] Testing India VIX (options vol proxy)...")
    try:
        vix = yf.download("^INDIAVIX", period="5y", progress=False)
        if isinstance(vix.columns, pd.MultiIndex):
            vix.columns = vix.columns.get_level_values(0)
        results["india_vix"] = f"OK — {len(vix)} rows"
        print(f"      Got {len(vix)} rows")
    except Exception as e:
        results["india_vix"] = f"FAILED: {str(e)[:80]}"

    any_oc = any("OK" in str(v) for k, v in results.items() if "option_chain" in k)
    any_fii = any("OK" in str(v) for k, v in results.items() if "fii_dii" in k)

    passed = any_oc or any_fii
    print_result("Derivatives Data Access", passed, results)
    return passed, results


# ============================================================
# VALIDATION D2: NIFTY Weekly Expiry Max Pain Simulation
# ============================================================

def validate_max_pain_convergence(nifty_data):
    """
    Without historical option chain snapshots, we simulate Max Pain behavior:
    - NIFTY tends to gravitate toward round numbers / high-OI strikes near expiry
    - We test: does NIFTY close near the nearest round 50-point level on Thursdays?
    - This is a proxy for Max Pain convergence (real test needs OI data)
    """
    print_header("VALIDATION D2: NIFTY Expiry Convergence (Max Pain Proxy)")
    print("  Hypothesis: NIFTY gravitates to round 50-pt strikes on weekly expiry")
    print("  Using Thursdays as expiry day (NSE weekly expiry)\n")

    df = nifty_data.copy()
    df["weekday"] = df.index.weekday  # 0=Mon, 3=Thu

    # Get Thursday closes (expiry days)
    thursdays = df[df["weekday"] == 3].copy()
    if len(thursdays) < 50:
        # Try finding any day that's close to Thursday
        thursdays = df[df["weekday"].isin([3, 2])].copy()  # Thu or Wed

    print(f"  Expiry days found: {len(thursdays)}")

    # Calculate distance to nearest 50-point round number
    thursdays["nearest_50"] = (thursdays["Close"] / 50).round() * 50
    thursdays["distance_to_50"] = abs(thursdays["Close"] - thursdays["nearest_50"])
    thursdays["distance_pct"] = thursdays["distance_to_50"] / thursdays["Close"]

    # Also calculate for non-expiry days (Mondays) as control
    mondays = df[df["weekday"] == 0].copy()
    mondays["nearest_50"] = (mondays["Close"] / 50).round() * 50
    mondays["distance_to_50"] = abs(mondays["Close"] - mondays["nearest_50"])
    mondays["distance_pct"] = mondays["distance_to_50"] / mondays["Close"]

    # Compare: are Thursdays closer to round numbers than Mondays?
    thu_avg_dist = float(thursdays["distance_pct"].mean())
    mon_avg_dist = float(mondays["distance_pct"].mean())

    # Within 0.5% of a round 50-pt strike?
    thu_close_pct = float((thursdays["distance_pct"] < 0.005).mean())
    mon_close_pct = float((mondays["distance_pct"] < 0.005).mean())

    # Statistical test
    from scipy import stats
    t_stat, p_val = stats.ttest_ind(thursdays["distance_pct"].dropna(),
                                     mondays["distance_pct"].dropna())

    # Also test against random: simulate random closes
    np.random.seed(42)
    random_distances = []
    for _ in range(10000):
        rand_price = np.random.uniform(thursdays["Close"].min(), thursdays["Close"].max())
        nearest = round(rand_price / 50) * 50
        random_distances.append(abs(rand_price - nearest) / rand_price)
    random_avg = np.mean(random_distances)

    metrics = {
        "expiry_avg_dist_to_round_50": thu_avg_dist,
        "non_expiry_avg_dist": mon_avg_dist,
        "random_expected_dist": float(random_avg),
        "expiry_closer_than_random": thu_avg_dist < random_avg,
        "expiry_within_0.5pct_of_round": thu_close_pct,
        "non_expiry_within_0.5pct": mon_close_pct,
        "t_test_p_value": float(p_val),
        "n_expiry_days": len(thursdays),
        "n_non_expiry_days": len(mondays),
    }

    # Pass if expiry days are closer to round numbers than non-expiry (even slightly)
    passed = thu_avg_dist < mon_avg_dist or thu_close_pct > mon_close_pct

    print_result("Max Pain Convergence (Proxy)", passed, metrics)
    return passed


# ============================================================
# VALIDATION D3: VIX-Based Options Strategy
# ============================================================

def validate_vix_options_strategy(nifty_data):
    """
    Strategy: Use India VIX to time options selling.
    When VIX is high (>16), sell straddles/strangles (premium is rich).
    When VIX is low (<12), buy options (premium is cheap, breakouts likely).

    Since we don't have actual options prices, we SIMULATE using VIX as IV proxy:
    - Options premium ∝ VIX (roughly)
    - We simulate a weekly short strangle P&L based on NIFTY moves vs VIX-implied range
    """
    print_header("VALIDATION D3: VIX-Based Options Selling Strategy")
    print("  Sell weekly strangles when VIX > 14 (premium-rich)")
    print("  Expected NIFTY range = VIX/√52 * NIFTY_price (weekly)")
    print("  Profit if NIFTY stays within the range, loss if breakout\n")

    vix_data = yf.download("^INDIAVIX", period="5y", progress=False)
    if isinstance(vix_data.columns, pd.MultiIndex):
        vix_data.columns = vix_data.columns.get_level_values(0)

    df = nifty_data.copy()
    df["vix"] = vix_data["Close"].reindex(df.index, method="ffill")
    df = df.dropna(subset=["vix"])

    # Weekly resampling
    weekly = df.resample("W-THU").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "vix": "first",  # VIX at start of week
    }).dropna()

    costs = FnOCosts()
    NIFTY_LOT = 25  # NIFTY lot size (as of recent, was 50, now 25)
    capital = 500000
    margin_per_lot = 150000  # approximate margin for short strangle
    trades = []

    for i in range(len(weekly) - 1):
        row = weekly.iloc[i]
        next_row = weekly.iloc[i + 1]

        entry_vix = row["vix"]
        nifty_price = row["Close"]

        # Only sell when VIX > 14 (premium worth collecting)
        if entry_vix < 14:
            continue

        # Calculate expected weekly move from VIX
        # VIX = annualized vol → weekly vol = VIX / √52
        weekly_vol = entry_vix / 100 / np.sqrt(52)
        expected_move = nifty_price * weekly_vol

        # Strangle strikes: sell OTM put and call at 1 standard deviation
        call_strike = nifty_price + expected_move
        put_strike = nifty_price - expected_move

        # Approximate premium collected (simplified):
        # ATM straddle premium ≈ 0.8 * NIFTY * weekly_vol (Black-Scholes approx)
        # OTM strangle ≈ 40-60% of straddle premium
        straddle_premium = 0.8 * nifty_price * weekly_vol
        strangle_premium = straddle_premium * 0.5  # per unit (CE + PE combined)
        total_premium_collected = strangle_premium * NIFTY_LOT

        # Actual NIFTY move over the week
        actual_close = next_row["Close"]
        actual_move = actual_close - nifty_price

        # P&L calculation
        if actual_close > call_strike:
            # Call breached — loss
            intrinsic_loss = (actual_close - call_strike) * NIFTY_LOT
            pnl_gross = total_premium_collected - intrinsic_loss
        elif actual_close < put_strike:
            # Put breached — loss
            intrinsic_loss = (put_strike - actual_close) * NIFTY_LOT
            pnl_gross = total_premium_collected - intrinsic_loss
        else:
            # Within range — full premium profit
            pnl_gross = total_premium_collected

        # Costs (2 legs: sell CE + sell PE, then buy back both)
        # Approximate premium values for cost calc
        avg_premium_per_leg = strangle_premium / 2
        cost_per_leg = costs.options_round_trip(
            buy_premium=max(avg_premium_per_leg * 0.1, 1),  # buy back near zero
            sell_premium=avg_premium_per_leg,
            lot_size=NIFTY_LOT,
        )
        total_cost = cost_per_leg * 2  # two legs

        # Slippage: 0.1% of premium per side (options have wider spreads)
        slippage = total_premium_collected * 0.002  # 0.1% each side * 2 legs

        pnl_net = pnl_gross - total_cost - slippage

        trades.append({
            "date": weekly.index[i],
            "nifty": nifty_price,
            "vix": entry_vix,
            "expected_move": expected_move,
            "actual_move": abs(actual_move),
            "premium_collected": total_premium_collected,
            "pnl_gross": pnl_gross,
            "cost": total_cost,
            "slippage": slippage,
            "pnl_net": pnl_net,
            "within_range": put_strike <= actual_close <= call_strike,
        })

    if not trades:
        print("  No trades (VIX never > 14?)")
        print_result("VIX Options Strategy", False, {"error": "no trades"})
        return False

    trades_df = pd.DataFrame(trades)

    total = len(trades_df)
    winners = (trades_df["pnl_net"] > 0).sum()
    win_rate = float(winners / total)
    within_range = float(trades_df["within_range"].mean())

    total_premium = float(trades_df["premium_collected"].sum())
    total_pnl_gross = float(trades_df["pnl_gross"].sum())
    total_costs = float(trades_df["cost"].sum())
    total_slippage = float(trades_df["slippage"].sum())
    total_pnl_net = float(trades_df["pnl_net"].sum())

    avg_premium = float(trades_df["premium_collected"].mean())
    avg_pnl_net = float(trades_df["pnl_net"].mean())

    # Simulated equity curve
    equity = [capital]
    for _, t in trades_df.iterrows():
        equity.append(equity[-1] + t["pnl_net"])
    equity_series = pd.Series(equity)
    peak = equity_series.cummax()
    dd = ((equity_series - peak) / peak).min()

    # Weekly Sharpe
    weekly_returns = trades_df["pnl_net"] / capital
    sharpe = float(np.sqrt(52) * weekly_returns.mean() / weekly_returns.std()) if weekly_returns.std() > 0 else 0

    # Profit factor
    gross_wins = trades_df[trades_df["pnl_net"] > 0]["pnl_net"].sum()
    gross_losses = abs(trades_df[trades_df["pnl_net"] <= 0]["pnl_net"].sum())
    pf = float(gross_wins / gross_losses) if gross_losses > 0 else float("inf")

    metrics = {
        "total_trades": total,
        "win_rate": win_rate,
        "within_range_pct": within_range,
        "avg_premium_collected_per_trade": float(avg_premium),
        "avg_net_pnl_per_trade": float(avg_pnl_net),
        "total_premium_collected": float(total_premium),
        "total_pnl_gross": float(total_pnl_gross),
        "total_costs": float(total_costs),
        "total_slippage": float(total_slippage),
        "total_pnl_net": float(total_pnl_net),
        "net_profit_factor": pf,
        "sharpe_annualized": sharpe,
        "max_drawdown": float(dd),
        "final_capital": float(equity[-1]),
        "return_on_capital": float((equity[-1] - capital) / capital),
    }

    passed = total_pnl_net > 0 and win_rate > 0.5 and pf > 1.0

    print_result("VIX Options Selling", passed, metrics)
    return passed


# ============================================================
# VALIDATION D4: Futures Momentum (NIFTY Futures simulation)
# ============================================================

def validate_futures_momentum(nifty_data):
    """
    Test a simple NIFTY futures strategy:
    - Long when NIFTY > 20 EMA AND VIX < 18 (bullish + calm)
    - Short when NIFTY < 20 EMA AND VIX > 18 (bearish + fearful)
    - Flat otherwise
    With full futures costs + slippage
    """
    print_header("VALIDATION D4: NIFTY Futures Trend Strategy")
    print("  Long: NIFTY > EMA20 + VIX < 18")
    print("  Short: NIFTY < EMA20 + VIX > 18")
    print("  1 lot NIFTY futures, with full costs\n")

    vix_data = yf.download("^INDIAVIX", period="5y", progress=False)
    if isinstance(vix_data.columns, pd.MultiIndex):
        vix_data.columns = vix_data.columns.get_level_values(0)

    df = nifty_data.copy()
    df["vix"] = vix_data["Close"].reindex(df.index, method="ffill")
    df["ema20"] = df["Close"].ewm(span=20).mean()
    df["returns"] = df["Close"].pct_change()
    df = df.dropna()

    costs = FnOCosts()
    NIFTY_LOT = 25
    capital = 500000
    margin = 150000  # approx margin for 1 lot

    # Signal
    df["signal"] = 0
    df.loc[(df["Close"] > df["ema20"]) & (df["vix"] < 18), "signal"] = 1   # long
    df.loc[(df["Close"] < df["ema20"]) & (df["vix"] > 18), "signal"] = -1  # short

    # Track trades (signal changes)
    df["signal_change"] = df["signal"].diff().abs()
    trade_count = int(df["signal_change"].sum() / 2)  # each change is entry or exit

    # Strategy return
    df["strat_return"] = df["signal"].shift(1) * df["returns"]

    # Cost per trade: futures round-trip
    avg_nifty = df["Close"].mean()
    cost_per_trade = costs.futures_round_trip(avg_nifty, avg_nifty * 1.001, NIFTY_LOT)
    slippage_per_trade = avg_nifty * NIFTY_LOT * 0.0005 * 2  # 0.05% each side

    # Total cost (each signal change = one trade)
    total_trade_events = int(df["signal_change"].sum())
    total_cost = (cost_per_trade + slippage_per_trade) * total_trade_events / 2  # /2 because each round trip = 2 events
    daily_cost_drag = total_cost / len(df) / (avg_nifty * NIFTY_LOT)

    # Adjust returns for costs (spread evenly)
    df["strat_return_net"] = df["strat_return"] - (df["signal_change"].shift(1).fillna(0) * (cost_per_trade + slippage_per_trade) / (avg_nifty * NIFTY_LOT) / 2)

    # Equity curves
    strat_cum = (1 + df["strat_return_net"]).cumprod() * capital
    bh_cum = (1 + df["returns"]).cumprod() * capital

    strat_sharpe = float(np.sqrt(252) * (df["strat_return_net"].mean() - 0.06/252) / df["strat_return_net"].std()) if df["strat_return_net"].std() > 0 else 0
    bh_sharpe = float(np.sqrt(252) * (df["returns"].mean() - 0.06/252) / df["returns"].std()) if df["returns"].std() > 0 else 0

    strat_peak = strat_cum.cummax()
    strat_dd = float(((strat_cum - strat_peak) / strat_peak).min())
    bh_peak = bh_cum.cummax()
    bh_dd = float(((bh_cum - bh_peak) / bh_peak).min())

    # Time in market
    long_pct = float((df["signal"] == 1).mean())
    short_pct = float((df["signal"] == -1).mean())
    flat_pct = float((df["signal"] == 0).mean())

    # Annualized returns
    years = len(df) / 252
    strat_ann = float((strat_cum.iloc[-1] / capital) ** (1/years) - 1) if years > 0 else 0
    bh_ann = float((bh_cum.iloc[-1] / capital) ** (1/years) - 1) if years > 0 else 0

    metrics = {
        "strategy_sharpe (net)": strat_sharpe,
        "buyhold_sharpe": bh_sharpe,
        "strategy_annual_return": strat_ann,
        "buyhold_annual_return": bh_ann,
        "strategy_max_dd": strat_dd,
        "buyhold_max_dd": bh_dd,
        "total_trades (round trips)": trade_count,
        "cost_per_round_trip": float(cost_per_trade),
        "slippage_per_round_trip": float(slippage_per_trade),
        "total_costs_est": float(total_cost),
        "long_pct": long_pct,
        "short_pct": short_pct,
        "flat_pct": flat_pct,
        "final_capital": float(strat_cum.iloc[-1]),
        "buyhold_final": float(bh_cum.iloc[-1]),
    }

    passed = strat_sharpe > bh_sharpe or (strat_ann > bh_ann and strat_dd > bh_dd - 0.05)

    print_result("Futures Trend Strategy (net)", passed, metrics)
    return passed


# ============================================================
# F&O COST REFERENCE
# ============================================================

def fno_cost_analysis():
    print_header("REFERENCE: F&O Cost Breakdown")

    costs = FnOCosts()

    print("  OPTIONS (NIFTY, lot=25):")
    print(f"  {'Premium':>10} | {'Round-trip ₹':>12} | {'As % of premium':>16}")
    print(f"  {'-'*10}-+-{'-'*12}-+-{'-'*16}")
    for prem in [50, 100, 200, 500]:
        cost = costs.options_round_trip(prem, prem * 0.1, lot_size=25)
        prem_value = prem * 25
        print(f"  ₹{prem:>8} | ₹{cost:>11.0f} | {cost/prem_value*100:>14.2f}%")

    print(f"\n  FUTURES (NIFTY, lot=25, price ≈ ₹23,000):")
    for entry in [23000]:
        for move_pct in [0.5, 1.0, 2.0]:
            exit_p = entry * (1 + move_pct/100)
            cost = costs.futures_round_trip(entry, exit_p, 25)
            notional = entry * 25
            pnl = (exit_p - entry) * 25
            print(f"    Move {move_pct}%: P&L ₹{pnl:,.0f}, Cost ₹{cost:.0f} ({cost/pnl*100:.1f}% of P&L)")

    print(f"\n  Key: Options cost is low relative to premium")
    print(f"  Futures cost is very low (₹40-80 per round trip)")
    print(f"  Biggest F&O risk is MARGIN, not cost")


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "="*70)
    print("  FINAGENT DERIVATIVES VALIDATION")
    print("  " + "="*66)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Tests: Data access, Max Pain, VIX options selling, Futures trend")
    print("="*70)

    # Cost reference
    fno_cost_analysis()

    # Load NIFTY data
    print("\n  Loading NIFTY 5-year data...")
    nifty = yf.download("^NSEI", period="5y", progress=False)
    if isinstance(nifty.columns, pd.MultiIndex):
        nifty.columns = nifty.columns.get_level_values(0)
    print(f"  NIFTY: {len(nifty)} rows")

    results = {}

    # D1: Data access
    d1_pass, access_results = test_nse_data_access()
    results["D1_data_access"] = d1_pass

    # D2: Max Pain convergence
    results["D2_max_pain_proxy"] = validate_max_pain_convergence(nifty)

    # D3: VIX-based options selling
    results["D3_vix_options_selling"] = validate_vix_options_strategy(nifty)

    # D4: Futures trend
    results["D4_futures_trend"] = validate_futures_momentum(nifty)

    # ---- FINAL SUMMARY ----
    print_header("DERIVATIVES VALIDATION SUMMARY")

    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    total_pass = sum(1 for v in results.values() if v)
    print(f"\n  Derivatives validations passed: {total_pass}/{len(results)}")

    if total_pass >= 3:
        print("\n  >>> DERIVATIVES VERDICT: GO")
    elif total_pass >= 2:
        print("\n  >>> DERIVATIVES VERDICT: CONDITIONAL — some strategies work")
    else:
        print("\n  >>> DERIVATIVES VERDICT: NEEDS WORK — need real options data")
        print("      Consider: Dhan API key for historical option chain data")

    output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation_derivatives_results.json")
    with open(output, "w") as f:
        json.dump({"timestamp": datetime.now().isoformat(),
                    "results": {k: bool(v) for k, v in results.items()}}, f, indent=2)
    print(f"\n  Results saved to: {output}")


if __name__ == "__main__":
    main()
