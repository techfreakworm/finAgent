"""EOD P&L report generator (ARCHITECTURE §6).

generate_eod_report(session_date, accounts):
  - Reads closed trades, risk events from the paper store.
  - Produces:
      reports/daily/YYYY-MM-DD.md   (markdown)
      reports/daily/YYYY-MM-DD.html (self-contained, inline CSS, no JS)
  - Both formats include:
      * Per-account AND per-strategy net P&L (₹ and % of ₹5L capital)
      * Full trade table (entry/exit times, prices, reason, net P&L)
      * Cost + slippage totals
      * Risk events
      * Win/loss stats
      * Cumulative equity curve (inline SVG polyline, HTML only)
      * Paper-vs-backtest drift note placeholder

publish(html_path):
  - Calls /opt/claude-soma/scripts/soma-publish <html_path>
  - 3-attempt exponential backoff (1 s, 2 s, 4 s)
  - Returns URL string on success, None on all failures
  - Secrets are never logged.
"""
from __future__ import annotations

import logging
import math
import subprocess
import time as _time_mod
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from algotrader.paper import store as _store

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OUT_DIR = _PROJECT_ROOT / "reports" / "daily"
_CAPITAL = 500_000.0          # ₹5L reference capital (ARCHITECTURE §5)
_SOMA_PUBLISH = "/opt/claude-soma/scripts/soma-publish"
_PUBLISH_DELAYS = (1.0, 2.0)   # delays between 3 total attempts (attempt 1, sleep, attempt 2, sleep, attempt 3)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_eod_report(
    session_date: date,
    accounts: list[str],
    capital: float = _CAPITAL,
    db_dir: Path | None = None,
    out_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Generate markdown + HTML EOD report for *session_date*.

    Returns ``(md_path, html_path)`` — both are guaranteed to be written
    before this function returns (write-local-first rule, ARCHITECTURE §6).

    Args:
        session_date: The trading session date.
        accounts:     List of account IDs to include.
        capital:      Reference capital for % calculations (default ₹5L).
        db_dir:       Override DB directory (None = production default).
        out_dir:      Override output directory (None = reports/daily/).
    """
    out_dir = (out_dir or _DEFAULT_OUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    date_str = session_date.isoformat()
    md_path   = out_dir / f"{date_str}.md"
    html_path = out_dir / f"{date_str}.html"

    # Gather data per account
    all_accounts_data: list[dict] = []
    for acct in accounts:
        trades     = _store.get_trades(acct, session_date, db_dir)
        risk_evts  = _store.get_risk_events(acct, session_date, db_dir)
        pnl_hist   = _store.get_daily_pnl_history(acct, limit=90, db_dir=db_dir)
        all_accounts_data.append({
            "account_id":  acct,
            "trades":      trades,
            "risk_events": risk_evts,
            "pnl_history": pnl_hist,
        })

    md_path.write_text(_render_markdown(session_date, all_accounts_data, capital),
                       encoding="utf-8")
    html_path.write_text(_render_html(session_date, all_accounts_data, capital),
                         encoding="utf-8")

    log.info("EOD report written: %s | %s", md_path, html_path)
    return md_path, html_path


def publish(
    html_path: Path,
    backoff_delays: Sequence[float] = _PUBLISH_DELAYS,
) -> str | None:
    """Publish *html_path* via soma-publish with exponential-backoff retry.

    Tries up to ``len(backoff_delays) + 1`` times (one attempt + retries).
    Returns the URL string from stdout on the first success, or None if all
    attempts fail.  Secrets are never logged.
    """
    max_attempts = len(backoff_delays) + 1
    for attempt in range(1, max_attempts + 1):
        try:
            result = subprocess.run(
                [_SOMA_PUBLISH, str(html_path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            url = result.stdout.strip()
            log.info("soma-publish succeeded (attempt %d): %s", attempt, url or "<no url>")
            return url if url else None
        except subprocess.CalledProcessError as exc:
            log.warning(
                "soma-publish attempt %d/%d failed (exit %s) — stderr suppressed",
                attempt, max_attempts, exc.returncode,
            )
        except FileNotFoundError:
            log.warning("soma-publish not found at %s — skipping publish", _SOMA_PUBLISH)
            return None
        except subprocess.TimeoutExpired:
            log.warning("soma-publish attempt %d/%d timed out", attempt, max_attempts)
        except Exception as exc:  # noqa: BLE001
            log.warning("soma-publish attempt %d/%d error: %s", attempt, max_attempts,
                        type(exc).__name__)

        if attempt <= len(backoff_delays):
            _time_mod.sleep(backoff_delays[attempt - 1])

    log.error("soma-publish: all %d attempts failed for %s", max_attempts, html_path.name)
    return None


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _account_stats(trades: list[dict], capital: float) -> dict:
    """Compute per-account aggregate and per-strategy breakdown."""
    if not trades:
        return {
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "gross_pnl": 0.0,
            "costs_total": 0.0,
            "slippage_total": 0.0,
            "net_pnl": 0.0,
            "net_pnl_pct": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "by_strategy": {},
        }

    net_pnls  = [t["net_pnl"]      for t in trades]
    wins      = [p for p in net_pnls if p > 0]
    losses    = [p for p in net_pnls if p <= 0]

    # per-strategy rollup
    strat_trades: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        strat_trades[t["strategy_id"]].append(t)

    by_strategy: dict[str, dict] = {}
    for sid, st in strat_trades.items():
        s_net    = sum(t["net_pnl"] for t in st)
        s_gross  = sum(t["gross_pnl"] for t in st)
        s_costs  = sum(t["costs_total"] for t in st)
        s_wins   = sum(1 for t in st if t["net_pnl"] > 0)
        by_strategy[sid] = {
            "trade_count": len(st),
            "win_count":   s_wins,
            "loss_count":  len(st) - s_wins,
            "win_rate":    s_wins / len(st) if st else 0.0,
            "gross_pnl":   s_gross,
            "costs_total": s_costs,
            "net_pnl":     s_net,
            "net_pnl_pct": s_net / capital * 100,
        }

    total_net    = sum(net_pnls)
    total_gross  = sum(t["gross_pnl"]     for t in trades)
    total_costs  = sum(t["costs_total"]   for t in trades)
    total_slip   = sum(t["slippage_paid"] for t in trades)

    return {
        "trade_count":     len(trades),
        "win_count":       len(wins),
        "loss_count":      len(losses),
        "win_rate":        len(wins) / len(trades) if trades else 0.0,
        "gross_pnl":       total_gross,
        "costs_total":     total_costs,
        "slippage_total":  total_slip,
        "net_pnl":         total_net,
        "net_pnl_pct":     total_net / capital * 100,
        "avg_win":         sum(wins)   / len(wins)   if wins   else 0.0,
        "avg_loss":        sum(losses) / len(losses) if losses else 0.0,
        "by_strategy":     by_strategy,
    }


# ---------------------------------------------------------------------------
# SVG equity curve
# ---------------------------------------------------------------------------

def _equity_svg(
    pnl_history: list[tuple[date, float]],
    capital: float = _CAPITAL,
    width: int = 580,
    height: int = 160,
    pad: int = 24,
) -> str:
    """Return an inline SVG polyline of the cumulative equity curve."""
    if not pnl_history:
        return (
            f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            f'<text x="{width//2}" y="{height//2}" text-anchor="middle" '
            f'fill="#888" font-family="monospace" font-size="12">'
            f'No history yet</text></svg>'
        )

    # Build cumulative equity series (starting from capital)
    cumul: list[float] = [capital]
    for _, pnl in pnl_history:
        cumul.append(cumul[-1] + pnl)

    n = len(cumul)
    w_inner = width  - 2 * pad
    h_inner = height - 2 * pad

    eq_min = min(cumul)
    eq_max = max(cumul)
    eq_range = eq_max - eq_min if eq_max != eq_min else 1.0

    def _x(i: int) -> float:
        return pad + (i / max(n - 1, 1)) * w_inner

    def _y(v: float) -> float:
        return pad + (1.0 - (v - eq_min) / eq_range) * h_inner

    points = " ".join(f"{_x(i):.1f},{_y(v):.1f}" for i, v in enumerate(cumul))

    # Colour: green if ending above capital, red if below
    stroke = "#22c55e" if cumul[-1] >= capital else "#ef4444"

    # Zero line (capital reference)
    cap_y = _y(capital)
    zero_line = (
        f'<line x1="{pad}" y1="{cap_y:.1f}" x2="{pad + w_inner}" y2="{cap_y:.1f}" '
        f'stroke="#94a3b8" stroke-width="0.8" stroke-dasharray="3,3"/>'
    )

    return (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;max-width:{width}px;height:{height}px">'
        f'{zero_line}'
        f'<polyline points="{points}" fill="none" stroke="{stroke}" '
        f'stroke-width="1.8" stroke-linejoin="round"/>'
        f'</svg>'
    )


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def _render_markdown(
    session_date: date,
    all_data: list[dict],
    capital: float,
) -> str:
    lines: list[str] = []
    lines.append(f"# EOD P&L Report — {session_date.isoformat()}")
    lines.append("")
    lines.append(
        f"*Capital reference: ₹{capital:,.0f}  |  Generated: "
        f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}*"
    )
    lines.append("")

    # Grand total
    all_trades = [t for d in all_data for t in d["trades"]]
    grand_net  = sum(t["net_pnl"] for t in all_trades)
    grand_pct  = grand_net / capital * 100
    sign       = "+" if grand_net >= 0 else ""
    lines.append(f"## Grand Total: {sign}₹{grand_net:,.0f} ({sign}{grand_pct:.2f}%)")
    lines.append("")

    for data in all_data:
        acct   = data["account_id"]
        trades = data["trades"]
        stats  = _account_stats(trades, capital)
        lines.append(f"---")
        lines.append(f"## Account: {acct}")
        lines.append("")

        net_sign = "+" if stats["net_pnl"] >= 0 else ""
        lines.append(
            f"**Net P&L:** {net_sign}₹{stats['net_pnl']:,.0f} "
            f"({net_sign}{stats['net_pnl_pct']:.2f}%)  "
            f"| Gross: ₹{stats['gross_pnl']:,.0f}  "
            f"| Costs: ₹{stats['costs_total']:,.0f}  "
            f"| Slippage: ₹{stats['slippage_total']:,.0f}"
        )
        lines.append("")
        lines.append(
            f"**Trades:** {stats['trade_count']}  "
            f"| Wins: {stats['win_count']}  "
            f"| Losses: {stats['loss_count']}  "
            f"| Win rate: {stats['win_rate']*100:.0f}%  "
            f"| Avg win: ₹{stats['avg_win']:,.0f}  "
            f"| Avg loss: ₹{stats['avg_loss']:,.0f}"
        )
        lines.append("")

        # Per-strategy summary
        if stats["by_strategy"]:
            lines.append("### Strategy Breakdown")
            lines.append("")
            lines.append("| Strategy | Trades | Win% | Net P&L | % Capital |")
            lines.append("|----------|--------|------|---------|-----------|")
            for sid, s in sorted(stats["by_strategy"].items()):
                s_sign = "+" if s["net_pnl"] >= 0 else ""
                lines.append(
                    f"| {sid} | {s['trade_count']} | {s['win_rate']*100:.0f}% | "
                    f"{s_sign}₹{s['net_pnl']:,.0f} | {s_sign}{s['net_pnl_pct']:.2f}% |"
                )
            lines.append("")

        # Trade table
        if trades:
            lines.append("### Trade Log")
            lines.append("")
            lines.append(
                "| # | Strategy | Symbol | Side | Qty | Entry Time | Entry ₹ | "
                "Exit Time | Exit ₹ | Reason | Net ₹ |"
            )
            lines.append(
                "|---|----------|--------|------|-----|------------|---------|"
                "-----------|--------|--------|-------|"
            )
            for i, t in enumerate(trades, 1):
                entry_t = t["entry_ts"].strftime("%H:%M")
                exit_t  = t["exit_ts"].strftime("%H:%M")
                reason  = (t["exit_reason"] or "").replace("_", " ")
                net_sign = "+" if t["net_pnl"] >= 0 else ""
                lines.append(
                    f"| {i} | {t['strategy_id']} | {t['symbol']} | {t['side']} | "
                    f"{t['quantity']} | {entry_t} | ₹{t['entry_price']:,.2f} | "
                    f"{exit_t} | ₹{t['exit_price']:,.2f} | {reason} | "
                    f"{net_sign}₹{t['net_pnl']:,.0f} |"
                )
            lines.append("")

        # Risk events
        if data["risk_events"]:
            lines.append("### Risk Events")
            lines.append("")
            for ev in data["risk_events"]:
                lines.append(f"- {ev}")
            lines.append("")
        else:
            lines.append("### Risk Events")
            lines.append("")
            lines.append("*None.*")
            lines.append("")

    # Drift placeholder
    lines.append("---")
    lines.append("## Paper vs Backtest Drift")
    lines.append("")
    lines.append(
        "*Drift analysis pending — will compare live fills vs backtest "
        "simulated fills once sufficient paper-trading history is available.*"
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

_HTML_CSS = """
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         font-size: 14px; line-height: 1.5; color: #1e293b;
         background: #f8fafc; margin: 0; padding: 16px; }
  .container { max-width: 1100px; margin: 0 auto; background: #fff;
               border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.08);
               padding: 24px 32px; }
  h1 { font-size: 20px; margin: 0 0 4px; color: #0f172a; }
  h2 { font-size: 16px; margin: 24px 0 8px; border-bottom: 1px solid #e2e8f0;
       padding-bottom: 4px; color: #0f172a; }
  h3 { font-size: 14px; margin: 16px 0 6px; color: #334155; }
  .subtitle { color: #64748b; font-size: 12px; margin-bottom: 20px; }
  .grand { font-size: 22px; font-weight: 700; margin: 8px 0 20px; }
  .pos  { color: #16a34a; } .neg { color: #dc2626; }
  .stat-row { display: flex; flex-wrap: wrap; gap: 20px; margin: 8px 0 16px; }
  .stat { background: #f1f5f9; border-radius: 6px; padding: 8px 14px; }
  .stat .label { font-size: 11px; color: #64748b; text-transform: uppercase;
                 letter-spacing: .5px; }
  .stat .value { font-size: 15px; font-weight: 600; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px;
          margin: 8px 0 16px; }
  th { background: #f1f5f9; text-align: left; padding: 6px 10px;
       font-size: 11px; text-transform: uppercase; letter-spacing: .4px;
       color: #475569; font-weight: 600; border-bottom: 2px solid #e2e8f0; }
  td { padding: 5px 10px; border-bottom: 1px solid #f1f5f9; }
  tr:hover td { background: #f8fafc; }
  .risk-list { background: #fef2f2; border-left: 3px solid #dc2626;
               padding: 8px 14px; border-radius: 0 6px 6px 0;
               margin: 8px 0; font-size: 13px; }
  .risk-list li { margin: 3px 0; }
  .no-risk { color: #64748b; font-style: italic; font-size: 13px; }
  .drift-note { background: #fefce8; border-left: 3px solid #ca8a04;
                padding: 10px 14px; border-radius: 0 6px 6px 0;
                color: #713f12; font-size: 13px; margin: 8px 0; }
  .equity-wrap { margin: 12px 0; }
  hr { border: none; border-top: 1px solid #e2e8f0; margin: 24px 0 0; }
  .section-sep { margin: 0 0 12px; }
"""


def _pnl_span(value: float) -> str:
    css = "pos" if value >= 0 else "neg"
    sign = "+" if value >= 0 else ""
    return f'<span class="{css}">{sign}₹{value:,.0f}</span>'


def _pnl_pct_span(value: float) -> str:
    css = "pos" if value >= 0 else "neg"
    sign = "+" if value >= 0 else ""
    return f'<span class="{css}">{sign}{value:.2f}%</span>'


def _render_html(
    session_date: date,
    all_data: list[dict],
    capital: float,
) -> str:
    parts: list[str] = []
    p = parts.append

    p("<!DOCTYPE html>")
    p('<html lang="en"><head><meta charset="utf-8">')
    p(f'<title>EOD Report {session_date.isoformat()}</title>')
    p(f"<style>{_HTML_CSS}</style></head><body>")
    p('<div class="container">')

    # Header
    p(f'<h1>EOD P&L Report — {session_date.isoformat()}</h1>')
    p(f'<div class="subtitle">Capital reference: ₹{capital:,.0f} &nbsp;|&nbsp; '
      f'Generated: {datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")}</div>')

    # Grand total
    all_trades = [t for d in all_data for t in d["trades"]]
    grand_net  = sum(t["net_pnl"] for t in all_trades)
    grand_pct  = grand_net / capital * 100
    p(f'<div class="grand">Grand Total: {_pnl_span(grand_net)} '
      f'&nbsp;({_pnl_pct_span(grand_pct)})</div>')

    for data in all_data:
        acct   = data["account_id"]
        trades = data["trades"]
        stats  = _account_stats(trades, capital)
        pnl_h  = data["pnl_history"]

        p('<hr class="section-sep">')
        p(f'<h2>Account: {acct}</h2>')

        # Key stats row
        p('<div class="stat-row">')
        for label, val in [
            ("Net P&L",   _pnl_span(stats["net_pnl"])),
            ("% Capital", _pnl_pct_span(stats["net_pnl_pct"])),
            ("Gross P&L", f"₹{stats['gross_pnl']:,.0f}"),
            ("Costs",     f"₹{stats['costs_total']:,.0f}"),
            ("Slippage",  f"₹{stats['slippage_total']:,.0f}"),
        ]:
            p(f'<div class="stat"><div class="label">{label}</div>'
              f'<div class="value">{val}</div></div>')

        for label, val in [
            ("Trades",    str(stats["trade_count"])),
            ("Wins",      str(stats["win_count"])),
            ("Losses",    str(stats["loss_count"])),
            ("Win Rate",  f"{stats['win_rate']*100:.0f}%"),
            ("Avg Win",   f"₹{stats['avg_win']:,.0f}"),
            ("Avg Loss",  f"₹{stats['avg_loss']:,.0f}"),
        ]:
            p(f'<div class="stat"><div class="label">{label}</div>'
              f'<div class="value">{val}</div></div>')
        p('</div>')  # stat-row

        # Per-strategy table
        if stats["by_strategy"]:
            p('<h3>Strategy Breakdown</h3>')
            p('<table><thead><tr>')
            for h in ["Strategy", "Trades", "Wins", "Win%", "Net P&L", "% Capital"]:
                p(f'<th>{h}</th>')
            p('</tr></thead><tbody>')
            for sid, s in sorted(stats["by_strategy"].items()):
                p(f'<tr><td>{sid}</td><td>{s["trade_count"]}</td>'
                  f'<td>{s["win_count"]}</td>'
                  f'<td>{s["win_rate"]*100:.0f}%</td>'
                  f'<td>{_pnl_span(s["net_pnl"])}</td>'
                  f'<td>{_pnl_pct_span(s["net_pnl_pct"])}</td></tr>')
            p('</tbody></table>')

        # Trade table
        if trades:
            p('<h3>Trade Log</h3>')
            p('<table><thead><tr>')
            for h in ["#", "Strategy", "Symbol", "Side", "Qty",
                      "Entry Time", "Entry ₹", "Exit Time", "Exit ₹",
                      "Reason", "Net ₹"]:
                p(f'<th>{h}</th>')
            p('</tr></thead><tbody>')
            for i, t in enumerate(trades, 1):
                entry_t  = t["entry_ts"].strftime("%H:%M")
                exit_t   = t["exit_ts"].strftime("%H:%M")
                reason   = (t["exit_reason"] or "").replace("_", " ")
                net_span = _pnl_span(t["net_pnl"])
                p(
                    f'<tr><td>{i}</td><td>{t["strategy_id"]}</td>'
                    f'<td>{t["symbol"]}</td><td>{t["side"]}</td>'
                    f'<td>{t["quantity"]}</td>'
                    f'<td>{entry_t}</td><td>₹{t["entry_price"]:,.2f}</td>'
                    f'<td>{exit_t}</td><td>₹{t["exit_price"]:,.2f}</td>'
                    f'<td>{reason}</td><td>{net_span}</td></tr>'
                )
            p('</tbody></table>')

        # Equity curve
        p('<div class="equity-wrap">')
        p('<h3>Cumulative Equity Curve</h3>')
        p(_equity_svg(pnl_h, capital=capital))
        p('</div>')

        # Risk events
        p('<h3>Risk Events</h3>')
        if data["risk_events"]:
            p('<ul class="risk-list">')
            for ev in data["risk_events"]:
                p(f'<li>{ev}</li>')
            p('</ul>')
        else:
            p('<div class="no-risk">None.</div>')

    # Paper vs backtest drift placeholder
    p('<hr>')
    p('<h2>Paper vs Backtest Drift</h2>')
    p('<div class="drift-note">'
      'Drift analysis pending — will compare live fills vs backtest simulated '
      'fills once sufficient paper-trading history is available. '
      'Calibration applied prospectively only (ARCHITECTURE §3.4 freeze rule).'
      '</div>')

    p('</div></body></html>')

    return "\n".join(parts)
