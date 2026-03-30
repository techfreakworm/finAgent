import os
"""
FinAgent — Multi-Instrument Simulation at ₹50K-₹5L
=====================================================
Tests short strangle across ALL available instruments to find
what's actually tradeable at each capital level.

Instruments:
  - NIFTY (lot 75, ~₹23K spot → notional ~₹17L)
  - BANKNIFTY (lot 30, ~₹50K spot → notional ~₹15L)
  - FINNIFTY (lot 65, ~₹24K spot → notional ~₹15.6L)
  - MIDCPNIFTY (lot 120, ~₹13K spot → notional ~₹15.6L)
  - Stock options: ITC, PNB, etc. (smaller notionals possible)

Post Nov 2024 SEBI rules:
  - Min contract value ₹15L → all index lot sizes increased
  - Only NIFTY + SENSEX retain weekly options
  - BANKNIFTY, FINNIFTY, MIDCPNIFTY → monthly only

Pre Nov 2024:
  - NIFTY lot=25, BANKNIFTY lot=15, FINNIFTY lot=25, MIDCPNIFTY lot=50
  - All had weekly options → more opportunities
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import requests
from pyxirr import xirr
from datetime import datetime, timedelta
import json, time, os

# ============================================================
# CONFIG
# ============================================================

DHAN_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
HEADERS = {'Accept':'application/json','Content-Type':'application/json','access-token':DHAN_TOKEN,'client-id':DHAN_CLIENT_ID}
BASE = 'https://api.dhan.co/v2'

MAX_LOSS_PCT = 0.20
CAPITALS = [50000, 100000, 200000, 300000, 400000, 500000]

# Instrument definitions
# lot_before = lot size before Nov 20 2024, lot_after = after
INSTRUMENTS = {
    "NIFTY": {
        "security_id": 13, "instrument": "OPTIDX",
        "lot_before": 25, "lot_after": 75,
        "weekly_available_after_nov24": True,
    },
    "BANKNIFTY": {
        "security_id": 25, "instrument": "OPTIDX",
        "lot_before": 15, "lot_after": 30,
        "weekly_available_after_nov24": False,  # SEBI discontinued weekly
    },
    "FINNIFTY": {
        "security_id": 27, "instrument": "OPTIDX",
        "lot_before": 25, "lot_after": 65,
        "weekly_available_after_nov24": False,
    },
    "MIDCPNIFTY": {
        "security_id": 442, "instrument": "OPTIDX",
        "lot_before": 50, "lot_after": 120,
        "weekly_available_after_nov24": False,
    },
    "ITC": {
        "security_id": 1660, "instrument": "OPTSTK",
        "lot_before": 1600, "lot_after": 1600,  # stock lot sizes didn't change
        "weekly_available_after_nov24": False,
    },
    "PNB": {
        "security_id": 10666, "instrument": "OPTSTK",
        "lot_before": 8000, "lot_after": 8000,
        "weekly_available_after_nov24": False,
    },
}

LOT_CHANGE = datetime(2024, 11, 20)

def get_lot(inst_name, dt):
    inst = INSTRUMENTS[inst_name]
    if isinstance(dt, str): dt = datetime.strptime(dt, "%Y-%m-%d")
    return inst["lot_after"] if dt >= LOT_CHANGE else inst["lot_before"]


# ============================================================
# COSTS (Zerodha)
# ============================================================

def strangle_cost(ce_sell_val, pe_sell_val, ce_buy_val, pe_buy_val):
    def sell_c(pv):
        b=20; stt=pv*0.000625; ex=pv*0.0005; se=pv*1e-6; g=(b+ex)*0.18
        return b+stt+ex+se+g
    def buy_c(pv):
        b=20; ex=pv*0.0005; se=pv*1e-6; st=pv*3e-5; g=(b+ex)*0.18
        return b+ex+se+st+g
    return sell_c(ce_sell_val)+sell_c(pe_sell_val)+buy_c(ce_buy_val)+buy_c(pe_buy_val)


# ============================================================
# FETCH DATA
# ============================================================

def fetch_data(sec_id, instrument, start, end, strike="ATM", opt_type="CALL"):
    """Fetch hourly rolling option data in 30-day chunks"""
    all_d = {"close":[], "iv":[], "oi":[], "spot":[], "timestamp":[]}
    cur = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    while cur < end_dt:
        ce = min(cur + timedelta(days=29), end_dt)
        payload = {
            "exchangeSegment": "NSE_FNO", "interval": "60",
            "securityId": sec_id, "instrument": instrument,
            "expiryFlag": "MONTH", "expiryCode": 1,
            "strike": strike, "drvOptionType": opt_type,
            "requiredData": ["close","iv","oi","spot"],
            "fromDate": cur.strftime("%Y-%m-%d"), "toDate": ce.strftime("%Y-%m-%d"),
        }
        resp = requests.post(f'{BASE}/charts/rollingoption', headers=HEADERS,
                             data=json.dumps(payload), timeout=30)
        time.sleep(0.8)

        if resp.status_code == 200:
            data = resp.json().get("data", {})
            key = "ce" if opt_type == "CALL" else "pe"
            opt = data.get(key, {})
            if opt and opt.get("timestamp"):
                for f in all_d:
                    vals = opt.get(f, [])
                    if vals: all_d[f].extend(vals)
        elif resp.status_code == 429:
            time.sleep(5)
            cur = ce + timedelta(days=1)
            continue

        cur = ce + timedelta(days=1)

    return all_d


# ============================================================
# SIMULATE ONE INSTRUMENT
# ============================================================

def simulate_instrument(inst_name, combined_df, starting_capital):
    """Run short strangle simulation"""
    floor = starting_capital * (1 - MAX_LOSS_PCT)
    capital = starting_capital
    trades = []
    skipped = 0
    floor_breached = False
    first_date = last_date = None

    combined_df['week'] = combined_df.index.to_period('W-THU')
    weeks = combined_df.groupby('week')

    for wp, wd in weeks:
        if floor_breached: continue
        if len(wd) < 3: continue

        entry = wd.iloc[0]
        spot = entry.get('spot', 0)
        if spot == 0 or pd.isna(spot): continue

        dt = wd.index[0].to_pydatetime()
        entry_date = dt.date()
        if first_date is None: first_date = entry_date

        lot = get_lot(inst_name, dt)

        ce_e = float(entry.get('ce_close', 0)) if not pd.isna(entry.get('ce_close', 0)) else 0
        pe_e = float(entry.get('pe_close', 0)) if not pd.isna(entry.get('pe_close', 0)) else 0
        if ce_e <= 0 and pe_e <= 0: continue

        # Margin: ~15-20% of notional for short strangle
        notional = spot * lot
        margin = notional * 0.15
        if margin > capital * 0.9:
            skipped += 1
            continue

        # Exit
        ex = wd.iloc[-1]
        exit_date = ex.name.to_pydatetime().date()
        last_date = exit_date

        ce_x = float(ex.get('ce_close', 0)) if not pd.isna(ex.get('ce_close', 0)) else 0
        pe_x = float(ex.get('pe_close', 0)) if not pd.isna(ex.get('pe_close', 0)) else 0

        prem_in = (ce_e + pe_e) * lot
        prem_out = (ce_x + pe_x) * lot
        gross = prem_in - prem_out
        cost = strangle_cost(ce_e*lot, pe_e*lot, ce_x*lot, pe_x*lot)
        slip = 1.0 * lot * 4
        net = gross - cost - slip

        capital += net
        trades.append({'date': exit_date, 'pnl': net, 'margin': margin, 'notional': notional})

        if capital < floor:
            floor_breached = True

    # XIRR
    xirr_val = None
    if first_date and last_date and first_date < last_date and len(trades) > 0:
        try:
            xirr_val = xirr([first_date, last_date], [-float(starting_capital), float(capital)])
        except:
            pass

    return {
        'n_trades': len(trades),
        'final': capital,
        'pnl': capital - starting_capital,
        'skipped': skipped,
        'floor_breached': floor_breached,
        'xirr': xirr_val,
        'avg_margin': np.mean([t['margin'] for t in trades]) if trades else 0,
        'avg_notional': np.mean([t['notional'] for t in trades]) if trades else 0,
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "="*70)
    print("  FINAGENT — ALL-INSTRUMENT SIMULATION")
    print("  " + "="*66)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Goal: Find what's tradeable at ₹50K-₹5L")
    print("="*70)

    start, end = "2024-03-01", "2026-03-28"

    # ============================================================
    # FETCH ALL INSTRUMENT DATA
    # ============================================================
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "all_instruments_cache.json")

    if os.path.exists(cache_file):
        print("\n  Loading cached data...")
        with open(cache_file) as f:
            all_data = json.load(f)
    else:
        all_data = {}
        for name, inst in INSTRUMENTS.items():
            print(f"\n  Fetching {name}...")
            print(f"    ATM CALL...", end=" ", flush=True)
            ce = fetch_data(inst["security_id"], inst["instrument"], start, end, "ATM", "CALL")
            print(f"{len(ce['timestamp'])} pts")

            print(f"    ATM PUT...", end=" ", flush=True)
            pe = fetch_data(inst["security_id"], inst["instrument"], start, end, "ATM", "PUT")
            print(f"{len(pe['timestamp'])} pts")

            all_data[name] = {"ce": ce, "pe": pe}
            time.sleep(1)

        with open(cache_file, 'w') as f:
            json.dump(all_data, f)
        print("\n  Cached to all_instruments_cache.json")

    # ============================================================
    # BUILD DATAFRAMES
    # ============================================================
    instrument_dfs = {}
    for name, data in all_data.items():
        ce = data['ce']
        pe = data['pe']
        if not ce['timestamp'] or not pe['timestamp']:
            print(f"  {name}: no data, skipping")
            continue

        ce_df = pd.DataFrame({
            'ce_close': ce['close'], 'ce_iv': ce.get('iv'),
            'spot': ce.get('spot'),
            'timestamp': pd.to_datetime(ce['timestamp'], unit='s'),
        }).set_index('timestamp')

        pe_df = pd.DataFrame({
            'pe_close': pe['close'], 'pe_iv': pe.get('iv'),
            'timestamp': pd.to_datetime(pe['timestamp'], unit='s'),
        }).set_index('timestamp')

        combined = ce_df.join(pe_df, how='outer').sort_index()
        instrument_dfs[name] = combined
        # Get approx spot price
        spots = combined['spot'].dropna()
        avg_spot = spots.mean() if len(spots) > 0 else 0
        print(f"  {name}: {len(combined)} rows, avg spot ≈ {avg_spot:,.0f}")

    # ============================================================
    # INSTRUMENT OVERVIEW TABLE
    # ============================================================
    print(f"\n  {'':=<90}")
    print(f"  INSTRUMENT OVERVIEW (Current lot sizes and margins)")
    print(f"  {'':=<90}")
    print(f"  {'Instrument':>12} | {'Lot(old)':>8} | {'Lot(new)':>8} | {'Spot':>8} | {'Notional':>12} | {'~Margin':>10} | {'Min Cap':>10} | {'Weekly?':>7}")
    print(f"  {'-'*12}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*12}-+-{'-'*10}-+-{'-'*10}-+-{'-'*7}")

    for name in INSTRUMENTS:
        inst = INSTRUMENTS[name]
        if name not in instrument_dfs: continue
        spots = instrument_dfs[name]['spot'].dropna()
        spot = spots.iloc[-1] if len(spots) > 0 else 0
        lot_old = inst['lot_before']
        lot_new = inst['lot_after']
        notional_new = spot * lot_new
        margin_new = notional_new * 0.15
        min_cap = margin_new / 0.9  # need margin < 90% of capital
        weekly = "Yes" if inst['weekly_available_after_nov24'] else "No"

        print(f"  {name:>12} | {lot_old:>8} | {lot_new:>8} | ₹{spot:>6,.0f} | ₹{notional_new:>10,.0f} | ₹{margin_new:>8,.0f} | ₹{min_cap:>8,.0f} | {weekly:>7}")

    # ============================================================
    # RUN SIMULATIONS
    # ============================================================
    print(f"\n  {'':=<110}")
    print(f"  SIMULATION: Short Strangle per Instrument per Capital Level (XIRR)")
    print(f"  {'':=<110}")

    # For each capital level, show which instruments are tradeable
    for cap in CAPITALS:
        floor = cap * (1 - MAX_LOSS_PCT)
        print(f"\n  --- Capital: ₹{cap//1000}K | Floor: ₹{floor//1000}K ---")
        print(f"  {'Instrument':>12} | {'Trades':>6} | {'Skipped':>7} | {'Final':>10} | {'Net P&L':>10} | {'XIRR':>8} | {'Margin/trade':>12} | {'Floor?':>6}")
        print(f"  {'-'*12}-+-{'-'*6}-+-{'-'*7}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}-+-{'-'*12}-+-{'-'*6}")

        for name in INSTRUMENTS:
            if name not in instrument_dfs: continue
            r = simulate_instrument(name, instrument_dfs[name].copy(), cap)

            if r['n_trades'] > 0:
                xirr_str = f"{r['xirr']*100:>+6.1f}%" if r['xirr'] is not None else "  N/A"
                fb = "YES" if r['floor_breached'] else "no"
                print(f"  {name:>12} | {r['n_trades']:>6} | {r['skipped']:>7} | ₹{r['final']:>8,.0f} | ₹{r['pnl']:>+8,.0f} | {xirr_str:>8} | ₹{r['avg_margin']:>10,.0f} | {fb:>6}")
            else:
                print(f"  {name:>12} | {'0':>6} | {r['skipped']:>7} | ₹{cap:>8,} | {'₹0':>10} | {'N/A':>8} | {'N/A':>12} | {'N/A':>6}")

    # ============================================================
    # BEST INSTRUMENT PER CAPITAL
    # ============================================================
    print(f"\n  {'':=<70}")
    print(f"  BEST INSTRUMENT PER CAPITAL LEVEL")
    print(f"  {'':=<70}")

    for cap in CAPITALS:
        best_name = None
        best_xirr = -999
        best_pnl = 0

        for name in INSTRUMENTS:
            if name not in instrument_dfs: continue
            r = simulate_instrument(name, instrument_dfs[name].copy(), cap)
            if r['n_trades'] > 0 and not r['floor_breached']:
                x = r['xirr'] if r['xirr'] is not None else 0
                if x > best_xirr:
                    best_xirr = x
                    best_name = name
                    best_pnl = r['pnl']

        if best_name:
            print(f"  ₹{cap//1000:>5}K → {best_name:>12} (XIRR {best_xirr*100:>+.1f}%, P&L ₹{best_pnl:>+,.0f})")
        else:
            print(f"  ₹{cap//1000:>5}K → No instrument tradeable (all skip due to margin)")


if __name__ == "__main__":
    main()
