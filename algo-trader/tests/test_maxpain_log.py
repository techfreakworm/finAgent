"""Unit tests for the max-pain / PCR / OI-wall logging harness (pre-reg).

All tests use a SYNTHETIC, hand-computable mini-chain fixture written to a tmp dir;
zero network I/O, zero real-data reads (the module's path constants are monkeypatched
onto tmp_path). Covered per the build spec:

  (a) max-pain argmin correct on a hand-computable case
  (b) PIN-GAP + PCR arithmetic
  (c) OI-wall definition (argmax per side, bracket flag, concentration)
  (d) `previous_oi` (NOT live `oi`) is the consumed source -- poisoning `oi` leaves
      every signal unchanged
  (e) idempotent re-run -- no duplicate (date, block) rows
  (f) `_designonly` flagging of burned expiry-Tuesdays
  plus: near_weekly block selection, the off-hours skip guard, the frozen-spot
  `outcome_reliable` flag, the paper-trade join + stop_fired rule, and the
  running-median split labeller.

Hand-computed oracle for the fixture chain (strikes 100/200/300, spacing 100):
    prev_oi  CE = {100:10, 200:50, 300:100}   PE = {100:100, 200:50, 300:10}
    settle-pain(K=100)=7000, (K=200)=2000, (K=300)=7000  -> MAX-PAIN = 200
    PCR = sum(PE)/sum(CE) = 160/160 = 1.0        abs_ln_pcr = 0
    CALL_WALL = 300 (max CE), PUT_WALL = 100 (max PE)
    spot0 = 210 -> pin_gap = 10/210 = 4.7619%, bracket(100<=210<=300)=True
    wall concentration = 100/160 = 0.625 (each side)
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from scripts import maxpain_pcr_log as M


# ------------------------------------------------------------------ fixtures

def _chain_rows(date: str, dte: int, expiry: str, spot_series: list[tuple[str, float]],
                prev_oi_ce: dict[float, int], prev_oi_pe: dict[float, int],
                oi_ce: dict[float, int] | None = None,
                oi_pe: dict[float, int] | None = None) -> list[dict]:
    """Build raw chain rows for one leg across a list of (HH:MM:SS, spot) snapshots.

    `previous_oi` is set from prev_oi_* (constant across snaps, as in real data);
    live `oi` defaults to previous_oi unless oi_* overrides it (poison test).
    """
    oi_ce = oi_ce or prev_oi_ce
    oi_pe = oi_pe or prev_oi_pe
    rows = []
    for hhmmss, spot in spot_series:
        snap_ts = f"{date}T{hhmmss}+05:30"
        for k in sorted(set(prev_oi_ce) | set(prev_oi_pe)):
            for opt, poi, loi in (("CE", prev_oi_ce, oi_ce), ("PE", prev_oi_pe, oi_pe)):
                rows.append({
                    "snap_ts": snap_ts, "expiry": expiry, "dte": dte, "spot": spot,
                    "strike": float(k), "opt_type": opt,
                    "last_price": 50.0, "top_bid_price": 0.0, "top_bid_quantity": 0,
                    "top_ask_price": 0.0, "top_ask_quantity": 0, "implied_volatility": 12.0,
                    "oi": int(loi.get(k, 0)), "volume": 100,
                    "previous_close_price": 50.0, "previous_oi": int(poi.get(k, 0)),
                    "previous_volume": 100, "average_price": 50.0, "security_id": 1,
                    "delta": 0.5, "gamma": 0.0, "theta": 0.0, "vega": 0.0,
                })
    return rows


_PREV_CE = {100: 10, 200: 50, 300: 100}
_PREV_PE = {100: 100, 200: 50, 300: 10}
# an intraday RTH spot path that varies (open 210, dips 205, rallies 215) -> range > 0
_SPOTS = [("09:14:05", 209.0), ("09:15:30", 210.0), ("09:18:00", 210.0),
          ("10:00:00", 205.0), ("12:00:00", 215.0), ("15:14:00", 212.0)]


def _write_chain(chain_dir, date: str, legs: list[list[dict]]) -> None:
    rows = [r for leg in legs for r in leg]
    pd.DataFrame(rows).to_parquet(chain_dir / f"{date}.parquet")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect all harness paths onto tmp_path; return the tmp dirs."""
    chain_dir = tmp_path / "chain"
    chain_dir.mkdir()
    nifty_dir = tmp_path / "nifty_1m"
    nifty_dir.mkdir()
    out = tmp_path / "eval.jsonl"
    paper = tmp_path / "paper-0dte.db"
    monkeypatch.setattr(M, "CHAIN_DIR", chain_dir)
    monkeypatch.setattr(M, "NIFTY_1M_DIR", nifty_dir)
    monkeypatch.setattr(M, "OUT_JSONL", out)
    monkeypatch.setattr(M, "PAPER_DB", paper)
    monkeypatch.setattr(M, "DESIGNONLY_DATES", set())
    return {"chain": chain_dir, "nifty": nifty_dir, "out": out, "paper": paper}


