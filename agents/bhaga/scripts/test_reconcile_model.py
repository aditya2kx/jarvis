"""Tests for reconcile_model — in-sync fixture passes, skewed cell fails."""
from __future__ import annotations

import datetime
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


def _load():
    os.environ.setdefault("BHAGA_DATASTORE", "disabled")
    import agents.bhaga.scripts.reconcile_model as m
    return m


class TestCompareTabs(unittest.TestCase):
    """Verify that _compare_tabs from verify_bq_parity is correctly wired."""

    def setUp(self):
        self.m = _load()

    def test_in_sync_fixture_passes(self):
        sheet_rows = [
            ["period_start", "period_end", "employee", "our_calc"],
            ["'2026-04-07", "'2026-04-20", "Alice", "125.00"],
            ["'2026-04-07", "'2026-04-20", "Bob",   "100.00"],
        ]
        bq_rows = [
            ["period_start", "period_end", "employee", "our_calc"],
            [datetime.date(2026, 4, 7), datetime.date(2026, 4, 20), "Alice", 125.0],
            [datetime.date(2026, 4, 7), datetime.date(2026, 4, 20), "Bob",   100.0],
        ]
        from agents.bhaga.scripts.verify_bq_parity import _compare_tabs
        result = _compare_tabs("tip_alloc_period", sheet_rows, bq_rows)
        mismatches = [m for m in result.get("mismatches", []) if m.get("type") != "row_count"]
        self.assertEqual(mismatches, [], f"Expected no mismatches, got: {mismatches}")

    def test_skewed_cell_fails_with_location(self):
        sheet_rows = [
            ["period_start", "employee", "our_calc"],
            ["'2026-04-07", "Alice", "125.00"],
        ]
        bq_rows = [
            ["period_start", "employee", "our_calc"],
            [datetime.date(2026, 4, 7), "Alice", 999.99],  # <-- drift
        ]
        from agents.bhaga.scripts.verify_bq_parity import _compare_tabs
        result = _compare_tabs("tip_alloc_period", sheet_rows, bq_rows)
        mismatches = [m for m in result.get("mismatches", []) if m.get("type") != "row_count"]
        self.assertTrue(len(mismatches) > 0, "Expected at least one mismatch for drifted our_calc")
        # The mismatch should contain the column name.
        col_names = {m.get("col") or m.get("col_name") for m in mismatches}
        self.assertTrue(
            any("our_calc" in str(c) for c in col_names),
            f"Expected 'our_calc' in mismatch column names, got: {col_names}"
        )


class TestReadBqAsRows(unittest.TestCase):
    """_read_bq_as_rows projects BQ dicts to the Sheet header order."""

    def setUp(self):
        self.m = _load()

    def test_projects_to_sheet_header_order(self):
        sheet_header = ["period_start", "employee", "our_calc"]
        bq_dicts = [
            {"period_start": datetime.date(2026, 4, 7), "employee": "Alice",
             "our_calc": 125.0, "materialized_at_utc": "2026-06-04"},
        ]
        with unittest.mock.patch.object(self.m, "read_query", return_value=bq_dicts):
            result = self.m._read_bq_as_rows("model_tip_alloc_period", ["period_start"], sheet_header=sheet_header)
        self.assertEqual(result[0], ["period_start", "employee", "our_calc"])
        self.assertEqual(result[1], [datetime.date(2026, 4, 7), "Alice", 125.0])
        # materialized_at_utc must not appear in output (in _SKIP_COLS)
        self.assertNotIn("materialized_at_utc", result[0])

    def test_returns_header_only_on_bq_error(self):
        sheet_header = ["period_start", "employee"]
        with unittest.mock.patch.object(self.m, "read_query", side_effect=Exception("BQ down")):
            result = self.m._read_bq_as_rows("model_tip_alloc_period", ["period_start"], sheet_header=sheet_header)
        self.assertEqual(result, [["period_start", "employee"]])

    def test_read_bq_as_rows_maps_employee_to_employee_name(self):
        sheet_header = ["period_start", "employee_name", "amount"]
        bq_dicts = [
            {"period_start": datetime.date(2026, 6, 1), "employee": "Alice", "amount": 100.0},
        ]
        with unittest.mock.patch.object(self.m, "read_query", return_value=bq_dicts):
            result = self.m._read_bq_as_rows(
                "adp_earnings", ["period_start"], sheet_header=sheet_header,
            )
        self.assertEqual(result[1][1], "Alice")


class TestAssertConservation(unittest.TestCase):

    def setUp(self):
        self.m = _load()

    def test_positive_allocated_passes(self):
        bq_rows = [
            {"period_start": "2026-04-07", "period_end": "2026-04-20",
             "total_allocated": 500.0, "is_open": False},
        ]
        with unittest.mock.patch.object(self.m, "read_query", return_value=bq_rows):
            violations = self.m._assert_tip_pool_conservation()
        self.assertEqual(violations, [])

    def test_zero_allocated_on_closed_period_fails(self):
        bq_rows = [
            {"period_start": "2026-04-07", "period_end": "2026-04-20",
             "total_allocated": 0.0, "is_open": False},
        ]
        with unittest.mock.patch.object(self.m, "read_query", return_value=bq_rows):
            violations = self.m._assert_tip_pool_conservation()
        self.assertTrue(len(violations) > 0)
        self.assertIn("2026-04-07", violations[0])

    def test_open_period_skipped(self):
        bq_rows = [
            {"period_start": "2026-06-02", "period_end": "2026-06-15",
             "total_allocated": 0.0, "is_open": True},  # open — skip
        ]
        with unittest.mock.patch.object(self.m, "read_query", return_value=bq_rows):
            violations = self.m._assert_tip_pool_conservation()
        self.assertEqual(violations, [])

    def test_bq_error_returns_informational_message(self):
        with unittest.mock.patch.object(self.m, "read_query", side_effect=Exception("BQ down")):
            violations = self.m._assert_tip_pool_conservation()
        self.assertTrue(len(violations) > 0)
        self.assertIn("BQ error", violations[0])


if __name__ == "__main__":
    unittest.main()
