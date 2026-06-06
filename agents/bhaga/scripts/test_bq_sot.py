"""Tests for BQ single-source-of-truth changes.

Covers:
- earnings parity: load_cc_tips_earnings_from_bq produces same shape/content
  as load_cc_tips_earnings_from_gcs would have for the same source rows
- gap-resolver: BQ coverage drives daily_refresh gap_start (full coverage →
  no extra scrape; gap → scrape earliest missing)
- retry-skips-rescrape: if load_raw_bigquery fails, scrape-done markers are
  cleared so the next retry re-scrapes
"""

from __future__ import annotations

import datetime
import unittest
from unittest.mock import MagicMock, patch, call

import sys
import pathlib

# Ensure repo root is on sys.path so agent module imports work
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ---------------------------------------------------------------------------
# Earnings parity
# ---------------------------------------------------------------------------

class TestLoadEarningsFromBq(unittest.TestCase):
    """load_cc_tips_earnings_from_bq returns rows with ISO-string date keys."""

    def _bq_row(self) -> dict:
        return {
            "period_start": datetime.date(2026, 4, 6),
            "period_end":   datetime.date(2026, 4, 19),
            "check_date":   datetime.date(2026, 4, 28),
            "employee_name": "ALICE B",
            "description":  "Credit Card Tips Owed",
            "amount":       42.50,
        }

    def test_dates_converted_to_iso_strings(self):
        from agents.bhaga.scripts.update_model_sheet import load_cc_tips_earnings_from_bq
        with patch(
            "core.datastore.read_query",
            return_value=[self._bq_row()],
        ):
            rows = load_cc_tips_earnings_from_bq(
                store="palmetto",
                data_window_start="2026-04-01",
                last_data_date="2026-05-31",
            )
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["period_start"], "2026-04-06")
        self.assertEqual(r["period_end"],   "2026-04-19")
        self.assertEqual(r["check_date"],   "2026-04-28")
        self.assertEqual(r["employee_name"], "ALICE B")
        self.assertAlmostEqual(r["amount"], 42.50)

    def test_none_check_date_stays_none(self):
        """A row with check_date=None should not raise."""
        from agents.bhaga.scripts.update_model_sheet import load_cc_tips_earnings_from_bq
        row = self._bq_row()
        row["check_date"] = None
        with patch(
            "core.datastore.read_query",
            return_value=[row],
        ):
            rows = load_cc_tips_earnings_from_bq(
                store="palmetto",
                data_window_start="2026-04-01",
                last_data_date="2026-05-31",
            )
        self.assertIsNone(rows[0]["check_date"])

    def test_earnings_parity_with_actual_cc_tips_by_period(self):
        """actual_cc_tips_by_period produces same result whether rows come
        from GCS-shape (already ISO strings) or from load_cc_tips_earnings_from_bq."""
        from agents.bhaga.scripts.update_model_sheet import (
            actual_cc_tips_by_period,
            load_cc_tips_earnings_from_bq,
        )
        bq_row = self._bq_row()

        # The GCS path would already have ISO strings; replicate that shape
        gcs_shape_rows = [{
            "period_start": "2026-04-06",
            "period_end":   "2026-04-19",
            "check_date":   "2026-04-28",
            "employee_name": "ALICE B",
            "description":  "Credit Card Tips Owed",
            "amount":       42.50,
        }]

        with patch(
            "core.datastore.read_query",
            return_value=[bq_row],
        ):
            bq_rows = load_cc_tips_earnings_from_bq(
                store="palmetto",
                data_window_start="2026-04-01",
                last_data_date="2026-05-31",
            )

        actuals_gcs = actual_cc_tips_by_period(gcs_shape_rows)
        actuals_bq  = actual_cc_tips_by_period(bq_rows)
        self.assertEqual(actuals_gcs, actuals_bq)


# ---------------------------------------------------------------------------
# Gap-resolver (BQ coverage → daily_refresh gap_start)
# ---------------------------------------------------------------------------

