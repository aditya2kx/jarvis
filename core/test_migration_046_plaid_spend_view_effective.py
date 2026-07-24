"""Tests for core/migrations/046_plaid_spend_view_effective.sql (Issue #160)."""
from __future__ import annotations

import pathlib
import unittest

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "046_plaid_spend_view_effective.sql"
).read_text()


class TestMigration046(unittest.TestCase):
    def test_view_name(self):
        self.assertIn(
            "`jarvis-bhaga-prod.bhaga.vw_plaid_spend_by_category_daily`",
            _MIGRATION,
        )

    def test_effective_category(self):
        self.assertIn("override_category_id", _MIGRATION)
        self.assertIn("category_label", _MIGRATION)
        self.assertIn("is_internal", _MIGRATION)
        self.assertIn("amount > 0", _MIGRATION)
