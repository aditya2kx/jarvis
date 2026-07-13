"""Tests for core/migrations/036_inventory_base_runway_dual.sql (Issue #164).

No live BigQuery in this environment, so these tests are structural: the
migration must (a) parse under datastore._split_statements the same way
ensure_schema() will apply it, and (b) preserve dual-slot Base runway
semantics — Actuals-only Restock 1/2, Status Risky when restock empty or
stockout < restock, Stockout 2 chain.
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "036_inventory_base_runway_dual.sql"
).read_text()


class TestMigrationParses(unittest.TestCase):
    def test_splits_into_single_view_statement(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 1, statements)
        self.assertIn("CREATE OR REPLACE VIEW", statements[0])
        self.assertIn("vw_inventory_base_runway", statements[0])


class TestColumns(unittest.TestCase):
    def test_dual_runway_columns_present(self):
        for col in (
            "AS Base",
            "AS Stock",
            "AS `Vel per day`",
            "AS `Days left`",
            "AS `Stockout 1`",
            "AS `Restock 1`",
            "AS `Qty 1`",
            "AS `Status 1`",
            "AS `Stockout 2`",
            "AS `Restock 2`",
            "AS `Qty 2`",
            "AS `Status 2`",
        ):
            self.assertIn(col, _MIGRATION)

    def test_legacy_single_columns_removed(self):
        self.assertNotIn("AS `Stockout date`", _MIGRATION)
        self.assertNotIn("AS `Next restock`", _MIGRATION)
        self.assertNotIn("AS `Restock qty`", _MIGRATION)

    def test_status_risky_and_fine(self):
        self.assertIn("'Risky'", _MIGRATION)
        self.assertIn("'Fine'", _MIGRATION)


class TestSemantics(unittest.TestCase):
    def test_reads_order_assistant_and_restock_orders(self):
        self.assertIn("vw_inventory_order_assistant", _MIGRATION)
        self.assertIn("inventory_restock_orders", _MIGRATION)

    def test_actuals_only_no_schedule(self):
        # Restock dates come from inventory_restock_orders, never schedule.
        self.assertNotIn("inventory_restock_schedule", _MIGRATION)
        self.assertNotIn("vw_order_reco_next_dates", _MIGRATION)

    def test_excludes_blade(self):
        self.assertIn("item != 'Blade'", _MIGRATION)

    def test_america_chicago_for_today(self):
        self.assertIn("CURRENT_DATE('America/Chicago')", _MIGRATION)

    def test_stockout1_uses_floor_days_left(self):
        self.assertIn("FLOOR(days_left)", _MIGRATION)

    def test_stockout2_chains_via_on_hand_at_d1(self):
        self.assertIn("on_hand_at_d1", _MIGRATION)

    def test_status_risky_when_restock_empty(self):
        self.assertIn("WHEN j.restock_1 IS NULL THEN 'Risky'", _MIGRATION)
        self.assertIn("WHEN j.restock_2 IS NULL THEN 'Risky'", _MIGRATION)

    def test_status_risky_when_stockout_before_restock(self):
        self.assertIn("WHEN j.stockout_1 < j.restock_1 THEN 'Risky'", _MIGRATION)
        self.assertIn("WHEN j.stockout_2 < j.restock_2 THEN 'Risky'", _MIGRATION)

    def test_orders_by_days_left_ascending(self):
        self.assertIn("ORDER BY j.days_left ASC NULLS LAST", _MIGRATION)

    def test_no_water_fill_logic(self):
        self.assertNotIn("GENERATE_ARRAY", _MIGRATION)


if __name__ == "__main__":
    unittest.main()
