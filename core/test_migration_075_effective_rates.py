"""Structural tests for migrations 074 + 075 (Issue #343 effective-dated rates).

075 copies the payroll view forward from 068 and the live labor views from 069.
These guard what a copy-paste can silently break: the shift-date join that
keeps closed periods on their old rate, the fallback that keeps unpriced
employees on the pre-075 formula, and 074's rerunnable seed.
"""
from __future__ import annotations

import pathlib
import re
import unittest

from core.datastore import _split_statements

_DIR = pathlib.Path(__file__).parent / "migrations"


def _strip_comments(sql: str) -> str:
    return "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )


_SQL_074 = _strip_comments((_DIR / "074_wage_rate_history_punch_notes.sql").read_text())
_SQL_075 = _strip_comments((_DIR / "075_payroll_labor_effective_rates.sql").read_text())

_EFFECTIVE_JOIN = re.compile(
    r"vw_wage_rate_effective`\s+er\s+ON er\.employee_id = s\.(canonical_name|employee_id)"
    r"\s+AND s\.date BETWEEN er\.effective_from AND er\.effective_to"
)


def _statement_creating(sql: str, name: str) -> str:
    pattern = re.compile(rf"CREATE OR REPLACE VIEW `[^`]*\.{re.escape(name)}`")
    matches = [s for s in _split_statements(sql) if pattern.search(s)]
    assert len(matches) == 1, f"expected exactly one CREATE for {name}"
    return matches[0]


class TestMigration074(unittest.TestCase):
    def test_seed_is_rerunnable(self):
        seed = [s for s in _split_statements(_SQL_074) if s.lstrip().startswith("INSERT")][0]
        self.assertIn("NOT EXISTS", seed)
        self.assertIn("DATE '2000-01-01'", seed)

    def test_ranges_end_the_day_before_the_next_change(self):
        view = _statement_creating(_SQL_074, "vw_wage_rate_effective")
        self.assertRegex(view, r"DATE_SUB\(\s*LEAD\(effective_date\) OVER \(PARTITION BY employee_id ORDER BY effective_date\),\s*INTERVAL 1 DAY")
        self.assertIn("DATE '9999-12-31'", view)

    def test_punch_note_column_is_rerunnable(self):
        self.assertIn("ADD COLUMN IF NOT EXISTS note STRING", _SQL_074)


class TestPayrollPricesByShiftDate(unittest.TestCase):
    view = _statement_creating(_SQL_075, "vw_model_payroll_period")

    def test_segments_join_the_rate_on_the_shift_date(self):
        self.assertRegex(self.view, _EFFECTIVE_JOIN)

    def test_segments_group_by_rate(self):
        self.assertRegex(self.view, r"GROUP BY p\.period_start, p\.period_end, p\.is_open, s\.canonical_name,\s*er\.wage_rate_dollars, er\.ot_rate_dollars")

    def test_any_unpriced_segment_falls_back_to_the_displayed_rate(self):
        self.assertIn("COUNTIF(wage_rate_dollars IS NULL) > 0", self.view)
        self.assertIn("WHEN p.est_wages IS NOT NULL THEN p.est_wages", self.view)
        self.assertIn("COALESCE(rae.wage_rate_dollars, w.wage_rate_dollars) AS wage_rate_dollars", self.view)

    def test_every_money_column_reads_the_one_gross(self):
        self.assertIn("CAST(ROUND(r.gross, 2) AS FLOAT64)", self.view)
        self.assertIn("COALESCE(r.gross, CAST(0 AS NUMERIC))", self.view)
        self.assertIn("r.gross - CAST(COALESCE(e.adp_wages_paid, 0) AS NUMERIC)", self.view)

    def test_roster_branches_all_carry_est_wages(self):
        # UNION ALL of SELECT * is positional; a branch missing est_wages
        # would shift every later column.
        for cte in ("tip_rows", "punch_rows", "carry_rows"):
            body = re.search(rf"{cte} AS \((.*?)\n\),", self.view, re.S).group(1)
            self.assertRegex(body, r"est_wages\s*\n\s*FROM", cte)

    def test_output_columns_unchanged_from_068(self):
        select = self.view[self.view.rindex("\nSELECT"):]
        for col in ("wage_rate_dollars", "ot_rate_dollars", "est_gross_pay", "est_total_pay",
                    "wage_diff", "tip_diff", "bonus_diff"):
            self.assertIn(col, select)


class TestLiveLaborPricesByShiftDate(unittest.TestCase):
    def test_both_live_views_join_the_effective_rate(self):
        for name in ("vw_labor_daily_live", "vw_labor_weekly_live"):
            view = _statement_creating(_SQL_075, name)
            self.assertRegex(view, _EFFECTIVE_JOIN, name)
            self.assertEqual(view.count("COALESCE(er.wage_rate_dollars, w.wage_rate_dollars)"), 2, name)
            self.assertNotRegex(view, r"total_hours \* IFNULL\(w\.wage_rate_dollars", name)


if __name__ == "__main__":
    unittest.main()
