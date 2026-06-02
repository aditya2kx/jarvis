#!/usr/bin/env python3
"""Offline tests for sandbox_e2e — pure logic + mocked orchestration.

The structural no-OTP guarantee is enforced in an isolated subprocess
(test_no_scrape_module_in_import_graph): if any future edit makes a scrape /
login / browser module reachable from the runner's import graph, this fails.
"""

import datetime
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import sandbox_e2e as e2e

D = datetime.date


class TestPureHelpers(unittest.TestCase):
    def test_dates_in_window_inclusive(self):
        self.assertEqual(
            e2e.dates_in_window(D(2026, 5, 1), D(2026, 5, 3)),
            [D(2026, 5, 1), D(2026, 5, 2), D(2026, 5, 3)],
        )

    def test_dates_in_window_single_day(self):
        self.assertEqual(e2e.dates_in_window(D(2026, 5, 1), D(2026, 5, 1)), [D(2026, 5, 1)])

    def test_dates_in_window_end_before_start_raises(self):
        with self.assertRaises(ValueError):
            e2e.dates_in_window(D(2026, 5, 3), D(2026, 5, 1))

    def test_staging_environ_sets_mode_and_all_sids(self):
        from agents.bhaga.scripts import sandbox_provision as sp
        ids = {k: f"id_{k}" for k in sp.PROFILE_KEYS}
        env = e2e.staging_environ(ids)
        self.assertEqual(env["BHAGA_SHEET_MODE"], "staging")
        self.assertEqual(env["BHAGA_STAGING_BHAGA_MODEL_SID"], "id_bhaga_model")
        self.assertEqual(len(env), len(sp.PROFILE_KEYS) + 1)

    def test_tab_counts_from_columns_excludes_header(self):
        raw = {"daily": [["h"], ["1"], ["2"]], "period_summary": [["h"]], "empty": []}
        self.assertEqual(
            e2e.tab_counts_from_columns(raw),
            {"daily": 2, "period_summary": 0, "empty": 0},
        )

    def test_format_evidence_ok(self):
        report = {
            "pr_number": 7, "status": "ok", "days": 2,
            "window": {"start": "2026-05-01", "end": "2026-05-02"},
            "restored_files": {"2026-05-01": 3, "2026-05-02": 2},
            "model_tab_counts": {"daily": 2, "period_summary": 1},
            "item_lines_ran": False,
            "slot": 1,
            "teardown": {"slot": 1, "released": True},
        }
        text = e2e.format_evidence(report)
        self.assertIn("PR #7", text)
        self.assertIn("status: **ok**", text)
        self.assertIn("`daily`: 2", text)
        self.assertIn("pool slot: 1", text)
        self.assertIn("released + cleared", text)
        self.assertIn("GCS files restored: 5", text)

    def test_format_evidence_error(self):
        report = {"pr_number": 1, "status": "error", "days": 1,
                  "window": {"start": "x", "end": "y"}, "error": "boom"}
        self.assertIn("error: `boom`", e2e.format_evidence(report))

    def test_item_lines_module_present(self):
        # backfill_item_lines_from_cache now ships with item_operations, so the
        # sandbox e2e picks up the item-lines backfill step automatically.
        self.assertTrue(e2e._item_lines_module_available())

    def test_select_window_picks_most_recent_max_days(self):
        dates = [D(2026, 5, 1), D(2026, 5, 2), D(2026, 5, 3), D(2026, 5, 5)]
        self.assertEqual(e2e.select_window(dates, 2), (D(2026, 5, 3), D(2026, 5, 5)))
        self.assertEqual(e2e.select_window(dates, 1), (D(2026, 5, 5), D(2026, 5, 5)))
        self.assertEqual(e2e.select_window(dates, 99), (D(2026, 5, 1), D(2026, 5, 5)))

    def test_select_window_empty_raises(self):
        with self.assertRaises(ValueError):
            e2e.select_window([], 2)

    def test_select_window_bad_max_days_raises(self):
        with self.assertRaises(ValueError):
            e2e.select_window([D(2026, 5, 1)], 0)


class TestWindowFilter(unittest.TestCase):
    def test_filters_by_date_field_inclusive(self):
        rows = [
            {"date_local": "2026-05-17", "x": 1},  # before window
            {"date_local": "2026-05-18", "x": 2},  # start boundary
            {"date_local": "2026-05-25", "x": 3},
            {"date_local": "2026-05-31", "x": 4},  # end boundary
            {"date_local": "2026-06-01", "x": 5},  # after window
        ]
        out = e2e.filter_rows_to_window(rows, "date_local", D(2026, 5, 18), D(2026, 5, 31))
        self.assertEqual([r["x"] for r in out], [2, 3, 4])

    def test_none_date_field_keeps_all(self):
        rows = [{"employee_id": "1"}, {"employee_id": "2"}]
        self.assertEqual(e2e.filter_rows_to_window(rows, None, D(2026, 5, 18), D(2026, 5, 31)), rows)


