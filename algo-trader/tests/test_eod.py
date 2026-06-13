"""Tests for EOD report pipeline and EOD watchdog (ARCHITECTURE §6).

Coverage:
  - generate_eod_report: both .md and .html rendered with correct totals
  - HTML: self-contained (no <script> tags), inline CSS present
  - publish(): retries on failure; returns None gracefully when all fail
  - eod_watchdog: check_open_positions detects a planted open position
  - eod_watchdog: check_report_exists detects missing files
  - paper store: round-trip write/read of trades, open positions, risk events
"""
from __future__ import annotations

import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Helpers to seed the fake store
# ---------------------------------------------------------------------------

def _make_ts(h: int, m: int, d: date = date(2026, 6, 11)) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, 0, tzinfo=IST)


def _seed_fake_store(tmp_path: Path) -> tuple[date, list[str]]:
    """Insert 2 accounts × 3 trades each into a tmp DB.

    Account 'alpha':
      trade_1: orb / NIFTY   / BUY  / net +500
      trade_2: orb / NIFTY   / SELL / net -100
      trade_3: vwap / RELIANCE/ BUY  / net +200

    Account 'beta':
      trade_4: orb  / BANKNIFTY / BUY / net +300
      trade_5: vwap / ITC       / BUY / net -50
      trade_6: trend/ INFY      / BUY / net +150

    Grand total net: +1000
    """
    from algotrader.paper import store

    session_date = date(2026, 6, 11)
    accounts = ["alpha", "beta"]

    # Account alpha
    store.init_db("alpha", db_dir=tmp_path)
    trades_alpha = [
        {
            "position_id":   "alpha_001",
            "strategy_id":   "orb",
            "symbol":        "NIFTY",
            "side":          "BUY",
            "quantity":      1,
            "entry_price":   24000.0,
            "entry_ts":      _make_ts(9, 30),
            "exit_price":    24600.0,
            "exit_ts":       _make_ts(10, 15),
            "exit_reason":   "target",
            "gross_pnl":     550.0,
            "net_pnl":       500.0,
            "costs_total":   30.0,
            "slippage_paid": 20.0,
        },
        {
            "position_id":   "alpha_002",
            "strategy_id":   "orb",
            "symbol":        "NIFTY",
            "side":          "SELL",
            "quantity":      1,
            "entry_price":   24500.0,
            "entry_ts":      _make_ts(11, 0),
            "exit_price":    24600.0,
            "exit_ts":       _make_ts(11, 45),
            "exit_reason":   "stop",
            "gross_pnl":     -70.0,
            "net_pnl":       -100.0,
            "costs_total":   30.0,
            "slippage_paid": 20.0,
        },
        {
            "position_id":   "alpha_003",
            "strategy_id":   "vwap",
            "symbol":        "RELIANCE",
            "side":          "BUY",
            "quantity":      10,
            "entry_price":   2900.0,
            "entry_ts":      _make_ts(13, 0),
            "exit_price":    2925.0,
            "exit_ts":       _make_ts(14, 30),
            "exit_reason":   "strategy_exit",
            "gross_pnl":     230.0,
            "net_pnl":       200.0,
            "costs_total":   30.0,
            "slippage_paid": 20.0,
        },
    ]
    for t in trades_alpha:
        store.record_trade("alpha", session_date, t, db_dir=tmp_path)

    # Account beta
    store.init_db("beta", db_dir=tmp_path)
    trades_beta = [
        {
            "position_id":   "beta_001",
            "strategy_id":   "orb",
            "symbol":        "BANKNIFTY",
            "side":          "BUY",
            "quantity":      1,
            "entry_price":   51000.0,
            "entry_ts":      _make_ts(9, 45),
            "exit_price":    51400.0,
            "exit_ts":       _make_ts(10, 30),
            "exit_reason":   "target",
            "gross_pnl":     370.0,
            "net_pnl":       300.0,
            "costs_total":   40.0,
            "slippage_paid": 30.0,
        },
        {
            "position_id":   "beta_002",
            "strategy_id":   "vwap",
            "symbol":        "ITC",
            "side":          "BUY",
            "quantity":      50,
            "entry_price":   480.0,
            "entry_ts":      _make_ts(12, 0),
            "exit_price":    479.0,
            "exit_ts":       _make_ts(12, 45),
            "exit_reason":   "time_stop",
            "gross_pnl":     -20.0,
            "net_pnl":       -50.0,
            "costs_total":   30.0,
            "slippage_paid": 20.0,
        },
        {
            "position_id":   "beta_003",
            "strategy_id":   "trend",
            "symbol":        "INFY",
            "side":          "BUY",
            "quantity":      5,
            "entry_price":   1800.0,
            "entry_ts":      _make_ts(14, 0),
            "exit_price":    1832.0,
            "exit_ts":       _make_ts(15, 10),
            "exit_reason":   "strategy_exit",
            "gross_pnl":     180.0,
            "net_pnl":       150.0,
            "costs_total":   30.0,
            "slippage_paid": 20.0,
        },
    ]
    for t in trades_beta:
        store.record_trade("beta", session_date, t, db_dir=tmp_path)

    # Also add a risk event for alpha
    store.record_risk_event(
        "alpha", session_date,
        "15:19:30+05:30: daily_loss_breaker tripped (demo event)",
        db_dir=tmp_path,
    )

    return session_date, accounts


