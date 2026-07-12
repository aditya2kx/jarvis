"""Tests for core/migrations/035_inventory_base_runway.sql (Issue #156).

No live BigQuery in this environment, so these tests are structural: the
migration must (a) parse under datastore._split_statements the same way
ensure_schema() will apply it, and (b) preserve the Base runway semantics
from the jam — burn-down days left, Actuals-only next restock, Risky/Fine.
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "035_inventory_base_runway.sql"
).read_text()


class TestMigrationParses(unittest.TestCase):
    def test_splits_into_single_view_statement(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 1, statements)
        self.assertIn("CREATE OR REPLACE VIEW", statements[0])
        self.assertIn("vw_inventory_base_runway", statements[0])

    def test_object_named_as_expected(self):
        self.assertIn(
            "`jarvis-bhaga-prod.bhaga.vw_inventory_base_runway`",
            _MIGRATION,
        )


class TestColumns(unittest.TestCase):
    def test_runway_columns_present(self):
        for col in (
            "AS Base",
            "AS Stock",
            "AS `Vel per day`",
            "AS `Days left`",
            "AS `Stockout date`",
            "AS `Next restock`",
            "AS `Restock qty`",
            "AS Status",
        ):
            self.assertIn(col, _MIGRATION)

    def test_status_risky_and_fine(self):
        self.assertIn("'Risky'", _MIGRATION)
        self.assertIn("'Fine'", _MIGRATION)


class TestSemantics(unittest.TestCase):
    def test_reads_order_assistant_and_restock_orders(self):
        self.assertIn("vw_inventory_order_assistant", _MIGRATION)
        self.assertIn("inventory_restock_orders", _MIGRATION)

    def test_excludes_blade(self):
        self.assertIn("item != 'Blade'", _MIGRATION)

    def test_america_chicago_for_today(self):
        self.assertIn("CURRENT_DATE('America/Chicago')", _MIGRATION)

    def test_actuals_only_next_restock(self):
        # Next restock comes from inventory_restock_orders, never schedule-only.
        self.assertNotIn("inventory_restock_schedule", _MIGRATION)
        self.assertIn(
            "delivery_date >= CURRENT_DATE('America/Chicago')",
            _MIGRATION,
        )

    def test_stockout_uses_floor_days_left(self):
        self.assertIn("FLOOR(days_left)", _MIGRATION)

    def test_orders_by_days_left_ascending(self):
        self.assertIn("ORDER BY w.days_left ASC NULLS LAST", _MIGRATION)

    def test_no_water_fill_logic(self):
        self.assertNotIn("GENERATE_ARRAY", _MIGRATION)
