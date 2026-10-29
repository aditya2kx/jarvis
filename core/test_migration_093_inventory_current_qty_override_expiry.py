"""Tests for core/migrations/093_inventory_current_qty_override_expiry.sql (Issue #438).

Structural only (no live BQ): a Current Qty override must apply only until the
next ClickUp closing for that base, instead of COALESCEing over every later
closing (058).
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_DIR = pathlib.Path(__file__).parent / "migrations"
_MIGRATION = (_DIR / "093_inventory_current_qty_override_expiry.sql").read_text()
_058 = (_DIR / "058_inventory_current_qty_overrides.sql").read_text()


def _body(sql: str) -> str:
    return sql[sql.index("CREATE OR REPLACE VIEW"):]


class TestMigrationParses(unittest.TestCase):
    def test_single_view_statement(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 1, statements)
        self.assertIn("vw_inventory_order_assistant", statements[0])


class TestOverrideExpiry(unittest.TestCase):
    def test_override_only_when_newer_than_latest_closing(self):
        self.assertIn(
            "IF(ov.updated_at >= t.submitted_ts, ov.quantity_units, t.quantity_units) AS current_qty",
            _MIGRATION,
        )
        self.assertNotIn("COALESCE(ov.quantity_units", _MIGRATION)

    def test_exposes_source(self):
        self.assertIn(
            "IF(ov.updated_at >= t.submitted_ts, 'override', 'clickup') AS current_qty_source",
            _MIGRATION,
        )
        self.assertIn("lr.current_qty_source,", _MIGRATION)

    def test_rest_of_view_unchanged_from_058(self):
        new = _body(_MIGRATION)
        new = new.replace(
            "    IF(ov.updated_at >= t.submitted_ts, ov.quantity_units, t.quantity_units) AS current_qty,\n"
            "    IF(ov.updated_at >= t.submitted_ts, 'override', 'clickup') AS current_qty_source,\n",
            "    COALESCE(ov.quantity_units, t.quantity_units) AS current_qty,\n",
        ).replace("  lr.current_qty_source,\n", "")
        self.assertEqual(new, _body(_058))


if __name__ == "__main__":
    unittest.main()
