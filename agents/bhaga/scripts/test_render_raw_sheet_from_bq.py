"""Tests for render_raw_sheet_from_bq — inverse mappers and spec coverage."""
from __future__ import annotations

import datetime
import os
import sys
import pathlib
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
os.environ.setdefault("BHAGA_DATASTORE", "disabled")

from agents.bhaga.scripts.render_raw_sheet_from_bq import (
    _TAB_SPECS,
    _LABEL_TO_SPEC,
    _inv_adp_shift,
    _inv_adp_punch,
    _inv_adp_wage_rate,
    _inv_adp_earnings,
    _inv_square_transaction,
    _inv_square_daily_rollup,
    _inv_square_item_line,
    _inv_square_item_daily,
    _inv_square_kds_daily,
    _inv_kds_ticket,
    _inv_google_review,
    _str_date,
    _str_ts,
)


class TestSpecCoverage(unittest.TestCase):
    """Verify all 11 raw tabs have a spec."""

    EXPECTED_LABELS = {
        "adp_shifts", "adp_punches", "adp_wage_rates", "adp_earnings",
        "square_transactions", "square_daily_rollup", "square_item_lines",
        "square_item_daily", "square_kds_daily", "square_kds_tickets",
        "reviews",
    }

    def test_all_11_tabs_have_specs(self):
        actual = {s["label"] for s in _TAB_SPECS}
        self.assertEqual(actual, self.EXPECTED_LABELS)

    def test_label_to_spec_index_matches_tab_specs(self):
        self.assertEqual(set(_LABEL_TO_SPEC.keys()), self.EXPECTED_LABELS)

    def test_each_spec_has_required_keys(self):
        required = {"label", "workbook_key", "bq_table", "date_col", "inv_map_fn", "write_fn"}
        for spec in _TAB_SPECS:
            missing = required - set(spec.keys())
            self.assertFalse(missing, f"Spec {spec['label']!r} missing keys: {missing}")


class TestInvAdpShift(unittest.TestCase):
    def setUp(self):
        self.bq_row = {
            "date": datetime.date(2026, 5, 1),
            "employee_id": "alvarez_s",
            "canonical_name": "Alvarez, Sebastian",
            "raw_employee_name": "ALVAREZ SEBASTIAN",
            "in_time": "08:00",
            "out_time": "16:00",
            "regular_hours": 8.0,
            "ot_hours": 0.0,
            "doubletime_hours": 0.0,
            "total_hours": 8.0,
            "shift_count": 1,
            "scraped_at_utc": datetime.datetime(2026, 5, 2, 3, 0, 0, tzinfo=datetime.timezone.utc),
        }

    def test_canonical_name_becomes_employee_name(self):
        d = _inv_adp_shift(self.bq_row)
        self.assertEqual(d["employee_name"], "Alvarez, Sebastian")

    def test_shift_count_becomes_punch_count(self):
        d = _inv_adp_shift(self.bq_row)
        self.assertEqual(d["punch_count"], 1)

    def test_date_is_iso_string(self):
        d = _inv_adp_shift(self.bq_row)
        self.assertEqual(d["date"], "2026-05-01")

    def test_scraped_at_utc_is_iso_string(self):
        d = _inv_adp_shift(self.bq_row)
        self.assertIn("2026-05-02", d["scraped_at_utc"])


class TestInvAdpPunch(unittest.TestCase):
    def setUp(self):
        self.bq_row = {
            "date": datetime.date(2026, 5, 1),
            "employee_id": "alvarez_s",
            "canonical_name": "Alvarez, Sebastian",
            "raw_employee_name": "ALVAREZ SEBASTIAN",
            "punch_index": 0,
            "in_time": "08:00",
            "out_time": "12:00",
            "regular_hours": 4.0,
            "ot_hours": 0.0,
            "doubletime_hours": 0.0,
            "scraped_at_utc": None,
        }

    def test_punch_index_becomes_punch_idx_in_day(self):
        d = _inv_adp_punch(self.bq_row)
        self.assertEqual(d["punch_idx_in_day"], 0)

    def test_canonical_name_becomes_employee_name(self):
        d = _inv_adp_punch(self.bq_row)
        self.assertEqual(d["employee_name"], "Alvarez, Sebastian")