def _make_paper_db(path, date: str, exit_times: tuple[str, str],
                   exit_reasons: tuple[str, str] = ("strategy_exit", "strategy_exit")) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE fn_trades (id INTEGER PRIMARY KEY, account_id TEXT, session_date TEXT, "
        "position_id TEXT, strategy_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, "
        "entry_price REAL, entry_ts TEXT, exit_price REAL, exit_ts TEXT, exit_reason TEXT, "
        "gross_pnl REAL, net_pnl REAL, costs_total REAL, slippage_paid REAL)"
    )
    legs = [
        ("NIFTY-Jun2026-200-CE", 40.0, 30.0, exit_times[0], exit_reasons[0], 1000.0, 950.0),
        ("NIFTY-Jun2026-200-PE", 45.0, 60.0, exit_times[1], exit_reasons[1], -1500.0, -1560.0),
    ]
    for i, (sym, ep, xp, xt, rsn, g, n) in enumerate(legs, 1):
        con.execute(
            "INSERT INTO fn_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, "paper-0dte", date, f"pos{i}", M.STRADDLE_STRATEGY_ID, sym, "SELL", 1,
             ep, f"{date}T09:16:00+05:30", xp, f"{date}T{xt}+05:30", rsn, g, n, 50.0, 10.0),
        )
    con.commit()
    con.close()


# --------------------------------------------------------- (a) max-pain argmin

class TestMaxPain:
    def test_argmin_hand_computed(self):
        ce = pd.Series(_PREV_CE, dtype=float).rename_axis("strike")
        pe = pd.Series(_PREV_PE, dtype=float).rename_axis("strike")
        assert M.max_pain_strike(ce, pe) == 200.0

    def test_argmin_tie_lowest(self):
        # symmetric book -> pain equal at the two outer strikes; interior strike wins,
        # and on a genuine tie the lowest strike is returned (first argmin).
        ce = pd.Series({100: 10, 200: 10}, dtype=float).rename_axis("strike")
        pe = pd.Series({100: 10, 200: 10}, dtype=float).rename_axis("strike")
        mp = M.max_pain_strike(ce, pe)
        assert mp in (100.0, 200.0)
        # pain(100)=max(200-100,0)*pe? -> puts pay at settle below strike:
        #   K=100: puts max(200-100)*10=1000 ; calls 0 -> 1000
        #   K=200: calls max(200-100)*10=1000 ; puts 0 -> 1000  => tie -> lowest 100
        assert mp == 100.0


# ------------------------------------------------- (b)/(c) full signal block

