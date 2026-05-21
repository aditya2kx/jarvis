#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.daily_refresh.

Run:
    python3 agents/bhaga/scripts/test_daily_refresh.py

Covers Layer C of the seamless_bhaga_refresh fix:
``compute_gap_window`` must refuse to silently fall back to "fresh
install" when the ``data_window_end`` config cell is non-empty but
unparseable, because doing so would burn ~60 days of Square API budget
plus a fresh 2FA challenge.

These tests target the pure extracted function — no Sheets, Playwright,
or Slack side effects.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.daily_refresh import compute_gap_window


class ComputeGapWindowTests(unittest.TestCase):
    DATA_START = datetime.date(2026, 3, 22)
    REFRESH_DATE = datetime.date(2026, 5, 21)

    def test_empty_cell_triggers_fresh_install(self):
        gap_start, gap_source = compute_gap_window(
            prev_end=None,
            cell_was_empty=True,
            data_start=self.DATA_START,
            refresh_date=self.REFRESH_DATE,
        )
        self.assertEqual(gap_start, self.DATA_START)
        self.assertTrue(
            gap_source.startswith("fresh install"),
            f"gap_source={gap_source!r} should start with 'fresh install'",
        )

    def test_unparseable_cell_raises_systemexit(self):
        with self.assertRaises(SystemExit) as cm:
            compute_gap_window(
                prev_end=None,
                cell_was_empty=False,
                data_start=self.DATA_START,
                refresh_date=self.REFRESH_DATE,
            )
        # The phrase below MUST stay in the error message — future
        # maintainers grep for it when investigating why a Square scrape
        # was aborted.
        self.assertIn("60-day Square re-scrape", str(cm.exception))

    def test_serial_recovered_yields_incremental_gap(self):
        # Layer B coerced "46162" -> date(2026, 5, 20) BEFORE we get here.
        prev_end = datetime.date(2026, 5, 20)
        gap_start, gap_source = compute_gap_window(
            prev_end=prev_end,
            cell_was_empty=False,
            data_start=self.DATA_START,
            refresh_date=self.REFRESH_DATE,
        )
        self.assertEqual(gap_start, datetime.date(2026, 5, 21))
        self.assertIn("2026-05-20", gap_source)
        self.assertIn("+ 1", gap_source)

    def test_iso_normal_incremental(self):
        # Routine nightly run — yesterday's window end + 1 = today.
        prev_end = datetime.date(2026, 5, 20)
        gap_start, gap_source = compute_gap_window(
            prev_end=prev_end,
            cell_was_empty=False,
            data_start=self.DATA_START,
            refresh_date=datetime.date(2026, 5, 21),
        )
        self.assertEqual(gap_start, datetime.date(2026, 5, 21))
        self.assertEqual(gap_source, "sheet.data_window_end=2026-05-20 + 1")

    def test_caught_up_yesterday_means_empty_gap(self):
        # prev_end == refresh_date → gap_start lands a day AFTER refresh_date,
        # which the caller interprets as "nothing to scrape".
        prev_end = datetime.date(2026, 5, 21)
        gap_start, _ = compute_gap_window(
            prev_end=prev_end,
            cell_was_empty=False,
            data_start=self.DATA_START,
            refresh_date=datetime.date(2026, 5, 21),
        )
        self.assertGreater(gap_start, datetime.date(2026, 5, 21))


if __name__ == "__main__":
    unittest.main()
