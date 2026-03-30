"""
Validate Dhan-only data migration — drop yfinance completely.
==============================================================
Tests:
1. Fetch Dhan instrument master → map all NIFTY 50 + NIFTYBEES + VIX
2. Fetch daily OHLCV for all 50 stocks via Dhan
3. Fetch India VIX via Dhan
4. Fetch NIFTYBEES (ETF for NIFTY exposure)
5. Compare data quality with yfinance
6. Run backtest with Dhan-only data
"""

import os
import sys
import warnings
warnings.filterwarnings("ignore")

import json
import time
import requests
import pandas as pd
import numpy as np
import ta
from datetime import datetime, timedelta
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

DHAN_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
BASE = "https://api.dhan.co/v2"

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "access-token": DHAN_TOKEN,
    "client-id": DHAN_CLIENT_ID,
}

# ============================================================
# NIFTY 50 CONSTITUENTS (NSE symbols → for matching)
# ============================================================

NIFTY_50_SYMBOLS = [
    "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJFINANCE", "BAJAJFINSV", "BEL", "BPCL", "BHARTIARTL",
    "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "ITC", "INDUSINDBK",
    "INFY", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "NESTLEIND", "NTPC", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SBIN", "SUNPHARMA", "TCS",
    "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TECHM", "TITAN",
    "TRENT", "ULTRACEMCO", "WIPRO", "SHRIRAMFIN", "ETERNAL",
]


def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


# ============================================================
# STEP 1: Fetch Dhan instrument master
# ============================================================

def fetch_instrument_master():
    """Download Dhan's instrument CSV and extract NIFTY 50 security IDs."""
    print_header("STEP 1: Fetch Dhan Instrument Master")

    # Dhan provides CSV instrument lists
    urls = {
        "NSE_EQ": "https://images.dhan.co/api-data/api-scrip-master-detailed.csv",
    }

    print("  Downloading instrument master CSV...")
    try:
        resp = requests.get(urls["NSE_EQ"], timeout=30)
        if resp.status_code != 200:
            print(f"  Failed: HTTP {resp.status_code}")
            return None

        # Parse CSV
        df = pd.read_csv(StringIO(resp.text))
        print(f"  Total instruments: {len(df)}")
        print(f"  Columns: {list(df.columns)[:10]}")

        # Show sample
        print(f"\n  Sample rows:")
        print(df.head(3).to_string())

        return df
    except Exception as e:
        print(f"  Error: {e}")
        return None