class _FakeRawReader:
    """Stand-in for reader.py: every read_raw_* returns canned, mixed-window rows."""
    def _square(self, sid, *, account):
        return [
            {"date_local": "2026-05-10", "v": "old"},   # out of window
            {"date_local": "2026-05-20", "v": "in"},    # in window
        ]
    read_raw_square_transactions = _square
    read_raw_square_daily_rollup = _square
    read_raw_square_item_lines = _square
    read_raw_square_item_daily_rollup = _square
    read_raw_kds_daily = _square

    def _adp_dated(self, sid, *, account):
        return [
            {"date": "2026-05-12", "employee_id": "1"},  # out
            {"date": "2026-05-19", "employee_id": "1"},  # in
        ]
    read_raw_adp_shifts = _adp_dated
    read_raw_adp_punches = _adp_dated

    def read_raw_adp_rates(self, sid, *, account):
        return [{"employee_id": "1"}, {"employee_id": "2"}]  # no date -> all kept


class _FakeRawWriter:
    def __init__(self):
        self.writes = []  # (writer_name, sid, rows)
    def _mk(self, name):
        def w(sid, rows, *, account, scraped_at_utc=None):
            self.writes.append((name, sid, rows))
            return {"total_after": len(rows)}
        return w
    def __getattr__(self, name):
        if name.startswith("write_raw_"):
            return self._mk(name)
        raise AttributeError(name)


class TestSeedSandboxRawFromProd(unittest.TestCase):
    PROFILE = {"google_sheets": {
        "bhaga_adp_raw": {"spreadsheet_id": "PROD_ADP"},
        "bhaga_square_raw": {"spreadsheet_id": "PROD_SQ"},
    }}

    def _patches(self, writer, *, sandbox_map=None):
        sandbox_map = sandbox_map or {"bhaga_adp_raw": "SBX_ADP", "bhaga_square_raw": "SBX_SQ"}
        return [
            mock.patch.object(e2e, "raw_reader", _FakeRawReader()),
            mock.patch.object(e2e, "raw_writer", writer),
            mock.patch.object(e2e, "_load_production_sheet_ids", lambda: {"PROD_ADP", "PROD_SQ"}),
            mock.patch.object(e2e, "resolve_sheet_id", lambda key, prof: sandbox_map[key]),
        ]

    def test_window_filter_and_writes_to_sandbox(self):
        writer = _FakeRawWriter()
        patches = self._patches(writer)
        for p in patches:
            p.start()
        try:
            seeded = e2e.seed_sandbox_raw_from_prod(
                profile=self.PROFILE, account="palmetto",
                start=D(2026, 5, 18), end=D(2026, 5, 31),
            )
        finally:
            for p in patches:
                p.stop()
        # dated tabs keep only the in-window row; rates (no date) keep both.
        self.assertEqual(seeded["square_transactions"], 1)
        self.assertEqual(seeded["adp_shifts"], 1)
        self.assertEqual(seeded["adp_rates"], 2)
        # every write targeted a sandbox sid, never a prod sid.
        for _name, sid, _rows in writer.writes:
            self.assertIn(sid, {"SBX_ADP", "SBX_SQ"})
            self.assertNotIn(sid, {"PROD_ADP", "PROD_SQ"})

    def test_refuses_to_write_production_sheet(self):
        # resolve_sheet_id mistakenly returns a prod sid -> hard isolation failure.
        writer = _FakeRawWriter()
        patches = self._patches(writer, sandbox_map={
            "bhaga_adp_raw": "PROD_ADP", "bhaga_square_raw": "SBX_SQ"})
        for p in patches:
            p.start()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                e2e.seed_sandbox_raw_from_prod(
                    profile=self.PROFILE, account="palmetto",
                    start=D(2026, 5, 18), end=D(2026, 5, 31),
                )
            self.assertIn("refusing to WRITE production sheet", str(ctx.exception))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(writer.writes, [])  # nothing written

    def test_refuses_non_prod_read_source(self):
        # The read SID must be a known production sheet — refuse to seed from a
        # non-prod source (the other half of the isolation invariant).
        writer = _FakeRawWriter()
        non_prod_profile = {"google_sheets": {
            "bhaga_adp_raw": {"spreadsheet_id": "NOT_PROD_ADP"},
            "bhaga_square_raw": {"spreadsheet_id": "NOT_PROD_SQ"},
        }}
        patches = self._patches(writer)
        for p in patches:
            p.start()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                e2e.seed_sandbox_raw_from_prod(
                    profile=non_prod_profile, account="palmetto",
                    start=D(2026, 5, 18), end=D(2026, 5, 31),
                )
            self.assertIn("non-prod source", str(ctx.exception))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(writer.writes, [])  # nothing written


class _FakeOverlayWriter:
    """Stand-in for writer.py covering the training_shifts overlay path.

    ``_read_tab`` returns a canned prod overlay grid (header + mixed-window
    rows); ``write_training_shifts`` records the (sid, rows) it was handed.
    """
    def __init__(self, grid):
        self._grid = grid
        self.writes = []  # (sid, rows)

    def _read_tab(self, sid, tab, token):
        return self._grid

    def write_training_shifts(self, sid, rows, *, account):
        self.writes.append((sid, rows))
        return {"total_after": len(rows)}