# ---------------------------------------------------------------------------
# Store round-trip tests
# ---------------------------------------------------------------------------

class TestPaperStore:
    def test_init_db_idempotent(self, tmp_path):
        from algotrader.paper import store
        # Calling init_db twice should not raise
        store.init_db("test", db_dir=tmp_path)
        store.init_db("test", db_dir=tmp_path)
        db = tmp_path / "test.db"
        assert db.exists()

    def test_record_and_get_trades(self, tmp_path):
        from algotrader.paper import store
        sd = date(2026, 6, 11)
        t = {
            "position_id": "p1", "strategy_id": "orb", "symbol": "NIFTY",
            "side": "BUY", "quantity": 1,
            "entry_price": 24000.0, "entry_ts": _make_ts(9, 30),
            "exit_price": 24500.0,  "exit_ts":  _make_ts(10, 0),
            "exit_reason": "target",
            "gross_pnl": 530.0, "net_pnl": 500.0,
            "costs_total": 30.0, "slippage_paid": 20.0,
        }
        store.record_trade("test", sd, t, db_dir=tmp_path)
        rows = store.get_trades("test", sd, db_dir=tmp_path)
        assert len(rows) == 1
        r = rows[0]
        assert r["symbol"] == "NIFTY"
        assert r["net_pnl"] == pytest.approx(500.0)
        # Datetimes must be IST-aware
        assert r["entry_ts"].tzinfo is not None
        assert r["entry_ts"].hour == 9
        assert r["entry_ts"].minute == 30

    def test_missing_db_returns_empty(self, tmp_path):
        from algotrader.paper import store
        sd = date(2026, 6, 11)
        assert store.get_trades("nonexistent", sd, db_dir=tmp_path) == []
        assert store.get_open_positions("nonexistent", sd, db_dir=tmp_path) == []
        assert store.get_risk_events("nonexistent", sd, db_dir=tmp_path) == []
        assert store.get_daily_pnl_history("nonexistent", db_dir=tmp_path) == []

    def test_open_positions_round_trip(self, tmp_path):
        from algotrader.paper import store
        sd = date(2026, 6, 11)
        pos = {
            "position_id": "p_open", "strategy_id": "orb",
            "symbol": "NIFTY", "side": "BUY", "quantity": 1,
            "entry_price": 24000.0, "entry_ts": _make_ts(9, 30),
            "stop_price": 23800.0, "target_price": 24500.0,
        }
        store.record_open_position("test", sd, pos, db_dir=tmp_path)
        rows = store.get_open_positions("test", sd, db_dir=tmp_path)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "NIFTY"
        assert rows[0]["entry_ts"].tzinfo is not None

        # Close it
        store.update_position_exit("test", "p_open", db_dir=tmp_path)
        rows2 = store.get_open_positions("test", sd, db_dir=tmp_path)
        assert rows2 == []

    def test_risk_events_round_trip(self, tmp_path):
        from algotrader.paper import store
        sd = date(2026, 6, 11)
        store.record_risk_event("test", sd, "breaker_tripped", db_dir=tmp_path)
        store.record_risk_event("test", sd, "floor_breached",  db_dir=tmp_path)
        evts = store.get_risk_events("test", sd, db_dir=tmp_path)
        assert evts == ["breaker_tripped", "floor_breached"]

    def test_daily_pnl_history(self, tmp_path):
        from algotrader.paper import store
        d1 = date(2026, 6, 10)
        d2 = date(2026, 6, 11)
        # Two trades on d1, one on d2
        for d, pid, net in [(d1, "t1", 500.0), (d1, "t2", 300.0), (d2, "t3", -100.0)]:
            store.record_trade("hist", d, {
                "position_id": pid, "strategy_id": "orb", "symbol": "NIFTY",
                "side": "BUY", "quantity": 1,
                "entry_price": 24000.0, "entry_ts": _make_ts(9, 30, d),
                "exit_price": 24500.0,  "exit_ts":  _make_ts(10, 0, d),
                "exit_reason": "target",
                "gross_pnl": net + 50.0, "net_pnl": net,
                "costs_total": 50.0, "slippage_paid": 20.0,
            }, db_dir=tmp_path)
        history = store.get_daily_pnl_history("hist", db_dir=tmp_path)
        assert len(history) == 2
        d1_hist = next(x for x in history if x[0] == d1)
        d2_hist = next(x for x in history if x[0] == d2)
        assert d1_hist[1] == pytest.approx(800.0)   # 500 + 300
        assert d2_hist[1] == pytest.approx(-100.0)


