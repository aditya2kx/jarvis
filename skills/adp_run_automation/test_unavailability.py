"""Unavailability parsers (Issue #337) — card text mirrors the 2026-09-27 ADP spike."""
from __future__ import annotations

import datetime
import unittest

from skills.adp_run_automation import schedule_backend as sb

PANE = """Pending requests (2)
Scheduling requests
Total 2 Pending
 Filter
Unavailability update
AB
Doe, Alex

Sat, Oct 3,

6:00 AM - 10:00 AM
4.00 HRS
Repeats
Every Saturday until Nov 1, 2026
Repeats until
Sun, Nov 1
Request expires
 Oct 2, 2026 9:00 PM
Add a comment
0/512
 APPROVE
 REJECT
Unavailability update
AB
Doe, Alex

Sun, Oct 4,

12:00 AM - 12:00 AM
24.00 HRS
Request expires
 Oct 3, 2026 3:00 PM
 APPROVE
 REJECT
"""


class TestParseUnavailabilityRequests(unittest.TestCase):
    def setUp(self):
        self.rows = sb.parse_unavailability_requests(PANE, scraped_on=datetime.date(2026, 9, 27))

    def test_one_row_per_card(self):
        self.assertEqual(len(self.rows), 2)
        self.assertTrue(all(r["status"] == "pending" for r in self.rows))
        self.assertEqual({r["raw_employee_name"] for r in self.rows}, {"Doe, Alex"})

    def test_weekly_repeat_with_window(self):
        r = self.rows[0]
        self.assertEqual(r["first_date"], "2026-10-03")
        self.assertEqual((r["from_time"], r["to_time"], r["all_day"]), ("06:00", "10:00", False))
        self.assertEqual((r["repeat_weekday"], r["repeat_until"]), (5, "2026-11-01"))
        self.assertEqual(r["expires_at_ct"], "2026-10-02T21:00:00")

    def test_midnight_to_midnight_is_all_day_one_off(self):
        r = self.rows[1]
        self.assertTrue(r["all_day"])
        self.assertIsNone(r["from_time"])
        self.assertIsNone(r["repeat_weekday"])
        self.assertEqual(r["expires_at_ct"], "2026-10-03T15:00:00")

    def test_year_rolls_over_near_new_year(self):
        pane = "Unavailability update\nDoe, Alex\nFri, Jan 1,\n9:00 AM - 1:00 PM\n4.00 HRS\n"
        rows = sb.parse_unavailability_requests(pane, scraped_on=datetime.date(2026, 12, 28))
        self.assertEqual(rows[0]["first_date"], "2027-01-01")
        self.assertEqual(rows[0]["to_time"], "13:00")

    def test_card_without_name_or_date_is_dropped(self):
        self.assertEqual(sb.parse_unavailability_requests(
            "Unavailability update\n6:00 AM - 10:00 AM\n", scraped_on=datetime.date(2026, 9, 27)), [])

    def test_empty(self):
        self.assertEqual(sb.parse_unavailability_requests(None, scraped_on=datetime.date(2026, 9, 27)), [])


class TestRequestTypes(unittest.TestCase):
    def test_counts(self):
        text = "Pending requests (2)\nUnavailability Requests\n2 Pending\nTime off Requests\n0 Pending\n"
        self.assertEqual(sb.parse_request_types(text), [
            {"request_type": "Unavailability Requests", "pending": 2},
            {"request_type": "Time off Requests", "pending": 0},
        ])


class TestGridUnavailability(unittest.TestCase):
    def _weeks(self, cell_text):
        return [{
            "week_label": "Week of Sep 28, 2026 - Oct 4, 2026",
            "employee_rows": [{"name": "Doe, Alex", "days": [
                {"header_index": 2, "cell_text": cell_text, "ranges": []},
                {"header_index": 3, "cell_text": "9:00 AM - 3:00 PM", "ranges": ["9:00 AM - 3:00 PM"]},
            ]}],
        }]

    def test_windowed_block(self):
        rows = sb.build_grid_unavailability_records(self._weeks("Unavailable 6:00 AM - 10:00 AM"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["first_date"], "2026-09-30")
        self.assertEqual((rows[0]["from_time"], rows[0]["to_time"]), ("06:00", "10:00"))
        self.assertEqual(rows[0]["status"], "approved")

    def test_all_day_block(self):
        rows = sb.build_grid_unavailability_records(self._weeks("Unavailable"))
        self.assertTrue(rows[0]["all_day"])

    def test_shift_cells_are_not_unavailability(self):
        self.assertEqual(sb.build_grid_unavailability_records(self._weeks("1:30 PM - 8:30 PM")), [])


if __name__ == "__main__":
    unittest.main()
