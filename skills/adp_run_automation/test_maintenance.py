#!/usr/bin/env python3
"""Tests for the ADP maintenance-banner parser (smart post-maintenance retry).

Covers the 2026-06-28 incident banner plus variants, DST correctness (ET in
summer = EDT/UTC-4), year inference, the no-end-date fallback, and rejection of
non-maintenance text.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation.maintenance import (  # noqa: E402
    compute_retry_at,
    default_retry_at,
    parse_maintenance_end,
)

UTC = datetime.timezone.utc
ET = ZoneInfo("America/New_York")


class TestParseMaintenanceEnd(unittest.TestCase):
    def test_incident_banner_summer_edt(self):
        """The real 6/28 banner → 2am ET on Jun 29 == 06:00 UTC (EDT, UTC-4)."""
        banner = ("Planned RUN Maintenance on Sun, Jun 28th from 10pm ET "
                  "to Mon, Jun 29th at 2am ET.")
        now = datetime.datetime(2026, 6, 29, 0, 16, tzinfo=ET)  # 00:16 ET, inside window
        end = parse_maintenance_end(banner, now=now)
        self.assertEqual(end, datetime.datetime(2026, 6, 29, 6, 0, tzinfo=UTC))

    def test_winter_est_offset(self):
        """A January window: 2am ET == 07:00 UTC (EST, UTC-5)."""
        banner = "Planned RUN Maintenance from 10pm ET to Tue, Jan 6th at 2am ET."
        now = datetime.datetime(2026, 1, 6, 0, 30, tzinfo=ET)
        end = parse_maintenance_end(banner, now=now)
        self.assertEqual(end, datetime.datetime(2026, 1, 6, 7, 0, tzinfo=UTC))

    def test_full_month_name_and_minutes_and_dotted_ampm(self):
        banner = ("RUN Maintenance scheduled from 11:30 PM ET to "
                  "Monday, June 29 at 1:15 a.m. ET")
        now = datetime.datetime(2026, 6, 28, 23, 45, tzinfo=ET)
        end = parse_maintenance_end(banner, now=now)
        # 1:15 am EDT Jun 29 == 05:15 UTC
        self.assertEqual(end, datetime.datetime(2026, 6, 29, 5, 15, tzinfo=UTC))

    def test_no_end_date_uses_next_occurrence(self):
        """Banner states 'to <time>' without repeating the date → next time >= now (ET)."""
        banner = "Planned RUN Maintenance from 10pm to 2am ET."
        now = datetime.datetime(2026, 6, 29, 0, 16, tzinfo=ET)  # before 2am today
        end = parse_maintenance_end(banner, now=now)
        self.assertEqual(end, datetime.datetime(2026, 6, 29, 6, 0, tzinfo=UTC))

    def test_no_end_date_rolls_to_tomorrow_when_past(self):
        banner = "Planned RUN Maintenance from 10pm to 2am ET."
        now = datetime.datetime(2026, 6, 29, 3, 0, tzinfo=ET)  # already past 2am today
        end = parse_maintenance_end(banner, now=now)
        self.assertEqual(end, datetime.datetime(2026, 6, 30, 6, 0, tzinfo=UTC))

    def test_non_maintenance_text_returns_none(self):
        self.assertIsNone(parse_maintenance_end("Welcome! Sign in with your User ID.",
                                                now=datetime.datetime.now(UTC)))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_maintenance_end("", now=datetime.datetime.now(UTC)))
        self.assertIsNone(parse_maintenance_end(None, now=datetime.datetime.now(UTC)))

    def test_naive_now_returns_none(self):
        banner = "Planned RUN Maintenance to Mon, Jun 29th at 2am ET."
        self.assertIsNone(parse_maintenance_end(banner, now=datetime.datetime(2026, 6, 29, 0, 16)))

    def test_year_rollover_inference(self):
        """A Jan banner parsed while now is the prior December bumps to next year."""
        banner = "Planned RUN Maintenance to Jan 2nd at 2am ET."
        now = datetime.datetime(2026, 12, 31, 23, 0, tzinfo=ET)
        end = parse_maintenance_end(banner, now=now)
        self.assertEqual(end.year, 2027)


class TestComputeRetryAt(unittest.TestCase):
    def test_default_buffer_is_seven_minutes(self):
        end = datetime.datetime(2026, 6, 29, 6, 0, tzinfo=UTC)
        self.assertEqual(compute_retry_at(end),
                         datetime.datetime(2026, 6, 29, 6, 7, tzinfo=UTC))

    def test_custom_buffer(self):
        end = datetime.datetime(2026, 6, 29, 6, 0, tzinfo=UTC)
        self.assertEqual(compute_retry_at(end, buffer_minutes=10),
                         datetime.datetime(2026, 6, 29, 6, 10, tzinfo=UTC))


class TestDefaultRetryAt(unittest.TestCase):
    """When ADP publishes no window-end (generic maintenance.html), fall back to
    a fixed backoff so the run still self-heals."""

    def test_default_delay_is_thirty_minutes(self):
        now = datetime.datetime(2026, 6, 29, 5, 57, tzinfo=UTC)
        self.assertEqual(default_retry_at(now=now),
                         datetime.datetime(2026, 6, 29, 6, 27, tzinfo=UTC))

    def test_explicit_delay_overrides(self):
        now = datetime.datetime(2026, 6, 29, 5, 57, tzinfo=UTC)
        self.assertEqual(default_retry_at(now=now, delay_minutes=10),
                         datetime.datetime(2026, 6, 29, 6, 7, tzinfo=UTC))

    def test_result_is_utc_aware(self):
        out = default_retry_at()
        self.assertIsNotNone(out.tzinfo)
        self.assertEqual(out.utcoffset(), datetime.timedelta(0))


if __name__ == "__main__":
    unittest.main()