class TestSeedSandboxTrainingShiftsFromProd(unittest.TestCase):
    PROFILE = {"google_sheets": {"bhaga_model": {"spreadsheet_id": "PROD_MODEL"}}}
    GRID = [
        ["employee_name", "date", "note"],
        ["Flores, Juan", "2026-05-18", "training"],     # in window
        ["Ortiz, Ximena", "2026-05-29", "training"],    # in window
        ["Padron, Lisette", "2026-05-23", "training"],  # in window
        ["Urrutia, Emely", "2026-05-23", "training"],   # in window
        ["Someone, Old", "2026-05-10", "training"],     # BEFORE window -> dropped
        ["Someone, New", "2026-06-05", "training"],     # AFTER window -> dropped
        ["", "", ""],                                   # blank -> skipped
    ]

    def _patches(self, writer, *, sandbox_sid="SBX_MODEL"):
        return [
            mock.patch.object(e2e, "raw_writer", writer),
            mock.patch.object(e2e, "refresh_access_token", lambda account=None: "tok"),
            mock.patch.object(e2e, "_load_production_sheet_ids", lambda: {"PROD_MODEL"}),
            mock.patch.object(e2e, "resolve_sheet_id", lambda key, prof: sandbox_sid),
        ]

    def test_mirrors_in_window_rows_to_sandbox(self):
        writer = _FakeOverlayWriter(self.GRID)
        patches = self._patches(writer)
        for p in patches:
            p.start()
        try:
            recs = e2e.seed_sandbox_training_shifts_from_prod(
                profile=self.PROFILE, account="palmetto",
                start=D(2026, 5, 18), end=D(2026, 5, 31),
            )
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(len(recs), 4)  # Juan, Ximena, Lisette, Emely — out-of-window dropped
        self.assertEqual(len(writer.writes), 1)
        sid, rows = writer.writes[0]
        self.assertEqual(sid, "SBX_MODEL")
        names = {r["employee_name"] for r in rows}
        self.assertEqual(names, {"Flores, Juan", "Ortiz, Ximena",
                                 "Padron, Lisette", "Urrutia, Emely"})
        self.assertTrue(all(D(2026, 5, 18).isoformat() <= r["date"]
                            <= D(2026, 5, 31).isoformat() for r in rows))

    def test_refuses_to_write_production_model(self):
        writer = _FakeOverlayWriter(self.GRID)
        patches = self._patches(writer, sandbox_sid="PROD_MODEL")  # mis-resolve to prod
        for p in patches:
            p.start()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                e2e.seed_sandbox_training_shifts_from_prod(
                    profile=self.PROFILE, account="palmetto",
                    start=D(2026, 5, 18), end=D(2026, 5, 31),
                )
            self.assertIn("refusing to WRITE production sheet", str(ctx.exception))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(writer.writes, [])

    def test_refuses_non_prod_read_source(self):
        writer = _FakeOverlayWriter(self.GRID)
        non_prod = {"google_sheets": {"bhaga_model": {"spreadsheet_id": "NOT_PROD"}}}
        patches = self._patches(writer)
        for p in patches:
            p.start()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                e2e.seed_sandbox_training_shifts_from_prod(
                    profile=non_prod, account="palmetto",
                    start=D(2026, 5, 18), end=D(2026, 5, 31),
                )
            self.assertIn("non-prod source", str(ctx.exception))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(writer.writes, [])

    def test_empty_overlay_writes_nothing(self):
        writer = _FakeOverlayWriter([["employee_name", "date", "note"]])  # header only
        patches = self._patches(writer)
        for p in patches:
            p.start()
        try:
            recs = e2e.seed_sandbox_training_shifts_from_prod(
                profile=self.PROFILE, account="palmetto",
                start=D(2026, 5, 18), end=D(2026, 5, 31),
            )
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(recs, [])
        self.assertEqual(writer.writes, [])


