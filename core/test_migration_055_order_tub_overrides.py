"""Structural tests for core/migrations/055_order_tub_overrides.sql."""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "055_order_tub_overrides.sql"
).read_text()


class TestMigration055Parses(unittest.TestCase):
    def test_splits_into_table_and_two_tvfs(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 3, [s[:80] for s in statements])
        self.assertIn("CREATE TABLE IF NOT EXISTS", statements[0])
        self.assertIn("inventory_order_tub_overrides", statements[0])
        self.assertIn("tvf_order_reco_slot1", statements[1])
        self.assertIn("tvf_order_reco_slot_n", statements[2])


class TestOverrideSemantics(unittest.TestCase):
    def test_override_table_columns(self):
        for col in ("delivery_date", "quantity_tubs", "updated_by", "updated_at"):
            self.assertIn(col, _MIGRATION)

    def test_overrides_cte_and_locked_budget(self):
        self.assertIn("inventory_order_tub_overrides", _MIGRATION)
        self.assertIn("locked_tubs", _MIGRATION)
        self.assertIn("ov.item IS NULL", _MIGRATION)

    def test_manual_zero_uses_item_presence_not_qty(self):
        # 0 is a valid pin — must key off ov.item, not override_tubs IS NOT NULL.
        self.assertIn("WHEN ov.item IS NOT NULL THEN ov.override_tubs", _MIGRATION)

    def test_actuals_still_win(self):
        self.assertIn("WHEN h.is_actual THEN COALESCE(a.actual_tubs, 0)", _MIGRATION)

    def test_blade_excluded_from_candidates(self):
        self.assertIn("o.item != 'Blade'", _MIGRATION)


if __name__ == "__main__":
    unittest.main()
