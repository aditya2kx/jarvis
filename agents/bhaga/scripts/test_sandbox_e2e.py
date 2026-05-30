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

    def test_item_lines_module_absent_on_main(self):
        # backfill_item_lines_from_cache is not on main (in-flight elsewhere).
        self.assertFalse(e2e._item_lines_module_available())

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
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s: counts),
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
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s: under),
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

    def test_keep_skips_teardown(self):
        teardown = mock.Mock()
        counts = {t: 1 for t in e2e.MODEL_VERIFY_MIN_ROWS}
        patches = self._common_mocks() + [
            mock.patch.object(e2e, "_read_model_tab_counts", lambda t, s: counts),
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