class TestTipPoolConservation(unittest.TestCase):
    # Real model-builder schema: date .. day_pool .. our_share.
    HEADER = ["date", "dow", "period_start", "period_end", "employee",
              "hours_worked", "day_pool", "team_hours_eligible",
              "pct_of_day_hours", "our_share"]

    def _row(self, date, emp, hours, day_pool, our_share):
        return [date, "Tue", "2026-05-18", "2026-05-31", emp,
                hours, day_pool, "8", "50", our_share]

    def test_conserved_passes(self):
        grid = [self.HEADER,
                self._row("2026-05-20", "A", "5", "100.00", "60.00"),
                self._row("2026-05-20", "B", "3", "100.00", "40.00"),
                self._row("2026-05-21", "A", "4", "30.00", "30.00")]
        res = e2e.assert_tip_pool_conserved(grid)
        self.assertEqual(res["dates_checked"], 2)
        self.assertEqual(res["max_residual_cents"], 0)

    def test_leak_raises(self):
        grid = [self.HEADER,
                self._row("2026-05-20", "A", "5", "100.00", "60.00"),
                self._row("2026-05-20", "B", "3", "100.00", "30.00")]  # 90 != 100
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_tip_pool_conserved(grid)
        self.assertIn("NOT conserved", str(ctx.exception))

    def test_inconsistent_day_pool_raises(self):
        # Same date, two different day_pool values -> builder bug, surface it.
        grid = [self.HEADER,
                self._row("2026-05-20", "A", "5", "100.00", "60.00"),
                self._row("2026-05-20", "B", "3", "120.00", "40.00")]  # pool disagrees
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_tip_pool_conserved(grid)
        self.assertIn("inconsistent day_pool", str(ctx.exception))

    def test_short_row_raises_not_silently_passes(self):
        # A truncated row must fail loudly, not default pool/alloc to 0 and pass.
        grid = [self.HEADER,
                ["2026-05-20", "Tue", "2026-05-18", "2026-05-31", "A"]]  # missing day_pool/our_share
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_tip_pool_conserved(grid)
        self.assertIn("too short", str(ctx.exception))

    def test_all_rows_skipped_raises(self):
        # Header present + a non-header row, but the date column is blank for
        # every row -> pool_by_date empty. Must fail, not return dates_checked=0.
        grid = [self.HEADER,
                ["", "Tue", "2026-05-18", "2026-05-31", "A",
                 "5", "50.00", "8", "100", "50.00"]]
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_tip_pool_conserved(grid)
        self.assertIn("no parseable date rows", str(ctx.exception))

    def test_legacy_header_fallback(self):
        # Fallback column names keep the check alive across a header rename.
        legacy = ["date_local", "employee_name", "tip_pool_dollars", "tip_allocation_dollars"]
        grid = [legacy,
                ["2026-05-20", "A", "100.00", "60.00"],
                ["2026-05-20", "B", "100.00", "40.00"]]
        res = e2e.assert_tip_pool_conserved(grid)
        self.assertEqual(res["dates_checked"], 1)


class TestExemptionsApplied(unittest.TestCase):
    DHEADER = ["date", "dow", "period_start", "period_end", "employee",
               "hours_worked", "day_pool", "team_hours_eligible",
               "pct_of_day_hours", "our_share"]
    PHEADER = ["period_start", "period_end", "coverage", "is_open", "employee",
               "hours_worked", "our_calc", "adp_paid", "diff", "diff_pct",
               "our_per_hour", "adp_per_hour", "likely_reason"]

    def _d(self, date, emp, pool, share):
        return [date, "Fri", "2026-05-18", "2026-05-31", emp,
                "5", pool, "10", "50%", share]

    def _p(self, emp, hours, our_calc):
        return ["2026-05-18", "2026-05-31", "full", "no", emp,
                hours, our_calc, "N/A", "N/A", "N/A", "0", "N/A", ""]

    def _scenario(self):
        # The model OMITS exempt shifts from tip_alloc_daily entirely.
        # 5/23: pool $100 — Lisette (whole-period exempt; only works 5/23) and
        # Emely (partial: also works 5/24) are exempt, so neither appears; the
        # full $100 redistributes to non-exempt Bob.
        # 5/24: pool $80 — Emely (not exempt that day) + Bob split it.
        daily = [self.DHEADER,
                 self._d("2026-05-23", "Bob", "100.00", "100.00"),
                 self._d("2026-05-24", "Urrutia, Emely", "80.00", "40.00"),
                 self._d("2026-05-24", "Bob", "80.00", "40.00")]
        # Lisette is absent from the period; Emely $40 (hours 5 = only 5/24),
        # Bob $140 (hours 9). total our_calc 180 == 100+80 pool.
        period = [self.PHEADER,
                  self._p("Urrutia, Emely", "5", "40.00"),
                  self._p("Bob", "9", "140.00")]
        exempt = {("Padron, Lisette", "2026-05-23"), ("Urrutia, Emely", "2026-05-23")}
        worked = {
            ("Padron, Lisette", "2026-05-23"): 8.0,
            ("Urrutia, Emely", "2026-05-23"): 8.0,
            ("Bob", "2026-05-23"): 4.0,
            ("Urrutia, Emely", "2026-05-24"): 5.0,
            ("Bob", "2026-05-24"): 5.0,
        }
        return daily, period, exempt, worked

    def test_happy_path_classifies_and_conserves(self):
        daily, period, exempt, worked = self._scenario()
        res = e2e.assert_exemptions_applied(daily, period, exempt, worked)
        self.assertEqual(res["worked_exempt_shifts_dropped"], 2)
        self.assertEqual(res["whole_period_exempt"], ["Padron, Lisette"])
        self.assertEqual(res["partial_exempt"], ["Urrutia, Emely"])
        self.assertEqual(res["exempt_days_redistributed"], ["2026-05-23"])
        self.assertEqual(res["period_our_calc_cents"], 18000)
        self.assertEqual(res["period_pool_cents"], 18000)

    def test_exempt_shift_with_nonzero_share_raises(self):
        daily, period, exempt, worked = self._scenario()
        # Lisette wrongly receives a share on her exempt day.
        daily.append(self._d("2026-05-23", "Padron, Lisette", "100.00", "10.00"))
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_exemptions_applied(daily, period, exempt, worked)
        self.assertIn("received our_share", str(ctx.exception))

    def test_whole_period_exempt_nonzero_our_calc_raises(self):
        daily, period, exempt, worked = self._scenario()
        period.append(self._p("Padron, Lisette", "8", "5.00"))  # must be 0/absent
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_exemptions_applied(daily, period, exempt, worked)
        self.assertIn("exempt for every worked day", str(ctx.exception))

    def test_partial_exempt_zero_our_calc_raises(self):
        daily, period, exempt, worked = self._scenario()
        period[1][6] = "0.00"  # Emely our_calc -> 0 though she worked 5/24
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_exemptions_applied(daily, period, exempt, worked)
        self.assertIn("worked non-exempt days too", str(ctx.exception))

    def test_partial_hours_not_dropped_raises(self):
        daily, period, exempt, worked = self._scenario()
        period[1][5] = "13"  # Emely hours include the exempt 5/23 shift (5+8)
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_exemptions_applied(daily, period, exempt, worked)
        self.assertIn("not dropped from the denominator", str(ctx.exception))

    def test_redistribution_leak_raises(self):
        daily, period, exempt, worked = self._scenario()
        daily[1][-1] = "90.00"  # Bob 5/23 share drops -> pool not fully distributed
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_exemptions_applied(daily, period, exempt, worked)
        self.assertIn("redistribution leak", str(ctx.exception))

    def test_period_not_conserved_raises(self):
        daily, period, exempt, worked = self._scenario()
        period[2][6] = "150.00"  # Bob 140 -> 150, total 190 != 180
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_exemptions_applied(daily, period, exempt, worked)
        self.assertIn("period not conserved", str(ctx.exception))

    def test_no_provable_effect_raises(self):
        # Exempt pair never worked (not in worked_hours) -> can't prove the
        # overlay dropped a real shift. Fail loudly (broken mirror/ADP seed).
        daily, period, _, worked = self._scenario()
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_exemptions_applied(
                daily, period, {("Ghost, Nobody", "2026-05-23")}, worked)
        self.assertIn("no provable effect", str(ctx.exception))


