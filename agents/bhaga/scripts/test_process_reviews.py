#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.process_reviews.

Run:
    python3 agents/bhaga/scripts/test_process_reviews.py

Covers Layer B's read-side defense in process_reviews — the
``_resolve_data_window_end`` helper must accept ISO,
apostrophe-prefixed, or Sheets-serial values (silently coerced) and
must raise a clear, operator-actionable error on truly bad junk.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.process_reviews import (
    _resolve_data_window_end,
    rebuild_review_bonus_period,
)


class ResolveDataWindowEndTests(unittest.TestCase):
    def test_iso_passes_through(self):
        d = _resolve_data_window_end({"data_window_end": "2026-05-20"})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_apostrophe_prefixed_stripped(self):
        # Layer A's own output round-trips cleanly through the helper.
        d = _resolve_data_window_end({"data_window_end": "'2026-05-20"})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_serial_silently_recovered(self):
        # 46162 == 2026-05-20 in Sheets serial. Layer B promises silent
        # recovery on this branch.
        d = _resolve_data_window_end({"data_window_end": "46162"})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_whitespace_tolerated(self):
        d = _resolve_data_window_end({"data_window_end": "  2026-05-20  "})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_garbage_raises_clear_error(self):
        with self.assertRaises(RuntimeError) as cm:
            _resolve_data_window_end({"data_window_end": "banana"})
        # The literal bad cell value MUST appear in the error so the
        # operator can grep for it in the sheet.
        self.assertIn("banana", str(cm.exception))

    def test_missing_key_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            _resolve_data_window_end({})
        self.assertIn("data_window_end", str(cm.exception))

    def test_empty_value_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            _resolve_data_window_end({"data_window_end": ""})
        self.assertIn("data_window_end", str(cm.exception))


class RebuildReviewBonusPeriodTests(unittest.TestCase):
    """rollup must not depend on a local Earnings XLSX (cloud has none)."""

    def test_rebuild_without_earnings_xlsx(self):
        profile = {
            "adp_run": {
                "pay_periods_anchor_end_date": "2026-05-17",
                "pay_frequency": "biweekly",
            },
            "calibration": {"first_data_window": {"start": "2026-02-17"}},
        }
        reviews = [
            {
                "shift_date_credited": "2026-05-28",
                "rating": 5,
                "named": ["Alice"],
                "allocations": {"Alice": 20},
            },
        ]
        with unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.add_sheet_if_missing",
            return_value="sheet123",
        ) as add_sheet, unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.clear_and_write_tab",
        ) as write_tab, unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.bold_header_row",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.format_currency_columns",
        ):
            n = rebuild_review_bonus_period(
                model_sid="model_sid",
                token="token",
                all_reviews=reviews,
                data_window_end=datetime.date(2026, 5, 29),
                profile=profile,
            )
        self.assertGreaterEqual(n, 1)
        add_sheet.assert_called_once()
        write_tab.assert_called_once()
        written = write_tab.call_args.kwargs["values"]
        self.assertEqual(written[0][0], "period_start")
        open_rows = [r for r in written[1:] if r[2] == "yes"]
        ends = {str(r[1]).lstrip("'") for r in open_rows}
        self.assertIn("2026-05-29", ends, "open period should end at data_window_end")


if __name__ == "__main__":
    unittest.main()
