"""Unit tests for the ws_shadow comparison and verdict logic.

All tests use SYNTHETIC bars — zero network I/O, zero feed connections.
Tests the compare() function and its phase segmentation, threshold checks,
and the CLEAN / NOT-CLEAN verdict paths.

Covered scenarios:
  1. Matching set -> CLEAN
  2. Close mismatch > 1 tick -> NOT-CLEAN (close-mismatch failure)
  3. Missing bars in ws -> NOT-CLEAN (count-parity failure)
  4. Parse anomaly (LTP <= 0) -> NOT-CLEAN (anomaly failure)
  5. Phase segmentation: OPEN/MID/CLOSE bucketing is correct
  6. OPEN-phase close mismatch -> NOT-CLEAN even if overall close% passes
"""
from __future__ import annotations

from datetime import datetime, date
from zoneinfo import ZoneInfo

import pytest

from scripts.ws_shadow import (
    compare,
    _phase,
    _is_anomaly,
    _close_within_tick,
    _ohlc_within_tick,
    _ts_aligned,
)
from algotrader.core import Bar, Instrument, Segment

IST = ZoneInfo("Asia/Kolkata")

# ----------------------------------------------------------------- fixtures

_INSTR = Instrument(
    symbol="TESTSTOCK",
    security_id="9999",
    segment=Segment.NSE_EQ,
    tick_size=0.05,
)

_INSTR_IDX = Instrument(
    symbol="NIFTY-FUT",
    security_id="13",
    segment=Segment.IDX,
    tick_size=0.05,
    is_derivative=True,
)


def _bar(instr: Instrument, minute_str: str, close: float, open_: float | None = None,
         high: float | None = None, low: float | None = None, volume: int = 1000) -> Bar:
    """Build a synthetic 1-min bar with ts_open at the given HH:MM today."""
    h, m = map(int, minute_str.split(":"))
    ts = datetime(2026, 6, 16, h, m, 0, tzinfo=IST)
    o = open_ if open_ is not None else close
    hi = high if high is not None else close
    lo = low if low is not None else close
    return Bar(
        instrument=instr,
        ts_open=ts,
        interval_min=1,
        open=o,
        high=hi,
        low=lo,
        close=close,
        volume=volume,
        complete=True,
    )


def _make_collections(
    ws_items: list[tuple[str, str, float]],
    rest_items: list[tuple[str, str, float]],
    instr: Instrument = _INSTR,
) -> tuple[dict, dict]:
    """Build ws_bars / rest_bars dicts from (symbol, minute, close) triples."""
    ws: dict = {}
    for sym, minute, close in ws_items:
        ws[(sym, minute)] = [_bar(instr, minute, close)]
    rest: dict = {}
    for sym, minute, close in rest_items:
        rest[(sym, minute)] = [_bar(instr, minute, close)]
    return ws, rest


# ================================================================= helpers

class TestHelpers:
    def test_phase_open(self):
        from datetime import time
        assert _phase(time(9, 15)) == "OPEN"
        assert _phase(time(9, 29)) == "OPEN"

    def test_phase_mid(self):
        from datetime import time
        assert _phase(time(9, 30)) == "MID"
        assert _phase(time(14, 59)) == "MID"

    def test_phase_close(self):
        from datetime import time
        assert _phase(time(15, 0)) == "CLOSE"
        assert _phase(time(15, 29)) == "CLOSE"
        assert _phase(time(15, 30)) == "CLOSE"

    def test_is_anomaly_negative(self):
        b = _bar(_INSTR, "10:00", -1.0)
        assert _is_anomaly(b)

    def test_is_anomaly_zero(self):
        b = _bar(_INSTR, "10:00", 0.0)
        assert _is_anomaly(b)

    def test_is_anomaly_nan(self):
        import math
        b = _bar(_INSTR, "10:00", math.nan)
        assert _is_anomaly(b)

    def test_is_anomaly_normal(self):
        b = _bar(_INSTR, "10:00", 21500.0)
        assert not _is_anomaly(b)

    def test_close_within_tick_same(self):
        wb = _bar(_INSTR, "10:00", 100.0)
        rb = _bar(_INSTR, "10:00", 100.0)
        assert _close_within_tick(wb, rb)

    def test_close_within_tick_one_tick(self):
        wb = _bar(_INSTR, "10:00", 100.0)
        rb = _bar(_INSTR, "10:00", 100.05)
        assert _close_within_tick(wb, rb)

    def test_close_outside_tick(self):
        wb = _bar(_INSTR, "10:00", 100.0)
        rb = _bar(_INSTR, "10:00", 100.10)
        assert not _close_within_tick(wb, rb)

    def test_ts_aligned_same(self):
        wb = _bar(_INSTR, "10:00", 100.0)
        rb = _bar(_INSTR, "10:00", 100.0)
        assert _ts_aligned(wb, rb)

    def test_ts_aligned_within_60s(self):
        from datetime import timedelta
        wb = _bar(_INSTR, "10:00", 100.0)
        rb = _bar(_INSTR, "10:00", 100.0)
        # Shift rb by 59 s — still aligned
        rb2 = Bar(
            instrument=rb.instrument,
            ts_open=rb.ts_open + timedelta(seconds=59),
            interval_min=rb.interval_min,
            open=rb.open, high=rb.high, low=rb.low, close=rb.close,
            volume=rb.volume, complete=rb.complete,
        )
        assert _ts_aligned(wb, rb2)