class TestProdRawAdpReconciliationCadence(unittest.TestCase):
    """The prod-raw verify path is CADENCE-SAFE: it requires adp_paid only for a
    closed period that has actually been paid (a covering GCS Earnings export
    carries its CC-tip lines), and skips otherwise instead of failing. Proven via
    the integration tests below; here we assert the cadence helper wiring."""

    def test_period_has_cc_tip_actuals_true_when_key_present(self):
        from agents.bhaga.scripts import update_model_sheet as ums
        with mock.patch.object(ums, "load_cc_tips_earnings_from_gcs",
                               return_value=[{"x": 1}]), \
             mock.patch.object(ums, "actual_cc_tips_by_period",
                               return_value={("2026-05-18", "2026-05-31"): {"A": 5000}}):
            self.assertTrue(ums.period_has_cc_tip_actuals(
                store="palmetto", period_start="2026-05-18",
                period_end="2026-05-31", last_data_date="2026-05-31"))

    def test_period_has_cc_tip_actuals_false_when_absent(self):
        from agents.bhaga.scripts import update_model_sheet as ums
        with mock.patch.object(ums, "load_cc_tips_earnings_from_gcs",
                               return_value=[{"x": 1}]), \
             mock.patch.object(ums, "actual_cc_tips_by_period", return_value={}):
            self.assertFalse(ums.period_has_cc_tip_actuals(
                store="palmetto", period_start="2026-05-18",
                period_end="2026-05-31", last_data_date="2026-05-31"))


