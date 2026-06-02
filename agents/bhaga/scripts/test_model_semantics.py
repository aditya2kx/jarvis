#!/usr/bin/env python3
"""Unit tests for the shared pure semantic post-conditions (model_semantics).

Covers the cadence-gating orchestrator (assert_model_semantics) and the
period/review/conservation helpers it composes. Reconciliation is cadence-safe:
a closed period is only required to populate adp_paid when the caller has
confirmed a covering Earnings export with CC-tip lines exists (see
update_model_sheet.period_has_cc_tip_actuals), so a just-closed/unpaid period
that legitimately shows N/A never trips the guard.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import model_semantics as ms

PHEADER = ["period_start", "period_end", "coverage", "is_open", "employee",
           "hours_worked", "our_calc", "adp_paid", "diff", "diff_pct",
           "our_per_hour", "adp_per_hour", "likely_reason"]
DHEADER = ["date", "dow", "period_start", "period_end", "employee",
           "hours_worked", "day_pool", "team_hours_eligible",
           "pct_of_day_hours", "our_share"]


def _prow(ps, pe, is_open, emp, adp):
    return [ps, pe, "full", is_open, emp, "5", "50.00", adp,
            "0", "0%", "10", "10", ""]


def _drow(date, emp, pool, share):
    return [date, "Tue", "2026-05-18", "2026-05-31", emp, "5",
            pool, "10", "50%", share]


class TestPeriodReconciled(unittest.TestCase):
    def test_populated_passes(self):
        grid = [PHEADER, _prow("2026-05-18", "2026-05-31", "no", "A", "50.00")]
        res = ms.assert_period_reconciled(grid, ("2026-05-18", "2026-05-31"))
        self.assertEqual(res["rows_reconciled"], 1)

    def test_na_raises(self):
        grid = [PHEADER, _prow("2026-05-18", "2026-05-31", "no", "A", "N/A")]
        with self.assertRaises(RuntimeError) as ctx:
            ms.assert_period_reconciled(grid, ("2026-05-18", "2026-05-31"))
        self.assertIn("adp reconciliation DEAD", str(ctx.exception))

    def test_missing_period_raises(self):
        grid = [PHEADER, _prow("2026-04-06", "2026-04-19", "no", "A", "50.00")]
        with self.assertRaises(RuntimeError) as ctx:
            ms.assert_period_reconciled(grid, ("2026-05-18", "2026-05-31"))
        self.assertIn("NO closed rows", str(ctx.exception))


class TestReviewBonusPresent(unittest.TestCase):
    def test_rows_present_passes(self):
        vals = [["period_start", "employee", "bonus"], ["2026-05-18", "A", "5.00"]]
        self.assertEqual(ms.assert_review_bonus_present(vals)["review_bonus_rows"], 1)

    def test_header_only_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            ms.assert_review_bonus_present([["period_start", "employee", "bonus"]])
        self.assertIn("0 data rows", str(ctx.exception))

    def test_empty_raises(self):
        with self.assertRaises(RuntimeError):
            ms.assert_review_bonus_present([])


class TestAssertModelSemantics(unittest.TestCase):
    """Cadence-gating: each check fires only when its precondition holds."""

    def _balanced_daily(self):
        return [DHEADER, _drow("2026-05-20", "A", "50.00", "50.00")]

    def _period(self, adp):
        return [PHEADER, _prow("2026-05-18", "2026-05-31", "no", "A", adp)]

    def test_all_checks_pass(self):
        report = ms.assert_model_semantics(
            tip_alloc_daily_values=self._balanced_daily(),
            tip_alloc_period_values=self._period("50.00"),
            review_bonus_values=[["h"], ["r"]],
            require_adp_period=("2026-05-18", "2026-05-31"),
            reviews_credited=True,
        )
        self.assertIn("tip_pool_conservation", report)
        self.assertIn("adp_reconciliation", report)
        self.assertIn("review_bonus", report)

    def test_conservation_always_runs_and_fails(self):
        leaky = [DHEADER, _drow("2026-05-20", "A", "50.00", "40.00")]
        with self.assertRaises(RuntimeError) as ctx:
            ms.assert_model_semantics(
                tip_alloc_daily_values=leaky,
                tip_alloc_period_values=self._period("50.00"),
                review_bonus_values=None,
            )
        self.assertIn("tip pool NOT conserved", str(ctx.exception))

    def test_adp_skipped_when_no_covering_period(self):
        # require_adp_period=None (no Earnings export found) -> N/A is tolerated.
        report = ms.assert_model_semantics(
            tip_alloc_daily_values=self._balanced_daily(),
            tip_alloc_period_values=self._period("N/A"),
            review_bonus_values=None,
            require_adp_period=None,
        )
        self.assertNotIn("adp_reconciliation", report)

    def test_adp_required_when_covering_period_present(self):
        with self.assertRaises(RuntimeError):
            ms.assert_model_semantics(
                tip_alloc_daily_values=self._balanced_daily(),
                tip_alloc_period_values=self._period("N/A"),
                review_bonus_values=None,
                require_adp_period=("2026-05-18", "2026-05-31"),
            )

    def test_reviews_skipped_when_not_credited(self):
        report = ms.assert_model_semantics(
            tip_alloc_daily_values=self._balanced_daily(),
            tip_alloc_period_values=self._period("50.00"),
            review_bonus_values=[["header_only"]],
            reviews_credited=False,
        )
        self.assertNotIn("review_bonus", report)

    def test_reviews_required_when_credited(self):
        with self.assertRaises(RuntimeError):
            ms.assert_model_semantics(
                tip_alloc_daily_values=self._balanced_daily(),
                tip_alloc_period_values=self._period("50.00"),
                review_bonus_values=[["header_only"]],
                reviews_credited=True,
            )


if __name__ == "__main__":
    unittest.main()
