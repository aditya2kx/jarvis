"""Structural tests for core/migrations/051_order_reco_closing_aware_today.sql."""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "051_order_reco_closing_aware_today.sql"
).read_text()


class TestMigration051Parses(unittest.TestCase):
    def test_single_view_replace(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 1, [s[:80] for s in statements])
        self.assertIn("vw_order_reco_next_dates", statements[0])
        self.assertIn("CREATE OR REPLACE VIEW", statements[0])


class TestClosingAwareTodayPredicate(unittest.TestCase):
    def test_keeps_strict_future(self):
        self.assertIn(
            "delivery_date > CURRENT_DATE('America/Chicago')",
            _MIGRATION,
        )

    def test_includes_today_without_closing(self):
        self.assertIn(
            "delivery_date = CURRENT_DATE('America/Chicago')",
            _MIGRATION,
        )
        self.assertIn("inventory_closing_daily", _MIGRATION)
        self.assertIn("submitted_date = CURRENT_DATE('America/Chicago')", _MIGRATION)
        self.assertIn("category = 'base'", _MIGRATION)
        self.assertIn("NOT EXISTS", _MIGRATION)

    def test_does_not_blanket_ge_today(self):
        # Must not regress to migration 031's unconditional >= today.
        self.assertNotIn(
            "delivery_date >= CURRENT_DATE('America/Chicago')",
            _MIGRATION,
        )


if __name__ == "__main__":
    unittest.main()