# ================================================================= compare scenarios

class TestVerdictClean:
    """A fully matching universe should yield CLEAN verdict."""

    def _make_full_universe(self) -> tuple[dict, dict, list, list, list[str]]:
        """50 minutes * 1 symbol, ws and rest identical."""
        minutes = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in range(0, 60)
                   if (h, m) >= (9, 15) and (h, m) <= (15, 29)][:50]
        close = 21500.0
        ws, rest = _make_collections(
            [(  "NIFTY-FUT", mm, close) for mm in minutes],
            [("NIFTY-FUT", mm, close) for mm in minutes],
            instr=_INSTR_IDX,
        )
        return ws, rest, [], [], ["NIFTY-FUT"]

    def test_clean_verdict(self):
        ws, rest, wa, ra, syms = self._make_full_universe()
        r = compare(ws, rest, wa, ra, syms)
        assert r["verdict"] == "CLEAN", r["failures"]
        assert r["verdict_ok"] is True
        assert r["failures"] == []

    def test_totals(self):
        ws, rest, wa, ra, syms = self._make_full_universe()
        r = compare(ws, rest, wa, ra, syms)
        t = r["totals"]
        assert t["close_mismatch"] == 0
        assert t["parse_anomalies"] == 0
        assert t["missing_in_ws"] == 0

    def test_instrument_count_ok(self):
        ws, rest, wa, ra, syms = self._make_full_universe()
        r = compare(ws, rest, wa, ra, syms)
        for row in r["per_instrument"]:
            if row["symbol"] == "NIFTY-FUT":
                assert row["count_ok"] is True


class TestVerdictCloseMismatch:
    """A universe where WS has a wrong close on many minutes -> NOT-CLEAN."""

    def test_mismatch_fails(self):
        # 100 matching minutes, but all close values differ by 0.10 (2 ticks)
        minutes = [f"10:{m:02d}" for m in range(0, 60)] + [f"11:{m:02d}" for m in range(0, 60)]
        # All in MID phase — still global > 1% mismatch
        ws, rest = _make_collections(
            [("TESTSTOCK", mm, 100.0)  for mm in minutes],
            [("TESTSTOCK", mm, 100.10) for mm in minutes],
        )
        r = compare(ws, rest, [], [], ["TESTSTOCK"])
        assert r["verdict"] == "NOT-CLEAN"
        assert any("close-mismatch" in f for f in r["failures"])

    def test_mismatch_recorded(self):
        minutes = ["10:00", "10:01"]
        ws, rest = _make_collections(
            [("TESTSTOCK", mm, 100.0)  for mm in minutes],
            [("TESTSTOCK", mm, 100.10) for mm in minutes],
        )
        r = compare(ws, rest, [], [], ["TESTSTOCK"])
        assert r["totals"]["close_mismatch"] == 2
        assert len(r["close_mismatches"]) == 2


class TestVerdictMissingBars:
    """WS missing bars on an instrument -> NOT-CLEAN (count-parity failure)."""

    def test_missing_bars_fails(self):
        # REST has 5 bars, WS has 2 -> diff=3 > threshold of 2
        rest_minutes = ["10:00", "10:01", "10:02", "10:03", "10:04"]
        ws_minutes   = ["10:00", "10:01"]
        ws, rest = _make_collections(
            [("TESTSTOCK", mm, 100.0) for mm in ws_minutes],
            [("TESTSTOCK", mm, 100.0) for mm in rest_minutes],
        )
        r = compare(ws, rest, [], [], ["TESTSTOCK"])
        assert r["verdict"] == "NOT-CLEAN"
        assert any("count" in f.lower() or "parity" in f.lower() for f in r["failures"])

    def test_within_parity_ok(self):
        # REST has 5 bars, WS has 4 -> diff=1 <= 2 threshold, and close matches
        rest_minutes = ["10:00", "10:01", "10:02", "10:03", "10:04"]
        ws_minutes   = ["10:00", "10:01", "10:02", "10:03"]
        ws, rest = _make_collections(
            [("TESTSTOCK", mm, 100.0) for mm in ws_minutes],
            [("TESTSTOCK", mm, 100.0) for mm in rest_minutes],
        )
        r = compare(ws, rest, [], [], ["TESTSTOCK"])
        # close-match rate on matched (4) minutes = 100%; count diff = 1 OK
        assert r["verdict"] == "CLEAN", r["failures"]

    def test_missing_in_ws_counted(self):
        ws, rest = _make_collections(
            [],
            [("TESTSTOCK", "10:00", 100.0)],
        )
        r = compare(ws, rest, [], [], ["TESTSTOCK"])
        assert r["totals"]["missing_in_ws"] >= 1


