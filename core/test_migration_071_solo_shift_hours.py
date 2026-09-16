"""Structural tests for core/migrations/071_solo_shift_hours.sql (Issue #309)."""
from __future__ import annotations

import pathlib
import re
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "071_solo_shift_hours.sql"
).read_text()


def _strip_comments(sql: str) -> str:
    """Drop ``--`` lines so assertions test SQL, not the prose explaining it."""
    return "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )


_SQL = _strip_comments(_MIGRATION)


def _statement_creating(name: str) -> str:
    """The single statement whose CREATE target is ``name``."""
    pattern = re.compile(
        rf"CREATE (?:TABLE IF NOT EXISTS|OR REPLACE VIEW) `[^`]*\.{re.escape(name)}`"
    )
    matches = [s for s in _split_statements(_SQL) if pattern.search(s)]
    assert len(matches) == 1, f"expected exactly one CREATE for {name}, got {len(matches)}"
    return matches[0]


class TestMigration071Parses(unittest.TestCase):
    def test_splits_into_table_and_two_views(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 3, [s[:80] for s in statements])
        self.assertIn("CREATE TABLE IF NOT EXISTS", statements[0])
        self.assertIn("model_solo_hours_daily", statements[0])
        self.assertIn("vw_solo_hours_daily", statements[1])
        self.assertIn("vw_solo_hours_period", statements[2])

    def test_table_is_partitioned_by_date(self):
        self.assertIn("PARTITION BY date", _MIGRATION)

    def test_loader_timestamp_column_name(self):
        # load_model_rows stamps materialized_at_utc; materialized_at silently
        # fails the staged MERGE with "Column ... is not present in table".
        self.assertIn("materialized_at_utc", _MIGRATION)
        self.assertNotRegex(_MIGRATION, r"materialized_at\s+TIMESTAMP")


class TestMoneyPrecision(unittest.TestCase):
    """bhaga.mdc invariant 4 — cents as integers, dollars only for display."""

    def test_premium_stored_as_int64_cents(self):
        self.assertRegex(_MIGRATION, r"premium_cents\s+INT64")

    def test_no_float_premium_column_on_the_table(self):
        self.assertNotIn("premium_dollars", _statement_creating("model_solo_hours_daily"))

    def test_minutes_stored_as_int64(self):
        for col in ("solo_minutes", "team_minutes", "total_minutes"):
            self.assertRegex(_MIGRATION, rf"{col}\s+INT64")


class TestPeriodRollupBoundsByStartOnly(unittest.TestCase):
    """Regression guard for the truncated-period-end bug found in sandbox.

    model_labor_period clips the OPEN period's pay_period_end to the model's
    data window (on 2026-09-15 the 2026-09-07 row ended 09-14, not 09-20). A
    `BETWEEN pay_period_start AND pay_period_end` join therefore dropped every
    solo hour worked after the window, understating the premium that gets keyed
    into ADP. Bound by start only.
    """

    def _period_view(self) -> str:
        return _statement_creating("vw_solo_hours_period")

    def test_does_not_bound_by_pay_period_end(self):
        view = self._period_view()
        self.assertNotIn("pay_period_end", view)
        self.assertNotRegex(view, r"(?i)between\s+p\.pay_period_start")

    def test_assigns_each_day_to_latest_start_at_or_before_it(self):
        view = self._period_view()
        self.assertIn("MAX(st.ps) AS period_start", view)
        self.assertIn("st.ps <= s.date", view)

    def test_reports_observed_coverage_so_partial_periods_are_visible(self):
        view = self._period_view()
        self.assertIn("MIN(date)", view)
        self.assertIn("MAX(date)", view)
        self.assertIn("first_date", view)
        self.assertIn("last_date", view)

    def test_eligibility_is_carried_not_recomputed_in_sql(self):
        # Eligibility is decided once, in the tested Python attribution, so the
        # threshold/rate policy has exactly one home.
        view = self._period_view()
        self.assertIn("LOGICAL_OR(eligible)", view)
        for literal in ("15.25", "16.25", "1525", "1625"):
            self.assertNotIn(literal, view)


class TestNoHardcodedPolicy(unittest.TestCase):
    """Tunables live in bhaga.store_config (user-preferences #29)."""

    def test_migration_carries_no_rate_or_threshold_literals(self):
        for literal in ("15.25", "16.25", "1525", "1625"):
            self.assertNotIn(
                literal, _SQL, f"{literal} must come from store_config, not the DDL"
            )

    def test_min_block_threshold_is_not_in_sql(self):
        self.assertNotRegex(_SQL, r"min_block")


class TestDailyRollup(unittest.TestCase):
    def test_exposes_single_cover_and_headcount(self):
        view = _statement_creating("vw_solo_hours_daily")
        for col in (
            "single_cover_minutes",
            "single_cover_hours",
            "team_hours",
            "total_hours",
            "headcount",
            "employees_with_solo",
        ):
            self.assertIn(col, view)

    def test_daily_rollup_groups_by_date(self):
        self.assertRegex(_statement_creating("vw_solo_hours_daily"), r"GROUP BY\s+date")


class TestFullyQualifiedReferences(unittest.TestCase):
    def test_every_reference_is_project_qualified(self):
        # core.datastore._rewrite_dataset rewrites the dataset for sandbox runs by
        # string-matching the fully-qualified prod name; a bare table name would
        # leak a sandbox write into prod.
        refs = re.findall(r"`([^`]+)`", _MIGRATION)
        for ref in refs:
            self.assertTrue(
                ref.startswith("jarvis-bhaga-prod.bhaga."),
                f"unqualified reference: {ref}",
            )


if __name__ == "__main__":
    unittest.main()
