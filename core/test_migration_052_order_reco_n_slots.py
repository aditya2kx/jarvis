"""Structural tests for core/migrations/052_order_reco_n_slots.sql."""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "052_order_reco_n_slots.sql"
).read_text()


class TestMigration052Parses(unittest.TestCase):
    def test_view_and_tvf(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 2, [s[:80] for s in statements])
        self.assertIn("vw_order_reco_next_dates", statements[0])
        self.assertIn("tvf_order_reco_slot_n", statements[1])


class TestNextDatesCap(unittest.TestCase):
    def test_config_driven_cap_default_4(self):
        self.assertIn("order_reco_max_slots", _MIGRATION)
        self.assertRegex(_MIGRATION, r"COALESCE\([\s\S]*?,\s*4\s*\)")
        self.assertNotIn("WHERE slot <= 2", _MIGRATION)

    def test_keeps_closing_aware_today(self):
        self.assertIn("inventory_closing_daily", _MIGRATION)
        self.assertIn("delivery_date = CURRENT_DATE('America/Chicago')", _MIGRATION)


class TestSlotNChainsFromPrev(unittest.TestCase):
    def test_reads_materialized_prev_not_nested_slot1(self):
        tvf = _MIGRATION[_MIGRATION.index("tvf_order_reco_slot_n") :]
        self.assertIn("inventory_order_reco", tvf)
        self.assertIn("target_slot - 1", tvf)
        self.assertIn("target_slot", tvf)
        self.assertNotIn("tvf_order_reco_slot1`(", tvf)


if __name__ == "__main__":
    unittest.main()