class TestInvAdpWageRate(unittest.TestCase):
    def setUp(self):
        self.bq_row = {
            "employee_id": "alvarez_s",
            "canonical_name": "Alvarez, Sebastian",
            "wage_rate_dollars": 15.0,
            "ot_rate_dollars": 22.5,
            "is_salaried": False,
            "multi_rate": True,
            "excluded_from_labor_pct": False,
            "earnings_json": '[{"rate": 15.0}]',
            "raw_employee_names_json": '["ALVAREZ SEBASTIAN"]',
            "scraped_at_utc": None,
        }

    def test_canonical_name_becomes_employee_name(self):
        d = _inv_adp_wage_rate(self.bq_row)
        self.assertEqual(d["employee_name"], "Alvarez, Sebastian")

    def test_earnings_json_becomes_rate_history_json(self):
        d = _inv_adp_wage_rate(self.bq_row)
        self.assertEqual(d["rate_history_json"], '[{"rate": 15.0}]')

    def test_multi_rate_preserved(self):
        d = _inv_adp_wage_rate(self.bq_row)
        self.assertTrue(d["multi_rate"])

    def test_raw_employee_names_json_passthrough(self):
        d = _inv_adp_wage_rate(self.bq_row)
        self.assertEqual(d["raw_employee_names_json"], '["ALVAREZ SEBASTIAN"]')


class TestInvAdpEarnings(unittest.TestCase):
    def test_employee_becomes_employee_name(self):
        bq_row = {
            "period_start": datetime.date(2026, 5, 1),
            "period_end": datetime.date(2026, 5, 14),
            "check_date": datetime.date(2026, 5, 20),
            "employee": "Alvarez, Sebastian",
            "raw_employee_name": "ALVAREZ SEBASTIAN",
            "description": "Regular",
            "hours": 80.0,
            "hourly_rate": 15.0,
            "amount": 1200.0,
            "scraped_at_utc": None,
        }
        d = _inv_adp_earnings(bq_row)
        self.assertEqual(d["employee_name"], "Alvarez, Sebastian")
        self.assertEqual(d["period_start"], "2026-05-01")


class TestInvSquareTransaction(unittest.TestCase):
    def test_date_is_string(self):
        bq_row = {
            "transaction_id": "tx-1",
            "date_local": datetime.date(2026, 5, 1),
            "event_type": "Payment",
            "gross_sales_cents": 1000,
            "discount_cents": 0,
            "net_sales_cents": 1000,
            "tip_cents": 100,
            "total_collected_cents": 1100,
            "net_total_cents": 1100,
            "source": "IN_PERSON",
            "staff_name": "Alvarez, Sebastian",
            "location": "Palmetto",
            "created_at_src_iso": "2026-05-01T10:00:00-04:00",
            "created_at_local_iso": "2026-05-01T09:00:00-05:00",
            "scraped_at_utc": None,
        }
        d = _inv_square_transaction(bq_row)
        self.assertEqual(d["date_local"], "2026-05-01")
        self.assertEqual(d["hour_local"], "")
        self.assertEqual(d["dow_local"], "")


class TestInvSquareKdsDaily(unittest.TestCase):
    def test_per_item_times_json_passthrough(self):
        bq_row = {
            "date_local": datetime.date(2026, 5, 1),
            "completed_tickets": 50,
            "completed_items": 150,
            "median_time_per_item_sec": 45.0,
            "p90_time_per_item_sec": 90.0,
            "p95_time_per_item_sec": 110.0,
            "p99_time_per_item_sec": 180.0,
            "pct_tickets_late": 0.05,
            "shift_start": "07:00",
            "shift_end": "16:00",
            "late_tickets": 3,
            "due_tickets": 60,
            "per_item_times_json": "[45, 50, 90]",
            "scraped_at_utc": None,
        }
        d = _inv_square_kds_daily(bq_row)
        self.assertEqual(d["per_item_times_json"], "[45, 50, 90]")


class TestInvGoogleReview(unittest.TestCase):
    def setUp(self):
        self.bq_row = {
            "review_id": "abc123",
            "post_ts_ct": "2026-05-01T10:30:00-05:00",
            "post_date_ct": datetime.date(2026, 5, 1),
            "rating": 5,
            "reviewer": "John D.",
            "comment": "Great coffee!",
            "named_baristas": "Alvarez, Sebastian",
            "named_status": "ok",
            "shift_date_credited": "2026-05-01",
            "shift_assignment_reason": "closest_before",
            "shift_members": "Alvarez, Sebastian; Johnson, Dolce",
            "trainees_on_shift": "",
            "named_credit_each": 10.0,
            "base_credit_each": 0.0,
            "total_bonus": 10.0,
            "review_url": "https://example.com/review/abc123",
            "ingested_at_utc": datetime.datetime(2026, 5, 2, 3, 0, 0, tzinfo=datetime.timezone.utc),
        }

    def test_post_ts_ct_has_apostrophe_prefix(self):
        d = _inv_google_review(self.bq_row)
        self.assertTrue(d["post_ts_ct"].startswith("'"))

    def test_post_date_ct_has_apostrophe_prefix(self):
        d = _inv_google_review(self.bq_row)
        self.assertTrue(d["post_date_ct"].startswith("'"))
        self.assertIn("2026-05-01", d["post_date_ct"])

    def test_shift_date_credited_has_apostrophe_prefix(self):
        d = _inv_google_review(self.bq_row)
        self.assertTrue(d["shift_date_credited"].startswith("'"))

    def test_clickup_message_id_is_blank(self):
        d = _inv_google_review(self.bq_row)
        self.assertEqual(d["clickup_message_id"], "")

    def test_review_id_preserved(self):
        d = _inv_google_review(self.bq_row)
        self.assertEqual(d["review_id"], "abc123")

    def test_empty_shift_date_credited_no_apostrophe(self):
        row = {**self.bq_row, "shift_date_credited": None}
        d = _inv_google_review(row)
        self.assertEqual(d["shift_date_credited"], "")


