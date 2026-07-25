"""Tests for core/migrations/047_plaid_exclude_from_accounting.sql (Issue #189)."""
from __future__ import annotations

import pathlib
import unittest

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "047_plaid_exclude_from_accounting.sql"
).read_text()


class TestMigration047(unittest.TestCase):
    def test_exclude_column(self):
        self.assertIn("exclude_from_accounting", _MIGRATION)

    def test_internal_transfers_seed(self):
        self.assertIn("internal_transfers", _MIGRATION)
        self.assertIn("Internal transfers", _MIGRATION)

    def test_account_mask_on_rules(self):
        self.assertIn("account_mask", _MIGRATION)
        self.assertIn("plaid_category_rules", _MIGRATION)

    def test_spend_view_uses_effective_exclude(self):
        self.assertIn(
            "`jarvis-bhaga-prod.bhaga.vw_plaid_spend_by_category_daily`",
            _MIGRATION,
        )
        self.assertIn("effective_exclude", _MIGRATION)

    def test_money_in_view(self):
        self.assertIn(
            "`jarvis-bhaga-prod.bhaga.vw_plaid_money_in_daily`",
            _MIGRATION,
        )
        self.assertIn("amount < 0", _MIGRATION)

    def test_migrate_is_internal(self):
        self.assertIn("is_internal", _MIGRATION)
        self.assertIn("override_category_id IS NULL", _MIGRATION)
