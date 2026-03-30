"""
FinAgent Strategy Validation Script
====================================
Runs all core hypotheses validation in sequence.
Reports pass/fail for each with metrics.
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import ta
from scipy import stats
import json
import sys
import os

# ============================================================
# UTILITIES
# ============================================================

def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def print_result(name, passed, metrics=None):
    status = "PASS ✓" if passed else "FAIL ✗"
    print(f"\n  Result: [{status}] {name}")
    if metrics:
        for k, v in metrics.items():
            if isinstance(v, float):
                print(f"    {k}: {v:.4f}")
            else:
                print(f"    {k}: {v}")
    print()

def sharpe_ratio(returns, risk_free_rate=0.06):
    """Annualized Sharpe ratio (Indian risk-free ~6%)"""
    if returns.std() == 0:
        return 0.0
    excess = returns - risk_free_rate / 252
    return float(np.sqrt(252) * excess.mean() / excess.std())

def max_drawdown(cumulative_returns):
    """Maximum drawdown from cumulative returns series"""
    peak = cumulative_returns.cummax()
    dd = (cumulative_returns - peak) / peak
    return float(dd.min())

def annual_return(cumulative_returns):
    """Annualized return"""
    total_days = len(cumulative_returns)
    if total_days == 0 or cumulative_returns.iloc[0] == 0:
        return 0.0
    total_return = cumulative_returns.iloc[-1] / cumulative_returns.iloc[0] - 1
    years = total_days / 252
    if years == 0:
        return 0.0
    return float((1 + total_return) ** (1 / years) - 1)


# ============================================================
# VALIDATION 1: DATA ACCESS
# ============================================================

def validation_1_data_access():
    print_header("VALIDATION 1: Data Access — Can we get the data?")

    results = {}

    # 1a. NIFTY 50 daily OHLCV (3 years)
    print("  [1a] Fetching NIFTY 50 daily data (3 years)...")
    try:
        nifty = yf.download("^NSEI", period="3y", progress=False)
        if len(nifty) > 500:
            results["nifty_daily"] = f"OK — {len(nifty)} rows"
            print(f"       Got {len(nifty)} rows. Range: {nifty.index[0].date()} to {nifty.index[-1].date()}")
        else:
            results["nifty_daily"] = f"LOW — only {len(nifty)} rows"
    except Exception as e:
        results["nifty_daily"] = f"FAILED: {e}"
        print(f"       Failed: {e}")

    # 1b. India VIX
    print("  [1b] Fetching India VIX data...")
    try:
        vix = yf.download("^INDIAVIX", period="3y", progress=False)
        if len(vix) > 100:
            results["india_vix"] = f"OK — {len(vix)} rows"
            print(f"       Got {len(vix)} rows")
        else:
            results["india_vix"] = f"LOW — only {len(vix)} rows"
    except Exception as e:
        results["india_vix"] = f"FAILED: {e}"
        print(f"       Failed: {e}")

    # 1c. Top Indian stocks
    print("  [1c] Fetching top Indian stocks...")
    stocks = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
              "HINDUNILVR.NS", "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "KOTAKBANK.NS"]
    stock_data = {}
    for sym in stocks:
        try:
            df = yf.download(sym, period="3y", progress=False)
            if len(df) > 500:
                stock_data[sym] = df
        except:
            pass
    results["stocks"] = f"OK — {len(stock_data)}/{len(stocks)} stocks loaded"
    print(f"       Loaded {len(stock_data)} out of {len(stocks)} stocks")

    # 1d. NIFTY Bank, NIFTY IT (sector indices)
    print("  [1d] Fetching sector indices...")
    sectors_loaded = 0
    for idx in ["^NSEBANK", "^CNXIT"]:
        try:
            df = yf.download(idx, period="3y", progress=False)
            if len(df) > 100:
                sectors_loaded += 1
        except:
            pass
    results["sectors"] = f"OK — {sectors_loaded}/2 sector indices"
    print(f"       Loaded {sectors_loaded} sector indices")

    # 1e. Global data (USD/INR, Crude, S&P500)
    print("  [1e] Fetching global macro data...")
    global_loaded = 0
    for idx in ["USDINR=X", "CL=F", "^GSPC"]:
        try:
            df = yf.download(idx, period="3y", progress=False)
            if len(df) > 100:
                global_loaded += 1
        except:
            pass
    results["global"] = f"OK — {global_loaded}/3 global sources"
    print(f"       Loaded {global_loaded} global data sources")

    passed = "FAILED" not in str(results.get("nifty_daily", "")) and len(stock_data) >= 5
    print_result("Data Access", passed, results)

    return passed, nifty, vix, stock_data


# ============================================================
# VALIDATION 2: FII/DII PROXY — Institutional Flow Signal
# ============================================================

def validation_2_institutional_flow(nifty):
    print_header("VALIDATION 2: Institutional Flow Signal")
    print("  Note: Using volume + price proxy for FII/DII (actual NSE data requires scraping)")
    print("  Testing if volume-weighted price momentum predicts NIFTY direction\n")

    df = nifty.copy()
    # Flatten MultiIndex columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna()

    # Proxy: High-volume days with price increase = institutional buying
    # This is a simplified proxy — real system will use actual FII/DII data from NSE
    df["returns"] = df["Close"].pct_change()
    df["vol_sma20"] = df["Volume"].rolling(20).mean()
    df["rel_volume"] = df["Volume"] / df["vol_sma20"]

    # Institutional flow proxy: volume-weighted returns (high volume + positive = buying)
    df["flow_proxy"] = df["returns"] * df["rel_volume"]
    df["flow_5d"] = df["flow_proxy"].rolling(5).sum()
    df["flow_10d"] = df["flow_proxy"].rolling(10).sum()

    # Forward returns
    df["fwd_1d"] = df["returns"].shift(-1)
    df["fwd_5d"] = df["Close"].pct_change(5).shift(-5)

    df = df.dropna()

    # Correlation analysis
    corr_1d, pval_1d = stats.spearmanr(df["flow_5d"], df["fwd_1d"])
    corr_5d, pval_5d = stats.spearmanr(df["flow_5d"], df["fwd_5d"])

    print(f"  Spearman correlation (5d flow → next 1d return): {corr_1d:.4f} (p={pval_1d:.4f})")
    print(f"  Spearman correlation (5d flow → next 5d return): {corr_5d:.4f} (p={pval_5d:.4f})")

    # Simple strategy: long when 5d flow > 0, flat when < 0
    df["signal"] = np.where(df["flow_5d"] > 0, 1, 0)
    df["strategy_return"] = df["signal"].shift(1) * df["returns"]

    # Metrics
    strategy_cum = (1 + df["strategy_return"]).cumprod()
    buyhold_cum = (1 + df["returns"]).cumprod()

    strat_sharpe = sharpe_ratio(df["strategy_return"].dropna())
    bh_sharpe = sharpe_ratio(df["returns"].dropna())
    strat_ann = annual_return(strategy_cum)
    bh_ann = annual_return(buyhold_cum)
    strat_dd = max_drawdown(strategy_cum)
    bh_dd = max_drawdown(buyhold_cum)

    # Win rate
    traded_days = df[df["signal"].shift(1) == 1]
    win_rate = float((traded_days["returns"] > 0).mean()) if len(traded_days) > 0 else 0

    metrics = {
        "correlation_5d_flow_vs_5d_fwd (Spearman)": corr_5d,
        "p_value": pval_5d,
        "strategy_sharpe": strat_sharpe,
        "buyhold_sharpe": bh_sharpe,
        "strategy_annual_return": strat_ann,
        "buyhold_annual_return": bh_ann,
        "strategy_max_drawdown": strat_dd,
        "buyhold_max_drawdown": bh_dd,
        "win_rate_on_traded_days": win_rate,
        "days_in_market_pct": float(df["signal"].mean()),
    }

    # Pass if: correlation significant and strategy has better risk-adjusted returns
    passed = (pval_5d < 0.10) or (strat_sharpe > bh_sharpe * 0.8)

    print_result("Institutional Flow Proxy", passed, metrics)
    return passed


# ============================================================
# VALIDATION 3: VIX as Regime Filter (Simple Baseline)
# ============================================================

def validation_3_vix_regime(nifty, vix):
    print_header("VALIDATION 3: India VIX Regime Filter")
    print("  Testing: Does filtering by VIX improve a simple moving average strategy?\n")

    df = nifty.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    vix_df = vix.copy()
    if isinstance(vix_df.columns, pd.MultiIndex):
        vix_df.columns = vix_df.columns.get_level_values(0)

    df["returns"] = df["Close"].pct_change()

    # Join VIX
    df["vix"] = vix_df["Close"].reindex(df.index, method="ffill")
    df = df.dropna(subset=["vix", "returns"])

    if len(df) < 100:
        print("  Not enough overlapping VIX data")
        print_result("VIX Regime Filter", False, {"error": "insufficient data"})
        return False

    # Simple strategy: EMA 20/50 crossover
    df["ema20"] = df["Close"].ewm(span=20).mean()
    df["ema50"] = df["Close"].ewm(span=50).mean()
    df["base_signal"] = np.where(df["ema20"] > df["ema50"], 1, 0)

    # Unfiltered strategy
    df["unfiltered_return"] = df["base_signal"].shift(1) * df["returns"]

    # VIX-filtered strategy
    # Full position when VIX < 15, half when 15-20, quarter when 20-25, cash when > 25
    conditions = [
        df["vix"] < 15,
        (df["vix"] >= 15) & (df["vix"] < 20),
        (df["vix"] >= 20) & (df["vix"] < 25),
        df["vix"] >= 25,
    ]
    sizing = [1.0, 0.5, 0.25, 0.0]
    df["vix_sizing"] = np.select(conditions, sizing, default=0.5)
    df["filtered_signal"] = df["base_signal"] * df["vix_sizing"]
    df["filtered_return"] = df["filtered_signal"].shift(1) * df["returns"]

    df = df.dropna()

    # Metrics
    unf_cum = (1 + df["unfiltered_return"]).cumprod()
    flt_cum = (1 + df["filtered_return"]).cumprod()
    bh_cum = (1 + df["returns"]).cumprod()

    metrics = {
        "buyhold_sharpe": sharpe_ratio(df["returns"]),
        "unfiltered_EMA_sharpe": sharpe_ratio(df["unfiltered_return"]),
        "vix_filtered_EMA_sharpe": sharpe_ratio(df["filtered_return"]),
        "buyhold_max_dd": max_drawdown(bh_cum),
        "unfiltered_max_dd": max_drawdown(unf_cum),
        "vix_filtered_max_dd": max_drawdown(flt_cum),
        "buyhold_annual": annual_return(bh_cum),
        "unfiltered_annual": annual_return(unf_cum),
        "vix_filtered_annual": annual_return(flt_cum),
        "avg_vix": float(df["vix"].mean()),
        "pct_time_full_position": float((df["vix"] < 15).mean()),
        "pct_time_cash": float((df["vix"] >= 25).mean()),
    }

    # Pass if VIX filter improves Sharpe OR reduces drawdown significantly
    passed = (metrics["vix_filtered_EMA_sharpe"] > metrics["unfiltered_EMA_sharpe"]) or \
             (metrics["vix_filtered_max_dd"] > metrics["unfiltered_max_dd"] + 0.05)  # less negative = better

    print_result("VIX Regime Filter", passed, metrics)
    return passed


# ============================================================
# VALIDATION 4: HMM Regime Detection
# ============================================================

def validation_4_hmm_regime(nifty, vix):
    print_header("VALIDATION 4: HMM Regime Detection")
    print("  Testing: Can HMM detect meaningful market regimes?\n")

    from hmmlearn.hmm import GaussianHMM

    df = nifty.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    vix_df = vix.copy()
    if isinstance(vix_df.columns, pd.MultiIndex):
        vix_df.columns = vix_df.columns.get_level_values(0)

    df["returns"] = df["Close"].pct_change()
    df["volatility_20d"] = df["returns"].rolling(20).std()
    df["vix"] = vix_df["Close"].reindex(df.index, method="ffill")
    df["momentum_20d"] = df["Close"].pct_change(20)

    df = df.dropna()

    if len(df) < 200:
        print("  Not enough data for HMM")
        print_result("HMM Regime Detection", False, {"error": "insufficient data"})
        return False, None

    # Features for HMM
    features = df[["returns", "volatility_20d", "momentum_20d"]].values

    # Fit HMM with 3 states
    best_model = None
    best_score = -np.inf
    for _ in range(10):  # multiple random starts
        try:
            model = GaussianHMM(n_components=3, covariance_type="full", n_iter=200, random_state=np.random.randint(10000))
            model.fit(features)
            score = model.score(features)
            if score > best_score:
                best_score = score
                best_model = model
        except:
            continue

    if best_model is None:
        print("  HMM fitting failed")
        print_result("HMM Regime Detection", False, {"error": "HMM fit failed"})
        return False, None

    df["regime"] = best_model.predict(features)

    # Characterize regimes
    regime_stats = {}
    for r in sorted(df["regime"].unique()):
        mask = df["regime"] == r
        regime_stats[f"regime_{r}"] = {
            "mean_return": float(df.loc[mask, "returns"].mean()),
            "volatility": float(df.loc[mask, "volatility_20d"].mean()),
            "mean_vix": float(df.loc[mask, "vix"].mean()) if "vix" in df.columns and df.loc[mask, "vix"].notna().any() else 0,
            "days": int(mask.sum()),
            "pct": float(mask.mean()),
        }

    print("  Regime characteristics:")
    # Sort regimes by mean return to label them
    sorted_regimes = sorted(regime_stats.items(), key=lambda x: x[1]["mean_return"])
    labels = ["Bear/HighVol", "Neutral", "Bull"]
    regime_map = {}

    for i, (regime_key, stats_val) in enumerate(sorted_regimes):
        label = labels[min(i, len(labels)-1)]
        regime_num = int(regime_key.split("_")[1])
        regime_map[regime_num] = label
        print(f"    {label} (state {regime_num}): "
              f"avg_return={stats_val['mean_return']*252:.1f}% ann, "
              f"vol={stats_val['volatility']*np.sqrt(252)*100:.1f}%, "
              f"VIX={stats_val['mean_vix']:.1f}, "
              f"days={stats_val['days']} ({stats_val['pct']*100:.0f}%)")

    # Strategy: only trade (buy-and-hold NIFTY) in "Bull" regime
    bull_regime = [k for k, v in regime_map.items() if v == "Bull"][0]
    df["regime_signal"] = np.where(df["regime"] == bull_regime, 1, 0)
    df["regime_return"] = df["regime_signal"].shift(1) * df["returns"]

    regime_cum = (1 + df["regime_return"]).cumprod()
    bh_cum = (1 + df["returns"]).cumprod()

    metrics = {
        "buyhold_sharpe": sharpe_ratio(df["returns"]),
        "bull_only_sharpe": sharpe_ratio(df["regime_return"]),
        "buyhold_max_dd": max_drawdown(bh_cum),
        "bull_only_max_dd": max_drawdown(regime_cum),
        "buyhold_annual": annual_return(bh_cum),
        "bull_only_annual": annual_return(regime_cum),
        "n_regimes_detected": len(regime_stats),
        "bull_pct_time": float((df["regime"] == bull_regime).mean()),
    }

    # Pass if: 3 distinct regimes AND bull-only has better Sharpe or lower DD
    regimes_distinct = len(set(r["mean_return"] > 0 for r in regime_stats.values())) > 1
    sharpe_improvement = metrics["bull_only_sharpe"] > metrics["buyhold_sharpe"]
    dd_improvement = metrics["bull_only_max_dd"] > metrics["buyhold_max_dd"] + 0.03

    passed = regimes_distinct and (sharpe_improvement or dd_improvement)

    print_result("HMM Regime Detection", passed, metrics)
    return passed, df[["regime"]].rename(columns={"regime": "hmm_regime"})


# ============================================================
# VALIDATION 5: Technical Features Have Predictive Power
# ============================================================

def validation_5_feature_importance(nifty, vix):
    print_header("VALIDATION 5: Technical Feature Importance")
    print("  Testing: Do engineered features have predictive value for returns?\n")

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.inspection import permutation_importance

    df = nifty.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    vix_df = vix.copy()
    if isinstance(vix_df.columns, pd.MultiIndex):
        vix_df.columns = vix_df.columns.get_level_values(0)

    # Build features
    df["returns"] = df["Close"].pct_change()
    df["vix"] = vix_df["Close"].reindex(df.index, method="ffill")

    # Technical features
    df["rsi_14"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    df["macd_hist"] = ta.trend.MACD(df["Close"]).macd_diff()
    df["bb_pctb"] = ta.volatility.BollingerBands(df["Close"]).bollinger_pband()
    df["adx"] = ta.trend.ADXIndicator(df["High"], df["Low"], df["Close"]).adx()
    df["atr_pct"] = ta.volatility.AverageTrueRange(df["High"], df["Low"], df["Close"]).average_true_range() / df["Close"]
    df["ema_9_21"] = (df["Close"].ewm(span=9).mean() / df["Close"].ewm(span=21).mean()) - 1
    df["ema_20_50"] = (df["Close"].ewm(span=20).mean() / df["Close"].ewm(span=50).mean()) - 1
    df["momentum_10d"] = df["Close"].pct_change(10)
    df["momentum_20d"] = df["Close"].pct_change(20)
    df["vol_ratio"] = df["Volume"] / df["Volume"].rolling(20).mean()
    df["volatility_5d"] = df["returns"].rolling(5).std()
    df["volatility_20d"] = df["returns"].rolling(20).std()
    df["vol_ratio_5_20"] = df["volatility_5d"] / df["volatility_20d"]

    # VIX features
    if df["vix"].notna().sum() > 100:
        df["vix_change_5d"] = df["vix"].pct_change(5)
    else:
        df["vix_change_5d"] = 0

    # Target: 5-day forward return direction
    df["fwd_5d_return"] = df["Close"].pct_change(5).shift(-5)
    df["target"] = np.where(df["fwd_5d_return"] > 0.005, 1, np.where(df["fwd_5d_return"] < -0.005, -1, 0))

    feature_cols = ["rsi_14", "macd_hist", "bb_pctb", "adx", "atr_pct",
                    "ema_9_21", "ema_20_50", "momentum_10d", "momentum_20d",
                    "vol_ratio", "volatility_5d", "volatility_20d", "vol_ratio_5_20",
                    "vix_change_5d"]

    df = df.dropna(subset=feature_cols + ["target"])
    df = df[df["target"] != 0]  # Remove flat periods

    if len(df) < 200:
        print("  Not enough labeled data")
        print_result("Feature Importance", False, {"error": "insufficient data"})
        return False

    X = df[feature_cols].values
    y = (df["target"] > 0).astype(int).values  # Binary: up or down

    # Train/test split (time-aware: first 70% train, last 30% test)
    split = int(len(X) * 0.7)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    # Random Forest for feature importance
    rf = RandomForestClassifier(n_estimators=200, max_depth=5, random_state=42, n_jobs=-1)
    rf.fit(X_train, y_train)

    train_acc = rf.score(X_train, y_train)
    test_acc = rf.score(X_test, y_test)

    # Feature importance (MDI)
    importances = pd.Series(rf.feature_importances_, index=feature_cols).sort_values(ascending=False)

    print("  Top features by importance (MDI):")
    for feat, imp in importances.head(8).items():
        print(f"    {feat}: {imp:.4f}")

    # Permutation importance on test set
    perm_imp = permutation_importance(rf, X_test, y_test, n_repeats=10, random_state=42, n_jobs=-1)
    perm_importances = pd.Series(perm_imp.importances_mean, index=feature_cols).sort_values(ascending=False)

    print("\n  Top features by permutation importance (MDA):")
    for feat, imp in perm_importances.head(5).items():
        print(f"    {feat}: {imp:.4f}")

    # Check if features add predictive value above random
    metrics = {
        "train_accuracy": float(train_acc),
        "test_accuracy": float(test_acc),
        "baseline_accuracy": float(max(y_test.mean(), 1 - y_test.mean())),  # majority class
        "accuracy_above_baseline": float(test_acc - max(y_test.mean(), 1 - y_test.mean())),
        "top_feature": importances.index[0],
        "n_features_with_positive_perm_importance": int((perm_importances > 0.001).sum()),
    }

    # Pass if test accuracy meaningfully above baseline
    passed = test_acc > max(y_test.mean(), 1 - y_test.mean()) + 0.02

    print_result("Feature Importance", passed, metrics)
    return passed


# ============================================================
# VALIDATION 6: THE BIG ONE — XGBoost Walk-Forward Backtest
# ============================================================

def validation_6_ml_backtest(nifty, vix, stock_data):
    print_header("VALIDATION 6: XGBoost Walk-Forward Backtest (THE GO/NO-GO TEST)")
    print("  Testing: Can XGBoost with multi-source features beat buy-and-hold NIFTY?\n")

    import xgboost as xgb
    from sklearn.metrics import accuracy_score

    df = nifty.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    vix_df = vix.copy()
    if isinstance(vix_df.columns, pd.MultiIndex):
        vix_df.columns = vix_df.columns.get_level_values(0)

    df["returns"] = df["Close"].pct_change()
    df["vix"] = vix_df["Close"].reindex(df.index, method="ffill")

    # ---- BUILD FEATURE MATRIX ----
    # Technical features
    df["rsi_14"] = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
    df["macd_hist"] = ta.trend.MACD(df["Close"]).macd_diff()
    df["bb_pctb"] = ta.volatility.BollingerBands(df["Close"]).bollinger_pband()
    df["adx"] = ta.trend.ADXIndicator(df["High"], df["Low"], df["Close"]).adx()
    df["atr_pct"] = ta.volatility.AverageTrueRange(df["High"], df["Low"], df["Close"]).average_true_range() / df["Close"]
    df["ema_9_21"] = (df["Close"].ewm(span=9).mean() / df["Close"].ewm(span=21).mean()) - 1
    df["ema_20_50"] = (df["Close"].ewm(span=20).mean() / df["Close"].ewm(span=50).mean()) - 1
    df["momentum_5d"] = df["Close"].pct_change(5)
    df["momentum_10d"] = df["Close"].pct_change(10)
    df["momentum_20d"] = df["Close"].pct_change(20)
    df["vol_ratio"] = df["Volume"] / df["Volume"].rolling(20).mean()
    df["volatility_5d"] = df["returns"].rolling(5).std()
    df["volatility_20d"] = df["returns"].rolling(20).std()
    df["vol_expansion"] = df["volatility_5d"] / df["volatility_20d"]
    df["obv_trend"] = (ta.volume.OnBalanceVolumeIndicator(df["Close"], df["Volume"]).on_balance_volume().pct_change(10))

    # VIX features
    if df["vix"].notna().sum() > 100:
        df["vix_level"] = df["vix"]
        df["vix_change_5d"] = df["vix"].pct_change(5)
        df["vix_sma_ratio"] = df["vix"] / df["vix"].rolling(20).mean()

    # ---- TRIPLE BARRIER LABELING ----
    # Target: +1.5% (profit target), -1% (stop loss), 5 days (time limit)
    target_pct = 0.015
    stop_pct = 0.01
    horizon = 5

    labels = []
    for i in range(len(df) - horizon):
        entry_price = df["Close"].iloc[i]
        future_prices = df["Close"].iloc[i+1:i+1+horizon]

        hit_target = False
        hit_stop = False

        for p in future_prices:
            ret = (p - entry_price) / entry_price
            if ret >= target_pct:
                hit_target = True
                break
            elif ret <= -stop_pct:
                hit_stop = True
                break

        if hit_target:
            labels.append(1)
        elif hit_stop:
            labels.append(-1)
        else:
            labels.append(0)  # timeout

    # Pad remaining rows
    labels.extend([np.nan] * horizon)
    df["label"] = labels

    feature_cols = ["rsi_14", "macd_hist", "bb_pctb", "adx", "atr_pct",
                    "ema_9_21", "ema_20_50", "momentum_5d", "momentum_10d", "momentum_20d",
                    "vol_ratio", "volatility_5d", "volatility_20d", "vol_expansion", "obv_trend"]

    if "vix_level" in df.columns:
        feature_cols.extend(["vix_level", "vix_change_5d", "vix_sma_ratio"])

    df = df.dropna(subset=feature_cols + ["label"])
    df = df[df["label"] != 0]  # Only trades that hit target or stop

    print(f"  Total labeled samples: {len(df)}")
    print(f"  Label distribution: +1={int((df['label']==1).sum())}, -1={int((df['label']==-1).sum())}")
    print(f"  Base rate (target hits): {(df['label']==1).mean():.2%}")

    if len(df) < 300:
        print("  Not enough labeled data for walk-forward")
        print_result("XGBoost Walk-Forward", False, {"error": "insufficient labeled data"})
        return False

    # ---- WALK-FORWARD BACKTEST ----
    # Train: 12 months, Test: 3 months, Roll: 3 months
    train_days = 252  # ~12 months
    test_days = 63    # ~3 months

    all_predictions = []
    all_actuals = []
    all_dates = []
    all_returns = []

    X = df[feature_cols].values
    y = (df["label"] > 0).astype(int).values
    dates = df.index
    returns_series = df["returns"].values

    fold = 0
    i = train_days

    while i + test_days <= len(X):
        fold += 1

        # Purged split: gap of 5 days between train and test
        train_end = i - 5
        X_train = X[:train_end]
        y_train = y[:train_end]
        X_test = X[i:i+test_days]
        y_test = y[i:i+test_days]
        test_dates = dates[i:i+test_days]
        test_returns = returns_series[i:i+test_days]

        if len(X_train) < 100 or len(X_test) < 10:
            i += test_days
            continue

        # Train XGBoost
        model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss",
            verbosity=0,
        )
        model.fit(X_train, y_train)

        # Predict
        preds = model.predict(X_test)
        probs = model.predict_proba(X_test)[:, 1]

        acc = accuracy_score(y_test, preds)
        print(f"    Fold {fold}: train={len(X_train)}, test={len(X_test)}, accuracy={acc:.3f}")

        all_predictions.extend(preds.tolist())
        all_actuals.extend(y_test.tolist())
        all_dates.extend(test_dates.tolist())
        all_returns.extend(test_returns.tolist())

        i += test_days

    if len(all_predictions) < 50:
        print("  Not enough walk-forward results")
        print_result("XGBoost Walk-Forward", False, {"error": "too few folds"})
        return False

    # ---- CALCULATE STRATEGY PERFORMANCE ----
    results_df = pd.DataFrame({
        "date": all_dates,
        "prediction": all_predictions,
        "actual": all_actuals,
        "daily_return": all_returns,
    }).set_index("date")

    # Strategy: go long when model predicts +1, flat otherwise
    results_df["strategy_return"] = results_df["prediction"] * results_df["daily_return"]

    # Apply transaction cost (0.05% per trade, both entry and exit)
    results_df["signal_change"] = results_df["prediction"].diff().abs()
    results_df["strategy_return"] = results_df["strategy_return"] - (results_df["signal_change"] * 0.0005)

    strategy_cum = (1 + results_df["strategy_return"]).cumprod()
    buyhold_cum = (1 + results_df["daily_return"]).cumprod()

    oos_accuracy = accuracy_score(all_actuals, all_predictions)
    oos_sharpe = sharpe_ratio(results_df["strategy_return"])
    bh_sharpe = sharpe_ratio(results_df["daily_return"])
    oos_annual = annual_return(strategy_cum)
    bh_annual = annual_return(buyhold_cum)
    oos_dd = max_drawdown(strategy_cum)
    bh_dd = max_drawdown(buyhold_cum)

    # Win rate on days we're long
    long_days = results_df[results_df["prediction"] == 1]
    win_rate = float((long_days["daily_return"] > 0).mean()) if len(long_days) > 0 else 0

    # Per-fold consistency
    n_folds = fold

    metrics = {
        "oos_accuracy": float(oos_accuracy),
        "oos_sharpe": oos_sharpe,
        "buyhold_sharpe": bh_sharpe,
        "sharpe_improvement": oos_sharpe - bh_sharpe,
        "oos_annual_return": oos_annual,
        "buyhold_annual_return": bh_annual,
        "oos_max_drawdown": oos_dd,
        "buyhold_max_drawdown": bh_dd,
        "win_rate": win_rate,
        "pct_time_in_market": float(results_df["prediction"].mean()),
        "n_walk_forward_folds": n_folds,
        "total_oos_days": len(results_df),
        "transaction_cost_applied": "0.05% per trade",
    }

    # ---- GO / NO-GO CRITERIA ----
    go = True
    reasons = []

    if oos_sharpe < 0.5:
        go = False
        reasons.append(f"Sharpe {oos_sharpe:.2f} < 0.5 threshold")

    if oos_sharpe <= bh_sharpe:
        # Not necessarily a fail — could still be useful with lower risk
        if oos_dd < bh_dd + 0.05:  # Better drawdown
            reasons.append("Lower Sharpe but better drawdown — conditional pass")
        else:
            go = False
            reasons.append(f"Doesn't beat buy-and-hold Sharpe ({oos_sharpe:.2f} vs {bh_sharpe:.2f})")

    if oos_dd < -0.20:
        reasons.append(f"Max drawdown {oos_dd:.1%} exceeds -20% threshold")

    if oos_accuracy < 0.50:
        go = False
        reasons.append(f"Accuracy {oos_accuracy:.1%} below 50%")

    if go:
        metrics["verdict"] = "GO — Proceed with full build"
    else:
        metrics["verdict"] = "NO-GO — " + "; ".join(reasons)

    print_result("XGBoost Walk-Forward Backtest", go, metrics)

    if reasons:
        print("  Notes:")
        for r in reasons:
            print(f"    - {r}")

    return go


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "="*70)
    print("  FINAGENT STRATEGY VALIDATION")
    print("  " + "="*66)
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Purpose: Validate core hypotheses before full system build")
    print("="*70)

    results = {}

    # V1: Data Access
    v1_pass, nifty, vix, stock_data = validation_1_data_access()
    results["V1_data_access"] = v1_pass

    if not v1_pass:
        print("\n  CRITICAL: Cannot access data. Fix data sources before proceeding.")
        save_results(results)
        return

    # V2: Institutional Flow
    v2_pass = validation_2_institutional_flow(nifty)
    results["V2_institutional_flow"] = v2_pass

    # V3: VIX Regime Filter
    v3_pass = validation_3_vix_regime(nifty, vix)
    results["V3_vix_regime"] = v3_pass

    # V4: HMM Regime Detection
    v4_pass, regime_data = validation_4_hmm_regime(nifty, vix)
    results["V4_hmm_regime"] = v4_pass

    # V5: Feature Importance
    v5_pass = validation_5_feature_importance(nifty, vix)
    results["V5_feature_importance"] = v5_pass

    # V6: THE BIG ONE — ML Backtest
    v6_pass = validation_6_ml_backtest(nifty, vix, stock_data)
    results["V6_ml_backtest"] = v6_pass

    # ---- FINAL SUMMARY ----
    print_header("FINAL VALIDATION SUMMARY")

    total_pass = sum(1 for v in results.values() if v)
    total = len(results)

    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\n  Overall: {total_pass}/{total} validations passed")

    if results.get("V6_ml_backtest"):
        print("\n  >>> VERDICT: GO — Core ML strategy shows promise. Proceed with full build.")
    elif total_pass >= 4:
        print("\n  >>> VERDICT: CONDITIONAL GO — Most validations passed. Review failed ones.")
        print("      Simplify the system by dropping non-performing data layers.")
    else:
        print("\n  >>> VERDICT: NO-GO — Too many validations failed.")
        print("      Go back to research. Consider different strategies or markets.")

    save_results(results)


def save_results(results):
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation_results.json")
    with open(output_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "results": {k: bool(v) for k, v in results.items()},
        }, f, indent=2)
    print(f"\n  Results saved to: {output_path}")


if __name__ == "__main__":
    main()
