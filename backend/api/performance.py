"""Performance / Analytics API — comprehensive trading KPIs, equity curve, monthly P&L."""

import logging
import math
from datetime import datetime
from collections import defaultdict
from fastapi import APIRouter

from backend.db.models import get_connection, get_trades, get_daily_pnl_history, get_strategy_summary, get_account
from backend.api.accounts_api import get_active_account_id

logger = logging.getLogger(__name__)
router = APIRouter(tags=["performance"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _compute_xirr(trades: list[dict], current_capital: float, starting_capital: float) -> float | None:
    """Compute XIRR from trade cash-flows using pyxirr."""
    try:
        from pyxirr import xirr as pyxirr_xirr
    except ImportError:
        return None

    dates = []
    amounts = []

    # Initial investment (outflow)
    if trades:
        sorted_trades = sorted(trades, key=lambda t: t.get("entry_date", ""))
        first_date = sorted_trades[0].get("entry_date", "")[:10]
        try:
            dates.append(datetime.strptime(first_date, "%Y-%m-%d").date())
        except (ValueError, TypeError):
            return None
        amounts.append(-starting_capital)

        # Each closed trade is a cash-flow event
        for t in sorted_trades:
            if t.get("exit_date") and t.get("pnl_net") is not None:
                try:
                    dt = datetime.strptime(t["exit_date"][:10], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue
                amounts.append(t["pnl_net"])
                dates.append(dt)

        # Terminal value (inflow — current portfolio value)
        today = datetime.now().date()
        if dates[-1] < today:
            dates.append(today)
            amounts.append(current_capital)
        else:
            # last trade date is today or future — just use it
            amounts[-1] += current_capital
    else:
        return None

    if len(dates) < 2:
        return None

    try:
        result = pyxirr_xirr(dates, amounts)
        if result is None or (isinstance(result, float) and (math.isnan(result) or math.isinf(result))):
            return None
        return round(result * 100, 2)  # as percentage
    except Exception:
        return None


def _compute_max_drawdown(history: list[dict]) -> float:
    """Max drawdown percentage from daily capital series (expects chronological order)."""
    if not history:
        return 0.0
    peak = 0.0
    max_dd = 0.0
    for rec in history:
        capital = rec.get("capital", 0)
        if capital > peak:
            peak = capital
        if peak > 0:
            dd = (peak - capital) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 2)


def _compute_sharpe(history: list[dict], risk_free_annual: float = 0.065) -> float | None:
    """Annualised Sharpe ratio from daily capital series."""
    if len(history) < 10:
        return None
    returns = []
    for i in range(1, len(history)):
        prev = history[i - 1].get("capital", 0)
        curr = history[i].get("capital", 0)
        if prev > 0:
            returns.append((curr - prev) / prev)
    if len(returns) < 5:
        return None
    mean_r = sum(returns) / len(returns)
    std_r = (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5
    if std_r == 0:
        return None
    daily_rf = (1 + risk_free_annual) ** (1 / 252) - 1
    sharpe = (mean_r - daily_rf) / std_r * math.sqrt(252)
    return round(sharpe, 2)


def _compute_avg_holding_days(trades: list[dict]) -> float | None:
    """Average holding period in calendar days for closed trades."""
    deltas = []
    for t in trades:
        if t.get("entry_date") and t.get("exit_date"):
            try:
                entry = datetime.strptime(t["entry_date"][:10], "%Y-%m-%d")
                exit_ = datetime.strptime(t["exit_date"][:10], "%Y-%m-%d")
                deltas.append((exit_ - entry).days)
            except (ValueError, TypeError):
                continue
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 1)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/performance")
def get_performance(account: str = None):
    """Comprehensive performance KPIs for an account."""
    acc_id = account or get_active_account_id()
    acc = get_account(acc_id) or {}

    starting_capital = acc.get("starting_capital", 500000)
    capital = acc.get("current_capital", starting_capital)

    # Fetch all closed trades (high limit)
    all_trades = get_trades(account_id=acc_id, limit=10000)
    closed_trades = [t for t in all_trades if t.get("exit_date")]

    # Fetch daily P&L history (chronological)
    history_raw = get_daily_pnl_history(account_id=acc_id, days=3650)
    history = list(reversed(history_raw))  # chronological

    # ---- Basic metrics ----
    total_trades = len(closed_trades)
    wins = [t for t in closed_trades if (t.get("pnl_net") or 0) > 0]
    losses = [t for t in closed_trades if (t.get("pnl_net") or 0) <= 0]

    total_pnl = sum(t.get("pnl_net", 0) or 0 for t in closed_trades)
    total_costs = sum(t.get("cost", 0) or 0 for t in closed_trades)
    total_slippage = sum(t.get("slippage", 0) or 0 for t in closed_trades)

    win_rate = _safe_div(len(wins), total_trades)
    return_pct = _safe_div(total_pnl, starting_capital) * 100

    gross_wins = sum(t.get("pnl_net", 0) or 0 for t in wins)
    gross_losses = abs(sum(t.get("pnl_net", 0) or 0 for t in losses))
    profit_factor = _safe_div(gross_wins, gross_losses) if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0)
    if profit_factor == float("inf"):
        profit_factor = 999.0  # cap for JSON serialization

    avg_win = _safe_div(gross_wins, len(wins)) if wins else 0
    avg_loss = _safe_div(gross_losses, len(losses)) if losses else 0

    pnl_values = [t.get("pnl_net", 0) or 0 for t in closed_trades]
    best_trade = max(pnl_values) if pnl_values else 0
    worst_trade = min(pnl_values) if pnl_values else 0

    # ---- Advanced metrics ----
    xirr = _compute_xirr(closed_trades, capital, starting_capital)
    max_drawdown_pct = _compute_max_drawdown(history)
    sharpe_ratio = _compute_sharpe(history)
    avg_holding_days = _compute_avg_holding_days(closed_trades)

    return {
        "account_id": acc_id,
        "capital": capital,
        "starting_capital": starting_capital,
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 2),
        "xirr": xirr,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "total_trades": total_trades,
        "total_costs": round(total_costs, 2),
        "total_slippage": round(total_slippage, 2),
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": sharpe_ratio,
        "avg_holding_days": avg_holding_days,
    }


