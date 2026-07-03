"""Tests for core/migrations/032_order_reco_combined.sql (Issue #137 iteration).

No live BigQuery in this environment, so these tests are structural: the
migration must (a) parse under datastore._split_statements the same way
ensure_schema() will apply it, and (b) preserve the combined-table design
from the plan -- shared identity columns pivoted once, per-date column
groups, and a per-date estimated/actual Source indicator computed from
inventory_restock_orders.
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "032_order_reco_combined.sql"
).read_text()


class TestMigrationParses(unittest.TestCase):
    def test_splits_into_single_view_statement(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 1, statements)
        self.assertIn("CREATE OR REPLACE VIEW", statements[0])
        self.assertIn("vw_order_reco_combined", statements[0])

    def test_object_named_as_expected(self):
        self.assertIn("`jarvis-bhaga-prod.bhaga.vw_order_reco_combined`", _MIGRATION)


class TestSharedColumnsPivotedOnce(unittest.TestCase):
    """Item / Current Qty / Avg per day are identical across slots (same
    source row) so they must be COALESCE'd into a single column each, not
    duplicated per date."""

    def test_shared_columns_coalesced_once(self):
        self.assertIn("COALESCE(s1.Item, s2.Item) AS Item", _MIGRATION)
        self.assertIn(
            "COALESCE(s1.`Current Qty`, s2.`Current Qty`) AS `Current Qty`",
            _MIGRATION,
        )
        self.assertIn(
            "COALESCE(s1.`Avg per day`, s2.`Avg per day`) AS `Avg per day`",
            _MIGRATION,
        )


class TestPerDateColumnGroups(unittest.TestCase):
    def test_slot1_columns_suffixed_1(self):
        for col in ("On Hand 1", "Order Tubs 1", "Order Weight 1", "After Restock 1", "Days Left 1"):
            self.assertIn(f"AS `{col}`", _MIGRATION)

    def test_slot2_columns_suffixed_2(self):
        for col in ("On Hand 2", "Order Tubs 2", "Order Weight 2", "After Restock 2", "Days Left 2"):
            self.assertIn(f"AS `{col}`", _MIGRATION)


class TestSourceIndicator(unittest.TestCase):
    """Observation 3: the table must show whether each date's numbers are
    estimated or based on uploaded actuals."""

    def test_source_columns_present(self):
        self.assertIn("AS `Source 1`", _MIGRATION)
        self.assertIn("AS `Source 2`", _MIGRATION)

    def test_source_derived_from_restock_orders_presence(self):
        self.assertIn("IF(src.actual1,'Actuals','Estimated') AS `Source 1`", _MIGRATION)
        # One presence-check subquery per date (actual1, actual2).
        self.assertEqual(_MIGRATION.count("delivery_date=dd.d1"), 1)
        self.assertEqual(_MIGRATION.count("delivery_date=dd.d2"), 1)

    def test_source2_null_when_no_second_date(self):
        self.assertIn(
            "IF(src.d2 IS NULL, NULL, IF(src.actual2,'Actuals','Estimated')) AS `Source 2`",
            _MIGRATION,
        )


class TestJoinShape(unittest.TestCase):
    def test_full_outer_join_on_item(self):
        self.assertIn("FROM s1 FULL OUTER JOIN s2 ON s1.Item = s2.Item", _MIGRATION)

    def test_reads_slot1_and_slot2_from_materialized_table(self):
        self.assertIn(
            "SELECT * FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco` WHERE store='palmetto' AND Slot=1",
            _MIGRATION,
        )
        self.assertIn(
            "SELECT * FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco` WHERE store='palmetto' AND Slot=2",
            _MIGRATION,
        )

    def test_no_water_fill_logic_reintroduced(self):
        # This view is a pure pivot/read -- the water-fill allocation must
        # stay solely in the TVFs (031), never re-derived here.
        self.assertNotIn("GENERATE_ARRAY", _MIGRATION)
        self.assertNotIn("ROW_NUMBER() OVER (ORDER BY sort_key", _MIGRATION)


class TestOrdering(unittest.TestCase):
    def test_orders_by_ord_then_current_qty(self):
        self.assertIn("ORDER BY _ord ASC, `Current Qty` DESC", _MIGRATION)
