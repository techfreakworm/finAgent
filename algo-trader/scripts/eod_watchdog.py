#!/usr/bin/env python3
"""EOD watchdog — independent 15:25 / 15:50 monitor (ARCHITECTURE §6).

Runs independently of the paper-trading loop.  Designed for a cron job:

    # Check open positions at 15:25 IST
    25 15 * * 1-5  ubuntu /home/ubuntu/projects/algo-trader/.venv/bin/python \
        /home/ubuntu/projects/algo-trader/scripts/eod_watchdog.py --accounts paper

    # Verify report exists at 15:50 IST
    50 15 * * 1-5  ubuntu /home/ubuntu/projects/algo-trader/.venv/bin/python \
        /home/ubuntu/projects/algo-trader/scripts/eod_watchdog.py --accounts paper --check-report

Checks:
    1. check_open_positions  — queries paper store for open positions on
       session_date; prints "ALERT ..." and returns True if any found.
       (Lead agent wires the ALERT line to Telegram notify.)

    2. check_report_exists   — verifies that reports/daily/YYYY-MM-DD.{md,html}
       both exist; prints "ALERT ..." if missing.

Both functions are importable for tests.  The __main__ block calls them
according to CLI flags.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow running as a script from project root without pip-installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from algotrader.paper import store as _store

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPORT_DIR   = _PROJECT_ROOT / "reports" / "daily"


# ---------------------------------------------------------------------------
# Public check functions (importable by tests)
# ---------------------------------------------------------------------------

def check_open_positions(
    session_date: date,
    accounts: list[str],
    db_dir: Path | None = None,
) -> bool:
    """Check all *accounts* for open positions on *session_date*.

    Prints ``ALERT open_positions ...`` for each open position found.
    Returns True if any were found, False otherwise.
    """
    found_any = False
    for acct in accounts:
        open_pos = _store.get_open_positions(acct, session_date, db_dir)
        if open_pos:
            found_any = True
            for pos in open_pos:
                print(
                    f"ALERT open_positions account={acct} "
                    f"position_id={pos['position_id']} "
                    f"symbol={pos['symbol']} side={pos['side']} "
                    f"qty={pos['quantity']} entry={pos['entry_price']:.2f} "
                    f"entered={pos['entry_ts'].strftime('%H:%M IST')} "
                    f"session={session_date}"
                )
    if not found_any:
        print(f"OK no_open_positions session={session_date} accounts={accounts}")
    return found_any


def check_report_exists(
    session_date: date,
    report_dir: Path | None = None,
) -> bool:
    """Verify both .md and .html report files exist for *session_date*.

    Prints ``ALERT report_missing ...`` if either file is absent.
    Returns True if both files exist, False otherwise.
    """
    d = (report_dir or _REPORT_DIR).resolve()
    date_str = session_date.isoformat()
    md_path   = d / f"{date_str}.md"
    html_path = d / f"{date_str}.html"

    ok = True
    for path in (md_path, html_path):
        if not path.exists():
            print(
                f"ALERT report_missing session={session_date} path={path}"
            )
            ok = False

    if ok:
        print(
            f"OK report_exists session={session_date} "
            f"md={md_path.name} html={html_path.name}"
        )
    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EOD watchdog — open-position check and report verification."
    )
    parser.add_argument(
        "--accounts",
        nargs="+",
        default=["paper"],
        metavar="ACCOUNT",
        help="Account IDs to check (default: paper).",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Session date to check (default: today IST).",
    )
    parser.add_argument(
        "--check-report",
        action="store_true",
        help="Also verify that the daily report file exists (for 15:50 cron).",
    )
    parser.add_argument(
        "--db-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help="Override DB directory (testing / staging).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 0 if no alerts, 1 if any alert fired."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    args = _parse_args(argv)

    session_date: date = args.date or datetime.now(IST).date()
    alerted = False

    # Always check open positions
    if check_open_positions(session_date, args.accounts, args.db_dir):
        alerted = True

    # Optionally check report files (15:50 cron pass)
    if args.check_report:
        if not check_report_exists(session_date):
            alerted = True

    return 1 if alerted else 0


if __name__ == "__main__":
    raise SystemExit(main())