@router.get("/performance/equity-curve")
def equity_curve(account: str = None):
    """Daily capital series for charting."""
    acc_id = account or get_active_account_id()
    history_raw = get_daily_pnl_history(account_id=acc_id, days=3650)
    history = list(reversed(history_raw))  # chronological
    return [{"date": r["date"], "capital": r["capital"]} for r in history]


@router.get("/performance/monthly")
def monthly_pnl(account: str = None):
    """Monthly aggregated P&L from closed trades."""
    acc_id = account or get_active_account_id()
    all_trades = get_trades(account_id=acc_id, limit=10000)
    closed_trades = [t for t in all_trades if t.get("exit_date")]

    months: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
    for t in closed_trades:
        try:
            month_key = t["exit_date"][:7]  # YYYY-MM
        except (TypeError, IndexError):
            continue
        pnl = t.get("pnl_net", 0) or 0
        months[month_key]["pnl"] += pnl
        months[month_key]["trades"] += 1
        if pnl > 0:
            months[month_key]["wins"] += 1

    result = []
    for month in sorted(months.keys()):
        m = months[month]
        result.append({
            "month": month,
            "pnl": round(m["pnl"], 2),
            "trades": m["trades"],
            "win_rate": round(_safe_div(m["wins"], m["trades"]), 4),
        })
    return result


@router.get("/performance/strategies")
def strategy_breakdown(account: str = None):
    """Per-strategy performance breakdown."""
    acc_id = account or get_active_account_id()
    summary = get_strategy_summary(account_id=acc_id)

    result = []
    for name, s in summary.items():
        total = s.get("total_trades", 0)
        winners = s.get("winners", 0)
        total_pnl = s.get("total_pnl", 0) or 0
        avg_pnl = s.get("avg_pnl", 0) or 0
        total_costs = s.get("total_costs", 0) or 0

        # Compute profit factor per strategy
        wins_pnl = 0.0
        losses_pnl = 0.0
        trades = get_trades(account_id=acc_id, strategy=name, limit=10000)
        for t in trades:
            pnl = t.get("pnl_net", 0) or 0
            if pnl > 0:
                wins_pnl += pnl
            else:
                losses_pnl += abs(pnl)
        pf = _safe_div(wins_pnl, losses_pnl) if losses_pnl > 0 else (999.0 if wins_pnl > 0 else 0)

        result.append({
            "strategy": name,
            "trades": total,
            "win_rate": round(_safe_div(winners, total), 4),
            "pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "profit_factor": round(pf, 2),
            "total_costs": round(total_costs, 2),
        })
    return result


@router.get("/performance/compare")
def compare_accounts(accounts: str = "paper,live"):
    """Side-by-side KPIs for multiple accounts."""
    account_ids = [a.strip() for a in accounts.split(",") if a.strip()]
    results = {}
    for acc_id in account_ids:
        acc = get_account(acc_id)
        if not acc:
            results[acc_id] = {"error": f"Account '{acc_id}' not found"}
            continue
        # Reuse the performance logic
        results[acc_id] = get_performance(account=acc_id)
        results[acc_id]["label"] = acc.get("label", acc_id)
    return results
