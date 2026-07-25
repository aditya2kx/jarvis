"""Tests for core/migrations/045_plaid_taxonomy_rules.sql (Issue #160)."""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "045_plaid_taxonomy_rules.sql"
).read_text()


class TestMigration045(unittest.TestCase):
    def test_splits(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertGreaterEqual(len(statements), 8, statements)

    def test_tables(self):
        self.assertIn("`jarvis-bhaga-prod.bhaga.plaid_taxonomy_nodes`", _MIGRATION)
        self.assertIn("`jarvis-bhaga-prod.bhaga.plaid_category_rules`", _MIGRATION)

    def test_txn_columns(self):
        for col in (
            "category_id",
            "subcategory_id",
            "rule_id",
            "override_category_id",
            "override_subcategory_id",
            "categorized_at",
        ):
            self.assertIn(col, _MIGRATION)
