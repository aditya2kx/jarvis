"""Structural tests for core/migrations/072_solo_shift_remote_hours.sql.

Migration 072 adds remote_minutes/remote_hours and republishes both solo views.
The views are copied forward from 071, so these tests guard the two things a
copy-paste can silently break: the NULL handling on the new columns, and the
period view's bound-by-start-only join.
"""
from __future__ import annotations

import pathlib
import re
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "072_solo_shift_remote_hours.sql"
).read_text()


def _strip_comments(sql: str) -> str:
    """Drop ``--`` lines so assertions test SQL, not the prose explaining it."""
    return "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )


_SQL = _strip_comments(_MIGRATION)


def _statement_creating(name: str) -> str:
    pattern = re.compile(rf"CREATE OR REPLACE VIEW `[^`]*\.{re.escape(name)}`")
    matches = [s for s in _split_statements(_SQL) if pattern.search(s)]
    assert len(matches) == 1, f"expected exactly one CREATE for {name}"
    return matches[0]


class TestMigration072Parses(unittest.TestCase):
    def test_splits_into_an_alter_and_two_views(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 3, [s[:80] for s in statements])
        self.assertIn("ALTER TABLE", statements[0])
        self.assertIn("vw_solo_hours_daily", statements[1])
        self.assertIn("vw_solo_hours_period", statements[2])

    def test_the_alter_is_rerunnable(self):
        # ensure_schema replays every migration on every apply.
        alter = [s for s in _split_statements(_SQL) if "ALTER TABLE" in s][0]
        self.assertEqual(alter.count("ADD COLUMN IF NOT EXISTS"), 2)

    def test_both_new_columns_are_added(self):
        alter = [s for s in _split_statements(_SQL) if "ALTER TABLE" in s][0]
        self.assertIn("remote_minutes INT64", alter)
        self.assertIn("remote_hours   FLOAT64", alter)


class TestRemoteIsASubsetOfTeam(unittest.TestCase):
    """remote_minutes is a slice of team_minutes, never a fourth bucket.

    If a view ever added remote to the total, solo + team == total would break
    (bhaga.mdc invariant 2) and the Labor page would double-count remote time.
    """

    def test_the_daily_view_derives_on_floor_by_subtraction(self):
        view = _statement_creating("vw_solo_hours_daily")
        self.assertRegex(
            view, r"SUM\(total_minutes\)\s*-\s*SUM\(COALESCE\(remote_minutes, 0\)\)"
        )

    def test_the_period_view_derives_on_floor_by_subtraction(self):
        view = _statement_creating("vw_solo_hours_period")
        self.assertRegex(
            view, r"SUM\(total_minutes\)\s*-\s*SUM\(remote_minutes\)"
        )

    def test_neither_view_adds_remote_into_a_total(self):
        for name in ("vw_solo_hours_daily", "vw_solo_hours_period"):
            view = _statement_creating(name)
            self.assertNotRegex(
                view, r"total_minutes\s*\+\s*\w*remote", f"{name} inflates the total"
            )


class TestPreMigrationRowsDoNotPoisonSums(unittest.TestCase):
    """Rows written before 072 have NULL remote_minutes.

    A bare SUM over a column containing NULL is fine in BigQuery, but
    SUM(total) - SUM(remote) yields NULL for any group with no remote rows at
    all unless the NULL is coalesced — which is every day in history.
    """


    def test_the_daily_view_coalesces_remote_minutes(self):
        view = _statement_creating("vw_solo_hours_daily")
        self.assertNotRegex(view, r"SUM\(remote_minutes\)")
        self.assertIn("COALESCE(remote_minutes, 0)", view)

    def test_the_period_view_coalesces_before_aggregating(self):
        # The period view coalesces once in the `assigned` CTE, so the outer
        # SUMs are already NULL-free.
        view = _statement_creating("vw_solo_hours_period")
        self.assertIn("COALESCE(s.remote_minutes, 0) AS remote_minutes", view)


class TestPeriodViewKeepsThe071Fix(unittest.TestCase):
    """071 bounds days by pay_period_start only; 072 must not reintroduce BETWEEN.

    model_labor_period truncates the open period's end to the data window, so a
    BETWEEN join silently drops solo hours worked after it and under-pays.
    """

    def test_no_between_join_on_period_end(self):
        view = _statement_creating("vw_solo_hours_period")
        self.assertNotIn("BETWEEN", view.upper())
        self.assertNotIn("pay_period_end", view)

    def test_days_assign_to_the_latest_start_at_or_before_them(self):
        view = _statement_creating("vw_solo_hours_period")
        self.assertIn("st.ps <= s.date", view)
        self.assertIn("MAX(st.ps) AS period_start", view)


class TestMoneyStaysInCents(unittest.TestCase):
    def test_premium_cents_is_the_aggregate_and_dollars_is_derived(self):
        for name in ("vw_solo_hours_daily", "vw_solo_hours_period"):
            view = _statement_creating(name)
            self.assertIn("SUM(premium_cents)", view)
            self.assertRegex(view, r"SUM\(premium_cents\) / 100\.0")


if __name__ == "__main__":
    unittest.main()
