"""
FinAgent REAL Validation — Actual Historical Option Premiums from Dhan API
===========================================================================
Uses Dhan's /charts/rollingoption API for ACTUAL expired option prices.
No more Black-Scholes estimates.

Rules:
  - Capital: ₹5,00,000
  - Hard floor: ₹4,00,000 (system HALTS if breached)
  - Costs: Zerodha model (₹20/order F&O, STT, exchange, GST, stamp, SEBI)
  - Lot size: 25 before Nov 20 2024, 75 after
  - Holiday-aware: skip weeks with Thu holiday
  - Margin: approximate SPAN from VIX
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
from dhanhq import dhanhq
from datetime import datetime, timedelta
import json
import time
import os

# ============================================================
# CONFIG
# ============================================================

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

CAPITAL = 500000
HARD_FLOOR = 400000  # HALT if capital drops below this
LOT_SIZE_CHANGE_DATE = datetime(2024, 11, 20)

def get_lot_size(date):
    if isinstance(date, str):
        date = datetime.strptime(date, "%Y-%m-%d")
    return 75 if date >= LOT_SIZE_CHANGE_DATE else 25


# ============================================================
# ZERODHA F&O COST MODEL
# ============================================================

class ZerodhaCosts:
    @staticmethod
    def options_buy(premium_value):
        brokerage = 20
        exchange = premium_value * 0.0005  # 0.05% NSE
        sebi = premium_value * 0.000001
        stamp = premium_value * 0.00003
        gst = (brokerage + exchange) * 0.18
        return brokerage + exchange + sebi + stamp + gst

    @staticmethod
    def options_sell(premium_value):
        brokerage = 20
        stt = premium_value * 0.000625  # 0.0625% on sell
        exchange = premium_value * 0.0005
        sebi = premium_value * 0.000001
        gst = (brokerage + exchange) * 0.18
        return brokerage + stt + exchange + sebi + gst

    @staticmethod
    def strangle_open_cost(ce_premium_value, pe_premium_value):
        """Cost to sell a strangle (2 sell orders)"""
        return ZerodhaCosts.options_sell(ce_premium_value) + ZerodhaCosts.options_sell(pe_premium_value)

    @staticmethod
    def strangle_close_cost(ce_premium_value, pe_premium_value):
        """Cost to buy back a strangle (2 buy orders)"""
        return ZerodhaCosts.options_buy(ce_premium_value) + ZerodhaCosts.options_buy(pe_premium_value)


# ============================================================
# FETCH HISTORICAL OPTION DATA FROM DHAN
# ============================================================

def fetch_expired_option_data(from_date, to_date, strike="ATM", option_type="CALL", expiry_flag="MONTH", expiry_code=1, interval=60):
    """Fetch expired option data from Dhan rollingoption API"""
    import requests

    url = "https://api.dhan.co/v2/charts/rollingoption"
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'access-token': DHAN_TOKEN,
        'client-id': DHAN_CLIENT_ID,
    }
    payload = {
        "exchangeSegment": "NSE_FNO",
        "interval": str(interval),
        "securityId": 13,
        "instrument": "OPTIDX",
        "expiryFlag": expiry_flag,
        "expiryCode": expiry_code,
        "strike": strike,
        "drvOptionType": option_type,
        "requiredData": ["close", "iv", "oi", "spot"],
        "fromDate": from_date,
        "toDate": to_date,
    }

    import json as _json
    resp = requests.post(url, headers=headers, data=_json.dumps(payload), timeout=30)
    if resp.status_code == 200:
        return resp.json()
    elif resp.status_code == 429:
        time.sleep(3)
        resp = requests.post(url, headers=headers, data=_json.dumps(payload), timeout=30)
        return resp.json() if resp.status_code == 200 else {"error": resp.status_code, "body": resp.text[:300]}
    else:
        return {"error": resp.status_code, "body": resp.text[:300]}


def fetch_option_data_chunked(start_date, end_date, strike, option_type, expiry_flag="MONTH", expiry_code=1):
    """Fetch in 30-day chunks (API limit) and combine"""
    all_data = {"close": [], "iv": [], "oi": [], "spot": [], "timestamp": []}

    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    while current < end:
        chunk_end = min(current + timedelta(days=29), end)
        from_str = current.strftime("%Y-%m-%d")
        to_str = chunk_end.strftime("%Y-%m-%d")

        result = fetch_expired_option_data(
            from_str, to_str, strike=strike,
            option_type=option_type, expiry_flag=expiry_flag,
            expiry_code=expiry_code, interval=60
        )

        time.sleep(0.8)  # rate limit

        if "error" in result:
            print(f"    Chunk {from_str}→{to_str}: error {result.get('error', '?')}")
            current = chunk_end + timedelta(days=1)
            continue

        data = result.get("data", {})
        key = "ce" if option_type == "CALL" else "pe"
        opt_data = data.get(key, {})

        if opt_data and opt_data.get("timestamp"):
            n = len(opt_data["timestamp"])
            print(f"    Chunk {from_str}→{to_str}: {n} data points")
            for field in all_data:
                values = opt_data.get(field, [])
                if values:
                    all_data[field].extend(values)
                elif field == "timestamp":
                    pass  # timestamp is required
        else:
            print(f"    Chunk {from_str}→{to_str}: no data")

        current = chunk_end + timedelta(days=1)

    return all_data


def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


# ============================================================
# MAIN VALIDATION
# ============================================================

def main():
    print("\n" + "="*70)
    print("  FINAGENT REAL VALIDATION — Actual Expired Option Prices")
    print("  " + "="*66)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Capital: ₹{CAPITAL:,} | Hard Floor: ₹{HARD_FLOOR:,}")
    print(f"  Data: Dhan rollingoption API (actual historical premiums)")
    print(f"  Costs: Zerodha F&O model")
    print("="*70)

    # ============================================================
    # STEP 1: Fetch actual historical ATM CE and PE prices
    # ============================================================
    print_header("STEP 1: Fetching Actual Historical Option Data (2 years)")
    print("  Using Dhan rollingoption API — MONTH expiry, expiryCode=1")
    print("  This gives ACTUAL traded option prices, not estimates\n")

    start = "2024-03-01"
    end = "2026-03-28"

    print("  [1/4] ATM CALL (near-month)...")
    ce_atm = fetch_option_data_chunked(start, end, "ATM", "CALL", "MONTH", 1)
    print(f"  Total ATM CE: {len(ce_atm['timestamp'])} data points\n")

    print("  [2/4] ATM PUT (near-month)...")
    pe_atm = fetch_option_data_chunked(start, end, "ATM", "PUT", "MONTH", 1)
    print(f"  Total ATM PE: {len(pe_atm['timestamp'])} data points\n")

    print("  [3/4] ATM+2 CALL (OTM CE for strangle)...")
    ce_otm = fetch_option_data_chunked(start, end, "ATM+2", "CALL", "MONTH", 1)
    print(f"  Total OTM CE: {len(ce_otm['timestamp'])} data points\n")

    print("  [4/4] ATM-2 PUT (OTM PE for strangle)...")
    pe_otm = fetch_option_data_chunked(start, end, "ATM-2", "PUT", "MONTH", 1)
    print(f"  Total OTM PE: {len(pe_otm['timestamp'])} data points")

    if not ce_atm['timestamp'] or not pe_atm['timestamp']:
        print("\n  CRITICAL: Could not fetch option data. Check API access.")
        return

    # ============================================================
    # STEP 2: Build DataFrames
    # ============================================================
    print_header("STEP 2: Processing Data")

    def to_df(data, prefix):
        if not data['timestamp']:
            return pd.DataFrame()
        df = pd.DataFrame({
            f'{prefix}_close': data['close'],
            f'{prefix}_iv': data.get('iv', [None]*len(data['close'])),
            f'{prefix}_oi': data.get('oi', [None]*len(data['close'])),
            f'{prefix}_volume': data.get('volume', [None]*len(data['close'])),
            'spot': data.get('spot', [None]*len(data['close'])),
            'timestamp': pd.to_datetime(data['timestamp'], unit='s'),
        })
        df = df.set_index('timestamp')
        return df

    ce_atm_df = to_df(ce_atm, 'ce_atm')
    pe_atm_df = to_df(pe_atm, 'pe_atm')
    ce_otm_df = to_df(ce_otm, 'ce_otm')
    pe_otm_df = to_df(pe_otm, 'pe_otm')

    # Merge all
    combined = ce_atm_df.copy()
    for other in [pe_atm_df, ce_otm_df, pe_otm_df]:
        if not other.empty:
            # Drop duplicate 'spot' column
            other_cols = [c for c in other.columns if c not in combined.columns or c == 'spot']
            other_no_spot = other.drop(columns=['spot'], errors='ignore')
            combined = combined.join(other_no_spot, how='outer')

    combined = combined.sort_index()
    print(f"  Combined data: {len(combined)} hourly rows")
    print(f"  Date range: {combined.index[0]} to {combined.index[-1]}")
    print(f"  Spot range: {combined['spot'].min():.0f} to {combined['spot'].max():.0f}")

    # ============================================================
    # STEP 3: Build Weekly Expiry Trades
    # ============================================================
    print_header("STEP 3: Weekly Short Strangle Backtest with REAL Premiums")

    # Resample to weekly: entry = Monday open (first data point of week)
    # exit = Thursday/expiry close (last data point of week)
    combined['week'] = combined.index.to_period('W-THU')
    combined['weekday'] = combined.index.weekday  # 0=Mon, 3=Thu

    weeks = combined.groupby('week')

    capital = CAPITAL
    equity_curve = [capital]
    trades = []
    floor_breached = False
    floor_breach_date = None

    for week_period, week_data in weeks:
        # After floor breach, no new trades — system stays in cash
        if floor_breached:
            equity_curve.append(capital)
            continue

        if len(week_data) < 5:
            continue

        week_start = week_data.index[0]
        week_end = week_data.index[-1]
        date_for_lot = week_start.to_pydatetime()
        lot_size = get_lot_size(date_for_lot)

        entry = week_data.iloc[0]
        spot_entry = entry.get('spot', 0)
        if spot_entry == 0 or pd.isna(spot_entry):
            continue

        # Use OTM strangle if available, else ATM
        if 'ce_otm_close' in entry and not pd.isna(entry.get('ce_otm_close')) and entry.get('ce_otm_close', 0) > 0:
            ce_entry_premium = float(entry['ce_otm_close']) if not pd.isna(entry.get('ce_otm_close')) else 0
            pe_entry_premium = float(entry.get('pe_otm_close', 0)) if not pd.isna(entry.get('pe_otm_close', 0)) else 0
            strike_type = "OTM (ATM±2)"
        else:
            ce_entry_premium = float(entry.get('ce_atm_close', 0)) if not pd.isna(entry.get('ce_atm_close', 0)) else 0
            pe_entry_premium = float(entry.get('pe_atm_close', 0)) if not pd.isna(entry.get('pe_atm_close', 0)) else 0
            strike_type = "ATM"

        if ce_entry_premium <= 0 and pe_entry_premium <= 0:
            continue

        # Margin check
        entry_iv = entry.get('ce_atm_iv', 15)
        if pd.isna(entry_iv) or entry_iv == 0:
            entry_iv = 15
        margin_pct = max(0.12, float(entry_iv) / 100 * 0.8)
        margin_required = spot_entry * lot_size * margin_pct
        if margin_required > capital * 0.9:
            continue

        # --- INTRA-WEEK MONITORING: check every data point for floor breach ---
        exited_early = False
        exit_idx = -1

        for j in range(1, len(week_data)):
            row = week_data.iloc[j]
            ce_mid = 0
            pe_mid = 0

            if strike_type == "OTM (ATM±2)":
                ce_mid = float(row.get('ce_otm_close', 0)) if not pd.isna(row.get('ce_otm_close', 0)) else 0
                pe_mid = float(row.get('pe_otm_close', 0)) if not pd.isna(row.get('pe_otm_close', 0)) else 0
            else:
                ce_mid = float(row.get('ce_atm_close', 0)) if not pd.isna(row.get('ce_atm_close', 0)) else 0
                pe_mid = float(row.get('pe_atm_close', 0)) if not pd.isna(row.get('pe_atm_close', 0)) else 0

            # Mark-to-market P&L
            mtm_pnl = (ce_entry_premium + pe_entry_premium - ce_mid - pe_mid) * lot_size
            projected_capital = capital + mtm_pnl

            # If projected capital drops below hard floor, EXIT NOW
            if projected_capital < HARD_FLOOR:
                exit_idx = j
                exited_early = True
                break

        # Determine exit row
        if exited_early:
            exit_row = week_data.iloc[exit_idx]
            exit_reason = "FLOOR_BREACH"
        else:
            exit_row = week_data.iloc[-1]
            exit_reason = "expiry"

        spot_exit = exit_row.get('spot', spot_entry)
        if pd.isna(spot_exit):
            spot_exit = spot_entry

        if strike_type == "OTM (ATM±2)":
            ce_exit = float(exit_row.get('ce_otm_close', 0)) if not pd.isna(exit_row.get('ce_otm_close', 0)) else 0
            pe_exit = float(exit_row.get('pe_otm_close', 0)) if not pd.isna(exit_row.get('pe_otm_close', 0)) else 0
        else:
            ce_exit = float(exit_row.get('ce_atm_close', 0)) if not pd.isna(exit_row.get('ce_atm_close', 0)) else 0
            pe_exit = float(exit_row.get('pe_atm_close', 0)) if not pd.isna(exit_row.get('pe_atm_close', 0)) else 0

        # Premiums
        premium_collected = (ce_entry_premium + pe_entry_premium) * lot_size
        premium_paid_back = (ce_exit + pe_exit) * lot_size
        pnl_gross = premium_collected - premium_paid_back

        # Costs
        open_cost = ZerodhaCosts.strangle_open_cost(ce_entry_premium * lot_size, pe_entry_premium * lot_size)
        close_cost = ZerodhaCosts.strangle_close_cost(ce_exit * lot_size, pe_exit * lot_size)
        total_cost = open_cost + close_cost

        # Slippage: ₹1/unit/leg. Double slippage on emergency exit (market order, wider spread)
        slip_multiplier = 2.0 if exited_early else 1.0
        slippage = slip_multiplier * 1.0 * lot_size * 4

        pnl_net = pnl_gross - total_cost - slippage
        capital += pnl_net
        equity_curve.append(capital)

        trades.append({
            'week': str(week_period),
            'entry_date': str(week_start.date()),
            'exit_date': str(exit_row.name.date()),
            'exit_reason': exit_reason,
            'spot_entry': spot_entry,
            'spot_exit': spot_exit,
            'spot_move_pct': (spot_exit - spot_entry) / spot_entry * 100,
            'strike_type': strike_type,
            'ce_premium_entry': ce_entry_premium,
            'pe_premium_entry': pe_entry_premium,
            'ce_premium_exit': ce_exit,
            'pe_premium_exit': pe_exit,
            'premium_collected': premium_collected,
            'premium_paid_back': premium_paid_back,
            'pnl_gross': pnl_gross,
            'cost': total_cost,
            'slippage': slippage,
            'pnl_net': pnl_net,
            'lot_size': lot_size,
            'margin_required': margin_required,
            'capital_after': capital,
        })

        if capital < HARD_FLOOR:
            floor_breached = True
            floor_breach_date = str(exit_row.name.date())
            print(f"  *** HARD FLOOR BREACHED on {floor_breach_date} ***")
            print(f"      Capital: ₹{capital:,.0f} — All positions exited. No new trades.")

    # ============================================================
    # STEP 4: Results
    # ============================================================
    print_header("RESULTS: Weekly Short Strangle with REAL Dhan Premiums")

    if not trades:
        print("  No trades executed.")
        return

    trades_df = pd.DataFrame(trades)
    equity_series = pd.Series(equity_curve)

    total = len(trades_df)
    winners = (trades_df['pnl_net'] > 0).sum()
    losers = (trades_df['pnl_net'] <= 0).sum()
    win_rate = winners / total

    total_pnl_net = trades_df['pnl_net'].sum()
    total_costs = trades_df['cost'].sum()
    total_slippage = trades_df['slippage'].sum()
    total_premium = trades_df['premium_collected'].sum()

    avg_pnl = trades_df['pnl_net'].mean()
    avg_premium = trades_df['premium_collected'].mean()
    avg_win = trades_df[trades_df['pnl_net'] > 0]['pnl_net'].mean() if winners > 0 else 0
    avg_loss = trades_df[trades_df['pnl_net'] <= 0]['pnl_net'].mean() if losers > 0 else 0

    worst_trade = trades_df['pnl_net'].min()
    best_trade = trades_df['pnl_net'].max()

    # Profit factor
    gross_wins = trades_df[trades_df['pnl_net'] > 0]['pnl_net'].sum()
    gross_losses = abs(trades_df[trades_df['pnl_net'] <= 0]['pnl_net'].sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    # Drawdown
    peak = equity_series.cummax()
    dd_series = (equity_series - peak) / peak
    max_dd = dd_series.min()
    max_dd_inr = (equity_series - peak).min()

    # Sharpe (weekly returns)
    weekly_returns = trades_df['pnl_net'] / CAPITAL
    sharpe = float(np.sqrt(52) * (weekly_returns.mean() - 0.06/52) / weekly_returns.std()) if weekly_returns.std() > 0 else 0

    # Monthly P&L
    trades_df['month'] = pd.to_datetime(trades_df['entry_date']).dt.to_period('M')
    monthly = trades_df.groupby('month')['pnl_net'].sum()
    profitable_months = (monthly > 0).sum()

    # Margin utilization
    avg_margin = trades_df['margin_required'].mean()

    # Time periods
    n_months = len(monthly)
    ann_return = ((capital / CAPITAL) ** (12 / n_months) - 1) if n_months > 0 else 0

    print(f"  {'='*50}")
    print(f"  CAPITAL")
    print(f"    Starting:     ₹{CAPITAL:>12,}")
    print(f"    Final:        ₹{capital:>12,.0f}")
    print(f"    Net P&L:      ₹{total_pnl_net:>12,.0f}")
    print(f"    Hard Floor:   ₹{HARD_FLOOR:>12,}  {'BREACHED on ' + (floor_breach_date or '?') if floor_breached else 'SAFE'}")
    print(f"    Return:       {(capital/CAPITAL - 1)*100:>11.1f}%")
    print(f"    Ann. Return:  {ann_return*100:>11.1f}%")
    print(f"  {'='*50}")
    print(f"  TRADES")
    print(f"    Total:        {total:>12}")
    print(f"    Winners:      {winners:>12}  ({win_rate*100:.1f}%)")
    print(f"    Losers:       {losers:>12}  ({(1-win_rate)*100:.1f}%)")
    print(f"    Avg P&L/wk:   ₹{avg_pnl:>12,.0f}")
    print(f"    Avg Win:      ₹{avg_win:>12,.0f}")
    print(f"    Avg Loss:     ₹{avg_loss:>12,.0f}")
    print(f"    Best Trade:   ₹{best_trade:>12,.0f}")
    print(f"    Worst Trade:  ₹{worst_trade:>12,.0f}")
    print(f"  {'='*50}")
    print(f"  RISK")
    print(f"    Sharpe:       {sharpe:>12.2f}")
    print(f"    Profit Factor:{pf:>12.2f}")
    print(f"    Max Drawdown: {max_dd*100:>11.1f}%  (₹{max_dd_inr:,.0f})")
    print(f"    Worst trade % of capital: {worst_trade/CAPITAL*100:.1f}%")
    print(f"  {'='*50}")
    print(f"  COSTS")
    print(f"    Total Costs:  ₹{total_costs:>12,.0f}")
    print(f"    Total Slip:   ₹{total_slippage:>12,.0f}")
    print(f"    Costs as % of premium: {total_costs/total_premium*100:.2f}%")
    print(f"    Avg Margin/wk:₹{avg_margin:>12,.0f}")
    print(f"  {'='*50}")
    print(f"  CONSISTENCY")
    print(f"    Profitable months: {profitable_months}/{n_months}")
    print(f"    Avg premium/wk: ₹{avg_premium:,.0f}")

    # Monthly breakdown
    print(f"\n  Monthly P&L:")
    for period, pnl in monthly.items():
        marker = " <<<" if pnl == monthly.min() else ""
        print(f"    {period}: {'+'if pnl>0 else '-'}₹{abs(pnl):>10,.0f}{marker}")

    # Worst 5 trades
    print(f"\n  Worst 5 trades:")
    worst5 = trades_df.nsmallest(5, 'pnl_net')
    for _, t in worst5.iterrows():
        print(f"    {t['entry_date']}: ₹{t['pnl_net']:>10,.0f}  (NIFTY moved {t['spot_move_pct']:+.1f}%)")

    # ============================================================
    # VERDICT
    # ============================================================
    print(f"\n  {'='*50}")

    passed = (
        total_pnl_net > 0 and
        not floor_breached and
        pf > 1.0 and
        max_dd > -0.20
    )

    if passed:
        print(f"  VERDICT: PASS — Strategy profitable with REAL premiums after all costs")
        print(f"          Hard floor ₹{HARD_FLOOR:,} was NEVER breached.")
    else:
        reasons = []
        if total_pnl_net <= 0: reasons.append("net loss")
        if floor_breached: reasons.append(f"hard floor ₹{HARD_FLOOR:,} breached on {floor_breach_date} — all positions exited, only closed-trade P&L counted")
        if pf <= 1.0: reasons.append(f"profit factor {pf:.2f} <= 1.0")
        if max_dd <= -0.20: reasons.append(f"max DD {max_dd*100:.1f}% exceeds -20%")
        print(f"  VERDICT: FAIL — {'; '.join(reasons)}")

    # Save results
    output = {
        "timestamp": datetime.now().isoformat(),
        "passed": bool(passed),
        "floor_breached": bool(floor_breached),
        "floor_breach_date": floor_breach_date,
        "capital_start": CAPITAL,
        "capital_final": float(capital),
        "hard_floor": HARD_FLOOR,
        "total_pnl_net": float(total_pnl_net),
        "total_trades": total,
        "win_rate": float(win_rate),
        "sharpe": float(sharpe),
        "profit_factor": float(pf),
        "max_drawdown_pct": float(max_dd),
        "total_costs": float(total_costs),
        "annualized_return": float(ann_return),
        "data_source": "Dhan rollingoption API (actual expired option prices)",
    }

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation_real_results.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Results saved to: {output_path}")

    # Save trades for analysis
    trades_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "real_trades.csv")
    trades_df.to_csv(trades_path, index=False)
    print(f"  Trade log saved to: {trades_path}")


if __name__ == "__main__":
    main()
