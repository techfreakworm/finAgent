#!/usr/bin/env python3
"""WS Shadow Validation harness.

Runs DhanLiveFeedV2 (WS) and RestPollingBarSource (REST) in PARALLEL on the
same universe as paper_trade.py.  NO bars reach any trade path — pure
observation and field-by-field comparison.

Output (written at 15:35 IST or on SIGTERM/stop):
    reports/ws_shadow/<YYYY-MM-DD>.json   — machine-readable per-instrument + totals
    reports/ws_shadow/<YYYY-MM-DD>.md     — human-readable summary + VERDICT

VERDICT: ws is CLEAN (promotable to --feed auto) iff ALL of:
  - Bar-count parity within 2 bars per instrument
  - >= 99 % of matched (symbol, minute) pairs have close within 1 tick
  - Zero parse anomalies (LTP <= 0, NaN, or ts misaligned)
  - OPEN phase (09:15-09:30) AND CLOSE phase (15:00-15:30) also pass the
    close-parity and count-parity thresholds independently.

PAPER/DATA ONLY.  No order-placement imports or calls.
"""
from __future__ import annotations

import json
import logging
import math
import os
import signal
import sys
import threading
import time as _time_mod
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("ws_shadow")

# ----------------------------------------------------------------- session times

_SESSION_START = time(9, 15, 0)
_REPORT_AT     = time(15, 35, 0)
_HARD_STOP     = time(15, 40, 0)

# Phase boundaries (bar ts_open time, IST)
_PHASE_OPEN_START  = time(9, 15, 0)
_PHASE_OPEN_END    = time(9, 30, 0)
_PHASE_MID_START   = time(9, 30, 0)
_PHASE_MID_END     = time(15, 0, 0)
_PHASE_CLOSE_START = time(15, 0, 0)
_PHASE_CLOSE_END   = time(15, 30, 0)

# Verdict thresholds
_COUNT_PARITY_MAX_DIFF  = 2      # bars per instrument
_CLOSE_MATCH_MIN_PCT    = 0.99   # 99 % of matched minutes must have close within 1 tick

# ----------------------------------------------------------------- helpers

def _ist_now() -> datetime:
    return datetime.now(IST)


def _phase(t: time) -> str:
    if _PHASE_OPEN_START <= t < _PHASE_OPEN_END:
        return "OPEN"
    if _PHASE_MID_START <= t < _PHASE_MID_END:
        return "MID"
    if _PHASE_CLOSE_START <= t <= _PHASE_CLOSE_END:
        return "CLOSE"
    return "OTHER"


def _is_anomaly(bar) -> bool:
    """Return True if bar values look like a parse anomaly."""
    try:
        if bar.close is None or (isinstance(bar.close, float) and math.isnan(bar.close)):
            return True
        if bar.close <= 0:
            return True
        if bar.open is None or (isinstance(bar.open, float) and math.isnan(bar.open)):
            return True
        return False
    except Exception:
        return True


# ================================================================= collector