class TestVerdictParseAnomaly:
    """Parse anomaly (LTP<=0) in ws_anomalies -> NOT-CLEAN."""

    def test_anomaly_fails(self):
        ws, rest = _make_collections(
            [("TESTSTOCK", "10:00", 100.0)],
            [("TESTSTOCK", "10:00", 100.0)],
        )
        # Inject a ws anomaly (already screened by collector; we pass it directly)
        ws_anomalies = [{"source": "ws", "symbol": "TESTSTOCK", "ts": "2026-06-16T10:00:00+05:30", "close": -1.0}]
        r = compare(ws, rest, ws_anomalies, [], ["TESTSTOCK"])
        assert r["verdict"] == "NOT-CLEAN"
        assert any("anomal" in f.lower() for f in r["failures"])
        assert r["totals"]["parse_anomalies"] == 1

    def test_rest_anomaly_also_fails(self):
        ws, rest = _make_collections(
            [("TESTSTOCK", "10:00", 100.0)],
            [("TESTSTOCK", "10:00", 100.0)],
        )
        rest_anomalies = [{"source": "rest", "symbol": "TESTSTOCK", "ts": "2026-06-16T10:00:00+05:30", "close": 0.0}]
        r = compare(ws, rest, [], rest_anomalies, ["TESTSTOCK"])
        assert r["verdict"] == "NOT-CLEAN"
        assert r["totals"]["parse_anomalies"] == 1


class TestPhaseSegmentation:
    """Bars must land in the correct phase bucket."""

    def test_open_phase_bars_segmented(self):
        # All minutes in OPEN phase (09:15-09:29)
        minutes = [f"09:{m:02d}" for m in range(15, 30)]
        ws, rest = _make_collections(
            [("TESTSTOCK", mm, 100.0) for mm in minutes],
            [("TESTSTOCK", mm, 100.0) for mm in minutes],
        )
        r = compare(ws, rest, [], [], ["TESTSTOCK"])
        # All 15 matched minutes should be in OPEN
        assert r["phases"]["OPEN"]["matched"] == 15
        assert r["phases"]["MID"]["matched"] == 0

    def test_close_phase_bars_segmented(self):
        minutes = ["15:00", "15:15", "15:29"]
        ws, rest = _make_collections(
            [("TESTSTOCK", mm, 100.0) for mm in minutes],
            [("TESTSTOCK", mm, 100.0) for mm in minutes],
        )
        r = compare(ws, rest, [], [], ["TESTSTOCK"])
        assert r["phases"]["CLOSE"]["matched"] == 3

    def test_open_phase_mismatch_fails_verdict(self):
        """Even if overall close% passes, OPEN phase failure -> NOT-CLEAN."""
        # Make OPEN phase 100% mismatch (2 bars) but add many matching MID bars so
        # overall close% could still pass WITHOUT the phase-specific check.
        open_minutes = ["09:15", "09:16"]
        mid_minutes  = [f"10:{m:02d}" for m in range(0, 60)]  # 60 matching

        ws_items = (
            [("TESTSTOCK", mm, 100.0)  for mm in open_minutes]  # mismatch in OPEN
            + [("TESTSTOCK", mm, 100.0) for mm in mid_minutes]   # match in MID
        )
        rest_items = (
            [("TESTSTOCK", mm, 100.10) for mm in open_minutes]  # 2-tick diff
            + [("TESTSTOCK", mm, 100.0) for mm in mid_minutes]
        )
        ws, rest = _make_collections(ws_items, rest_items)
        r = compare(ws, rest, [], [], ["TESTSTOCK"])

        # 2 open mismatches out of 2 matched = 0% close_match in OPEN
        # 60 mid matches out of 60 = 100%
        # Overall: 60/62 ≈ 96.8% < 99% -> would fail global too
        # But the phase check specifically catches OPEN
        assert r["verdict"] == "NOT-CLEAN"
        # failures should mention OPEN
        assert any("OPEN" in f for f in r["failures"])

    def test_close_phase_mismatch_fails_verdict(self):
        """CLOSE phase failure -> NOT-CLEAN."""
        close_minutes = ["15:00", "15:01"]
        mid_minutes   = [f"10:{m:02d}" for m in range(0, 60)]

        ws_items = (
            [("TESTSTOCK", mm, 100.10) for mm in close_minutes]  # mismatch
            + [("TESTSTOCK", mm, 100.0)  for mm in mid_minutes]
        )
        rest_items = (
            [("TESTSTOCK", mm, 100.20) for mm in close_minutes]  # 2-tick diff
            + [("TESTSTOCK", mm, 100.0)  for mm in mid_minutes]
        )
        ws, rest = _make_collections(ws_items, rest_items)
        r = compare(ws, rest, [], [], ["TESTSTOCK"])
        assert r["verdict"] == "NOT-CLEAN"
        assert any("CLOSE" in f for f in r["failures"])