class TestSignalBlock:
    def _block(self):
        rows = _chain_rows("2026-07-07", 0, "2026-07-07", _SPOTS, _PREV_CE, _PREV_PE)
        leg = pd.DataFrame(rows)
        leg["snap_dt"] = pd.to_datetime(leg["snap_ts"])
        return M.signal_block(leg)

    def test_spot0_median_of_window(self):
        assert self._block()["spot0"] == 210.0

    def test_pin_gap_arithmetic(self):
        # |210-200|/210 * 100 = 4.7619%
        assert self._block()["pin_gap_pct"] == pytest.approx(4.7619, abs=1e-3)

    def test_pcr_and_lnpcr(self):
        b = self._block()
        assert b["pcr"] == pytest.approx(1.0, abs=1e-9)
        assert b["abs_ln_pcr"] == pytest.approx(0.0, abs=1e-9)

    def test_walls_and_bracket(self):
        b = self._block()
        assert b["call_wall"] == 300.0
        assert b["put_wall"] == 100.0
        assert b["wall_bracket_flag"] is True

    def test_wall_concentration(self):
        b = self._block()
        assert b["call_wall_concentration"] == pytest.approx(0.625, abs=1e-6)
        assert b["put_wall_concentration"] == pytest.approx(0.625, abs=1e-6)

    def test_max_pain_in_block(self):
        assert self._block()["max_pain"] == 200.0


# ----------------------------------- (d) previous_oi consumed, NOT live oi

class TestPrevOiIsSource:
    def test_poisoned_oi_does_not_move_signal(self):
        # Live `oi` is set to values that WOULD flip max-pain and both walls if used:
        # huge CE at 100, huge PE at 300 (opposite of the previous_oi structure).
        poison_ce = {100: 10_000_000, 200: 1, 300: 1}
        poison_pe = {100: 1, 200: 1, 300: 10_000_000}
        rows = _chain_rows("2026-07-07", 0, "2026-07-07", _SPOTS,
                           _PREV_CE, _PREV_PE, oi_ce=poison_ce, oi_pe=poison_pe)
        leg = pd.DataFrame(rows)
        leg["snap_dt"] = pd.to_datetime(leg["snap_ts"])
        b = M.signal_block(leg)
        # unchanged from the clean oracle -> the signal reads previous_oi, not oi
        assert b["max_pain"] == 200.0
        assert b["call_wall"] == 300.0
        assert b["put_wall"] == 100.0
        assert b["pcr"] == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------- (f) design-only + block select

class TestBlocksAndDesignonly:
    def test_tuesday_emits_two_blocks(self, env):
        d0 = _chain_rows("2026-07-07", 0, "2026-07-07", _SPOTS, _PREV_CE, _PREV_PE)
        d7 = _chain_rows("2026-07-07", 7, "2026-07-14", _SPOTS, _PREV_CE, _PREV_PE)
        _write_chain(env["chain"], "2026-07-07", [d0, d7])
        rows, skip = M.process_date("2026-07-07")
        assert skip is None
        blocks = sorted(r["block"] for r in rows)
        assert blocks == ["expiry_0dte", "near_weekly"]
        near = next(r for r in rows if r["block"] == "near_weekly")
        assert near["dte"] == 7 and near["ancillary"] is True

    def test_nonexpiry_emits_only_near_weekly(self, env):
        d6 = _chain_rows("2026-07-01", 6, "2026-07-07", _SPOTS, _PREV_CE, _PREV_PE)
        d13 = _chain_rows("2026-07-01", 13, "2026-07-14", _SPOTS, _PREV_CE, _PREV_PE)
        _write_chain(env["chain"], "2026-07-01", [d6, d13])
        rows, skip = M.process_date("2026-07-01")
        assert skip is None
        assert [r["block"] for r in rows] == ["near_weekly"]
        assert rows[0]["dte"] == 6  # smallest positive dte (nearest non-expiring weekly)

    def test_designonly_flag(self, env, monkeypatch):
        monkeypatch.setattr(M, "DESIGNONLY_DATES", {"2026-07-07"})
        d0 = _chain_rows("2026-07-07", 0, "2026-07-07", _SPOTS, _PREV_CE, _PREV_PE)
        d7 = _chain_rows("2026-07-07", 7, "2026-07-14", _SPOTS, _PREV_CE, _PREV_PE)
        _write_chain(env["chain"], "2026-07-07", [d0, d7])
        rows, _ = M.process_date("2026-07-07")
        exp = next(r for r in rows if r["block"] == "expiry_0dte")
        near = next(r for r in rows if r["block"] == "near_weekly")
        assert exp["_designonly"] is True
        assert near["_designonly"] is False  # ancillary is never a burned expiry row


# ------------------------------------------------------ (e) idempotent re-run

