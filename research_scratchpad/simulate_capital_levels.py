"""
FinAgent — Multi-Capital Simulation
=====================================
Runs the validated short strangle strategy at different capital levels.
Uses the SAME real Dhan option data already fetched.
Honest about margin constraints — if capital is too low, trades get skipped.

Also tests equity strategies (momentum, mean reversion) at each level
since they DON'T need F&O margin.

Capital levels: ₹50K, ₹1L, ₹2L, ₹3L, ₹4L, ₹5L
Hard floor: 20% max drawdown at each level
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import requests
import json
import time
import os
import ta

# ============================================================
# CONFIG
# ============================================================

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

CAPITAL_LEVELS = [50000, 100000, 200000, 300000, 400000, 500000]
MAX_LOSS_PCT = 0.20  # 20% max drawdown at each level

LOT_SIZE_CHANGE_DATE = datetime(2024, 11, 20)

def get_lot_size(date):
    if isinstance(date, str):
        date = datetime.strptime(date, "%Y-%m-%d")
    return 75 if date >= LOT_SIZE_CHANGE_DATE else 25


# ============================================================
# ZERODHA COST MODELS
# ============================================================

class ZerodhaCosts:
    @staticmethod
    def options_sell_cost(premium_value):
        brokerage = 20
        stt = premium_value * 0.000625
        exchange = premium_value * 0.0005
        sebi = premium_value * 0.000001
        gst = (brokerage + exchange) * 0.18
        return brokerage + stt + exchange + sebi + gst

    @staticmethod
    def options_buy_cost(premium_value):
        brokerage = 20
        exchange = premium_value * 0.0005
        sebi = premium_value * 0.000001
        stamp = premium_value * 0.00003
        gst = (brokerage + exchange) * 0.18
        return brokerage + exchange + sebi + stamp + gst

    @staticmethod
    def strangle_round_trip(ce_sell, pe_sell, ce_buy, pe_buy):
        return (ZerodhaCosts.options_sell_cost(ce_sell) +
                ZerodhaCosts.options_sell_cost(pe_sell) +
                ZerodhaCosts.options_buy_cost(ce_buy) +
                ZerodhaCosts.options_buy_cost(pe_buy))

    @staticmethod
    def delivery_round_trip(buy_value, sell_value):
        """Equity delivery costs (Zerodha — ₹0 brokerage)"""
        stt = (buy_value + sell_value) * 0.001
        exchange = (buy_value + sell_value) * 0.0000345
        sebi = (buy_value + sell_value) * 0.000001
        stamp = buy_value * 0.00015
        gst = exchange * 0.18
        dp = 15.93
        return stt + exchange + sebi + stamp + gst + dp


# ============================================================
# FETCH OPTION DATA FROM DHAN
# ============================================================

def fetch_option_chunk(from_date, to_date, strike, option_type, expiry_code=1):
    url = "https://api.dhan.co/v2/charts/rollingoption"
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'access-token': DHAN_TOKEN,
        'client-id': DHAN_CLIENT_ID,
    }
    payload = {
        "exchangeSegment": "NSE_FNO",
        "interval": "60",
        "securityId": 13,
        "instrument": "OPTIDX",
        "expiryFlag": "MONTH",
        "expiryCode": expiry_code,
        "strike": strike,
        "drvOptionType": option_type,
        "requiredData": ["close", "iv", "oi", "spot"],
        "fromDate": from_date,
        "toDate": to_date,
    }
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    if resp.status_code == 200:
        return resp.json()
    elif resp.status_code == 429:
        time.sleep(3)
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
        return resp.json() if resp.status_code == 200 else None
    return None


def fetch_all_option_data(start, end, strike, option_type):
    all_data = {"close": [], "iv": [], "oi": [], "spot": [], "timestamp": []}
    current = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    while current < end_dt:
        chunk_end = min(current + timedelta(days=29), end_dt)
        result = fetch_option_chunk(current.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d"), strike, option_type)
        time.sleep(0.8)

        if result:
            data = result.get("data", {})
            key = "ce" if option_type == "CALL" else "pe"
            opt = data.get(key, {})
            if opt and opt.get("timestamp"):
                for field in all_data:
                    vals = opt.get(field, [])
                    if vals:
                        all_data[field].extend(vals)

        current = chunk_end + timedelta(days=1)

    return all_data


# ============================================================
# OPTIONS STRATEGY SIMULATION
# ============================================================

def run_options_simulation(combined_df, starting_capital):
    """Run short strangle on weekly data with given capital"""
    hard_floor = starting_capital * (1 - MAX_LOSS_PCT)
    capital = starting_capital
    trades = []
    equity = [capital]
    floor_breached = False
    skipped_margin = 0

    combined_df['week'] = combined_df.index.to_period('W-THU')
    weeks = combined_df.groupby('week')

    for week_period, week_data in weeks:
        if floor_breached:
            equity.append(capital)
            continue

        if len(week_data) < 5:
            continue

        entry = week_data.iloc[0]
        spot = entry.get('spot', 0)
        if spot == 0 or pd.isna(spot):
            continue

        date_for_lot = week_data.index[0].to_pydatetime()
        lot_size = get_lot_size(date_for_lot)

        # Choose OTM if available
        if 'ce_otm_close' in entry and not pd.isna(entry.get('ce_otm_close')) and entry.get('ce_otm_close', 0) > 0:
            ce_entry = float(entry['ce_otm_close']) if not pd.isna(entry.get('ce_otm_close')) else 0
            pe_entry = float(entry.get('pe_otm_close', 0)) if not pd.isna(entry.get('pe_otm_close', 0)) else 0
            col_ce = 'ce_otm_close'
            col_pe = 'pe_otm_close'
        else:
            ce_entry = float(entry.get('ce_atm_close', 0)) if not pd.isna(entry.get('ce_atm_close', 0)) else 0
            pe_entry = float(entry.get('pe_atm_close', 0)) if not pd.isna(entry.get('pe_atm_close', 0)) else 0
            col_ce = 'ce_atm_close'
            col_pe = 'pe_atm_close'

        if ce_entry <= 0 and pe_entry <= 0:
            continue

        # Margin check
        entry_iv = entry.get('ce_atm_iv', 15)
        if pd.isna(entry_iv) or entry_iv == 0:
            entry_iv = 15
        margin_pct = max(0.12, float(entry_iv) / 100 * 0.8)
        margin_required = spot * lot_size * margin_pct

        if margin_required > capital * 0.9:
            skipped_margin += 1
            continue

        # Intra-week floor check
        exited_early = False
        exit_idx = -1
        for j in range(1, len(week_data)):
            row = week_data.iloc[j]
            ce_mid = float(row.get(col_ce, 0)) if not pd.isna(row.get(col_ce, 0)) else 0
            pe_mid = float(row.get(col_pe, 0)) if not pd.isna(row.get(col_pe, 0)) else 0
            mtm = (ce_entry + pe_entry - ce_mid - pe_mid) * lot_size
            if capital + mtm < hard_floor:
                exit_idx = j
                exited_early = True
                break

        exit_row = week_data.iloc[exit_idx] if exited_early else week_data.iloc[-1]
        ce_exit = float(exit_row.get(col_ce, 0)) if not pd.isna(exit_row.get(col_ce, 0)) else 0
        pe_exit = float(exit_row.get(col_pe, 0)) if not pd.isna(exit_row.get(col_pe, 0)) else 0

        premium_in = (ce_entry + pe_entry) * lot_size
        premium_out = (ce_exit + pe_exit) * lot_size
        pnl_gross = premium_in - premium_out

        cost = ZerodhaCosts.strangle_round_trip(
            ce_entry * lot_size, pe_entry * lot_size,
            ce_exit * lot_size, pe_exit * lot_size
        )
        slip_mult = 2.0 if exited_early else 1.0
        slippage = slip_mult * 1.0 * lot_size * 4

        pnl_net = pnl_gross - cost - slippage
        capital += pnl_net
        equity.append(capital)

        trades.append({
            'pnl_net': pnl_net,
            'cost': cost,
            'slippage': slippage,
            'exit_reason': 'FLOOR_EXIT' if exited_early else 'expiry',
        })

        if capital < hard_floor:
            floor_breached = True

    return {
        'trades': trades,
        'equity': equity,
        'floor_breached': floor_breached,
        'skipped_margin': skipped_margin,
        'final_capital': capital,
    }


# ============================================================
# EQUITY STRATEGY: Mean Reversion RSI (delivery, no margin needed)
# ============================================================

def run_equity_mean_reversion(stock_data, starting_capital):
    """RSI mean reversion on individual stocks — delivery trades, max 20% per position"""
    hard_floor = starting_capital * (1 - MAX_LOSS_PCT)
    capital = starting_capital
    max_pos_pct = 0.20
    trades = []
    floor_breached = False

    for sym, df in stock_data.items():
        if floor_breached:
            break

        df = df.copy()
        df['rsi'] = ta.momentum.RSIIndicator(df['Close'], window=14).rsi()
        df = df.dropna()

        in_trade = False
        entry_price = 0
        entry_date = None

        for idx, row in df.iterrows():
            if floor_breached:
                break

            if not in_trade and row['rsi'] < 30:
                pos_value = capital * max_pos_pct
                if pos_value < 5000:  # minimum meaningful trade
                    continue
                in_trade = True
                entry_price = row['Close']
                entry_date = idx

            elif in_trade:
                days = (idx - entry_date).days
                pnl_pct = (row['Close'] - entry_price) / entry_price

                if row['rsi'] > 50 or pnl_pct < -0.05 or days > 20:
                    pos_value = capital * max_pos_pct
                    buy_val = pos_value
                    sell_val = pos_value * (1 + pnl_pct)
                    cost = ZerodhaCosts.delivery_round_trip(buy_val, sell_val)
                    slippage = pos_value * 0.001  # 0.05% each side
                    pnl_net_pct = pnl_pct - (cost + slippage) / pos_value
                    pnl_net_inr = pos_value * pnl_net_pct

                    capital += pnl_net_inr
                    trades.append({'pnl_net': pnl_net_inr, 'pnl_pct': pnl_net_pct})
                    in_trade = False

                    if capital < hard_floor:
                        floor_breached = True

    return {
        'trades': trades,
        'final_capital': capital,
        'floor_breached': floor_breached,
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "="*70)
    print("  FINAGENT — MULTI-CAPITAL SIMULATION")
    print("  " + "="*66)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Capital levels: {', '.join(f'₹{c//1000}K' for c in CAPITAL_LEVELS)}")
    print(f"  Max drawdown: {MAX_LOSS_PCT*100:.0f}% at each level")
    print(f"  Strategies: Options (short strangle) + Equity (RSI mean reversion)")
    print("="*70)

    # ============================================================
    # LOAD OPTION DATA (from cache or API)
    # ============================================================
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "option_data_cache.json")

    if os.path.exists(cache_file):
        print("\n  Loading cached option data...")
        with open(cache_file) as f:
            cached = json.load(f)
        ce_atm = cached['ce_atm']
        pe_atm = cached['pe_atm']
        ce_otm = cached['ce_otm']
        pe_otm = cached['pe_otm']
        print(f"  Loaded: {len(ce_atm['timestamp'])} data points per series")
    else:
        print("\n  Fetching option data from Dhan API (this takes ~3 min)...")
        start, end = "2024-03-01", "2026-03-28"

        print("  [1/4] ATM CALL...")
        ce_atm = fetch_all_option_data(start, end, "ATM", "CALL")
        print(f"    {len(ce_atm['timestamp'])} points")

        print("  [2/4] ATM PUT...")
        pe_atm = fetch_all_option_data(start, end, "ATM", "PUT")
        print(f"    {len(pe_atm['timestamp'])} points")

        print("  [3/4] ATM+2 CALL...")
        ce_otm = fetch_all_option_data(start, end, "ATM+2", "CALL")
        print(f"    {len(ce_otm['timestamp'])} points")

        print("  [4/4] ATM-2 PUT...")
        pe_otm = fetch_all_option_data(start, end, "ATM-2", "PUT")
        print(f"    {len(pe_otm['timestamp'])} points")

        # Cache for re-runs
        with open(cache_file, 'w') as f:
            json.dump({'ce_atm': ce_atm, 'pe_atm': pe_atm, 'ce_otm': ce_otm, 'pe_otm': pe_otm}, f)
        print("  Cached to option_data_cache.json")

    # Build combined DataFrame
    def to_df(data, prefix):
        if not data['timestamp']:
            return pd.DataFrame()
        df = pd.DataFrame({
            f'{prefix}_close': data['close'],
            f'{prefix}_iv': data.get('iv', [None]*len(data['close'])),
            f'{prefix}_oi': data.get('oi', [None]*len(data['close'])),
            'spot': data.get('spot', [None]*len(data['close'])),
            'timestamp': pd.to_datetime(data['timestamp'], unit='s'),
        })
        return df.set_index('timestamp')

    combined = to_df(ce_atm, 'ce_atm')
    for other, prefix in [(pe_atm, 'pe_atm'), (ce_otm, 'ce_otm'), (pe_otm, 'pe_otm')]:
        odf = to_df(other, prefix)
        if not odf.empty:
            odf = odf.drop(columns=['spot'], errors='ignore')
            combined = combined.join(odf, how='outer')
    combined = combined.sort_index()

    # ============================================================
    # LOAD EQUITY DATA
    # ============================================================
    print("\n  Loading equity data for mean reversion strategy...")
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
    print(f"  Loaded {len(stock_data)} stocks")

    # ============================================================
    # RUN SIMULATIONS
    # ============================================================
    print("\n" + "="*70)
    print("  SIMULATION RESULTS")
    print("="*70)

    # Header
    print(f"\n  {'':=<70}")
    print(f"  OPTIONS STRATEGY (Short Strangle — Monthly NIFTY, Real Premiums)")
    print(f"  {'':=<70}")
    print(f"  {'Capital':>10} | {'Floor':>10} | {'Final':>10} | {'P&L':>10} | {'Return':>8} | {'Trades':>6} | {'Win%':>5} | {'Sharpe':>6} | {'MaxDD':>7} | {'Skipped':>7} | {'Floor?':>7}")
    print(f"  {'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}-+-{'-'*6}-+-{'-'*5}-+-{'-'*6}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}")

    options_results = {}
    for cap in CAPITAL_LEVELS:
        result = run_options_simulation(combined.copy(), cap)
        trades = result['trades']
        final = result['final_capital']
        floor = cap * (1 - MAX_LOSS_PCT)

        n_trades = len(trades)
        if n_trades > 0:
            wins = sum(1 for t in trades if t['pnl_net'] > 0)
            win_rate = wins / n_trades * 100
            pnl = final - cap
            ret = pnl / cap * 100

            weekly_rets = pd.Series([t['pnl_net'] / cap for t in trades])
            sharpe = float(np.sqrt(52) * (weekly_rets.mean() - 0.06/52) / weekly_rets.std()) if weekly_rets.std() > 0 else 0

            eq = pd.Series(result['equity'])
            peak = eq.cummax()
            max_dd = float(((eq - peak) / peak).min()) * 100

            fb = "YES" if result['floor_breached'] else "no"
            print(f"  ₹{cap//1000:>6}K | ₹{floor//1000:>6}K | ₹{final:>8,.0f} | ₹{pnl:>+8,.0f} | {ret:>+6.1f}% | {n_trades:>6} | {win_rate:>4.0f}% | {sharpe:>5.1f} | {max_dd:>+6.1f}% | {result['skipped_margin']:>7} | {fb:>7}")
        else:
            print(f"  ₹{cap//1000:>6}K | ₹{floor//1000:>6}K | ₹{cap:>8,.0f} | {'₹0':>10} | {'0.0%':>8} | {'0':>6} | {'N/A':>5} | {'N/A':>6} | {'N/A':>7} | {result['skipped_margin']:>7} | {'N/A':>7}")

        options_results[cap] = {
            'trades': n_trades,
            'final': final,
            'pnl': final - cap,
            'skipped': result['skipped_margin'],
            'floor_breached': result['floor_breached'],
        }

    # ============================================================
    # EQUITY STRATEGY
    # ============================================================
    print(f"\n  {'':=<70}")
    print(f"  EQUITY STRATEGY (RSI Mean Reversion — Delivery, No Margin Needed)")
    print(f"  {'':=<70}")
    print(f"  {'Capital':>10} | {'Floor':>10} | {'Final':>10} | {'P&L':>10} | {'Return':>8} | {'Trades':>6} | {'Win%':>5} | {'Floor?':>7}")
    print(f"  {'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}-+-{'-'*6}-+-{'-'*5}-+-{'-'*7}")

    equity_results = {}
    for cap in CAPITAL_LEVELS:
        result = run_equity_mean_reversion(stock_data, cap)
        trades = result['trades']
        final = result['final_capital']
        floor = cap * (1 - MAX_LOSS_PCT)
        n = len(trades)

        if n > 0:
            wins = sum(1 for t in trades if t['pnl_net'] > 0)
            win_rate = wins / n * 100
            pnl = final - cap
            ret = pnl / cap * 100
            fb = "YES" if result['floor_breached'] else "no"
            print(f"  ₹{cap//1000:>6}K | ₹{floor//1000:>6}K | ₹{final:>8,.0f} | ₹{pnl:>+8,.0f} | {ret:>+6.1f}% | {n:>6} | {win_rate:>4.0f}% | {fb:>7}")
        else:
            print(f"  ₹{cap//1000:>6}K | ₹{floor//1000:>6}K | ₹{cap:>8,.0f} | {'₹0':>10} | {'0.0%':>8} | {'0':>6} | {'N/A':>5} | {'N/A':>7}")

        equity_results[cap] = {
            'trades': n,
            'final': final,
            'pnl': final - cap,
            'floor_breached': result['floor_breached'],
        }

    # ============================================================
    # COMBINED VIEW
    # ============================================================
    print(f"\n  {'':=<70}")
    print(f"  COMBINED STRATEGY (Options + Equity at each capital level)")
    print(f"  {'':=<70}")
    print(f"  {'Capital':>10} | {'Opt P&L':>10} | {'Eq P&L':>10} | {'Best Strategy':>25} | {'Recommendation':>20}")
    print(f"  {'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*25}-+-{'-'*20}")

    for cap in CAPITAL_LEVELS:
        opt = options_results.get(cap, {})
        eq = equity_results.get(cap, {})
        opt_pnl = opt.get('pnl', 0)
        eq_pnl = eq.get('pnl', 0)
        opt_trades = opt.get('trades', 0)

        if opt_trades == 0:
            best = "Equity only (no margin)"
            rec = "Start here"
        elif opt_pnl > eq_pnl and not opt.get('floor_breached', False):
            best = "Options (strangle)"
            rec = "Good to go"
        elif eq_pnl > 0:
            best = "Equity (mean reversion)"
            rec = "Scale up for options"
        else:
            best = "Neither profitable"
            rec = "Need more capital"

        print(f"  ₹{cap//1000:>6}K | ₹{opt_pnl:>+8,.0f} | ₹{eq_pnl:>+8,.0f} | {best:>25} | {rec:>20}")

    # ============================================================
    # KEY INSIGHTS
    # ============================================================
    print(f"\n  {'':=<70}")
    print(f"  KEY INSIGHTS")
    print(f"  {'':=<70}")

    # Find minimum capital for options
    min_opt_cap = None
    for cap in CAPITAL_LEVELS:
        if options_results.get(cap, {}).get('trades', 0) > 10:
            min_opt_cap = cap
            break

    if min_opt_cap:
        print(f"  - Minimum capital for NIFTY options: ₹{min_opt_cap//1000}K")
        print(f"    (below this, margin requirements block most/all trades)")
    else:
        print(f"  - NIFTY options need substantial capital (margin ≈ ₹1.5-2.5L per lot)")

    print(f"  - NIFTY lot size is 75 (post Nov 2024) — notional value ≈ ₹17L")
    print(f"  - Short strangle margin ≈ ₹1.5-2.5L depending on VIX")
    print(f"  - Equity strategies work at ANY capital (delivery = ₹0 brokerage)")
    print(f"  - Recommended path: Start equity at ₹50K → scale to options at ₹3L+")

    # Save
    all_results = {
        "timestamp": datetime.now().isoformat(),
        "options": {str(k): {kk: vv for kk, vv in v.items() if kk != 'trades_detail'} for k, v in options_results.items()},
        "equity": {str(k): v for k, v in equity_results.items()},
    }
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi_capital_results.json")
    with open(output_path, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_path}")


if __name__ == "__main__":
    main()