# ---------------------------------------------------------------------------
# EOD report tests
# ---------------------------------------------------------------------------

class TestEodReport:
    def test_both_files_created(self, tmp_path):
        from algotrader.reports.eod import generate_eod_report
        session_date, accounts = _seed_fake_store(tmp_path)
        out_dir = tmp_path / "reports"
        md_path, html_path = generate_eod_report(
            session_date, accounts,
            db_dir=tmp_path, out_dir=out_dir,
        )
        assert md_path.exists(), f"Markdown not written: {md_path}"
        assert html_path.exists(), f"HTML not written: {html_path}"
        assert md_path.suffix == ".md"
        assert html_path.suffix == ".html"
        assert session_date.isoformat() in md_path.name
        assert session_date.isoformat() in html_path.name

    def test_markdown_contains_grand_total(self, tmp_path):
        from algotrader.reports.eod import generate_eod_report
        session_date, accounts = _seed_fake_store(tmp_path)
        out_dir = tmp_path / "reports"
        md_path, _ = generate_eod_report(
            session_date, accounts,
            db_dir=tmp_path, out_dir=out_dir,
        )
        content = md_path.read_text(encoding="utf-8")
        # Grand total = alpha(+500-100+200) + beta(+300-50+150) = 600+400 = 1000
        assert "1,000" in content or "+₹1,000" in content or "₹1,000" in content
        # Both account names present
        assert "alpha" in content
        assert "beta" in content
        # Strategy names present
        assert "orb" in content
        assert "vwap" in content

    def test_markdown_contains_trade_symbols(self, tmp_path):
        from algotrader.reports.eod import generate_eod_report
        session_date, accounts = _seed_fake_store(tmp_path)
        out_dir = tmp_path / "reports"
        md_path, _ = generate_eod_report(
            session_date, accounts,
            db_dir=tmp_path, out_dir=out_dir,
        )
        content = md_path.read_text(encoding="utf-8")
        for sym in ["NIFTY", "RELIANCE", "BANKNIFTY", "ITC", "INFY"]:
            assert sym in content, f"{sym} missing from report"

    def test_html_no_javascript(self, tmp_path):
        from algotrader.reports.eod import generate_eod_report
        session_date, accounts = _seed_fake_store(tmp_path)
        out_dir = tmp_path / "reports"
        _, html_path = generate_eod_report(
            session_date, accounts,
            db_dir=tmp_path, out_dir=out_dir,
        )
        content = html_path.read_text(encoding="utf-8")
        assert "<script" not in content.lower(), "HTML must not contain <script> tags"

    def test_html_has_inline_css(self, tmp_path):
        from algotrader.reports.eod import generate_eod_report
        session_date, accounts = _seed_fake_store(tmp_path)
        out_dir = tmp_path / "reports"
        _, html_path = generate_eod_report(
            session_date, accounts,
            db_dir=tmp_path, out_dir=out_dir,
        )
        content = html_path.read_text(encoding="utf-8")
        assert "<style>" in content, "HTML must contain inline <style>"

    def test_html_contains_grand_total(self, tmp_path):
        from algotrader.reports.eod import generate_eod_report
        session_date, accounts = _seed_fake_store(tmp_path)
        out_dir = tmp_path / "reports"
        _, html_path = generate_eod_report(
            session_date, accounts,
            db_dir=tmp_path, out_dir=out_dir,
        )
        content = html_path.read_text(encoding="utf-8")
        # Grand total 1000 visible
        assert "1,000" in content
        # Drift placeholder present
        assert "drift" in content.lower() or "Drift" in content

    def test_html_has_svg_equity_curve(self, tmp_path):
        from algotrader.reports.eod import generate_eod_report
        session_date, accounts = _seed_fake_store(tmp_path)
        out_dir = tmp_path / "reports"
        _, html_path = generate_eod_report(
            session_date, accounts,
            db_dir=tmp_path, out_dir=out_dir,
        )
        content = html_path.read_text(encoding="utf-8")
        assert "<svg" in content, "HTML must contain an SVG equity curve"

    def test_html_risk_events_shown(self, tmp_path):
        from algotrader.reports.eod import generate_eod_report
        session_date, accounts = _seed_fake_store(tmp_path)
        out_dir = tmp_path / "reports"
        _, html_path = generate_eod_report(
            session_date, accounts,
            db_dir=tmp_path, out_dir=out_dir,
        )
        content = html_path.read_text(encoding="utf-8")
        assert "breaker" in content.lower()

    def test_empty_accounts_renders_gracefully(self, tmp_path):
        from algotrader.reports.eod import generate_eod_report
        session_date = date(2026, 6, 11)
        out_dir = tmp_path / "reports"
        md_path, html_path = generate_eod_report(
            session_date, ["no_trades_account"],
            db_dir=tmp_path, out_dir=out_dir,
        )
        assert md_path.exists()
        assert html_path.exists()
        # Both should render with zero totals, not crash
        md_content = md_path.read_text()
        assert "0" in md_content  # zero trades / P&L


