"""Tests for core/migrations/030_restock_plan.sql (Issue #137).

Structural tests only (no live BigQuery in this environment): the migration
must parse under datastore._split_statements the same way ensure_schema()
will apply it, and declare the two operator-data tables with their merge-key
columns, so cloud/webhook/handler.py's restock command has somewhere to write.
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "030_restock_plan.sql"
).read_text()


class TestMigrationParses(unittest.TestCase):
    def test_splits_into_two_ddl_statements(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 2, statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS", statements[0])
        self.assertIn("CREATE TABLE IF NOT EXISTS", statements[1])

    def test_objects_named_as_expected(self):
        self.assertIn("`jarvis-bhaga-prod.bhaga.inventory_restock_schedule`", _MIGRATION)
        self.assertIn("`jarvis-bhaga-prod.bhaga.inventory_restock_orders`", _MIGRATION)


class TestScheduleTableColumns(unittest.TestCase):
    def test_merge_key_columns_present(self):
        self.assertIn("store          STRING  NOT NULL", _MIGRATION)
        self.assertIn("delivery_date  DATE    NOT NULL", _MIGRATION)


class TestOrdersTableColumns(unittest.TestCase):
    def test_columns_present(self):
        self.assertIn("item           STRING   NOT NULL", _MIGRATION)
        self.assertIn("quantity_tubs  FLOAT64", _MIGRATION)