class ShadowCollector:
    """Thread-safe store for ws and rest bars, keyed by (symbol, minute_str)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # ws_bars[(symbol, minute_str)] = list[Bar]
        self._ws_bars:   dict[tuple[str, str], list] = defaultdict(list)
        self._rest_bars: dict[tuple[str, str], list] = defaultdict(list)
        self._ws_anomalies:   list[dict] = []
        self._rest_anomalies: list[dict] = []

    # ----------------------------------------------------------------- callbacks

    def on_ws_bar(self, ev) -> None:
        bar = ev.bar
        if _is_anomaly(bar):
            with self._lock:
                self._ws_anomalies.append({
                    "source": "ws",
                    "symbol": bar.instrument.symbol,
                    "ts": bar.ts_open.isoformat(),
                    "close": bar.close,
                })
            log.warning("WS anomaly: %s @ %s close=%s", bar.instrument.symbol, bar.ts_open, bar.close)
            return
        key = (bar.instrument.symbol, bar.ts_open.strftime("%H:%M"))
        with self._lock:
            self._ws_bars[key].append(bar)

    def on_rest_bar(self, ev) -> None:
        bar = ev.bar
        if _is_anomaly(bar):
            with self._lock:
                self._rest_anomalies.append({
                    "source": "rest",
                    "symbol": bar.instrument.symbol,
                    "ts": bar.ts_open.isoformat(),
                    "close": bar.close,
                })
            log.warning("REST anomaly: %s @ %s close=%s", bar.instrument.symbol, bar.ts_open, bar.close)
            return
        key = (bar.instrument.symbol, bar.ts_open.strftime("%H:%M"))
        with self._lock:
            self._rest_bars[key].append(bar)

    # ----------------------------------------------------------------- snapshot

    def snapshot(self) -> tuple[dict, dict, list, list]:
        """Return (ws_bars, rest_bars, ws_anomalies, rest_anomalies) under lock."""
        with self._lock:
            return (
                dict(self._ws_bars),
                dict(self._rest_bars),
                list(self._ws_anomalies),
                list(self._rest_anomalies),
            )


# ================================================================= comparison

def _close_within_tick(ws_bar, rest_bar) -> bool:
    tick = ws_bar.instrument.tick_size
    if tick <= 0:
        tick = 0.05
    return abs(ws_bar.close - rest_bar.close) <= tick


def _ohlc_within_tick(ws_bar, rest_bar) -> bool:
    tick = ws_bar.instrument.tick_size
    if tick <= 0:
        tick = 0.05
    return (
        abs(ws_bar.open  - rest_bar.open)  <= tick
        and abs(ws_bar.high  - rest_bar.high)  <= tick
        and abs(ws_bar.low   - rest_bar.low)   <= tick
        and abs(ws_bar.close - rest_bar.close) <= tick
    )


def _ts_aligned(ws_bar, rest_bar) -> bool:
    """Bar-start timestamps within 60 s of each other."""
    diff = abs((ws_bar.ts_open - rest_bar.ts_open).total_seconds())
    return diff <= 60


def _volume_present(bar) -> bool:
    return bar.volume is not None and bar.volume >= 0


PhaseStats = dict  # typed inline below


def _empty_phase_stats() -> PhaseStats:
    return {
        "matched": 0,
        "close_match": 0,
        "close_mismatch": 0,
        "ohlc_match": 0,
        "ts_misaligned": 0,
        "missing_in_ws": 0,
        "missing_in_rest": 0,
    }


def compare(
    ws_bars:   dict[tuple[str, str], list],
    rest_bars: dict[tuple[str, str], list],
    ws_anomalies:   list[dict],
    rest_anomalies: list[dict],
    all_symbols:    list[str],
) -> dict:
    """Build comparison report dict from raw bar collections."""

    # All minute keys
    all_keys: set[tuple[str, str]] = set(ws_bars) | set(rest_bars)

    # Per-symbol: ws count, rest count
    ws_count:   dict[str, int] = defaultdict(int)
    rest_count: dict[str, int] = defaultdict(int)
    for (sym, _), bars in ws_bars.items():
        ws_count[sym] += len(bars)
    for (sym, _), bars in rest_bars.items():
        rest_count[sym] += len(bars)

    # Global phase stats
    phases: dict[str, PhaseStats] = {
        "OPEN":  _empty_phase_stats(),
        "MID":   _empty_phase_stats(),
        "CLOSE": _empty_phase_stats(),
        "OTHER": _empty_phase_stats(),
    }

    # Per-matched-minute detail
    close_mismatches: list[dict] = []

    for sym, minute_str in sorted(all_keys):
        try:
            t = datetime.strptime(minute_str, "%H:%M").time()
        except ValueError:
            t = time(0, 0)
        ph = _phase(t)
        ps = phases[ph]

        ws_list   = ws_bars.get((sym, minute_str), [])
        rest_list = rest_bars.get((sym, minute_str), [])

        if ws_list and not rest_list:
            ps["missing_in_rest"] += 1
            continue
        if rest_list and not ws_list:
            ps["missing_in_ws"] += 1
            continue

        # Both present — compare first bar from each source
        wb = ws_list[0]
        rb = rest_list[0]
        ps["matched"] += 1

        if not _ts_aligned(wb, rb):
            ps["ts_misaligned"] += 1

        if not _volume_present(wb):
            pass  # counted in anomalies

        if _close_within_tick(wb, rb):
            ps["close_match"] += 1
        else:
            ps["close_mismatch"] += 1
            close_mismatches.append({
                "symbol": sym,
                "minute": minute_str,
                "ws_close":   wb.close,
                "rest_close": rb.close,
                "diff":       round(wb.close - rb.close, 4),
                "tick":       wb.instrument.tick_size,
                "phase":      ph,
            })

        if _ohlc_within_tick(wb, rb):
            ps["ohlc_match"] += 1

    # Per-instrument count differences
    per_instrument: list[dict] = []
    for sym in sorted(set(list(ws_count) + list(rest_count) + all_symbols)):
        wc = ws_count.get(sym, 0)
        rc = rest_count.get(sym, 0)
        per_instrument.append({
            "symbol":     sym,
            "ws_bars":    wc,
            "rest_bars":  rc,
            "diff":       abs(wc - rc),
            "count_ok":   abs(wc - rc) <= _COUNT_PARITY_MAX_DIFF,
        })

    # Totals
    total_matched       = sum(p["matched"]        for p in phases.values())
    total_close_match   = sum(p["close_match"]    for p in phases.values())
    total_close_mismatch= sum(p["close_mismatch"] for p in phases.values())
    total_missing_ws    = sum(p["missing_in_ws"]  for p in phases.values())
    total_missing_rest  = sum(p["missing_in_rest"]for p in phases.values())
    total_ts_misaligned = sum(p["ts_misaligned"]  for p in phases.values())
    total_anomalies     = len(ws_anomalies) + len(rest_anomalies)

    close_pct = (total_close_match / total_matched) if total_matched > 0 else 1.0

    # --- count-parity check
    count_parity_ok = all(r["count_ok"] for r in per_instrument)

    # --- per-phase close parity (OPEN and CLOSE must pass independently)
    def _phase_close_ok(ph: str) -> bool:
        ps = phases[ph]
        m = ps["matched"]
        if m == 0:
            return True  # no data in phase — don't fail on it
        return (ps["close_match"] / m) >= _CLOSE_MATCH_MIN_PCT

    open_ok  = _phase_close_ok("OPEN")
    close_ok = _phase_close_ok("CLOSE")

    # --- VERDICT
    verdict_ok = (
        count_parity_ok
        and close_pct >= _CLOSE_MATCH_MIN_PCT
        and total_anomalies == 0
        and open_ok
        and close_ok
    )

    failures: list[str] = []
    if not count_parity_ok:
        bad = [r for r in per_instrument if not r["count_ok"]]
        failures.append(
            f"bar-count parity failed for {len(bad)} instrument(s): "
            + ", ".join(f"{r['symbol']} ws={r['ws_bars']} rest={r['rest_bars']}" for r in bad[:5])
        )
    if close_pct < _CLOSE_MATCH_MIN_PCT:
        failures.append(
            f"close-mismatch rate {1-close_pct:.1%} > 1% "
            f"({total_close_mismatch} of {total_matched} matched minutes)"
        )
    if total_anomalies > 0:
        failures.append(
            f"{total_anomalies} parse anomalies "
            f"(ws={len(ws_anomalies)}, rest={len(rest_anomalies)})"
        )
    if not open_ok:
        op = phases["OPEN"]
        rate = op["close_match"] / op["matched"] if op["matched"] else 0
        failures.append(f"OPEN phase close-match only {rate:.1%} < 99%")
    if not close_ok:
        cp = phases["CLOSE"]
        rate = cp["close_match"] / cp["matched"] if cp["matched"] else 0
        failures.append(f"CLOSE phase close-match only {rate:.1%} < 99%")

    return {
        "verdict":         "CLEAN" if verdict_ok else "NOT-CLEAN",
        "verdict_ok":      verdict_ok,
        "failures":        failures,
        "thresholds": {
            "count_parity_max_diff": _COUNT_PARITY_MAX_DIFF,
            "close_match_min_pct":   _CLOSE_MATCH_MIN_PCT,
        },
        "totals": {
            "matched_minutes":    total_matched,
            "close_match":        total_close_match,
            "close_mismatch":     total_close_mismatch,
            "close_match_pct":    round(close_pct, 6),
            "missing_in_ws":      total_missing_ws,
            "missing_in_rest":    total_missing_rest,
            "ts_misaligned":      total_ts_misaligned,
            "parse_anomalies":    total_anomalies,
        },
        "phases":           {k: dict(v) for k, v in phases.items()},
        "per_instrument":   per_instrument,
        "close_mismatches": close_mismatches[:50],  # cap at 50 for readability
        "anomalies": {
            "ws":   ws_anomalies,
            "rest": rest_anomalies,
        },
    }


# ================================================================= report writers

def _write_json(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str))
    log.info("JSON report written: %s", path)


def _write_md(report: dict, path: Path, session_date: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    v = report["verdict"]
    totals = report["totals"]
    phases = report["phases"]

    lines += [
        f"# WS Shadow Report — {session_date}",
        "",
        f"## VERDICT: {v}",
        "",
    ]
    if report["failures"]:
        lines.append("**Failures:**")
        for f in report["failures"]:
            lines.append(f"- {f}")
        lines.append("")
    else:
        lines.append("All thresholds passed — WS feed is promotable to `--feed auto`.")
        lines.append("")

    lines += [
        "## Totals",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Matched minutes | {totals['matched_minutes']} |",
        f"| Close match | {totals['close_match']} ({totals['close_match_pct']:.2%}) |",
        f"| Close mismatch | {totals['close_mismatch']} |",
        f"| Missing in WS | {totals['missing_in_ws']} |",
        f"| Missing in REST | {totals['missing_in_rest']} |",
        f"| TS misaligned | {totals['ts_misaligned']} |",
        f"| Parse anomalies | {totals['parse_anomalies']} |",
        "",
    ]

    lines += ["## Phase Breakdown", ""]
    for ph in ("OPEN", "MID", "CLOSE"):
        ps = phases.get(ph, {})
        m = ps.get("matched", 0)
        cm = ps.get("close_match", 0)
        pct = f"{cm/m:.2%}" if m else "n/a"
        lines.append(
            f"**{ph}**: matched={m}, close_match={cm} ({pct}), "
            f"close_mismatch={ps.get('close_mismatch',0)}, "
            f"missing_ws={ps.get('missing_in_ws',0)}, "
            f"missing_rest={ps.get('missing_in_rest',0)}"
        )
    lines.append("")

    lines += ["## Per-Instrument Bar Counts", ""]
    lines += ["| Symbol | WS | REST | Diff | OK |", "|--------|----|----|------|-----|"]
    for r in report["per_instrument"]:
        ok = "✓" if r["count_ok"] else "✗"
        lines.append(f"| {r['symbol']} | {r['ws_bars']} | {r['rest_bars']} | {r['diff']} | {ok} |")
    lines.append("")

    if report["close_mismatches"]:
        lines += ["## Close Mismatches (first 50)", ""]
        lines += ["| Symbol | Minute | WS Close | REST Close | Diff | Phase |",
                  "|--------|--------|----------|------------|------|-------|"]
        for mm in report["close_mismatches"]:
            lines.append(
                f"| {mm['symbol']} | {mm['minute']} | {mm['ws_close']} "
                f"| {mm['rest_close']} | {mm['diff']} | {mm['phase']} |"
            )
        lines.append("")

    if report["anomalies"]["ws"] or report["anomalies"]["rest"]:
        lines += ["## Parse Anomalies", ""]
        for a in report["anomalies"]["ws"] + report["anomalies"]["rest"]:
            lines.append(f"- [{a['source'].upper()}] {a['symbol']} @ {a['ts']} close={a['close']}")
        lines.append("")

    path.write_text("\n".join(lines) + "\n")
    log.info("Markdown report written: %s", path)


# ================================================================= main

def _build_all_subs():
    """Build the full universe: futures + equity subs (mirrors paper_trade.py)."""
    from scripts.paper_trade import _build_futures_subs, _build_equity_subs
    fut_subs, _ = _build_futures_subs()
    eq_subs = _build_equity_subs()
    return fut_subs + eq_subs


def main() -> None:
    session_date = _ist_now().date()
    log.info("ws_shadow: session_date=%s", session_date)

    # --- token
    try:
        from algotrader.data.token_manager import get_valid_token
        import os
        access_token = get_valid_token() or ""
        client_id = os.environ.get("DHAN_CLIENT_ID", "")
    except Exception:
        log.exception("token_manager failed")
        access_token = ""
        client_id = ""

    # --- build universe
    try:
        all_subs = _build_all_subs()
    except Exception:
        log.exception("Failed to build subscriptions — exiting")
        sys.exit(1)

    all_symbols = [instr.symbol for _, _, instr in all_subs]
    log.info("ws_shadow: %d instruments in universe", len(all_subs))

    # --- set up collector
    collector = ShadowCollector()

    # --- import feeds
    from algotrader.data.ws_feed_v2 import DhanLiveFeedV2
    from algotrader.data.rest_poll_feed import RestPollingBarSource

    ws_feed = DhanLiveFeedV2(
        subscriptions=all_subs,
        on_bar=collector.on_ws_bar,
        client_id=client_id,
        access_token=access_token,
    )
    rest_feed = RestPollingBarSource(
        subscriptions=all_subs,
        on_bar=collector.on_rest_bar,
        access_token=access_token,
    )

    # --- stop handler
    stop_evt = threading.Event()

    def _shutdown(signum, frame) -> None:
        log.info("ws_shadow: received signal %s — stopping", signum)
        stop_evt.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    # --- wait until session start (09:15)
    while not stop_evt.is_set():
        now = _ist_now()
        t   = now.time()
        if t >= _SESSION_START:
            break
        log.info("ws_shadow: pre-market (%s < 09:15) — waiting 10 s", t.strftime("%H:%M:%S"))
        stop_evt.wait(timeout=10)

    if stop_evt.is_set():
        log.info("ws_shadow: stopped before session start — no report written")
        return

    log.info("ws_shadow: starting both feeds")
    ws_feed.start()
    rest_feed.start()

    try:
        # --- main wait loop
        while not stop_evt.is_set():
            now = _ist_now()
            t   = now.time()
            if t >= _REPORT_AT:
                log.info("ws_shadow: 15:35 reached — writing report")
                break
            stop_evt.wait(timeout=10)
    finally:
        log.info("ws_shadow: stopping feeds")
        ws_feed.stop()
        rest_feed.stop()

    # --- build and write report
    ws_bars, rest_bars, ws_anoms, rest_anoms = collector.snapshot()
    log.info(
        "ws_shadow: snapshot: ws_keys=%d rest_keys=%d ws_anoms=%d rest_anoms=%d",
        len(ws_bars), len(rest_bars), len(ws_anoms), len(rest_anoms),
    )

    report = compare(ws_bars, rest_bars, ws_anoms, rest_anoms, all_symbols)

    reports_dir = _PROJECT_ROOT / "reports" / "ws_shadow"
    date_str = session_date.isoformat()
    _write_json(report, reports_dir / f"{date_str}.json")
    _write_md(report,  reports_dir / f"{date_str}.md", session_date)

    verdict = report["verdict"]
    log.info("ws_shadow VERDICT: %s", verdict)
    if report["failures"]:
        for f in report["failures"]:
            log.warning("  FAIL: %s", f)
    print(f"\nws_shadow VERDICT: {verdict}")
    if report["failures"]:
        for f in report["failures"]:
            print(f"  FAIL: {f}")
    else:
        print("  All thresholds passed — WS is promotable to --feed auto")


if __name__ == "__main__":
    main()