class TestIdempotency:
    def test_rerun_replaces_not_duplicates(self, env):
        d0 = _chain_rows("2026-07-07", 0, "2026-07-07", _SPOTS, _PREV_CE, _PREV_PE)
        d7 = _chain_rows("2026-07-07", 7, "2026-07-14", _SPOTS, _PREV_CE, _PREV_PE)
        _write_chain(env["chain"], "2026-07-07", [d0, d7])
        for _ in range(3):
            rows, _ = M.process_date("2026-07-07")
            M.upsert(rows, replace_dates={"2026-07-07"})
        written = [__import__("json").loads(l) for l in env["out"].read_text().splitlines() if l.strip()]
        keys = [(r["date"], r["block"]) for r in written]
        assert len(keys) == 2
        assert len(set(keys)) == 2  # no duplicates


# ----------------------------------------------------------- skip / outcome

class TestSkipAndOutcome:
    def test_offhours_session_skipped(self, env):
        # only a 22:41 snapshot -> no 09:15-09:20 window -> skipped with a reason
        d = _chain_rows("2026-06-21", 2, "2026-06-23", [("22:41:24", 200.0)], _PREV_CE, _PREV_PE)
        _write_chain(env["chain"], "2026-06-21", [d])
        rows, skip = M.process_date("2026-06-21")
        assert rows == []
        assert skip is not None and "09:15" in skip

    def test_outcome_reliable_true_when_spot_varies(self, env):
        d7 = _chain_rows("2026-07-01", 6, "2026-07-07", _SPOTS, _PREV_CE, _PREV_PE)
        _write_chain(env["chain"], "2026-07-01", [d7])
        rows, _ = M.process_date("2026-07-01")
        r = rows[0]
        assert r["outcome_reliable"] is True
        # range = (215-205)/210 * 100
        assert r["realized_range_pct"] == pytest.approx((215 - 205) / 210 * 100, abs=1e-3)

    def test_outcome_unreliable_when_spot_frozen(self, env):
        frozen = [("09:15:30", 210.0), ("10:00:00", 210.0), ("15:14:00", 210.0)]
        d7 = _chain_rows("2026-07-01", 6, "2026-07-07", frozen, _PREV_CE, _PREV_PE)
        _write_chain(env["chain"], "2026-07-01", [d7])
        rows, _ = M.process_date("2026-07-01")
        r = rows[0]
        assert r["outcome_reliable"] is False
        assert r["realized_range_pct"] == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------ paper join + stop_fired

class TestPaperJoin:
    def _run(self, env, exit_times, reasons=("strategy_exit", "strategy_exit")):
        _make_paper_db(env["paper"], "2026-07-07", exit_times, reasons)
        d0 = _chain_rows("2026-07-07", 0, "2026-07-07", _SPOTS, _PREV_CE, _PREV_PE)
        d7 = _chain_rows("2026-07-07", 7, "2026-07-14", _SPOTS, _PREV_CE, _PREV_PE)
        _write_chain(env["chain"], "2026-07-07", [d0, d7])
        rows, _ = M.process_date("2026-07-07")
        return next(r for r in rows if r["block"] == "expiry_0dte")

    def test_net_is_sum_of_legs(self, env):
        r = self._run(env, ("15:10:00", "15:10:00"))
        assert r["paper_trade"]["straddle_net"] == pytest.approx(950.0 + -1560.0, abs=1e-6)

    def test_stop_fired_time_based(self, env):
        # both legs exit at 11:56 (< 15:10 flat) -> stop_fired True (time-based)
        r = self._run(env, ("11:56:00", "11:56:00"))
        assert r["paper_trade"]["stop_fired"] is True

    def test_no_stop_when_flat_exit(self, env):
        # exit AT 15:10 flat with a plain strategy_exit reason -> not a stop
        r = self._run(env, ("15:10:00", "15:10:00"))
        assert r["paper_trade"]["stop_fired"] is False

    def test_stop_fired_reason_based(self, env):
        # exit at the flat time but reason names a stop -> stop_fired True
        r = self._run(env, ("15:10:00", "15:10:00"), reasons=("stop_loss", "strategy_exit"))
        assert r["paper_trade"]["stop_fired"] is True

    def test_near_weekly_has_no_paper_trade(self, env):
        _make_paper_db(env["paper"], "2026-07-07", ("11:56:00", "11:56:00"))
        d0 = _chain_rows("2026-07-07", 0, "2026-07-07", _SPOTS, _PREV_CE, _PREV_PE)
        d7 = _chain_rows("2026-07-07", 7, "2026-07-14", _SPOTS, _PREV_CE, _PREV_PE)
        _write_chain(env["chain"], "2026-07-07", [d0, d7])
        rows, _ = M.process_date("2026-07-07")
        near = next(r for r in rows if r["block"] == "near_weekly")
        assert "paper_trade" not in near