def map_nifty50_ids(instrument_df):
    """Map NIFTY 50 symbols to Dhan security IDs."""
    print_header("STEP 2: Map NIFTY 50 to Dhan Security IDs")

    if instrument_df is None:
        print("  No instrument data available")
        return {}

    # Identify relevant columns
    cols = list(instrument_df.columns)
    print(f"  Available columns: {cols}")

    # Use known Dhan column names
    sym_col = "SYMBOL_NAME" if "SYMBOL_NAME" in cols else "UNDERLYING_SYMBOL"
    id_col = "SECURITY_ID" if "SECURITY_ID" in cols else "UNDERLYING_SECURITY_ID"
    exch_col = "EXCH_ID" if "EXCH_ID" in cols else None
    seg_col = "SEGMENT" if "SEGMENT" in cols else None
    series_col = "SERIES" if "SERIES" in cols else None

    print(f"  Symbol column: {sym_col}")
    print(f"  Security ID column: {id_col}")
    print(f"  Exchange column: {exch_col}")

    if not sym_col or not id_col:
        # Try alternate approach — just show all column values for first row
        print(f"\n  First row values:")
        for c in cols:
            print(f"    {c}: {instrument_df[c].iloc[0]}")
        return {}

    # Filter to NSE equity cash segment only
    nse_df = instrument_df
    if exch_col and seg_col:
        nse_df = instrument_df[
            (instrument_df[exch_col].astype(str).str.upper() == "NSE") &
            (instrument_df[seg_col].astype(str).str.upper().isin(["E", "EQ"]))
        ]
        if len(nse_df) == 0:
            # Try broader filter
            nse_df = instrument_df[instrument_df[exch_col].astype(str).str.upper() == "NSE"]
        print(f"  NSE equity instruments: {len(nse_df)}")
    elif exch_col:
        nse_df = instrument_df[instrument_df[exch_col].astype(str).str.contains("NSE", case=False, na=False)]
        print(f"  NSE instruments: {len(nse_df)}")

    # Map NIFTY 50 symbols
    mapping = {}
    missing = []

    def safe_int(val):
        try:
            if pd.isna(val):
                return None
            return int(val)
        except (ValueError, TypeError):
            return None

    for symbol in NIFTY_50_SYMBOLS:
        matches = nse_df[nse_df[sym_col].astype(str).str.upper() == symbol.upper()]
        if len(matches) > 0:
            sec_id = safe_int(matches.iloc[0][id_col])
            if sec_id:
                mapping[symbol] = sec_id
            else:
                missing.append(symbol)
        else:
            # Try partial match
            partial = nse_df[nse_df[sym_col].astype(str).str.upper().str.contains(symbol.upper(), na=False)]
            if len(partial) > 0:
                sec_id = safe_int(partial.iloc[0][id_col])
                if sec_id:
                    mapping[symbol] = sec_id
                else:
                    missing.append(symbol)
            else:
                missing.append(symbol)

    # Also find NIFTYBEES — search entire instrument list (it's an ETF)
    bees = instrument_df[instrument_df[sym_col].astype(str).str.upper().str.contains("NIFTYBEES", na=False)]
    if len(bees) > 0:
        sec_id = safe_int(bees.iloc[0][id_col])
        if sec_id:
            mapping["NIFTYBEES"] = sec_id
            print(f"  NIFTYBEES: {mapping['NIFTYBEES']}")

    # India VIX — search entire list
    vix = instrument_df[instrument_df[sym_col].astype(str).str.upper().str.contains("INDIA VIX", na=False)]
    if len(vix) > 0:
        sec_id = safe_int(vix.iloc[0][id_col])
        if sec_id:
            mapping["INDIA_VIX"] = sec_id
            print(f"  INDIA VIX: {mapping['INDIA_VIX']}")

    print(f"\n  Mapped: {len(mapping)}/{len(NIFTY_50_SYMBOLS)} NIFTY 50 stocks")
    if missing:
        print(f"  Missing: {missing}")

    return mapping


# ============================================================
# STEP 3: Test Dhan historical data fetch
# ============================================================

def fetch_dhan_daily(security_id, from_date, to_date, instrument="EQUITY", exchange="NSE_EQ"):
    """Fetch daily OHLCV from Dhan."""
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": exchange,
        "instrument": instrument,
        "fromDate": from_date,
        "toDate": to_date,
    }
    resp = requests.post(f"{BASE}/charts/historical", headers=HEADERS, data=json.dumps(payload), timeout=30)
    if resp.status_code == 200:
        return resp.json()
    return None


def test_data_fetch(mapping):
    """Test fetching data for a few stocks + VIX + NIFTYBEES."""
    print_header("STEP 3: Test Dhan Data Fetch")

    from_date = "2024-01-01"
    to_date = "2026-01-31"

    test_symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "SBIN", "NIFTYBEES"]
    results = {}

    for sym in test_symbols:
        sec_id = mapping.get(sym)
        if not sec_id:
            print(f"  {sym}: no security ID")
            continue

        data = fetch_dhan_daily(sec_id, from_date, to_date)
        time.sleep(0.5)

        if data and "close" in data:
            n = len(data["close"])
            print(f"  {sym} (ID={sec_id}): {n} daily candles")
            results[sym] = {
                "security_id": sec_id,
                "candles": n,
                "data": data,
            }
        else:
            print(f"  {sym} (ID={sec_id}): FAILED — {str(data)[:100]}")

    # Test VIX
    vix_id = mapping.get("INDIA_VIX")
    if vix_id:
        # VIX is an index, use IDX_I segment
        for seg, inst in [("IDX_I", "INDEX"), ("NSE_EQ", "EQUITY")]:
            data = fetch_dhan_daily(vix_id, from_date, to_date, instrument=inst, exchange=seg)
            time.sleep(0.5)
            if data and "close" in data:
                n = len(data["close"])
                print(f"  INDIA_VIX (ID={vix_id}, {seg}/{inst}): {n} daily candles")
                results["INDIA_VIX"] = {"security_id": vix_id, "candles": n, "data": data, "segment": seg, "instrument": inst}
                break
            else:
                print(f"  INDIA_VIX (ID={vix_id}, {seg}/{inst}): no data")

    # Also test NIFTY 50 index (ID=13)
    nifty_data = fetch_dhan_daily(13, from_date, to_date, instrument="INDEX", exchange="IDX_I")
    if nifty_data and "close" in nifty_data:
        print(f"  NIFTY 50 (ID=13): {len(nifty_data['close'])} daily candles")
        results["NIFTY_50"] = {"security_id": 13, "candles": len(nifty_data["close"]), "data": nifty_data}

    return results


