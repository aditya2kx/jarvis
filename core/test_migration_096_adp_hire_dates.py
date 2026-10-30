"""Tests for core/migrations/096_adp_hire_dates.sql (Issue #450).

Structural only (no live BQ here): one idempotent CREATE with the columns the pay_info writer MERGEs.
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "096_adp_hire_dates.sql"
).read_text()


class TestMigrationParses(unittest.TestCase):
    def test_single_idempotent_create(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        statements = [s for s in statements if not all(
            l.strip().startswith("--") or not l.strip() for l in s.splitlines()
        )]
        self.assertEqual(len(statements), 1, statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_hire_dates`", statements[0])

    def test_columns_match_writer(self):
        for col in ("store", "employee_name", "hire_date", "source", "scraped_at_utc"):
            self.assertIn(col, _MIGRATION)
        self.assertIn("hire_date       DATE", _MIGRATION)


if __name__ == "__main__":
    unittest.main()