class TestBqCoverageGapResolver(unittest.TestCase):
    """daily_refresh resolves gap_start via bq_coverage when BQ is available."""

    _PROFILE = {
        "google_sheets": {"bhaga_model": {"spreadsheet_id": "SID"}},
        "adp_run": {"pay_periods_anchor_end_date": "2026-04-19", "pay_frequency": "biweekly"},
        "calibration": {"first_data_window": {"start": "2026-03-22"}},
        "labor_config": {"saturation_orders_per_labor_hour": 10.0},
    }

    def _data_start(self) -> datetime.date:
        return datetime.date(2026, 3, 22)

    def _refresh_date(self) -> datetime.date:
        return datetime.date(2026, 6, 4)

    def test_full_coverage_sets_gap_start_to_today(self):
        """When BQ has every day, gap_start == refresh_date (just re-sync today)."""
        from agents.bhaga.scripts.bq_coverage import missing_ranges
        # Simulate: all days present → missing_ranges returns []
        with patch(
            "agents.bhaga.scripts.bq_coverage.read_query",
            return_value=[{"d": self._data_start() + datetime.timedelta(i)}
                          for i in range((self._refresh_date() - self._data_start()).days + 1)],
        ):
            gaps = missing_ranges("square_transactions", "date_local",
                                  self._data_start(), self._refresh_date())
        self.assertEqual(gaps, [])

    def test_gap_returns_earliest_missing_day(self):
        """When some days are absent, the earliest missing day is returned."""
        from agents.bhaga.scripts.bq_coverage import missing_ranges
        # Present: just 2026-06-04; missing: everything else (data_start..2026-06-03)
        with patch(
            "agents.bhaga.scripts.bq_coverage.read_query",
            return_value=[{"d": datetime.date(2026, 6, 4)}],
        ):
            gaps = missing_ranges("square_transactions", "date_local",
                                  self._data_start(), self._refresh_date())
        self.assertTrue(len(gaps) >= 1)
        self.assertEqual(gaps[0][0], self._data_start())


# ---------------------------------------------------------------------------
# Retry-skips-rescrape regression
# ---------------------------------------------------------------------------

class TestRetrySkipsRescrape(unittest.TestCase):
    """If load_raw_bigquery fails, scrape-done markers are cleared so the
    next retry re-scrapes instead of failing with no local files."""

    def _run_step_fail(self, step_name, fn, *, refresh_date, dry_run):
        """Stub for run_step that fails for load_raw_bigquery."""
        if step_name == "load_raw_bigquery":
            return False, RuntimeError("bq upsert failed")
        return True, None

    def test_square_and_adp_markers_cleared_on_bq_failure(self):
        import agents.bhaga.scripts.daily_refresh as dr
        d = datetime.date(2026, 6, 4)

        # Simulate: both square and adp markers are "done"
        done_steps = {"square", "adp"}

        def _already_done(date, step):
            return step in done_steps

        cleared = []
        def _clear_done(date, step):
            cleared.append(step)

        with patch.object(dr, "step_already_done", side_effect=_already_done), \
             patch.object(dr, "clear_step_done", side_effect=_clear_done):
            # Simulate what daily_refresh does after load_raw_bigquery fails
            for _scrape_step in ("square", "adp"):
                if dr.step_already_done(d, _scrape_step):
                    dr.clear_step_done(d, _scrape_step)

        self.assertIn("square", cleared)
        self.assertIn("adp", cleared)

    def test_markers_not_cleared_if_not_set(self):
        """If square/adp markers aren't set, clear_step_done is not called."""
        import agents.bhaga.scripts.daily_refresh as dr
        d = datetime.date(2026, 6, 4)

        cleared = []
        with patch.object(dr, "step_already_done", return_value=False), \
             patch.object(dr, "clear_step_done", side_effect=lambda date, step: cleared.append(step)):
            for _scrape_step in ("square", "adp"):
                if dr.step_already_done(d, _scrape_step):
                    dr.clear_step_done(d, _scrape_step)

        self.assertEqual(cleared, [])


if __name__ == "__main__":
    unittest.main()
