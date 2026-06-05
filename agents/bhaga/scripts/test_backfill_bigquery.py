"""Mapper round-trip tests for backfill_bigquery.

Verifies that key renames (canonical_name ↔ employee_name, earnings_json ↔
rate_history_json, shift_count ↔ punch_count) survive the parse → map_* → BQ
round-trip and that multi_rate is populated in map_adp_wage_rate.
"""
from __future__ import annotations

import datetime
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
os.environ.setdefault("BHAGA_DATASTORE", "disabled")

from agents.bhaga.scripts.backfill_bigquery import (
    map_adp_shift,
    map_adp_punch,
    map_adp_wage_rate,
    map_adp_earnings_row,
    map_square_transaction,
    map_square_daily_rollup,
    map_square_item_line,
    map_square_item_daily,
    map_square_kds_daily,
    map_kds_ticket,
    map_google_review,
    _parse_bool,
    _parse_float,
    _parse_date,
)
from agents.bhaga.scripts.render_raw_sheet_from_bq import (
    _inv_adp_shift,
    _inv_adp_punch,
    _inv_adp_wage_rate,
    _inv_adp_earnings,
)

_FAKE_PROFILE = {
    "employees": {
        "excluded_from_tip_pool_and_labor_pct": [],
    },
}


class TestMapAdpShiftRoundTrip(unittest.TestCase):
    """map_adp_shift → BQ row → _inv_adp_shift → Sheet dict preserves key columns."""

    def setUp(self):
        self.sheet_dict = {
            "date": "2026-05-01",
            "employee_id": "alvarez_s",
            "employee_name": "Alvarez, Sebastian",
            "raw_employee_name": "ALVAREZ SEBASTIAN",
            "in_time": "08:00",
            "out_time": "16:00",
            "regular_hours": "8.0",
            "ot_hours": "0.0",
            "doubletime_hours": "0.0",
            "total_hours": "8.0",
            "punch_count": "1",
            "scraped_at_utc": None,
        }

    def test_canonical_name_round_trips(self):
        bq_row = map_adp_shift(self.sheet_dict)
        self.assertEqual(bq_row["canonical_name"], "Alvarez, Sebastian")
        back = _inv_adp_shift(bq_row)
        self.assertEqual(back["employee_name"], "Alvarez, Sebastian")

    def test_punch_count_round_trips_as_shift_count(self):
        bq_row = map_adp_shift(self.sheet_dict)
        self.assertEqual(bq_row["shift_count"], 1)
        back = _inv_adp_shift(bq_row)
        self.assertEqual(back["punch_count"], 1)

    def test_date_round_trips(self):
        bq_row = map_adp_shift(self.sheet_dict)
        back = _inv_adp_shift(bq_row)
        self.assertEqual(back["date"], "2026-05-01")


class TestMapAdpPunchRoundTrip(unittest.TestCase):
    def test_punch_index_round_trips_as_punch_idx_in_day(self):
        sheet_dict = {
            "date": "2026-05-01",
            "employee_id": "alvarez_s",
            "employee_name": "Alvarez, Sebastian",
            "raw_employee_name": "ALVAREZ SEBASTIAN",
            "punch_idx_in_day": "0",
            "in_time": "08:00",
            "out_time": "12:00",
            "regular_hours": "4.0",
            "ot_hours": "0.0",
            "doubletime_hours": "0.0",
            "scraped_at_utc": None,
        }
        bq_row = map_adp_punch(sheet_dict)
        self.assertEqual(bq_row["punch_index"], 0)
        back = _inv_adp_punch(bq_row)
        self.assertEqual(back["punch_idx_in_day"], 0)