# ---------------------------------------------------------------------------
# Publish tests
# ---------------------------------------------------------------------------

class TestPublish:
    def test_publish_retries_three_times_on_failure(self, tmp_path, monkeypatch):
        """publish() should try 3 times (1 attempt + 2 retries) on failure."""
        from algotrader.reports.eod import publish

        html = tmp_path / "report.html"
        html.write_text("<html>test</html>", encoding="utf-8")

        call_log: list[list] = []

        def _mock_run(cmd, **kwargs):
            call_log.append(list(cmd))
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, "run", _mock_run)
        # Speed up test by removing sleeps
        monkeypatch.setattr("algotrader.reports.eod._time_mod.sleep", lambda _: None)

        result = publish(html, backoff_delays=(0.0, 0.0))

        assert result is None
        assert len(call_log) == 3, f"Expected 3 attempts, got {len(call_log)}"

    def test_publish_returns_url_on_success(self, tmp_path, monkeypatch):
        """publish() returns the URL from stdout on first successful call."""
        from algotrader.reports.eod import publish

        html = tmp_path / "report.html"
        html.write_text("<html>test</html>", encoding="utf-8")

        fake_url = "https://soma.example.com/reports/2026-06-11"

        def _mock_run(cmd, **kwargs):
            proc = subprocess.CompletedProcess(cmd, 0, stdout=fake_url, stderr="")
            return proc

        monkeypatch.setattr(subprocess, "run", _mock_run)

        result = publish(html, backoff_delays=(0.0, 0.0))
        assert result == fake_url

    def test_publish_returns_none_when_soma_missing(self, tmp_path, monkeypatch):
        """publish() returns None gracefully when soma-publish is not installed."""
        from algotrader.reports.eod import publish

        html = tmp_path / "report.html"
        html.write_text("<html>test</html>", encoding="utf-8")

        def _mock_run(cmd, **kwargs):
            raise FileNotFoundError(f"{cmd[0]}: not found")

        monkeypatch.setattr(subprocess, "run", _mock_run)

        result = publish(html, backoff_delays=(0.0, 0.0))
        assert result is None

    def test_publish_retries_then_succeeds(self, tmp_path, monkeypatch):
        """publish() retries and returns URL when first attempt fails."""
        from algotrader.reports.eod import publish

        html = tmp_path / "report.html"
        html.write_text("<html>test</html>", encoding="utf-8")

        call_count = [0]
        fake_url   = "https://soma.example.com/reports/2026-06-11"

        def _mock_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] < 2:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=fake_url, stderr="")

        monkeypatch.setattr(subprocess, "run", _mock_run)
        monkeypatch.setattr("algotrader.reports.eod._time_mod.sleep", lambda _: None)

        result = publish(html, backoff_delays=(0.0, 0.0))
        assert result == fake_url
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# EOD watchdog tests
# ---------------------------------------------------------------------------

