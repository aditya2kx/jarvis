"""Tests for core/migrations/095_clickup_mention_aliases.sql (Issue #447).

Structural only (no live BQ): the table DDL plus an insert-only seed, so
re-applying the migration never overwrites a runtime `clickup-mention set`.
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "095_clickup_mention_aliases.sql"
).read_text()


class TestMigrationParses(unittest.TestCase):
    def test_create_then_seed(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        statements = [s for s in statements if not all(
            l.strip().startswith("--") or not l.strip() for l in s.splitlines()
        )]
        self.assertEqual(len(statements), 2, statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS", statements[0])
        self.assertIn("MERGE", statements[1])

    def test_seed_is_insert_only(self):
        self.assertIn("WHEN NOT MATCHED THEN", _MIGRATION)
        self.assertNotIn("WHEN MATCHED", _MIGRATION)


if __name__ == "__main__":
    unittest.main()