class TestDryRunSmoke(unittest.TestCase):
    """dry-run smoke: all 11 specs produce valid records without writing."""

    def test_dry_run_all_specs(self):
        from unittest.mock import patch, MagicMock
        import agents.bhaga.scripts.render_raw_sheet_from_bq as m
        import json

        # Mock profile and BQ reads
        mock_profile = {
            "google_account_key": "palmetto",
            "google_sheets": {
                "bhaga_adp_raw": {"spreadsheet_id": "adp-sid"},
                "bhaga_square_raw": {"spreadsheet_id": "sq-sid"},
                "bhaga_review_raw": {"spreadsheet_id": "rev-sid"},
            },
        }

        def fake_read_query(sql):
            return []

        with patch.object(m.pathlib.Path, "read_text", return_value=json.dumps(mock_profile)), \
             patch.object(m, "read_query", fake_read_query), \
             patch.object(m, "resolve_sheet_id", return_value="fake-sid"):
            results = m.render("palmetto", dry_run=True)

        self.assertEqual(len(results), 11)
        for label, info in results.items():
            self.assertIn("bq_rows", info, f"{label}: missing bq_rows")
            self.assertEqual(info["bq_rows"], 0)
            self.assertIsNone(info.get("upsert_result"))


class TestAdpEarningsHeaderDriftRepair(unittest.TestCase):
    def test_adp_earnings_header_drift_triggers_full_replace(self):
        from unittest.mock import patch
        import agents.bhaga.scripts.render_raw_sheet_from_bq as m

        bq_row = {
            "period_start": datetime.date(2026, 6, 1),
            "period_end": datetime.date(2026, 6, 14),
            "check_date": datetime.date(2026, 6, 15),
            "employee": "Alice",
            "raw_employee_name": "ALICE",
            "description": "Regular",
            "hours": 80.0,
            "hourly_rate": 15.0,
            "amount": 1200.0,
            "scraped_at_utc": datetime.datetime(2026, 6, 13, tzinfo=datetime.timezone.utc),
        }

        def drift_write(sid, recs, account="palmetto", scraped_at_utc=None):
            raise ValueError("Header drift on tab 'earnings' (workbook 'BHAGA ADP Raw'")

        replace_calls: list = []

        def fake_replace(sid, recs, account="palmetto", scraped_at_utc=None):
            replace_calls.append(recs)
            return {"replaced": True, "total_after": len(recs)}

        profile_json = (
            '{"google_account_key":"palmetto",'
            '"google_sheets":{"bhaga_adp_raw":{"spreadsheet_id":"adp-sid"}}}'
        )
        earnings_spec = next(s for s in m._TAB_SPECS if s["label"] == "adp_earnings")
        old_write = earnings_spec["write_fn"]

        try:
            earnings_spec["write_fn"] = drift_write
            with patch.object(m.pathlib.Path, "read_text", return_value=profile_json), \
                 patch.object(m, "read_query", return_value=[bq_row]), \
                 patch.object(m, "resolve_sheet_id", return_value="fake-sid"), \
                 patch("agents.bhaga.scripts.render_raw_sheet_from_bq.replace_raw_adp_earnings", side_effect=fake_replace):
                results = m.render("palmetto", tabs=["adp_earnings"], since=datetime.date(2026, 6, 1))
        finally:
            earnings_spec["write_fn"] = old_write

        self.assertEqual(len(replace_calls), 1)
        self.assertEqual(replace_calls[0][0]["employee_name"], "Alice")
        self.assertNotIn("error", results.get("adp_earnings", {}))


if __name__ == "__main__":
    unittest.main()
