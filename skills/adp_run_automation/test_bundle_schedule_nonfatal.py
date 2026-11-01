"""A Team Schedule failure inside one Sync ADP login must not stop timecards, punches or employees.

PROGRESS.md documents the schedule scrape as best-effort. The 2026-10-30 runs that
missed #TEMPUS_WEEKLY_SCHEDULE were PR #436 write dry runs, where the schedule is
the whole job; in the read bundle the same error must stay a recorded partial.
"""
from __future__ import annotations

import contextlib
import datetime
import pathlib
import tempfile
import unittest
from unittest import mock

from skills.adp_run_automation import pay_info_backend as pib
from skills.adp_run_automation import runner as r

ANCHOR_ERR = "Team Schedule anchor #TEMPUS_WEEKLY_SCHEDULE not found on dashboard after 2 tries"


class BundleScheduleFailure(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        root = pathlib.Path(self.td.name)
        self.page = mock.MagicMock(url="https://runpayrollmain.adp.com/@t-1/v2/")
        self.page.evaluate.return_value = ""
        self.page.screenshot.side_effect = lambda path, full_page: pathlib.Path(path).write_bytes(b"png")

        @contextlib.contextmanager
        def fake_session(**_kw):
            yield mock.MagicMock(), self.page

        tc = root / "Timecard.xlsx"
        gaps = root / "TimecardsUI.json"
        self.stubs = {
            "timecard": mock.MagicMock(return_value=tc),
            "schedule": mock.MagicMock(side_effect=RuntimeError(ANCHOR_ERR)),
            "gaps": mock.MagicMock(return_value={"rows": []}),
            "upload": mock.MagicMock(return_value="gs://bhaga-scrape-cache/2026-11-01/evidence/s.png"),
        }
        patches = [
            mock.patch.object(r, "DOWNLOADS_DIR", root),
            mock.patch.object(r, "EVIDENCE_DIR", root / "evidence"),
            mock.patch.object(r, "adp_session", fake_session),
            mock.patch.object(r, "_timecard_within_session", self.stubs["timecard"]),
            mock.patch.object(r, "_write_target_meta"),
            mock.patch.object(r, "_mark_run_step_done"),
            mock.patch.object(r, "_schedule_within_session", self.stubs["schedule"]),
            mock.patch.object(r, "_timecard_gaps_within_session", self.stubs["gaps"]),
            mock.patch.object(r, "_write_timecards_ui_json", return_value=gaps),
            mock.patch.object(r, "_upload_evidence_to_gcs", self.stubs["upload"]),
            mock.patch("skills.store_profile.load_aliases", return_value={}),
            mock.patch("skills.store_profile.load_exclusions", return_value={}),
            mock.patch.multiple(
                pib,
                puncher_names_from_session_files=mock.DEFAULT,
                puncher_names_from_bq=mock.DEFAULT,
                directory_roster=mock.DEFAULT,
                known_hire_names=mock.DEFAULT,
                scrape_pay_info_rates=mock.DEFAULT,
                scrape_hire_dates=mock.DEFAULT,
                write_pay_info_json=mock.DEFAULT,
            ),
        ]
        for p in patches:
            started = p.start()
            if isinstance(started, dict):
                self.pib = started
            self.addCleanup(p.stop)
        self.pib["puncher_names_from_session_files"].return_value = ["Nguyen, Quyen P"]
        self.pib["puncher_names_from_bq"].return_value = []
        self.pib["directory_roster"].return_value = [
            {"name": "Rodriguez, Eileen", "status": "Active"},
            {"name": "Phong Vu", "status": ""},
        ]
        self.pib["known_hire_names"].return_value = set()
        self.pib["scrape_pay_info_rates"].return_value = ([{"employee": "Nguyen, Quyen P"}], {})
        self.pib["scrape_hire_dates"].return_value = ({}, {})
        self.pib["write_pay_info_json"].return_value = root / "PayInfoRates.json"

    def _run(self):
        return r.download_adp_bundle(
            store="palmetto", target_date=datetime.date(2026, 10, 31),
            include_earnings=False, include_schedule=True, include_extras=True,
            headed=False,
        )

    def test_schedule_error_is_recorded_and_everything_else_still_runs(self):
        result = self._run()

        self.assertEqual(list(result["errors"]), ["adp_schedule"])
        self.assertIn(ANCHOR_ERR, result["errors"]["adp_schedule"])
        self.assertIsNotNone(result["timecard_xlsx"])
        self.assertIsNone(result["schedule_json"])
        self.assertIsNotNone(result["timecards_ui_json"])
        self.stubs["timecard"].assert_called_once()
        self.stubs["gaps"].assert_called_once()
        self.pib["directory_roster"].assert_called_once()
        self.pib["scrape_pay_info_rates"].assert_called_once()
        self.assertEqual(result["directory_roster"][0]["name"], "Rodriguez, Eileen")
        self.assertIn("pay_info_json", result)

    def test_schedule_failure_screenshot_goes_to_gcs(self):
        with mock.patch("builtins.print") as out:
            self._run()
        lines = [" ".join(map(str, c.args)) for c in out.call_args_list]
        crumb = [line for line in lines if "label=adp-bundle-schedule-fail" in line]
        self.assertEqual(len(crumb), 1, lines)
        self.assertIn("shot=gs://bhaga-scrape-cache/", crumb[0])
        self.assertFalse(any(".bhaga/state/screenshots" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
