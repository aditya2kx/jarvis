"""Structural tests for core/migrations/048_inventory_usage_day_overrides.sql."""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "048_inventory_usage_day_overrides.sql"
).read_text()


class TestMigration048Parses(unittest.TestCase):
    def test_splits_into_table_and_two_views(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 3, [s[:80] for s in statements])
        self.assertIn("CREATE TABLE IF NOT EXISTS", statements[0])
        self.assertIn("inventory_usage_day_overrides", statements[0])
        self.assertIn("vw_inventory_order_assistant", statements[1])
        self.assertIn("vw_inventory_usage_day_audit", statements[2])


class TestOverrideSemantics(unittest.TestCase):
    def test_force_include_and_exclude_modes(self):
        self.assertIn("force_include", _MIGRATION)
        self.assertIn("force_exclude", _MIGRATION)
        self.assertIn("inventory_usage_day_overrides", _MIGRATION)

    def test_effective_eligible_joins_overrides(self):
        self.assertIn("rule_eligible", _MIGRATION)
        self.assertIn("OR o.mode = 'force_include'", _MIGRATION)
        self.assertIn("IFNULL(o.mode, '') != 'force_exclude'", _MIGRATION)

    def test_force_include_bypasses_outlier_flags(self):
        self.assertIn("IFNULL(e.override_mode, '') != 'force_include'", _MIGRATION)
        self.assertIn("IFNULL(override_mode, '') != 'force_include'", _MIGRATION)

    def test_operator_force_exclude_note(self):
        self.assertIn("operator force_exclude", _MIGRATION)

    def test_thirty_day_chicago_window(self):
        self.assertIn("INTERVAL 30 DAY", _MIGRATION)
        self.assertIn("CURRENT_DATE('America/Chicago')", _MIGRATION)

    def test_audit_columns(self):
        for col in (
            "AS qty",
            "AS delta",
            "AS rule_eligible",
            "AS in_avg",
            "AS status",
            "AS reason",
            "AS override_mode",
            "AS high_bar",
            "AS similar_tomorrow_passes",
        ):
            self.assertIn(col, _MIGRATION)


if __name__ == "__main__":
    unittest.main()
