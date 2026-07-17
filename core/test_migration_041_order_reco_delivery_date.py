"""Structural tests for core/migrations/041_order_reco_delivery_date.sql."""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "041_order_reco_delivery_date.sql"
).read_text()


class TestMigration041Parses(unittest.TestCase):
    def test_splits_into_expected_statements(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        # ALTER, next_dates VIEW, slot1 TVF, slot2 TVF, combined VIEW
        self.assertEqual(len(statements), 5, [s[:80] for s in statements])
        self.assertIn("ADD COLUMN IF NOT EXISTS delivery_date", statements[0])
        self.assertIn("vw_order_reco_next_dates", statements[1])
        self.assertIn("tvf_order_reco_slot1", statements[2])
        self.assertIn("tvf_order_reco_slot2", statements[3])
        self.assertIn("vw_order_reco_combined", statements[4])


class TestNextDatesStrictlyFuture(unittest.TestCase):
    def test_excludes_today(self):
        self.assertIn(
            "delivery_date > CURRENT_DATE('America/Chicago')",
            _MIGRATION,
        )
        self.assertNotIn(
            "delivery_date >= CURRENT_DATE('America/Chicago')",
            _MIGRATION,
        )


class TestDeliveryDateBind(unittest.TestCase):
    def test_tvfs_emit_delivery_date(self):
        self.assertIn("dd.d1 AS delivery_date", _MIGRATION)
        self.assertIn("dd.d2 AS delivery_date", _MIGRATION)

    def test_combined_joins_by_delivery_date_not_slot_alone(self):
        self.assertIn("r.delivery_date = dd.d1", _MIGRATION)
        self.assertIn("r.delivery_date = dd.d2", _MIGRATION)
        # Must not pivot solely on Slot=1/2 (the desync bug).
        self.assertNotIn("WHERE store='palmetto' AND Slot=1", _MIGRATION)
        self.assertNotIn("WHERE store = 'palmetto' AND Slot = 1", _MIGRATION)


class TestSlot2StillReadsMaterializedSlot1(unittest.TestCase):
    def test_no_nested_slot1_tvf(self):
        slot2_start = _MIGRATION.index("tvf_order_reco_slot2")
        slot2_body = _MIGRATION[slot2_start:]
        self.assertNotIn("tvf_order_reco_slot1`(", slot2_body)
        self.assertIn("FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco`", slot2_body)


if __name__ == "__main__":
    unittest.main()
