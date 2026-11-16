"""Structural tests for migration 098 (soft-ended employee perks).

098 copies vw_model_payroll_period forward from 075; the only intended change
is that the perks CTE skips perks from their end_period_start on. A copy that
drifts anywhere else would silently change payroll numbers.
"""
from __future__ import annotations

import pathlib
import re
import unittest

from core.datastore import _split_statements

_DIR = pathlib.Path(__file__).parent / "migrations"

_END_FILTER = "AND (e.end_period_start IS NULL OR p.period_start < e.end_period_start)"
_MATCH = (
    "IFNULL(e.pay_period, '') = '' OR e.pay_period = CONCAT( "
    "CAST(p.period_start AS STRING), '..', CAST(p.period_end AS STRING) )"
)


def _strip_comments(sql: str) -> str:
    return "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )


def _payroll_view(path: str) -> str:
    sql = _strip_comments((_DIR / path).read_text())
    pattern = re.compile(r"CREATE OR REPLACE VIEW `[^`]*\.vw_model_payroll_period`")
    matches = [s for s in _split_statements(sql) if pattern.search(s)]
    assert len(matches) == 1, f"expected exactly one payroll view in {path}"
    return matches[0]


def _squash(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


_SQL_098 = _strip_comments((_DIR / "098_employee_perks_end.sql").read_text())
_VIEW_075 = _squash(_payroll_view("075_payroll_labor_effective_rates.sql"))
_VIEW_098 = _squash(_payroll_view("098_employee_perks_end.sql"))


class TestEmployeePerksEndColumns(unittest.TestCase):
    def test_columns_are_rerunnable(self):
        for col in ("end_period_start DATE", "ended_at TIMESTAMP", "ended_by STRING"):
            self.assertIn(f"ADD COLUMN IF NOT EXISTS {col}", _SQL_098)

    def test_no_row_is_deleted(self):
        self.assertNotRegex(_SQL_098, r"(?i)\bDELETE\b|\bTRUNCATE\b")


class TestPayrollViewSkipsEndedPerks(unittest.TestCase):
    def test_recurring_or_dated_match_is_grouped_before_the_end_filter(self):
        # Without the parentheses the end filter would bind only to the dated
        # branch and a recurring perk would never stop.
        self.assertIn(f"ON ( {_MATCH} ) {_END_FILTER}", _VIEW_098)

    def test_view_is_075_apart_from_the_perks_filter(self):
        self.assertIn(f"ON {_MATCH}", _VIEW_075)
        self.assertEqual(
            _VIEW_075.replace(f"ON {_MATCH}", f"ON ( {_MATCH} ) {_END_FILTER}"),
            _VIEW_098,
        )


if __name__ == "__main__":
    unittest.main()