# ------------------------------------------------ running-median split label

class TestSplitLabels:
    def _eval_row(self, date, pin_gap, block="expiry_0dte", designonly=False):
        return {"date": date, "block": block, "pin_gap_pct": pin_gap,
                "_designonly": designonly, "outcome_reliable": True}

    def test_insufficient_history_below_threshold(self):
        rows = [self._eval_row(f"2026-07-{d:02d}", 0.2 + 0.01 * i)
                for i, d in enumerate(range(1, M.MIN_SPLIT_N))]  # one fewer than needed
        out = M.apply_split_labels(rows)
        assert all(r["split_label"] == "insufficient_history" for r in out)

    def test_favorable_unfavorable_split(self):
        # Accrue MIN_SPLIT_N rows of history, then feed a LARGE gap (labelled
        # unfavorable, > running median) and a SMALL gap (labelled favorable,
        # <= running median). Small pin-gap = near the pin = favorable (spec).
        base = [0.20, 0.30, 0.40, 0.50, 0.60][:M.MIN_SPLIT_N]
        gaps = base + [0.90, 0.05]
        rows = [self._eval_row(f"2026-08-{i+1:02d}", g) for i, g in enumerate(gaps)]
        out = M.apply_split_labels(rows)
        # first MIN_SPLIT_N-1 rows have insufficient history
        assert all(r["split_label"] == "insufficient_history"
                   for r in out[:M.MIN_SPLIT_N - 1])
        big = next(r for r in out if r["pin_gap_pct"] == 0.90)
        small = next(r for r in out if r["pin_gap_pct"] == 0.05)
        assert big["split_label"] == "unfavorable"    # 0.90 > running median
        assert small["split_label"] == "favorable"    # 0.05 <= running median
        # the label used a real running median (recorded on the row)
        assert small["split_median_pin_gap_pct"] is not None

    def test_designonly_and_ancillary_never_favorable(self):
        rows = [
            self._eval_row("2026-06-23", 0.17, designonly=True),
            self._eval_row("2026-06-30", 0.19, designonly=True),
            self._eval_row("2026-07-07", 0.25, block="near_weekly"),
        ]
        out = M.apply_split_labels(rows)
        by = {(r["date"], r["block"]): r["split_label"] for r in out}
        assert by[("2026-06-23", "expiry_0dte")] == "designonly"
        assert by[("2026-06-30", "expiry_0dte")] == "designonly"
        assert by[("2026-07-07", "near_weekly")] == "ancillary"

    def test_designonly_excluded_from_median(self):
        # design-only rows must not contribute to the running median that labels
        # eval rows -> a burned row with an extreme gap cannot shift eval labels.
        rows = [self._eval_row("2026-06-23", 99.0, designonly=True)]
        rows += [self._eval_row(f"2026-09-{i+1:02d}", g)
                 for i, g in enumerate([0.10, 0.20, 0.30, 0.40, 0.50, 0.60][:max(M.MIN_SPLIT_N, 6)])]
        out = M.apply_split_labels(rows)
        eval_rows = [r for r in out if not r["_designonly"]]
        med_used = [r["split_median_pin_gap_pct"] for r in eval_rows if r["split_median_pin_gap_pct"] is not None]
        # the median never approaches the poisoned 99.0 -> burned row excluded
        assert med_used and max(med_used) < 1.0
