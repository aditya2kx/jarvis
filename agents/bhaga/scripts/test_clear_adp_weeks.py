"""Unit tests for clear_adp_weeks — the deletes it builds and what it refuses; no BigQuery, no Cloud Run."""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import unittest
from unittest import mock

from agents.bhaga.scripts import adp_schedule_write as w
from agents.bhaga.scripts import clear_adp_weeks as C

D9, D10 = dt.date(2026, 11, 9), dt.date(2026, 11, 10)
ASSIGNED = [
    {"date": D10, "employee": "Kim, Dee", "shift_ranges_json": json.dumps(["8:30 AM - 1:00 PM"])},
    {"date": D9, "employee": "Lee, Ana", "shift_ranges_json": json.dumps(["7:00 AM - 1:00 PM DRAFT", "4:00 PM - 8:30 PM"])},
    {"date": D9, "employee": "Roe, Bo", "shift_ranges_json": "not json"},
]
OPEN = [{"date": D9, "shift_range": "2:00 PM - 8:00 PM"}]


class ClearRowsTests(unittest.TestCase):
    def test_every_assigned_and_open_shift_becomes_a_delete(self):
        rows, published = C.clear_rows(ASSIGNED, OPEN)
        self.assertEqual(
            [(r["kind"], r["date"], r["start_min"], r["end_min"], r["employee"]) for r in rows],
            [("shift-delete", D9, 420, 780, "Lee, Ana"), ("shift-delete", D9, 960, 1230, "Lee, Ana"),
             ("shift-delete", D10, 510, 780, "Kim, Dee"), ("open-delete", D9, 840, 1200, None)],
        )
        self.assertEqual(published, 2)
        self.assertTrue(all(r["to_employee"] is None and r["count"] is None for r in rows))

    def test_limit_keeps_the_first_assigned_shifts_only(self):
        rows, published = C.clear_rows(ASSIGNED, OPEN, limit=1)
        self.assertEqual([(r["kind"], r["start_min"]) for r in rows], [("shift-delete", 420)])
        self.assertEqual(published, 0)

    def test_summary_matches_the_console_tray_text(self):
        rows, _ = C.clear_rows(ASSIGNED, OPEN)
        self.assertEqual(C._summary(rows[0]), "Lee, Ana · Mon Nov 9 · 7:00 AM–1:00 PM")
        self.assertEqual(C._summary(rows[-1]), "Open shift · Mon Nov 9 · 2:00 PM–8:00 PM")


class MainTests(unittest.TestCase):
    TODAY = dt.date(2026, 11, 2)

    def _main(self, *args, busy=None):
        def query(sql, params, *, table):
            return ASSIGNED if table == "adp_scheduled_shifts" else OPEN

        out = io.StringIO()
        with mock.patch.object(w, "_today_ct", return_value=self.TODAY), \
             mock.patch.object(w, "_query", side_effect=query), \
             mock.patch.object(w, "fetch_job_executions", return_value=[]), \
             mock.patch.object(w, "running_schedule_write", return_value=busy), \
             mock.patch.object(w, "queue_changes", return_value="batch-1") as queue, \
             mock.patch.object(w, "start_apply_job", return_value="exec-1") as start, \
             contextlib.redirect_stdout(out):
            rc = C.main(["--by", "prod-ops PR #482", *args])
        return rc, out.getvalue(), queue, start

    def test_queues_one_batch_starts_apply_and_prints_no_names(self):
        rc, out, queue, start = self._main("2026-11-09", "2026-11-29")
        self.assertEqual(rc, 0)
        rows = queue.call_args.args[1]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["requested_by"] == "prod-ops PR #482" and r["change_id"] for r in rows))
        start.assert_called_once_with("palmetto", "batch-1")
        self.assertIn("shifts=3 published=2 open=1", out)
        for name in ("Kim", "Lee", "Roe"):
            self.assertNotIn(name, out)

    def test_dry_run_queues_nothing(self):
        rc, out, queue, start = self._main("--dry-run", "2026-11-09", "2026-11-29")
        self.assertEqual(rc, 0)
        self.assertIn("nothing queued", out)
        queue.assert_not_called()
        start.assert_not_called()

    def test_refuses_without_queueing(self):
        for args, busy, why in (
            (["2026-11-02", "2026-11-29"], None, "after today"),
            (["2026-11-09", "2026-11-08"], None, "after today"),
            (["2026-11-09", "2026-12-31"], None, "at most 35 days"),
            (["2026-11-09", "2026-11-29"], "exec/7", "ADP is busy"),
        ):
            rc, out, queue, start = self._main(*args, busy=busy)
            self.assertNotEqual(rc, 0, why)
            self.assertIn(why, out)
            queue.assert_not_called()
            start.assert_not_called()

    def test_refuses_more_than_one_apply_takes(self):
        with mock.patch.object(w, "_MAX_CHANGES", 3):
            rc, out, queue, _ = self._main("2026-11-09", "2026-11-29")
        self.assertEqual(rc, 2)
        self.assertIn("one Apply takes 3", out)
        queue.assert_not_called()


class StartApplyJobTests(unittest.TestCase):
    def test_runs_the_apply_mode_with_the_consoles_two_hour_timeout(self):
        with mock.patch.object(w, "_gcp_token", return_value="t"), \
             mock.patch.object(w.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"metadata": {"name": "exec-1"}}'
            self.assertEqual(w.start_apply_job("palmetto", "batch-1"), "exec-1")
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["overrides"]["timeout"], "7200s")
        env = {e["name"]: e["value"] for e in body["overrides"]["containerOverrides"][0]["env"]}
        self.assertEqual(env, {"BHAGA_ADP_SCHEDULE_WRITE": "apply", "BHAGA_IGNORE_HALT": "1",
                               "BHAGA_STORE": "palmetto", "BHAGA_SCHEDULE_BATCH_ID": "batch-1"})


if __name__ == "__main__":
    unittest.main()
