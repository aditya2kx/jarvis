"""Tests for core/migrations/037_plaid_transactions.sql (Issue #158)."""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "037_plaid_transactions.sql"
).read_text()


class TestMigrationParses(unittest.TestCase):
    def test_splits_into_expected_statements(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 3, statements)
        self.assertTrue(any("plaid_items" in s for s in statements))
        self.assertTrue(any("plaid_transactions" in s for s in statements))
        self.assertTrue(any("vw_plaid_spend_by_category_daily" in s for s in statements))

    def test_object_names(self):
        self.assertIn("`jarvis-bhaga-prod.bhaga.plaid_items`", _MIGRATION)
        self.assertIn("`jarvis-bhaga-prod.bhaga.plaid_transactions`", _MIGRATION)
        self.assertIn(
            "`jarvis-bhaga-prod.bhaga.vw_plaid_spend_by_category_daily`",
            _MIGRATION,
        )


class TestSemantics(unittest.TestCase):
    def test_outflows_only_in_spend_view(self):
        self.assertIn("amount > 0", _MIGRATION)

    def test_pfc_primary_present(self):
        self.assertIn("pfc_primary", _MIGRATION)

    def test_no_access_token_column(self):
        # Tokens stay in Secret Manager, never BQ (comment may mention the word).
        ddl = "\n".join(
            line for line in _MIGRATION.splitlines() if not line.strip().startswith("--")
        )
        self.assertNotIn("access_token", ddl)
