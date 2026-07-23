"""Tests for core/migrations/042_inventory_prefer_tub_over_packaging.sql.

Structural only (no live BQ): migration must parse and encode the packaging
preference so Current Qty prefers tub readings over box/case dual fields.
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "042_inventory_prefer_tub_over_packaging.sql"
).read_text()


class TestMigrationParses(unittest.TestCase):
    def test_splits_into_two_view_statements(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 2, statements)
        self.assertIn("vw_inventory_base_latest_daily", statements[0])
        self.assertIn("vw_inventory_order_assistant", statements[1])


class TestPackagingPreference(unittest.TestCase):
    def test_prefers_non_packaging_raw_text(self):
        self.assertIn(r"\b(box|boxes|case|cases)\b", _MIGRATION)
        self.assertIn("THEN 1", _MIGRATION)
        self.assertIn("ELSE 0", _MIGRATION)
        self.assertIn("field_id ASC", _MIGRATION)

    def test_both_views_use_preference(self):
        self.assertEqual(_MIGRATION.count(r"\b(box|boxes|case|cases)\b"), 2)


if __name__ == "__main__":
    unittest.main()
