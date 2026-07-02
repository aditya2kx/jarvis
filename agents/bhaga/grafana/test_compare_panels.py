"""Tests for compare_panels.py (Issue #126) — pure-function coverage only.

Network/Grafana calls are exercised manually (see README.md), not mocked
here — these tests lock the SQL-inlining and diff logic, which is where a
silent bug would produce a false PASS (the worst failure mode for an
evidence harness).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import compare_panels as cp  # noqa: E402


class TestInlineMigrationObject(unittest.TestCase):
    def test_tvf_call_gets_inlined_with_params_substituted(self):
        sql = "SELECT * FROM `jarvis-bhaga-prod.bhaga.tvf_order_reco`(10, 120)"
        inlined = cp._inline_migration_object(sql)
        self.assertNotIn("tvf_order_reco", inlined)
        self.assertNotIn("CREATE", inlined)
        # ship_days/max_tubs literal substitution landed in the body.
        self.assertIn("current_qty - 10 *", inlined)
        self.assertIn("120 - SUM(on_hand_arrival)", inlined)

    def test_view_ref_gets_inlined(self):
        sql = "SELECT * FROM `jarvis-bhaga-prod.bhaga.vw_order_assistant_table`"
        inlined = cp._inline_migration_object(sql)
        self.assertNotIn("vw_order_assistant_table", inlined)
        self.assertNotIn("CREATE", inlined)
        self.assertIn("vw_inventory_order_assistant", inlined)

    def test_inlined_sql_has_no_leftover_ddl_keywords(self):
        for sql in (
            "SELECT * FROM `bhaga.tvf_order_reco`($oa_ship_days, $oa_max_tubs)",
            "SELECT * FROM `bhaga.vw_order_assistant_table`",
        ):
            inlined = cp._inline_migration_object(
                sql.replace("$oa_ship_days", "10").replace("$oa_max_tubs", "120")
            )
            self.assertNotRegex(inlined, r"CREATE\s+OR\s+REPLACE")


class TestDiffRows(unittest.TestCase):
    def test_identical_rows_return_none(self):
        a = {"columns": ["x"], "rows": [(1,), (2,)]}
        b = {"columns": ["x"], "rows": [(2,), (1,)]}  # order-insensitive
        self.assertIsNone(cp._diff_rows(a, b))

    def test_extra_branch_row_is_reported(self):
        a = {"columns": ["x"], "rows": [(1,)]}
        b = {"columns": ["x"], "rows": [(1,), (2,)]}
        diff = cp._diff_rows(a, b)
        self.assertIsNotNone(diff)
        self.assertIn("only in branch", diff)

    def test_column_mismatch_is_reported(self):
        a = {"columns": ["x"], "rows": []}
        b = {"columns": ["x", "y"], "rows": []}
        diff = cp._diff_rows(a, b)
        self.assertIn("COLUMN MISMATCH", diff)

    def test_query_error_is_reported(self):
        a = {"error": "boom"}
        b = {"columns": [], "rows": []}
        diff = cp._diff_rows(a, b)
        self.assertIn("query error", diff)


class TestOaPanelIds(unittest.TestCase):
    def test_only_order_assistant_panels_are_waived(self):
        self.assertEqual(cp.OA_PANEL_IDS, frozenset({79, 81}))


if __name__ == "__main__":
    unittest.main()
