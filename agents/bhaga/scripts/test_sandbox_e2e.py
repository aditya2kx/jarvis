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


class TestTipPoolConservation(unittest.TestCase):
    HEADER = ["date_local", "employee_id", "employee_name", "hours_worked",
              "share_of_day_hours_pct", "tip_pool_dollars", "tip_allocation_dollars"]

    def test_conserved_passes(self):
        grid = [self.HEADER,
                ["2026-05-20", "1", "A", "5", "50", "100.00", "60.00"],
                ["2026-05-20", "2", "B", "3", "50", "100.00", "40.00"],
                ["2026-05-21", "1", "A", "4", "100", "30.00", "30.00"]]
        res = e2e.assert_tip_pool_conserved(grid)
        self.assertEqual(res["dates_checked"], 2)
        self.assertEqual(res["max_residual_cents"], 0)

    def test_leak_raises(self):
        grid = [self.HEADER,
                ["2026-05-20", "1", "A", "5", "50", "100.00", "60.00"],
                ["2026-05-20", "2", "B", "3", "50", "100.00", "30.00"]]  # 90 != 100
        with self.assertRaises(RuntimeError) as ctx:
            e2e.assert_tip_pool_conserved(grid)
        self.assertIn("NOT conserved", str(ctx.exception))


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
        balanced = [
            ["date_local", "employee_id", "employee_name", "hours_worked",
             "share_of_day_hours_pct", "tip_pool_dollars", "tip_allocation_dollars"],
            ["2026-05-20", "1", "A", "5", "100", "50.00", "50.00"],
        ]
        teardown = mock.Mock(return_value={"deleted": []})
        seed = mock.Mock(return_value={"adp_shifts": 10, "square_transactions": 200})
        patches = [
            mock.patch.object(e2e.sandbox_provision, "provision",
                              lambda **k: {"ids": ids, "seed_counts": {}}),
            mock.patch.object(e2e.sandbox_provision, "_load_pointer",
                              lambda store: {"google_account_key": "palmetto"}),
            mock.patch.object(e2e, "refresh_access_token", lambda account=None: "tok"),
            mock.patch.object(e2e, "_apply_staging_env", mock.Mock()),
            mock.patch.object(e2e, "seed_sandbox_raw_from_prod", seed),
            mock.patch.object(e2e, "_run_model_build", mock.Mock(return_value=0)),
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s, tabs=None: counts),
            mock.patch.object(e2e, "_read_model_grid", lambda t, s, tab: balanced),
            mock.patch.object(e2e.sandbox_provision, "teardown", teardown),
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
        self.assertEqual(report["seeded_rows"], {"adp_shifts": 10, "square_transactions": 200})
        self.assertEqual(report["tip_pool_conservation"]["dates_checked"], 1)

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
    def test_main_writes_evidence_and_returns_zero_on_ok(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as tf:
            ev = tf.name
        try:
            report = {"status": "ok", "pr_number": 3, "days": 1,
                      "window": {"start": "2026-05-01", "end": "2026-05-01"}}
            with mock.patch.object(e2e, "run_e2e", lambda **k: report):
                rc = e2e.main(["--pr-number", "3", "--start", "2026-05-01",
                               "--end", "2026-05-01", "--evidence-file", ev])
            self.assertEqual(rc, 0)
            with open(ev) as f:
                self.assertIn("PR #3", f.read())
        finally:
            os.unlink(ev)

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
