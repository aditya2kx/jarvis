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


class ReviewBonusBqSinkTests(unittest.TestCase):
    """M3: rebuild_review_bonus_period writes to BQ when BHAGA_DATASTORE=bigquery."""

    _PROFILE = {
        "adp_run": {
            "pay_periods_anchor_end_date": "2026-05-17",
            "pay_frequency": "biweekly",
        },
        "calibration": {"first_data_window": {"start": "2026-02-17"}},
    }
    _REVIEWS = [
        {
            "shift_date_credited": "2026-05-28",
            "rating": 5,
            "named": ["Alice"],
            "allocations": {"Alice": 20},
        },
    ]

    def _run_rebuild(self, *, bq_enabled: bool, load_raises: bool = False):
        """Run rebuild_review_bonus_period with mocked Sheet+BQ calls."""
        bq_calls: list[dict] = []

        def fake_load_model_rows(table, rows, **_kw):
            if load_raises:
                raise RuntimeError("BQ unavailable")
            bq_calls.append({"table": table, "n": len(rows) - 1})
            return len(rows) - 1

        with unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.add_sheet_if_missing",
            return_value="sheet123",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.clear_and_write_tab",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.bold_header_row",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews.format_currency_columns",
        ), unittest.mock.patch(
            "agents.bhaga.scripts.process_reviews._bq_enabled",
            return_value=bq_enabled,
        ), unittest.mock.patch(
            "agents.bhaga.scripts.materialize_model_bq.load_model_rows",
            side_effect=fake_load_model_rows,
        ):
            n = rebuild_review_bonus_period(
                model_sid="model_sid",
                token="token",
                all_reviews=self._REVIEWS,
                data_window_end=datetime.date(2026, 5, 29),
                profile=self._PROFILE,
            )
        return n, bq_calls

    def test_bq_disabled_no_bq_call(self):
        n, bq_calls = self._run_rebuild(bq_enabled=False)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(bq_calls, [], "BQ should not be called when disabled")

    def test_bq_enabled_writes_to_correct_table(self):
        n, bq_calls = self._run_rebuild(bq_enabled=True)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(len(bq_calls), 1)
        self.assertEqual(bq_calls[0]["table"], "model_review_bonus_period")

    def test_bq_error_does_not_fail_sheet_write(self):
        """BQ error must be non-fatal — Sheet write still completes."""
        n, bq_calls = self._run_rebuild(bq_enabled=True, load_raises=True)
        # Sheet write succeeded (n >= 1) despite BQ error.
        self.assertGreaterEqual(n, 1)


class TestBqPrimaryReviews(unittest.TestCase):
    """Verify BQ-primary reviews path: write to google_reviews BQ, read from BQ."""

    def _make_parsed_review(self) -> dict:
        from agents.bhaga.scripts.process_reviews import build_review_row, REVIEW_HEADER_ROW
        return {
            "review_id": "rev-001",
            "post_dt_ct": datetime.datetime(2026, 5, 1, 10, 30, 0,
                                            tzinfo=__import__("zoneinfo").ZoneInfo("America/Chicago")),
            "post_date_ct": datetime.date(2026, 5, 1),
            "rating": 5,
            "reviewer": "John D.",
            "comment": "Great coffee!",
            "named": ["Alvarez, Sebastian"],
            "named_status": "ok",
            "shift_date_credited": None,
            "shift_assignment_reason": "no_match",
            "shift_members_credited": [],
            "trainees_on_shift": [],
            "allocations": {},
            "total_bonus_dollars": 0.0,
            "ingested_at_utc": "2026-05-01T15:00:00+00:00",
            "review_url": "https://example.com/rev-001",
        }

    def test_rec_to_bq_shape_excludes_clickup_message_id(self):
        """_rec_to_bq_shape must produce a BQ row without clickup_message_id."""
        from agents.bhaga.scripts.process_reviews import _rec_to_bq_shape
        rec = self._make_parsed_review()
        bq_row = _rec_to_bq_shape(rec)
        self.assertNotIn("clickup_message_id", bq_row)
        self.assertEqual(bq_row["review_id"], "rev-001")

    def test_rec_to_bq_shape_strips_apostrophes(self):
        """Apostrophe text-protection must be stripped before calling map_google_review."""
        from agents.bhaga.scripts.process_reviews import _rec_to_bq_shape
        import agents.bhaga.scripts.process_reviews as pr_module
        rec = self._make_parsed_review()
        bq_row = _rec_to_bq_shape(rec)
        # post_date_ct should be a date object (parsed), not a string starting with "'"
        self.assertIsInstance(bq_row.get("post_date_ct"), (datetime.date, type(None)))

    def test_latest_review_ts_ms_returns_none_when_bq_disabled(self):
        """When BHAGA_DATASTORE != bigquery, high-water mark returns None."""
        import unittest.mock as mock
        from agents.bhaga.scripts.process_reviews import _latest_review_ts_ms
        with mock.patch("agents.bhaga.scripts.process_reviews._bq_enabled", return_value=False):
            result = _latest_review_ts_ms()
        self.assertIsNone(result)

    def test_read_all_reviews_from_bq_builds_allocations(self):
        """_read_all_reviews reads from google_reviews BQ and rebuilds allocations."""
        import unittest.mock as mock
        from agents.bhaga.scripts.process_reviews import _read_all_reviews

        bq_rows = [{
            "review_id": "rev-001",
            "post_ts_ct": "2026-05-01T10:30:00-05:00",
            "post_date_ct": datetime.date(2026, 5, 1),
            "rating": 5,
            "reviewer": "John D.",
            "comment": "Great coffee!",
            "named_baristas": "Alvarez, Sebastian",
            "named_status": "ok",
            "shift_date_credited": "",
            "shift_assignment_reason": "no_match",
            "shift_members": "",
            "trainees_on_shift": "",
            "named_credit_each": 0.0,
            "base_credit_each": 0.0,
            "total_bonus": 0.0,
            "review_url": "https://example.com/rev-001",
            "ingested_at_utc": datetime.datetime(2026, 5, 1, 15, 0, 0,
                                                  tzinfo=datetime.timezone.utc),
        }]

        with mock.patch("agents.bhaga.scripts.process_reviews.read_query", return_value=bq_rows):
            result = _read_all_reviews(excluded_permanent=set(), training_through={})

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["review_id"], "rev-001")
        self.assertIn("allocations", result[0])
        self.assertIn("named", result[0])

    def test_ensure_review_raw_tabs_does_not_create_reviews_tab(self):
        """_ensure_review_raw_tabs must only create unparseable tab, not reviews tab."""
        import unittest.mock as mock
        from agents.bhaga.scripts.process_reviews import _ensure_review_raw_tabs

        created_tabs: list[str] = []

        def fake_add_sheet(sid, token, tab_name, column_count=10):
            created_tabs.append(tab_name)
            return 1

        def fake_tab_has_data(sid, token, tab):
            return True  # pretend tab has data so we skip the seed-header call

        with mock.patch("agents.bhaga.scripts.process_reviews.add_sheet_if_missing", fake_add_sheet), \
             mock.patch("agents.bhaga.scripts.process_reviews._tab_has_any_data", fake_tab_has_data):
            _ensure_review_raw_tabs("fake-sid", "fake-token")

        self.assertNotIn("reviews", created_tabs,
                         "reviews tab should NOT be created (rendered from BQ instead)")
        self.assertIn("unparseable", created_tabs)


if __name__ == "__main__":
    unittest.main()
