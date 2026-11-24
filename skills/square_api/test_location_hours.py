#!/usr/bin/env python3
"""Unit tests for skills/square_api/location_hours."""

from __future__ import annotations

import datetime
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_api import location_hours  # noqa: E402


def _period(day: str, start: str, end: str) -> dict:
    return {"day_of_week": day, "start_local_time": start, "end_local_time": end}


class WeekHoursTest(unittest.TestCase):
    def test_every_day(self):
        loc = {"business_hours": {"periods": [_period(d, "09:00:00", "18:00:00")
                                              for d in location_hours.DAYS]}}
        self.assertEqual(location_hours.week_hours(loc), [(540, 1080)] * 7)

    def test_missing_day_is_closed(self):
        loc = {"business_hours": {"periods": [_period("MON", "07:00", "20:00")]}}
        self.assertEqual(location_hours.week_hours(loc), [(420, 1200)] + [None] * 6)

    def test_split_hours_span_the_break(self):
        loc = {"business_hours": {"periods": [_period("SAT", "12:00:00", "20:00:00"),
                                              _period("SAT", "07:00:00", "11:00:00")]}}
        self.assertEqual(location_hours.week_hours(loc)[5], (420, 1200))

    def test_midnight_close(self):
        loc = {"business_hours": {"periods": [_period("FRI", "10:00:00", "00:00:00")]}}
        self.assertEqual(location_hours.week_hours(loc)[4], (600, 1440))

    def test_no_business_hours(self):
        self.assertEqual(location_hours.week_hours({}), [None] * 7)


class SnapshotTest(unittest.TestCase):
    def test_rows_merge_per_store_day(self):
        client = MagicMock()
        client.get.return_value = {"location": {"business_hours": {"periods": [
            _period("MON", "09:00:00", "18:00:00")]}}}
        with patch.object(location_hours.datastore, "load_rows", return_value=7) as load:
            n = location_hours.snapshot("houston", "LOC1", datetime.date(2026, 11, 23), client)
        self.assertEqual(n, 7)
        client.get.assert_called_once_with("/v2/locations/LOC1")
        rows = load.call_args.args[1]
        self.assertEqual(len(rows), 7)
        self.assertEqual((rows[0]["open_min"], rows[0]["close_min"]), (540, 1080))
        self.assertIsNone(rows[1]["open_min"])
        self.assertEqual(load.call_args.kwargs["merge_keys"], ["store", "as_of_date", "day_of_week"])

    def test_no_hours_raises_without_writing(self):
        client = MagicMock()
        client.get.return_value = {"location": {}}
        with patch.object(location_hours.datastore, "load_rows") as load:
            with self.assertRaises(ValueError):
                location_hours.snapshot("houston", "LOC1", datetime.date(2026, 11, 23), client)
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