class TestEodWatchdog:
    def test_check_open_positions_no_alert(self, tmp_path, capsys):
        """No ALERT when there are no open positions."""
        from scripts.eod_watchdog import check_open_positions
        sd = date(2026, 6, 11)
        result = check_open_positions(sd, ["paper"], db_dir=tmp_path)
        assert result is False
        captured = capsys.readouterr()
        assert "ALERT" not in captured.out
        assert "OK" in captured.out

    def test_check_open_positions_detects_open(self, tmp_path, capsys):
        """ALERT printed and True returned when open position exists."""
        from algotrader.paper import store
        from scripts.eod_watchdog import check_open_positions

        sd = date(2026, 6, 11)
        store.init_db("paper", db_dir=tmp_path)
        store.record_open_position("paper", sd, {
            "position_id": "open_001",
            "strategy_id": "orb",
            "symbol":      "NIFTY",
            "side":        "BUY",
            "quantity":    1,
            "entry_price": 24000.0,
            "entry_ts":    _make_ts(9, 30),
            "stop_price":  23800.0,
        }, db_dir=tmp_path)

        result = check_open_positions(sd, ["paper"], db_dir=tmp_path)

        assert result is True
        captured = capsys.readouterr()
        assert "ALERT" in captured.out
        assert "NIFTY" in captured.out
        assert "open_001" in captured.out

    def test_check_open_positions_multiple_accounts(self, tmp_path, capsys):
        """Checks all passed accounts; alerts for each open position found."""
        from algotrader.paper import store
        from scripts.eod_watchdog import check_open_positions

        sd = date(2026, 6, 11)
        # Only beta has an open position
        store.init_db("alpha", db_dir=tmp_path)
        store.init_db("beta",  db_dir=tmp_path)
        store.record_open_position("beta", sd, {
            "position_id": "b_pos", "strategy_id": "vwap",
            "symbol": "BANKNIFTY", "side": "SELL",
            "quantity": 1, "entry_price": 51000.0,
            "entry_ts": _make_ts(10, 0),
        }, db_dir=tmp_path)

        result = check_open_positions(sd, ["alpha", "beta"], db_dir=tmp_path)
        assert result is True
        captured = capsys.readouterr()
        assert "BANKNIFTY" in captured.out
        assert "beta" in captured.out

    def test_check_report_exists_ok(self, tmp_path, capsys):
        """Returns True when both report files exist."""
        from scripts.eod_watchdog import check_report_exists

        sd = date(2026, 6, 11)
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        (report_dir / f"{sd.isoformat()}.md").write_text("# test")
        (report_dir / f"{sd.isoformat()}.html").write_text("<html/>")

        result = check_report_exists(sd, report_dir=report_dir)
        assert result is True
        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_check_report_exists_missing(self, tmp_path, capsys):
        """Returns False and prints ALERT when report file is absent."""
        from scripts.eod_watchdog import check_report_exists

        sd = date(2026, 6, 11)
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        # Only .md exists; .html is missing

        result = check_report_exists(sd, report_dir=report_dir)
        assert result is False
        captured = capsys.readouterr()
        assert "ALERT" in captured.out

    def test_watchdog_main_returns_1_on_alert(self, tmp_path, monkeypatch):
        """main() returns exit code 1 when an open position is found."""
        from algotrader.paper import store
        from scripts.eod_watchdog import main

        sd = date(2026, 6, 11)
        store.init_db("paper", db_dir=tmp_path)
        store.record_open_position("paper", sd, {
            "position_id": "alert_pos", "strategy_id": "orb",
            "symbol": "NIFTY", "side": "BUY", "quantity": 1,
            "entry_price": 24000.0, "entry_ts": _make_ts(9, 30),
        }, db_dir=tmp_path)

        exit_code = main([
            "--date", sd.isoformat(),
            "--accounts", "paper",
            "--db-dir", str(tmp_path),
        ])
        assert exit_code == 1

    def test_watchdog_main_returns_0_when_clean(self, tmp_path):
        """main() returns exit code 0 when no open positions found."""
        from scripts.eod_watchdog import main

        sd = date(2026, 6, 11)
        exit_code = main([
            "--date", sd.isoformat(),
            "--accounts", "paper",
            "--db-dir", str(tmp_path),
        ])
        assert exit_code == 0
