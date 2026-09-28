"""Structural tests for migrations 080/081 and the `-- jarvis:script` splitter mode (Issue #350)."""
from __future__ import annotations

import pathlib
import re
import unittest

from core.datastore import _split_statements

_DIR = pathlib.Path(__file__).parent / "migrations"
_080 = (_DIR / "080_order_reco_history.sql").read_text()
_081 = (_DIR / "081_sp_refresh_order_reco.sql").read_text()


class TestScriptDirective(unittest.TestCase):
    def test_script_file_is_one_statement(self):
        stmts = _split_statements(_081)
        self.assertEqual(len(stmts), 1)
        self.assertIn("CREATE OR REPLACE PROCEDURE", stmts[0])
        self.assertTrue(stmts[0].rstrip().endswith("END;"))

    def test_plain_file_still_splits(self):
        self.assertEqual(_split_statements("SELECT 1; SELECT ';'; -- x;\nSELECT 2;"),
                         ["SELECT 1", "SELECT ';'", "-- x;\nSELECT 2"])

    def test_directive_must_lead_the_file(self):
        self.assertEqual(len(_split_statements("SELECT 1;\n-- jarvis:script\nSELECT 2;")), 2)

    def test_empty_script(self):
        self.assertEqual(_split_statements("-- jarvis:script\n"), [])


class TestMigration080(unittest.TestCase):
    def test_history_tables_are_partitioned(self):
        for table in ("inventory_order_reco_history", "inventory_order_reco_runs", "inventory_edit_log"):
            m = re.search(rf"CREATE TABLE IF NOT EXISTS `[^`]*\.{table}`.*?\)\s*PARTITION BY", _080, re.S)
            self.assertIsNotNone(m, table)

    def test_live_table_gets_source_and_run_id(self):
        self.assertIn("ADD COLUMN IF NOT EXISTS Source STRING", _080)
        self.assertIn("ADD COLUMN IF NOT EXISTS run_id STRING", _080)

    def test_fingerprint_covers_every_input(self):
        view = _080[_080.index("vw_order_reco_inputs_fingerprint"):]
        for src in ("inventory_order_tub_overrides", "inventory_restock_orders", "inventory_restock_schedule",
                    "inventory_current_qty_overrides", "inventory_usage_day_overrides",
                    "inventory_closing_daily", "store_config", "CURRENT_DATE('America/Chicago')"):
            self.assertIn(src, view)

    def test_no_destructive_statements(self):
        self.assertNotRegex(_080, r"(?i)\b(DROP|DELETE|TRUNCATE)\b")


class TestMigration081(unittest.TestCase):
    def test_store_is_a_parameter_not_hardcoded(self):
        self.assertNotIn("'palmetto'", _081)

    def test_view_read_once_per_pass(self):
        self.assertEqual(_081.count("vw_inventory_order_assistant`"), 1)

    def test_swap_and_history_commit_together(self):
        txn = _081[_081.index("BEGIN TRANSACTION;"):_081.index("COMMIT TRANSACTION;")]
        self.assertIn("MERGE `jarvis-bhaga-prod.bhaga.inventory_order_reco`", txn)
        self.assertIn("inventory_order_reco_history", txn)
        self.assertIn("'committed'", txn)

    def test_deterministic_tie_break(self):
        self.assertIn("ORDER BY sort_key, item, k", _081)

    def test_history_is_append_only(self):
        self.assertNotRegex(_081, r"(?i)(DELETE|UPDATE|MERGE)[^;]*inventory_order_reco_(history|runs)")

    def test_failures_leave_a_breadcrumb(self):
        self.assertIn("order_reco_failed run_id=", _081)
        self.assertIn("'superseded'", _081)
        self.assertIn("ROLLBACK TRANSACTION", _081)


if __name__ == "__main__":
    unittest.main()