class TestInvokeMain(unittest.TestCase):
    def test_invoke_main_sets_argv_and_restores(self):
        captured = {}

        def fake_main():
            captured["argv"] = list(sys.argv)
            return 0

        before = list(sys.argv)
        rc = e2e._invoke_main(fake_main, ["--store", "palmetto"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["argv"], ["sandbox_e2e", "--store", "palmetto"])
        self.assertEqual(sys.argv, before)

    def test_invoke_main_none_return_becomes_zero(self):
        self.assertEqual(e2e._invoke_main(lambda: None, []), 0)


class TestRunE2E(unittest.TestCase):
    def _common_mocks(self):
        from agents.bhaga.scripts import sandbox_provision as sp
        ids = {k: f"id_{k}" for k in sp.PROFILE_KEYS}
        return [
            mock.patch.object(e2e.sandbox_provision, "provision",
                              lambda **k: {"ids": ids, "seed_counts": {"employees_rows": 12}}),
            mock.patch.object(e2e.sandbox_provision, "_load_pointer",
                              lambda store: {"google_account_key": "palmetto"}),
            mock.patch.object(e2e, "refresh_access_token", lambda account=None: "tok"),
            mock.patch.object(e2e, "_apply_staging_env", mock.Mock()),
            mock.patch.object(e2e, "_replay_from_gcs", lambda s, en: {"2026-05-01": 4}),
            mock.patch.object(e2e, "_run_backfill", mock.Mock(return_value=0)),
            mock.patch.object(e2e, "_maybe_run_item_lines", lambda store: False),
            mock.patch.object(e2e, "_run_model_build", mock.Mock(return_value=0)),
        ]

    def test_happy_path_status_ok_and_teardown_called(self):
        teardown = mock.Mock(return_value={"deleted": ["x"]})
        counts = {t: 1 for t in e2e.MODEL_VERIFY_MIN_ROWS}
        patches = self._common_mocks() + [
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s, tabs=None: counts),
            mock.patch.object(e2e.sandbox_provision, "teardown", teardown),
        ]
        for p in patches:
            p.start()
        try:
            report = e2e.run_e2e(store="palmetto", pr_number=7, start=D(2026, 5, 1), end=D(2026, 5, 1))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["model_tab_counts"], counts)
        teardown.assert_called_once()

    def test_teardown_runs_even_when_verification_fails(self):
        teardown = mock.Mock(return_value={"deleted": []})
        under = {t: 0 for t in e2e.MODEL_VERIFY_MIN_ROWS}  # under-populated -> assertion raises
        patches = self._common_mocks() + [
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s, tabs=None: under),
            mock.patch.object(e2e.sandbox_provision, "teardown", teardown),
        ]
        for p in patches:
            p.start()
        try:
            with self.assertRaises(RuntimeError):
                e2e.run_e2e(store="palmetto", pr_number=7, start=D(2026, 5, 1), end=D(2026, 5, 1))
        finally:
            for p in patches:
                p.stop()
        teardown.assert_called_once()  # finally guarantees cleanup

    def test_prod_raw_source_seeds_and_checks_conservation(self):
        from agents.bhaga.scripts import sandbox_provision as sp
        ids = {k: f"id_{k}" for k in sp.PROFILE_KEYS}
        counts = {t: 1 for t in e2e.PROD_RAW_VERIFY_MIN_ROWS}
        # Canonical tip_alloc_daily schema (the primary _col() path that fires
        # against the real model sheet), not the legacy fallback names.
        balanced = [
            ["date", "dow", "period_start", "period_end", "employee",
             "hours_worked", "day_pool", "team_hours_eligible",
             "pct_of_day_hours", "our_share"],
            ["2026-05-20", "Tue", "2026-05-18", "2026-05-31", "A",
             "5", "50.00", "8", "100", "50.00"],
        ]
        period_grid = [
            ["period_start", "period_end", "coverage", "is_open", "employee",
             "hours_worked", "our_calc", "adp_paid", "diff", "diff_pct",
             "our_per_hour", "adp_per_hour", "likely_reason"],
            ["2026-05-18", "2026-05-31", "full", "no", "A",
             "5", "50.00", "50.00", "0", "0%", "10", "10", ""],
        ]
        grids = {"tip_alloc_daily": balanced, "tip_alloc_period": period_grid}
        teardown = mock.Mock(return_value={"deleted": []})
        seed = mock.Mock(return_value={"adp_shifts": 10, "square_transactions": 200})
        # No exempt shifts in this integration fixture; the exemption logic has
        # its own dedicated unit tests (TestExemptionsApplied).
        seed_ts = mock.Mock(return_value=[])
        patches = [
            mock.patch.object(e2e.sandbox_provision, "provision",
                              lambda **k: {"ids": ids, "seed_counts": {}}),
            mock.patch.object(e2e.sandbox_provision, "_load_pointer",
                              lambda store: {"google_account_key": "palmetto"}),
            mock.patch.object(e2e, "refresh_access_token", lambda account=None: "tok"),
            mock.patch.object(e2e, "_apply_staging_env", mock.Mock()),
            mock.patch.object(e2e, "seed_sandbox_raw_from_prod", seed),
            mock.patch.object(e2e, "seed_sandbox_training_shifts_from_prod", seed_ts),
            mock.patch.object(e2e, "_run_model_build", mock.Mock(return_value=0)),
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s, tabs=None: counts),
            mock.patch.object(e2e, "_read_model_grids", lambda t, s, tabs: grids),
            mock.patch.object(e2e, "_read_worked_hours",
                              lambda sid, *, account, start, end: {}),
            mock.patch.object(e2e.sandbox_provision, "teardown", teardown),
            # The seeded closed period HAS been paid (covering Earnings export
            # carries CC-tip lines), so reconciliation is required + must be alive.
            mock.patch.object(e2e.update_model_sheet, "period_has_cc_tip_actuals",
                              lambda **k: True),
        ]
        for p in patches:
            p.start()
        try:
            report = e2e.run_e2e(store="palmetto", pr_number=7, start=D(2026, 5, 18),
                                 end=D(2026, 5, 31), source="prod-raw")
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["source"], "prod-raw")
        seed.assert_called_once()
        seed_ts.assert_called_once()
        self.assertEqual(report["seeded_rows"], {"adp_shifts": 10, "square_transactions": 200})
        self.assertEqual(report["seeded_training_shifts"], 0)
        self.assertEqual(report["tip_pool_conservation"]["dates_checked"], 1)
        self.assertEqual(report["exemptions"]["period_our_calc_cents"], 5000)
        self.assertEqual(report["exemptions"]["period_pool_cents"], 5000)
        # The revived adp_paid reconciliation must be asserted alive in CI for a
        # PAID closed period (cadence gate satisfied).
        self.assertEqual(report["adp_reconciliation"]["period"], "2026-05-31")
        self.assertEqual(report["adp_reconciliation"]["rows_reconciled"], 1)

    def test_prod_raw_adp_reconciliation_cadence_skips_when_unpaid(self):
        # A just-closed period whose payroll hasn't run yet (no covering Earnings
        # export with CC-tip lines) legitimately shows adp_paid=N/A: the verify
        # path must SKIP reconciliation, not fail (the real 5/18-5/31 case).
        from agents.bhaga.scripts import sandbox_provision as sp
        ids = {k: f"id_{k}" for k in sp.PROFILE_KEYS}
        counts = {t: 1 for t in e2e.PROD_RAW_VERIFY_MIN_ROWS}
        balanced = [
            ["date", "dow", "period_start", "period_end", "employee",
             "hours_worked", "day_pool", "team_hours_eligible",
             "pct_of_day_hours", "our_share"],
            ["2026-05-20", "Tue", "2026-05-18", "2026-05-31", "A",
             "5", "50.00", "8", "100", "50.00"],
        ]
        # adp_paid is N/A here — and that is CORRECT for an unpaid period.
        period_grid = [
            ["period_start", "period_end", "coverage", "is_open", "employee",
             "hours_worked", "our_calc", "adp_paid", "diff", "diff_pct",
             "our_per_hour", "adp_per_hour", "likely_reason"],
            ["2026-05-18", "2026-05-31", "full", "no", "A",
             "5", "50.00", "N/A", "N/A", "N/A", "10", "", "No ADP earnings export"],
        ]
        grids = {"tip_alloc_daily": balanced, "tip_alloc_period": period_grid}
        patches = [
            mock.patch.object(e2e.sandbox_provision, "provision",
                              lambda **k: {"ids": ids, "seed_counts": {}}),
            mock.patch.object(e2e.sandbox_provision, "_load_pointer",
                              lambda store: {"google_account_key": "palmetto"}),
            mock.patch.object(e2e, "refresh_access_token", lambda account=None: "tok"),
            mock.patch.object(e2e, "_apply_staging_env", mock.Mock()),
            mock.patch.object(e2e, "seed_sandbox_raw_from_prod",
                              mock.Mock(return_value={"adp_shifts": 10})),
            mock.patch.object(e2e, "seed_sandbox_training_shifts_from_prod",
                              mock.Mock(return_value=[])),
            mock.patch.object(e2e, "_run_model_build", mock.Mock(return_value=0)),
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s, tabs=None: counts),
            mock.patch.object(e2e, "_read_model_grids", lambda t, s, tabs: grids),
            mock.patch.object(e2e, "_read_worked_hours",
                              lambda sid, *, account, start, end: {}),
            mock.patch.object(e2e.sandbox_provision, "teardown",
                              mock.Mock(return_value={"deleted": []})),
            mock.patch.object(e2e.update_model_sheet, "period_has_cc_tip_actuals",
                              lambda **k: False),
        ]
        for p in patches:
            p.start()
        try:
            report = e2e.run_e2e(store="palmetto", pr_number=7, start=D(2026, 5, 18),
                                 end=D(2026, 5, 31), source="prod-raw")
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["adp_reconciliation"]["status"], "cadence-skip")

    def test_evidence_file_written_on_success_and_failure(self):
        # run_e2e writes the evidence file in its finally so it exists on BOTH
        # paths — critical because the failure path re-raises (a write in main
        # would be skipped) and that's exactly when the diagnostic matters.
        import tempfile
        teardown = mock.Mock(return_value={"deleted": []})

        ok_counts = {t: 1 for t in e2e.MODEL_VERIFY_MIN_ROWS}
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as tf:
            ev_ok = tf.name
        patches = self._common_mocks() + [
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s, tabs=None: ok_counts),
            mock.patch.object(e2e.sandbox_provision, "teardown", teardown),
        ]
        for p in patches:
            p.start()
        try:
            e2e.run_e2e(store="palmetto", pr_number=7, start=D(2026, 5, 1),
                        end=D(2026, 5, 1), evidence_file=ev_ok)
        finally:
            for p in patches:
                p.stop()
        with open(ev_ok) as f:
            self.assertIn("PR #7", f.read())
        os.unlink(ev_ok)

        under = {t: 0 for t in e2e.MODEL_VERIFY_MIN_ROWS}  # forces an assertion failure
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as tf:
            ev_fail = tf.name
        patches = self._common_mocks() + [
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s, tabs=None: under),
            mock.patch.object(e2e.sandbox_provision, "teardown", teardown),
        ]
        for p in patches:
            p.start()
        try:
            with self.assertRaises(RuntimeError):
                e2e.run_e2e(store="palmetto", pr_number=7, start=D(2026, 5, 1),
                            end=D(2026, 5, 1), evidence_file=ev_fail)
        finally:
            for p in patches:
                p.stop()
        with open(ev_fail) as f:
            body = f.read()
        self.assertIn("PR #7", body)
        self.assertIn("error:", body)  # the diagnostic line survives the re-raise
        os.unlink(ev_fail)

    def test_keep_skips_teardown(self):
        teardown = mock.Mock()
        counts = {t: 1 for t in e2e.MODEL_VERIFY_MIN_ROWS}
        patches = self._common_mocks() + [
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s, tabs=None: counts),
            mock.patch.object(e2e.sandbox_provision, "teardown", teardown),
        ]
        for p in patches:
            p.start()
        try:
            report = e2e.run_e2e(store="palmetto", pr_number=7, start=D(2026, 5, 1),
                                 end=D(2026, 5, 1), teardown_after=False)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(report["status"], "ok")
        teardown.assert_not_called()