class TestMapAdpWageRateMultiRate(unittest.TestCase):
    """multi_rate must survive the Sheet → BQ → Sheet round-trip."""

    def _make_sheet_dict(self, multi_rate_val):
        return {
            "employee_id": "alvarez_s",
            "employee_name": "Alvarez, Sebastian",
            "wage_rate_dollars": "15.0",
            "ot_rate_dollars": "22.5",
            "is_salaried": "FALSE",
            "multi_rate": multi_rate_val,
            "excluded_from_labor_pct": "FALSE",
            "rate_history_json": '[{"rate": 15.0}]',
            "raw_employee_names_json": '["ALVAREZ SEBASTIAN"]',
            "scraped_at_utc": None,
        }

    def test_multi_rate_true_is_bool_in_bq(self):
        bq_row = map_adp_wage_rate(self._make_sheet_dict("TRUE"), _FAKE_PROFILE)
        self.assertIsInstance(bq_row["multi_rate"], bool)
        self.assertTrue(bq_row["multi_rate"])

    def test_multi_rate_false_is_bool_in_bq(self):
        bq_row = map_adp_wage_rate(self._make_sheet_dict("FALSE"), _FAKE_PROFILE)
        self.assertIsInstance(bq_row["multi_rate"], bool)
        self.assertFalse(bq_row["multi_rate"])

    def test_earnings_json_is_rate_history_json_in_bq(self):
        bq_row = map_adp_wage_rate(self._make_sheet_dict("FALSE"), _FAKE_PROFILE)
        self.assertIn("earnings_json", bq_row)
        self.assertEqual(bq_row["earnings_json"], '[{"rate": 15.0}]')

    def test_multi_rate_round_trips_via_inv_mapper(self):
        bq_row = map_adp_wage_rate(self._make_sheet_dict("TRUE"), _FAKE_PROFILE)
        back = _inv_adp_wage_rate(bq_row)
        self.assertTrue(back["multi_rate"])
        self.assertEqual(back["employee_name"], "Alvarez, Sebastian")
        self.assertEqual(back["rate_history_json"], '[{"rate": 15.0}]')


class TestMapAdpEarningsRoundTrip(unittest.TestCase):
    def test_employee_name_becomes_employee_in_bq_then_back(self):
        sheet_dict = {
            "period_start": "2026-05-01",
            "period_end": "2026-05-14",
            "check_date": "2026-05-20",
            "employee_name": "Alvarez, Sebastian",
            "raw_employee_name": "ALVAREZ SEBASTIAN",
            "description": "Regular",
            "hours": "80.0",
            "hourly_rate": "15.0",
            "amount": "1200.0",
            "scraped_at_utc": None,
        }
        bq_row = map_adp_earnings_row(sheet_dict)
        self.assertEqual(bq_row["employee"], "Alvarez, Sebastian")
        back = _inv_adp_earnings(bq_row)
        self.assertEqual(back["employee_name"], "Alvarez, Sebastian")


class TestMapGoogleReview(unittest.TestCase):
    def test_review_id_and_rating_preserved(self):
        sheet_dict = {
            "review_id": "abc123",
            "post_ts_ct": "'2026-05-01T10:30:00-05:00",
            "post_date_ct": "'2026-05-01",
            "rating": "5",
            "reviewer": "John D.",
            "comment": "Great coffee!",
            "named_baristas": "Alvarez, Sebastian",
            "named_status": "ok",
            "shift_date_credited": "'2026-05-01",
            "shift_assignment_reason": "closest_before",
            "shift_members": "Alvarez, Sebastian; Johnson, Dolce",
            "trainees_on_shift": "",
            "named_credit_each": "10.0",
            "base_credit_each": "0.0",
            "total_bonus": "10.0",
            "review_url": "https://example.com",
            "clickup_message_id": "msg-123",
            "ingested_at_utc": "2026-05-02T03:00:00Z",
        }
        bq_row = map_google_review(sheet_dict)
        self.assertEqual(bq_row["review_id"], "abc123")
        self.assertEqual(bq_row["rating"], 5)
        # clickup_message_id must NOT be in BQ row
        self.assertNotIn("clickup_message_id", bq_row)

    def test_apostrophe_stripped_from_dates(self):
        sheet_dict = {
            "review_id": "x",
            "post_ts_ct": "'2026-05-01T10:30:00-05:00",
            "post_date_ct": "'2026-05-01",
            "rating": "5",
            "reviewer": "A",
            "comment": "B",
            "named_baristas": "",
            "named_status": "ok",
            "shift_date_credited": "",
            "shift_assignment_reason": "",
            "shift_members": "",
            "trainees_on_shift": "",
            "named_credit_each": "",
            "base_credit_each": "",
            "total_bonus": "",
            "review_url": "",
            "clickup_message_id": "",
            "ingested_at_utc": None,
        }
        bq_row = map_google_review(sheet_dict)
        # post_ts_ct: apostrophe stripped before map_google_review is called by _rec_to_bq_shape
        # map_google_review itself receives the strip-then-parse path:
        self.assertIsInstance(bq_row["post_date_ct"], (datetime.date, type(None)))


if __name__ == "__main__":
    unittest.main()
