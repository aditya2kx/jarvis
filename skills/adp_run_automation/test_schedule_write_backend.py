"""ADP Team Schedule write helpers (Issue #337)."""
from __future__ import annotations

import datetime
import unittest

from skills.adp_run_automation import schedule_write_backend as wb


class Helpers(unittest.TestCase):
    def test_fmt_time(self):
        self.assertEqual(wb.fmt_time(390), "6:30 AM")
        self.assertEqual(wb.fmt_time(0), "12:00 AM")
        self.assertEqual(wb.fmt_time(720), "12:00 PM")
        self.assertEqual(wb.fmt_time(1260), "9:00 PM")
        with self.assertRaises(ValueError):
            wb.fmt_time(1440)

    def test_fmt_date(self):
        self.assertEqual(wb.fmt_date("2026-10-04"), "10/04/2026")

    def test_week_label_start(self):
        self.assertEqual(
            wb.week_label_start("Week of Sep 28, 2026 - Oct 4, 2026"), datetime.date(2026, 9, 28)
        )
        with self.assertRaises(ValueError):
            wb.week_label_start("Monthly Schedule")

    def test_match_employee(self):
        names = ["Doe, Alex J", "Doe, Alexis", "Roe, Sam"]
        self.assertEqual(wb.match_employee(names, "Doe, Alex"), "Doe, Alex J")
        self.assertEqual(wb.match_employee(names, "roe,  sam"), "Roe, Sam")
        self.assertIsNone(wb.match_employee(names, "Poe, Kim"))
        # Two middle-initial variants → ambiguous, never guess.
        self.assertIsNone(wb.match_employee(["Doe, Alex J", "Doe, Alex K"], "Doe, Alex"))

    def test_publish_count(self):
        self.assertEqual(wb.publish_count(" Publish drafts (3) "), 3)
        self.assertIsNone(wb.publish_count("Publish"))


if __name__ == "__main__":
    unittest.main()