class TestMainCli(unittest.TestCase):
    def test_main_passes_evidence_file_through_to_run_e2e(self):
        # main no longer writes the evidence file itself — run_e2e owns the write
        # (in its finally, so it survives the failure re-raise). main's only job
        # is to thread --evidence-file through.
        captured = {}
        report = {"status": "ok", "pr_number": 3, "days": 1,
                  "window": {"start": "2026-05-01", "end": "2026-05-01"}}

        def fake_run(**kw):
            captured.update(kw)
            return report

        with mock.patch.object(e2e, "run_e2e", fake_run):
            rc = e2e.main(["--pr-number", "3", "--start", "2026-05-01",
                           "--end", "2026-05-01", "--evidence-file", "/tmp/ev.md"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["evidence_file"], "/tmp/ev.md")

    def test_main_returns_one_on_error_status(self):
        report = {"status": "error", "pr_number": 3, "days": 1,
                  "window": {"start": "2026-05-01", "end": "2026-05-01"}, "error": "x"}
        with mock.patch.object(e2e, "run_e2e", lambda **k: report):
            rc = e2e.main(["--pr-number", "3", "--start", "2026-05-01", "--end", "2026-05-01"])
        self.assertEqual(rc, 1)

    def test_main_auto_window_selects_from_gcs(self):
        captured = {}
        report = {"status": "ok", "pr_number": 3, "days": 1,
                  "window": {"start": "2026-05-04", "end": "2026-05-05"}}

        def fake_run(**kwargs):
            captured.update(kwargs)
            return report

        with mock.patch.object(e2e.gcs_cache, "list_cached_dates",
                               lambda: [D(2026, 5, 4), D(2026, 5, 5)]), \
             mock.patch.object(e2e, "run_e2e", fake_run):
            rc = e2e.main(["--pr-number", "3", "--auto-window", "--max-days", "2"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["start"], D(2026, 5, 4))
        self.assertEqual(captured["end"], D(2026, 5, 5))

    def test_main_requires_window(self):
        with self.assertRaises(SystemExit):
            e2e.main(["--pr-number", "3"])

    def test_main_period_last_closed_resolves_from_anchor(self):
        captured = {}
        report = {"status": "ok", "pr_number": 3, "days": 14,
                  "window": {"start": "2026-05-18", "end": "2026-05-31"}}

        def fake_run(**kwargs):
            captured.update(kwargs)
            return report

        profile = {"adp_run": {"pay_periods_anchor_end_date": "2026-05-17",
                               "pay_frequency": "Biweekly"}}
        with mock.patch.object(e2e.sandbox_provision, "_load_pointer", lambda store: profile), \
             mock.patch.object(e2e, "run_e2e", fake_run), \
             mock.patch("agents.bhaga.scripts.sandbox_e2e.datetime") as mdt:
            mdt.datetime.now.return_value = datetime.datetime(2026, 6, 2, 12, 0)
            mdt.date = datetime.date
            rc = e2e.main(["--pr-number", "3", "--source", "prod-raw", "--period", "last-closed"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["start"], D(2026, 5, 18))
        self.assertEqual(captured["end"], D(2026, 5, 31))
        self.assertEqual(captured["source"], "prod-raw")


class TestNoOtpStructuralGuarantee(unittest.TestCase):
    def test_no_scrape_module_in_import_graph(self):
        """Import the runner in a FRESH interpreter and assert no scrape /
        login / browser module is reachable. This is the structural proof that
        the e2e can never trigger an OTP or a portal login."""
        repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
        code = (
            "import sys; "
            "import agents.bhaga.scripts.sandbox_e2e as m; "
            "bad=[f for f in m.FORBIDDEN_MODULES if f in sys.modules]; "
            "print('BAD:'+repr(bad)); "
            "sys.exit(1 if bad else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.abspath(repo_root),
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0,
                         f"scrape module leaked into runner import graph:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("BAD:[]", proc.stdout)


if __name__ == "__main__":
    unittest.main()