# ============================================================
# STEP 4: Fetch all NIFTY 50 stocks and run backtest
# ============================================================

def fetch_all_nifty50(mapping):
    """Fetch daily data for all NIFTY 50 stocks via Dhan."""
    print_header("STEP 4: Fetch All NIFTY 50 Stocks (Dhan-only)")

    from_date = "2021-01-01"
    to_date = "2026-01-31"

    stock_data = {}
    failed = []

    for i, sym in enumerate(NIFTY_50_SYMBOLS):
        sec_id = mapping.get(sym)
        if not sec_id:
            failed.append(sym)
            continue

        data = fetch_dhan_daily(sec_id, from_date, to_date)
        time.sleep(0.5)

        if data and "close" in data and len(data["close"]) > 200:
            df = pd.DataFrame({
                "Open": data.get("open", []),
                "High": data.get("high", []),
                "Low": data.get("low", []),
                "Close": data["close"],
                "Volume": data.get("volume", []),
            })
            if "timestamp" in data:
                df.index = pd.to_datetime(data["timestamp"], unit="s")
            df = df.sort_index()
            stock_data[sym] = df
        else:
            failed.append(sym)

        if (i + 1) % 10 == 0:
            print(f"  Progress: {i+1}/{len(NIFTY_50_SYMBOLS)} ({len(stock_data)} loaded)")

    print(f"\n  Loaded: {len(stock_data)}/{len(NIFTY_50_SYMBOLS)} stocks")
    if failed:
        print(f"  Failed: {failed}")

    return stock_data


def run_backtest(stock_data, use_intraday_stops=False):
    """Run mean reversion backtest — same as backtest_proper.py."""
    capital = 500000
    hard_floor = 400000
    rsi_entry = 30
    rsi_exit = 55
    stop_loss_pct = -0.10
    max_hold_days = 20
    max_position_pct = 0.20

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
                    intraday_low_pnl = (row["Low"] - entry_price) / entry_price
                    stop_triggered = intraday_low_pnl <= stop_loss_pct
                    exit_price = entry_price * (1 + stop_loss_pct) if stop_triggered else row["Close"]
                else:
                    exit_price = row["Close"]
                    stop_triggered = (exit_price - entry_price) / entry_price <= stop_loss_pct

                pnl_pct = (exit_price - entry_price) / entry_price
                should_exit = stop_triggered or row["rsi"] > rsi_exit or days_held > max_hold_days

                if should_exit:
                    reason = "stop" if stop_triggered else ("target" if row["rsi"] > rsi_exit else "timeout")
                    pos_value = capital * max_position_pct
                    stt = (pos_value + pos_value * (1 + pnl_pct)) * 0.001
                    cost = stt + 16 + pos_value * 0.001  # STT + DP + slippage
                    pnl_net = pos_value * pnl_pct - cost
                    capital += pnl_net
                    in_trade = False

                    trades.append({"symbol": sym, "pnl_net": pnl_net, "reason": reason, "days": days_held})
                    if capital < hard_floor:
                        floor_breached = True

    total = len(trades)
    if total == 0:
        return {"trades": 0, "pnl": 0, "capital": capital}

    winners = sum(1 for t in trades if t["pnl_net"] > 0)
    total_pnl = capital - 500000

    return {
        "trades": total,
        "winners": winners,
        "win_rate": winners / total,
        "pnl": total_pnl,
        "capital": capital,
        "floor_breached": floor_breached,
        "stops": sum(1 for t in trades if t["reason"] == "stop"),
        "targets": sum(1 for t in trades if t["reason"] == "target"),
        "timeouts": sum(1 for t in trades if t["reason"] == "timeout"),
    }


# ============================================================
# STEP 5: Compare with yfinance
# ============================================================

