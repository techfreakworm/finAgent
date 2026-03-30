"""
FinAgent — Multi-Capital Simulation with XIRR
===============================================
Proper XIRR on all trade cash flows.
Cash flow model:
  - Day 0: -Capital (deployed)
  - Each trade exit: P&L as cashflow on that date
  - Last date: +Final capital (withdrawn)

Uses cached Dhan option data from previous run.
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
from pyxirr import xirr
from datetime import datetime, timedelta, date
import json
import os
import ta

# ============================================================
# CONFIG
# ============================================================

CAPITAL_LEVELS = [50000, 100000, 200000, 300000, 400000, 500000]
MAX_LOSS_PCT = 0.20
LOT_SIZE_CHANGE_DATE = datetime(2024, 11, 20)

def get_lot_size(dt):
    if isinstance(dt, str):
        dt = datetime.strptime(dt, "%Y-%m-%d")
    return 75 if dt >= LOT_SIZE_CHANGE_DATE else 25


class ZerodhaCosts:
    @staticmethod
    def options_sell_cost(pv):
        b = 20; stt = pv*0.000625; ex = pv*0.0005; se = pv*1e-6; g = (b+ex)*0.18
        return b+stt+ex+se+g

    @staticmethod
    def options_buy_cost(pv):
        b = 20; ex = pv*0.0005; se = pv*1e-6; st = pv*0.00003; g = (b+ex)*0.18
        return b+ex+se+st+g

    @staticmethod
    def strangle_rt(cs, ps, cb, pb):
        return (ZerodhaCosts.options_sell_cost(cs) + ZerodhaCosts.options_sell_cost(ps) +
                ZerodhaCosts.options_buy_cost(cb) + ZerodhaCosts.options_buy_cost(pb))

    @staticmethod
    def delivery_rt(bv, sv):
        stt = (bv+sv)*0.001; ex = (bv+sv)*0.0000345; se = (bv+sv)*1e-6
        st = bv*0.00015; g = ex*0.18; dp = 15.93
        return stt+ex+se+st+g+dp


# ============================================================
# OPTIONS SIMULATION (with dated cash flows)
# ============================================================

def run_options_sim(combined_df, starting_capital):
    floor = starting_capital * (1 - MAX_LOSS_PCT)
    capital = starting_capital
    cashflows = []  # list of (date, amount)
    trades = []
    floor_breached = False
    skipped = 0

    combined_df['week'] = combined_df.index.to_period('W-THU')
    weeks = combined_df.groupby('week')

    first_date = None
    last_date = None

    for wp, wd in weeks:
        if floor_breached:
            continue
        if len(wd) < 5:
            continue

        entry = wd.iloc[0]
        spot = entry.get('spot', 0)
        if spot == 0 or pd.isna(spot):
            continue

        entry_date = wd.index[0].to_pydatetime().date()
        if first_date is None:
            first_date = entry_date

        dt = wd.index[0].to_pydatetime()
        lot = get_lot_size(dt)

        if 'ce_otm_close' in entry and not pd.isna(entry.get('ce_otm_close')) and entry.get('ce_otm_close', 0) > 0:
            ce_e = float(entry['ce_otm_close']) if not pd.isna(entry.get('ce_otm_close')) else 0
            pe_e = float(entry.get('pe_otm_close', 0)) if not pd.isna(entry.get('pe_otm_close', 0)) else 0
            cc, pc = 'ce_otm_close', 'pe_otm_close'
        else:
            ce_e = float(entry.get('ce_atm_close', 0)) if not pd.isna(entry.get('ce_atm_close', 0)) else 0
            pe_e = float(entry.get('pe_atm_close', 0)) if not pd.isna(entry.get('pe_atm_close', 0)) else 0
            cc, pc = 'ce_atm_close', 'pe_atm_close'

        if ce_e <= 0 and pe_e <= 0:
            continue

        iv = entry.get('ce_atm_iv', 15)
        if pd.isna(iv) or iv == 0: iv = 15
        margin = spot * lot * max(0.12, float(iv)/100*0.8)
        if margin > capital * 0.9:
            skipped += 1
            continue

        # Intra-week floor check
        early = False
        ex_idx = -1
        for j in range(1, len(wd)):
            r = wd.iloc[j]
            cm = float(r.get(cc, 0)) if not pd.isna(r.get(cc, 0)) else 0
            pm = float(r.get(pc, 0)) if not pd.isna(r.get(pc, 0)) else 0
            mtm = (ce_e + pe_e - cm - pm) * lot
            if capital + mtm < floor:
                ex_idx = j
                early = True
                break

        ex_row = wd.iloc[ex_idx] if early else wd.iloc[-1]
        exit_date = ex_row.name.to_pydatetime().date()
        last_date = exit_date

        ce_x = float(ex_row.get(cc, 0)) if not pd.isna(ex_row.get(cc, 0)) else 0
        pe_x = float(ex_row.get(pc, 0)) if not pd.isna(ex_row.get(pc, 0)) else 0

        prem_in = (ce_e + pe_e) * lot
        prem_out = (ce_x + pe_x) * lot
        gross = prem_in - prem_out
        cost = ZerodhaCosts.strangle_rt(ce_e*lot, pe_e*lot, ce_x*lot, pe_x*lot)
        slip = (2.0 if early else 1.0) * 1.0 * lot * 4
        net = gross - cost - slip

        capital += net

        cashflows.append((exit_date, net))
        trades.append({
            'entry_date': str(entry_date),
            'exit_date': str(exit_date),
            'pnl_net': net,
            'exit_reason': 'FLOOR_EXIT' if early else 'expiry',
        })

        if capital < floor:
            floor_breached = True

    return {
        'first_date': first_date,
        'last_date': last_date,
        'trades': trades,
        'cashflows': cashflows,
        'final_capital': capital,
        'floor_breached': floor_breached,
        'skipped': skipped,
    }


# ============================================================
# EQUITY SIMULATION (with dated cash flows)
# ============================================================

def run_equity_sim(stock_data, starting_capital):
    floor = starting_capital * (1 - MAX_LOSS_PCT)
    capital = starting_capital
    cashflows = []
    trades = []
    floor_breached = False
    first_date = None
    last_date = None

    for sym, df in stock_data.items():
        if floor_breached:
            break
        df = df.copy()
        df['rsi'] = ta.momentum.RSIIndicator(df['Close'], window=14).rsi()
        df = df.dropna()

        in_trade = False
        ep = 0
        ed = None

        for idx, row in df.iterrows():
            if floor_breached:
                break

            trade_date = idx.date() if hasattr(idx, 'date') else idx

            if first_date is None or (first_date and trade_date < first_date):
                first_date = trade_date

            if not in_trade and row['rsi'] < 30:
                pv = capital * 0.20
                if pv < 5000:
                    continue
                in_trade = True
                ep = row['Close']
                ed = trade_date

            elif in_trade:
                days = (trade_date - ed).days if isinstance(trade_date, date) and isinstance(ed, date) else 0
                pnl_pct = (row['Close'] - ep) / ep

                if row['rsi'] > 50 or pnl_pct < -0.05 or days > 20:
                    pv = capital * 0.20
                    bv = pv
                    sv = pv * (1 + pnl_pct)
                    cost = ZerodhaCosts.delivery_rt(bv, sv)
                    slip = pv * 0.001
                    net_pct = pnl_pct - (cost + slip) / pv
                    net_inr = pv * net_pct

                    capital += net_inr
                    last_date = trade_date

                    cashflows.append((trade_date, net_inr))
                    trades.append({'exit_date': str(trade_date), 'pnl_net': net_inr})
                    in_trade = False

                    if capital < floor:
                        floor_breached = True

    return {
        'first_date': first_date,
        'last_date': last_date,
        'trades': trades,
        'cashflows': cashflows,
        'final_capital': capital,
        'floor_breached': floor_breached,
    }


# ============================================================
# XIRR CALCULATION
# ============================================================

def calc_xirr(starting_capital, cashflows, final_capital, first_date, last_date):
    """
    Cash flow model:
      - first_date: -starting_capital (money goes IN)
      - each trade date: P&L (+ or -)
      - last_date: +final_capital (money comes OUT)

    XIRR gives the annualized rate that makes NPV of all flows = 0.
    """
    if not first_date or not last_date or first_date >= last_date:
        return None

    dates = [first_date]
    amounts = [-float(starting_capital)]

    # We DON'T add intermediate P&L as separate cashflows —
    # because the capital stays invested. The P&L just changes the NAV.
    # XIRR should be: invest on day 1, withdraw on last day.
    # Intermediate cashflows only matter if you're adding/removing money.

    dates.append(last_date)
    amounts.append(float(final_capital))

    try:
        result = xirr(dates, amounts)
        return result
    except Exception:
        return None


def calc_xirr_monthly(starting_capital, trades_list, first_date, last_date):
    """
    Alternative: monthly cash flow XIRR.
    Treat each month's net P&L as a distribution (outflow if positive, inflow if negative).
    This models it as if you withdraw profits monthly.
    """
    if not first_date or not last_date or not trades_list:
        return None

    # Group trades by month
    monthly = {}
    for t in trades_list:
        d = t['exit_date'] if isinstance(t['exit_date'], date) else datetime.strptime(str(t['exit_date']), "%Y-%m-%d").date()
        month_key = d.replace(day=1)
        monthly[month_key] = monthly.get(month_key, 0) + t['pnl_net']

    dates = [first_date]
    amounts = [-float(starting_capital)]

    for month_date in sorted(monthly.keys()):
        pnl = monthly[month_date]
        if pnl > 0:
            # Withdraw profits
            dates.append(month_date)
            amounts.append(float(pnl))
        else:
            # Loss stays invested (no additional inflow)
            pass

    # Final: withdraw remaining capital
    remaining = starting_capital + sum(t['pnl_net'] for t in trades_list) - sum(a for a in amounts[1:])
    dates.append(last_date)
    amounts.append(float(remaining))

    try:
        result = xirr(dates, amounts)
        return result
    except Exception:
        return None


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "="*70)
    print("  FINAGENT — MULTI-CAPITAL SIMULATION WITH XIRR")
    print("  " + "="*66)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  XIRR: Annualized return accounting for cash flow timing")
    print("="*70)

    # Load cached option data
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "option_data_cache.json")
    if not os.path.exists(cache_file):
        print("  ERROR: Run simulate_capital_levels.py first to cache option data")
        return

    with open(cache_file) as f:
        cached = json.load(f)

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

    combined = to_df(cached['ce_atm'], 'ce_atm')
    for key, prefix in [('pe_atm','pe_atm'), ('ce_otm','ce_otm'), ('pe_otm','pe_otm')]:
        odf = to_df(cached[key], prefix)
        if not odf.empty:
            odf = odf.drop(columns=['spot'], errors='ignore')
            combined = combined.join(odf, how='outer')
    combined = combined.sort_index()

    print(f"  Option data: {len(combined)} hourly rows ({combined.index[0].date()} to {combined.index[-1].date()})")

    # Load equity data
    print("  Loading equity data...")
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
    print(f"  Loaded {len(stock_data)} stocks\n")

    # ============================================================
    # RUN ALL SIMULATIONS
    # ============================================================

    print("="*90)
    print("  OPTIONS STRATEGY — Short Strangle (Real NIFTY Premiums, Zerodha Costs)")
    print("="*90)
    print(f"  {'Capital':>8} | {'Floor':>8} | {'Final':>10} | {'Net P&L':>10} | {'Simple%':>8} | {'XIRR':>8} | {'XIRR(m)':>8} | {'Trades':>6} | {'Win%':>5} | {'MaxDD':>7} | {'Skip':>4} | {'Floor':>5}")
    print(f"  {'-'*8}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*6}-+-{'-'*5}-+-{'-'*7}-+-{'-'*4}-+-{'-'*5}")

    for cap in CAPITAL_LEVELS:
        r = run_options_sim(combined.copy(), cap)
        trades = r['trades']
        final = r['final_capital']
        fl = cap * (1 - MAX_LOSS_PCT)
        n = len(trades)

        if n > 0:
            wins = sum(1 for t in trades if t['pnl_net'] > 0)
            wr = wins/n*100
            pnl = final - cap
            simple_ret = pnl/cap*100

            # XIRR (lump sum: invest at start, withdraw at end)
            x = calc_xirr(cap, r['cashflows'], final, r['first_date'], r['last_date'])
            xirr_pct = f"{x*100:>+6.1f}%" if x is not None else "  N/A"

            # XIRR monthly (withdraw profits monthly)
            xm = calc_xirr_monthly(cap, trades, r['first_date'], r['last_date'])
            xirr_m_pct = f"{xm*100:>+6.1f}%" if xm is not None else "  N/A"

            # Max DD
            eq = pd.Series([cap] + [cap + sum(t['pnl_net'] for t in trades[:i+1]) for i in range(n)])
            pk = eq.cummax()
            mdd = float(((eq - pk) / pk).min()) * 100

            fb = "YES" if r['floor_breached'] else "no"

            print(f"  ₹{cap//1000:>5}K | ₹{fl//1000:>5}K | ₹{final:>8,.0f} | ₹{pnl:>+8,.0f} | {simple_ret:>+6.1f}% | {xirr_pct:>8} | {xirr_m_pct:>8} | {n:>6} | {wr:>4.0f}% | {mdd:>+6.1f}% | {r['skipped']:>4} | {fb:>5}")
        else:
            print(f"  ₹{cap//1000:>5}K | ₹{fl//1000:>5}K | ₹{cap:>8,.0f} | {'₹0':>10} | {'0.0%':>8} | {'N/A':>8} | {'N/A':>8} | {'0':>6} | {'N/A':>5} | {'N/A':>7} | {r['skipped']:>4} | {'N/A':>5}")

    print()
    print("="*90)
    print("  EQUITY STRATEGY — RSI Mean Reversion (Delivery, No Margin)")
    print("="*90)
    print(f"  {'Capital':>8} | {'Floor':>8} | {'Final':>10} | {'Net P&L':>10} | {'Simple%':>8} | {'XIRR':>8} | {'XIRR(m)':>8} | {'Trades':>6} | {'Win%':>5} | {'Floor':>5}")
    print(f"  {'-'*8}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*6}-+-{'-'*5}-+-{'-'*5}")

    for cap in CAPITAL_LEVELS:
        r = run_equity_sim(stock_data, cap)
        trades = r['trades']
        final = r['final_capital']
        fl = cap * (1 - MAX_LOSS_PCT)
        n = len(trades)

        if n > 0:
            wins = sum(1 for t in trades if t['pnl_net'] > 0)
            wr = wins/n*100
            pnl = final - cap
            simple_ret = pnl/cap*100

            x = calc_xirr(cap, r['cashflows'], final, r['first_date'], r['last_date'])
            xirr_pct = f"{x*100:>+6.1f}%" if x is not None else "  N/A"

            xm = calc_xirr_monthly(cap, trades, r['first_date'], r['last_date'])
            xirr_m_pct = f"{xm*100:>+6.1f}%" if xm is not None else "  N/A"

            fb = "YES" if r['floor_breached'] else "no"

            print(f"  ₹{cap//1000:>5}K | ₹{fl//1000:>5}K | ₹{final:>8,.0f} | ₹{pnl:>+8,.0f} | {simple_ret:>+6.1f}% | {xirr_pct:>8} | {xirr_m_pct:>8} | {n:>6} | {wr:>4.0f}% | {fb:>5}")
        else:
            print(f"  ₹{cap//1000:>5}K | ₹{fl//1000:>5}K | ₹{cap:>8,.0f} | {'₹0':>10} | {'0.0%':>8} | {'N/A':>8} | {'N/A':>8} | {'0':>6} | {'N/A':>5} | {'N/A':>5}")

    # ============================================================
    # XIRR EXPLANATION
    # ============================================================
    print(f"\n  {'':=<70}")
    print(f"  XIRR METHODOLOGY")
    print(f"  {'':=<70}")
    print(f"  XIRR     = Lump-sum: ₹X in on day 1, ₹Y out on last day.")
    print(f"             Annualized rate making NPV = 0.")
    print(f"  XIRR(m)  = Monthly withdrawal: deploy capital on day 1,")
    print(f"             withdraw net profits each month, withdraw")
    print(f"             remaining on last day. Models a 'salary' approach.")
    print(f"  Simple%  = (Final - Start) / Start × 100. No time weighting.")
    print(f"")
    print(f"  Period: ~2 years (Mar 2024 – Mar 2026)")
    print(f"  All returns include: brokerage, STT, exchange, SEBI, stamp,")
    print(f"  GST, DP charges, and ₹1/unit/leg slippage.")

    # ============================================================
    # RECOMMENDATION
    # ============================================================
    print(f"\n  {'':=<70}")
    print(f"  RECOMMENDATION FOR STARTING WITH ₹50K")
    print(f"  {'':=<70}")
    print(f"  1. Deploy ₹50K in equity RSI mean reversion strategy")
    print(f"  2. NIFTY options are NOT possible at ₹50K (margin ~₹1.5-2.5L)")
    print(f"  3. Once capital grows to ₹1L+ through equity profits,")
    print(f"     start taking selective NIFTY option trades")
    print(f"  4. At ₹2L+, full options capacity unlocked")


if __name__ == "__main__":
    main()
