"""Structural tests for core/migrations/067_order_reco_write_then_swap.sql."""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "067_order_reco_write_then_swap.sql"
).read_text()


class TestMigration067(unittest.TestCase):
    def test_replaces_slot_n_only(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 1, [s[:80] for s in statements])
        self.assertIn("tvf_order_reco_slot_n", statements[0])
        self.assertNotIn("tvf_order_reco_slot1", statements[0])

    def test_s_prev_qualifies_latest_generation(self):
        self.assertIn("QUALIFY ROW_NUMBER()", _MIGRATION)
        self.assertIn("PARTITION BY Item ORDER BY refreshed_at DESC", _MIGRATION)


if __name__ == "__main__":
    unittest.main()