def compare_with_yfinance(dhan_data, mapping):
    """Compare a few stocks between Dhan and yfinance."""
    print_header("STEP 5: Compare Dhan vs yfinance Data Quality")

    import yfinance as yf

    test = ["RELIANCE", "TCS", "HDFCBANK"]
    for sym in test:
        sec_id = mapping.get(sym)
        if not sec_id or sym not in dhan_data:
            continue

        # Dhan
        dhan_df = dhan_data[sym]
        dhan_last = dhan_df["Close"].iloc[-1]
        dhan_rows = len(dhan_df)

        # yfinance
        yf_df = yf.download(f"{sym}.NS", period="5y", progress=False)
        if isinstance(yf_df.columns, pd.MultiIndex):
            yf_df.columns = yf_df.columns.get_level_values(0)
        yf_last = yf_df["Close"].iloc[-1] if len(yf_df) > 0 else 0
        yf_rows = len(yf_df)

        diff_pct = abs(dhan_last - yf_last) / yf_last * 100 if yf_last > 0 else 0

        print(f"  {sym}:")
        print(f"    Dhan:     {dhan_rows} rows, last close ₹{dhan_last:.2f}")
        print(f"    yfinance: {yf_rows} rows, last close ₹{yf_last:.2f}")
        print(f"    Price diff: {diff_pct:.2f}%")


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "=" * 70)
    print("  DHAN-ONLY MIGRATION VALIDATION")
    print("  " + "=" * 66)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Goal: Verify Dhan API can replace yfinance for all data needs")
    print("=" * 70)

    if not DHAN_TOKEN:
        print("\n  ERROR: DHAN_ACCESS_TOKEN not set in .env")
        return

    # Step 1: Instrument master
    instruments = fetch_instrument_master()

    # Step 2: Map NIFTY 50
    mapping = map_nifty50_ids(instruments)

    if len(mapping) < 10:
        print("\n  Not enough mappings. Trying alternate approach...")
        # Fallback: use Dhan's compact CSV
        try:
            compact_url = "https://images.dhan.co/api-data/api-scrip-master.csv"
            resp = requests.get(compact_url, timeout=30)
            if resp.status_code == 200:
                instruments = pd.read_csv(StringIO(resp.text))
                print(f"  Compact CSV: {len(instruments)} rows, columns: {list(instruments.columns)}")
                mapping = map_nifty50_ids(instruments)
        except Exception as e:
            print(f"  Fallback failed: {e}")

    if not mapping:
        print("\n  FAILED: Cannot map symbols to security IDs")
        return

    # Save mapping for future use
    mapping_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dhan_nifty50_mapping.json")
    with open(mapping_file, "w") as f:
        json.dump(mapping, f, indent=2)
    print(f"\n  Mapping saved to: {mapping_file}")

    # Step 3: Test fetch
    test_results = test_data_fetch(mapping)

    # Step 4: Fetch all and backtest
    stock_data = fetch_all_nifty50(mapping)

    if len(stock_data) >= 20:
        print_header("STEP 6: Backtest with Dhan Data (Daily + Intraday Stops)")

        daily = run_backtest(stock_data, use_intraday_stops=False)
        intraday = run_backtest(stock_data, use_intraday_stops=True)

        print(f"  {'Mode':<12} {'Trades':>7} {'Win%':>6} {'PF':>6} {'P&L':>12} {'Floor':>8}")
        print(f"  {'-'*60}")
        print(f"  {'Daily':<12} {daily['trades']:>7} {daily['win_rate']*100:>5.1f}% {'':>6} ₹{daily['pnl']:>+10,.0f} {'BREACH' if daily['floor_breached'] else 'SAFE':>8}")
        print(f"  {'Intraday':<12} {intraday['trades']:>7} {intraday['win_rate']*100:>5.1f}% {'':>6} ₹{intraday['pnl']:>+10,.0f} {'BREACH' if intraday['floor_breached'] else 'SAFE':>8}")
        print(f"\n  Stocks in universe: {len(stock_data)}")

        # Step 5: Compare with yfinance
        compare_with_yfinance(stock_data, mapping)

        if daily['pnl'] > 0 and intraday['pnl'] > 0 and not daily['floor_breached'] and not intraday['floor_breached']:
            print(f"\n  >>> VERDICT: PASS — Dhan data produces profitable results")
            print(f"      Safe to migrate. Drop yfinance.")
        else:
            print(f"\n  >>> VERDICT: INVESTIGATE — check data quality")
    else:
        print(f"\n  Only {len(stock_data)} stocks loaded — need more for reliable backtest")


if __name__ == "__main__":
    main()
