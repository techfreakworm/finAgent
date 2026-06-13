"""Tests for algotrader/data/option_resolver.py.

All tests run against the real api-scrip-master.csv on disk — no mocking of
the CSV parsing itself, so the tests validate the actual scrip-master data.
Network access is not required (refresh_scrip_master is not called).

Design constraints:
  - No order-placement imports.
  - No live network calls (refresh_scrip_master is tested in isolation with a
    mock; the resolver tests use the existing CSV).
  - All tests deterministic (no live-price dependency — strikes and expiries
    are chosen from known values in the current CSV).
"""
from __future__ import annotations

import time as _time_mod
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from algotrader.core import Segment
from algotrader.data.instruments import FnoInstrument
from algotrader.data.option_resolver import (
    _DOWNLOAD_URL,
    _SCRIP_PATH,
    _df_cache,
    nearest_atm_strike,
    resolve_option,
    refresh_scrip_master,
    _load_df,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Today on the test run machine (2026-06-13).  We pin a date that is known to
# have weekly NIFTY and monthly BANKNIFTY expiries in the current CSV.
_ON = date(2026, 6, 13)   # Friday; nearest NIFTY weekly = 2026-06-16 (Tue)


def _clear_cache() -> None:
    """Evict the module-level DataFrame cache so tests start clean."""
    import algotrader.data.option_resolver as mod
    import threading
    with mod._cache_lock:
        mod._df_cache = None


# ---------------------------------------------------------------------------
# nearest_atm_strike
# ---------------------------------------------------------------------------

class TestNearestAtmStrike:
    """Strike-grid rounding for NIFTY (50) and BANKNIFTY (100)."""

    def test_nifty_rounds_down(self):
        # 24370 / 50 = 487.4 → rounds to 487 → 24350
        assert nearest_atm_strike("NIFTY", 24370.0) == 24350.0

    def test_nifty_rounds_up(self):
        # 24380 / 50 = 487.6 → rounds to 488 → 24400
        assert nearest_atm_strike("NIFTY", 24380.0) == 24400.0

    def test_nifty_exact_multiple(self):
        assert nearest_atm_strike("NIFTY", 24350.0) == 24350.0

    def test_banknifty_rounds_down(self):
        # 54349 / 100 = 543.49 → 543 → 54300
        assert nearest_atm_strike("BANKNIFTY", 54349.0) == 54300.0

    def test_banknifty_rounds_up(self):
        # 54351 / 100 = 543.51 → 544 → 54400
        assert nearest_atm_strike("BANKNIFTY", 54351.0) == 54400.0

    def test_banknifty_exact_multiple(self):
        assert nearest_atm_strike("BANKNIFTY", 54400.0) == 54400.0


# ---------------------------------------------------------------------------
# resolve_option — NIFTY weekly
# ---------------------------------------------------------------------------

class TestResolveOptionNiftyWeekly:
    """NIFTY option resolution uses the nearest expiry (weekly or monthly)."""

    def setup_method(self):
        _clear_cache()

    def test_nifty_ce_returns_fno_instrument(self):
        """resolve_option returns a FnoInstrument for a real NIFTY CE."""
        # Strike 24350 exists in the Jun-16 weekly (confirmed in CSV).
        instr = resolve_option("NIFTY", 24350.0, "CE", _ON)
        assert isinstance(instr, FnoInstrument)

    def test_nifty_ce_security_id_plausible(self):
        """security_id is a non-empty numeric string (real Dhan SMST ID)."""
        instr = resolve_option("NIFTY", 24350.0, "CE", _ON)
        assert instr.security_id.isdigit(), (
            f"security_id {instr.security_id!r} is not numeric"
        )
        assert int(instr.security_id) > 0

    def test_nifty_ce_expiry_within_7_days(self):
        """Nearest NIFTY weekly expiry is <= 7 calendar days away from _ON."""
        instr = resolve_option("NIFTY", 24350.0, "CE", _ON)
        # Parse expiry from symbol: "NIFTY-Jun2026-24350-CE"
        # Use _load_df to get the expiry directly
        df = _load_df()
        row = df[df["security_id"] == instr.security_id]
        assert not row.empty
        expiry: date = row.iloc[0]["expiry_date"]
        assert expiry >= _ON, f"expiry {expiry} is before on={_ON}"
        assert (expiry - _ON).days <= 7, (
            f"expiry {expiry} is more than 7 days from {_ON}: "
            f"{(expiry - _ON).days} days"
        )

    def test_nifty_pe_same_expiry_as_ce(self):
        """CE and PE at the same strike resolve to the same expiry."""
        ce = resolve_option("NIFTY", 24350.0, "CE", _ON)
        pe = resolve_option("NIFTY", 24350.0, "PE", _ON)
        df = _load_df()
        ce_exp = df[df["security_id"] == ce.security_id].iloc[0]["expiry_date"]
        pe_exp = df[df["security_id"] == pe.security_id].iloc[0]["expiry_date"]
        assert ce_exp == pe_exp

    def test_nifty_ce_different_security_from_pe(self):
        """CE and PE are distinct instruments."""
        ce = resolve_option("NIFTY", 24350.0, "CE", _ON)
        pe = resolve_option("NIFTY", 24350.0, "PE", _ON)
        assert ce.security_id != pe.security_id

    def test_nifty_segment_is_nse_fno(self):
        instr = resolve_option("NIFTY", 24350.0, "CE", _ON)
        assert instr.segment is Segment.NSE_FNO

    def test_nifty_is_derivative(self):
        instr = resolve_option("NIFTY", 24350.0, "CE", _ON)
        assert instr.is_derivative is True

    def test_nifty_underlying_field(self):
        instr = resolve_option("NIFTY", 24350.0, "CE", _ON)
        assert instr.underlying == "NIFTY"

    def test_nifty_lot_size_from_dated_schedule(self):
        """lot_size() uses the instruments.py dated schedule (65 as of 2026)."""
        instr = resolve_option("NIFTY", 24350.0, "CE", _ON)
        assert instr.lot_size(_ON) == 65

    def test_nifty_tick_size_from_csv(self):
        """tick_size is read from the scrip master (5.0 for index options)."""
        instr = resolve_option("NIFTY", 24350.0, "CE", _ON)
        assert instr.tick_size == pytest.approx(5.0)

    def test_nifty_symbol_contains_strike(self):
        """Symbol string encodes the strike."""
        instr = resolve_option("NIFTY", 24350.0, "CE", _ON)
        assert "24350" in instr.symbol
        assert instr.symbol.startswith("NIFTY-")
        assert instr.symbol.endswith("-CE")

    def test_nifty_lookup_error_on_bad_strike(self):
        """Requesting a non-existent strike raises LookupError."""
        with pytest.raises(LookupError, match="NIFTY"):
            resolve_option("NIFTY", 1.0, "CE", _ON)   # no such strike

    def test_nifty_lookup_error_far_future_date(self):
        """Date beyond all available expiries raises LookupError."""
        with pytest.raises(LookupError):
            resolve_option("NIFTY", 24350.0, "CE", date(2099, 1, 1))

    def test_atm_roundtrip(self):
        """nearest_atm_strike -> resolve_option works end-to-end."""
        spot = 24372.0
        atm = nearest_atm_strike("NIFTY", spot)
        instr = resolve_option("NIFTY", atm, "CE", _ON)
        assert isinstance(instr, FnoInstrument)
        assert str(int(atm)) in instr.symbol


# ---------------------------------------------------------------------------
# resolve_option — BANKNIFTY monthly fallback
# ---------------------------------------------------------------------------

class TestResolveOptionBankniftyMonthly:
    """BANKNIFTY resolves only to monthly expiries (SEM_EXPIRY_FLAG='M')."""

    def setup_method(self):
        _clear_cache()

    def test_banknifty_returns_fno_instrument(self):
        # Strike 65400 exists in the Jun-30 monthly (confirmed in CSV).
        instr = resolve_option("BANKNIFTY", 65400.0, "CE", _ON)
        assert isinstance(instr, FnoInstrument)

    def test_banknifty_expiry_is_monthly(self):
        """Resolved expiry must carry SEM_EXPIRY_FLAG == 'M'."""
        instr = resolve_option("BANKNIFTY", 65400.0, "CE", _ON)
        df = _load_df()
        row = df[df["security_id"] == instr.security_id]
        assert not row.empty
        assert row.iloc[0]["expiry_flag"] == "M", (
            f"Expected monthly flag 'M', got {row.iloc[0]['expiry_flag']!r}"
        )

    def test_banknifty_expiry_is_nearest_monthly(self):
        """The resolved expiry is the NEAREST monthly on or after _ON."""
        instr = resolve_option("BANKNIFTY", 65400.0, "CE", _ON)
        df = _load_df()
        row = df[df["security_id"] == instr.security_id]
        expiry: date = row.iloc[0]["expiry_date"]
        assert expiry >= _ON

        # Find all monthly BANKNIFTY expiries >= _ON
        bn_mask = (
            df["symbol"].str.startswith("BANKNIFTY-")
            & (df["expiry_flag"] == "M")
            & (df["expiry_date"] >= _ON)
        )
        min_monthly: date = df[bn_mask]["expiry_date"].min()
        assert expiry == min_monthly, (
            f"Expected nearest monthly {min_monthly}, got {expiry}"
        )

    def test_banknifty_underlying_field(self):
        instr = resolve_option("BANKNIFTY", 65400.0, "CE", _ON)
        assert instr.underlying == "BANKNIFTY"

    def test_banknifty_lot_size(self):
        """lot_size() uses the dated schedule (30 as of Jan-2026+)."""
        instr = resolve_option("BANKNIFTY", 65400.0, "CE", _ON)
        assert instr.lot_size(_ON) == 30

    def test_banknifty_security_id_numeric(self):
        instr = resolve_option("BANKNIFTY", 65400.0, "CE", _ON)
        assert instr.security_id.isdigit()
        assert int(instr.security_id) > 0

    def test_banknifty_symbol_encodes_strike(self):
        instr = resolve_option("BANKNIFTY", 65400.0, "CE", _ON)
        assert "65400" in instr.symbol
        assert instr.symbol.startswith("BANKNIFTY-")
        assert instr.symbol.endswith("-CE")


# ---------------------------------------------------------------------------
# refresh_scrip_master
# ---------------------------------------------------------------------------

class TestRefreshScripMaster:
    """refresh_scrip_master skips download when file is fresh."""

    def test_skips_download_when_fresh(self, tmp_path, monkeypatch):
        """No request is made when the file is < 24 h old."""
        fake_csv = tmp_path / "api-scrip-master.csv"
        fake_csv.write_text("dummy")  # mtime = now

        monkeypatch.setattr(
            "algotrader.data.option_resolver._SCRIP_PATH", fake_csv
        )
        mock_get = MagicMock()
        with patch("requests.get", mock_get):
            refresh_scrip_master(force=False)
        mock_get.assert_not_called()

    def test_downloads_when_stale(self, tmp_path, monkeypatch):
        """requests.get is called when the file is older than 24 h."""
        fake_csv = tmp_path / "api-scrip-master.csv"
        fake_csv.write_text("dummy")
        # Make the file appear old by back-dating its mtime
        old_mtime = _time_mod.time() - 86_400 - 60
        import os
        os.utime(fake_csv, (old_mtime, old_mtime))

        monkeypatch.setattr(
            "algotrader.data.option_resolver._SCRIP_PATH", fake_csv
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content.return_value = [b"new,content\n"]
        mock_get = MagicMock(return_value=mock_response)
        with patch("requests.get", mock_get):
            refresh_scrip_master(force=False)
        mock_get.assert_called_once_with(
            _DOWNLOAD_URL, timeout=60, stream=True
        )

    def test_force_always_downloads(self, tmp_path, monkeypatch):
        """force=True downloads even if the file is fresh."""
        fake_csv = tmp_path / "api-scrip-master.csv"
        fake_csv.write_text("dummy")  # mtime = now

        monkeypatch.setattr(
            "algotrader.data.option_resolver._SCRIP_PATH", fake_csv
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content.return_value = [b"fresh,data\n"]
        mock_get = MagicMock(return_value=mock_response)
        with patch("requests.get", mock_get):
            refresh_scrip_master(force=True)
        mock_get.assert_called_once()
