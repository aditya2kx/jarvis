"""Effective-dated wage rates (Issue #343)."""

from __future__ import annotations

import datetime
import unittest

from skills.adp_run_automation.wage_rate_history import (
    SEED_DATE,
    history_rows_for_changes,
    history_rows_from_earnings,
    pay_period_start,
    resolve_effective_date,
)

D = datetime.date
ANCHOR = D(2026, 5, 17)  # palmetto.json pay_periods_anchor_end_date (biweekly)


class TestPayPeriodStart(unittest.TestCase):
    def test_periods_around_the_raise(self):
        self.assertEqual(pay_period_start(D(2026, 9, 25), anchor_end=ANCHOR), D(2026, 9, 21))
        self.assertEqual(pay_period_start(D(2026, 9, 21), anchor_end=ANCHOR), D(2026, 9, 21))
        self.assertEqual(pay_period_start(D(2026, 10, 4), anchor_end=ANCHOR), D(2026, 9, 21))
        self.assertEqual(pay_period_start(D(2026, 9, 20), anchor_end=ANCHOR), D(2026, 9, 7))

    def test_dates_before_the_anchor(self):
        self.assertEqual(pay_period_start(D(2026, 5, 17), anchor_end=ANCHOR), D(2026, 5, 4))
        self.assertEqual(pay_period_start(D(2026, 5, 3), anchor_end=ANCHOR), D(2026, 4, 20))


class TestResolveEffectiveDate(unittest.TestCase):
    TODAY = D(2026, 9, 25)

    def test_added_on_in_current_period_is_used(self):
        self.assertEqual(
            resolve_effective_date(
                today=self.TODAY, added_on="2026-09-21",
                latest_effective=SEED_DATE, anchor_end=ANCHOR,
            ),
            (D(2026, 9, 21), "added_on"),
        )

    def test_hire_date_added_on_falls_back_to_period_start(self):
        self.assertEqual(
            resolve_effective_date(
                today=self.TODAY, added_on="2026-03-02",
                latest_effective=SEED_DATE, anchor_end=ANCHOR,
            ),
            (D(2026, 9, 21), "period_start"),
        )

    def test_added_on_not_after_latest_change_falls_back(self):
        self.assertEqual(
            resolve_effective_date(
                today=self.TODAY, added_on="2026-09-10",
                latest_effective=D(2026, 9, 21), anchor_end=ANCHOR,
            ),
            (D(2026, 9, 21), "period_start"),
        )

    def test_missing_or_malformed_added_on(self):
        for added in (None, "", "09/21/2026"):
            self.assertEqual(
                resolve_effective_date(
                    today=self.TODAY, added_on=added,
                    latest_effective=SEED_DATE, anchor_end=ANCHOR,
                ),
                (D(2026, 9, 21), "period_start"),
            )

    def test_future_added_on_is_ignored(self):
        self.assertEqual(
            resolve_effective_date(
                today=self.TODAY, added_on="2026-09-28",
                latest_effective=SEED_DATE, anchor_end=ANCHOR,
            )[1],
            "period_start",
        )


class TestHistoryRowsForChanges(unittest.TestCase):
    LATEST = {"Johnson, Dolce": {"effective_date": SEED_DATE, "wage_rate_dollars": 16.25}}

    def _rows(self, rates, latest=None):
        return history_rows_for_changes(
            rates, self.LATEST if latest is None else latest,
            today=D(2026, 9, 25), anchor_end=ANCHOR,
        )

    def test_raise_gets_a_row_starting_at_the_period(self):
        rows = self._rows([{"employee_id": "Johnson, Dolce", "wage_rate_dollars": 18.0, "added_on": None}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["effective_date"], D(2026, 9, 21))
        self.assertEqual(rows[0]["wage_rate_dollars"], 18.0)
        self.assertEqual(rows[0]["source"], "pay_info")

    def test_unchanged_rate_writes_nothing(self):
        self.assertEqual(
            self._rows([{"employee_id": "Johnson, Dolce", "wage_rate_dollars": 16.25}]), [],
        )

    def test_first_observation_seeds_from_2000(self):
        rows = self._rows([{"employee_id": "New, Hire", "wage_rate_dollars": 15.25}], latest={})
        self.assertEqual(rows[0]["effective_date"], SEED_DATE)

    def test_missing_rate_or_name_is_skipped(self):
        self.assertEqual(self._rows([{"employee_id": "Johnson, Dolce", "wage_rate_dollars": None}]), [])
        self.assertEqual(self._rows([{"wage_rate_dollars": 18.0}]), [])


def _line(emp, check, start, desc="Regular"):
    return {"employee_name": emp, "check_date": check, "period_start": start, "description": desc}


class TestHistoryRowsFromEarnings(unittest.TestCase):
    def test_older_check_never_undoes_a_newer_pay_info_change(self):
        latest = {"Johnson, Dolce": {"effective_date": D(2026, 9, 21), "wage_rate_dollars": 18.0}}
        rows = history_rows_from_earnings(
            [{"employee_id": "Johnson, Dolce", "wage_rate_dollars": 16.25}],
            [_line("Johnson, Dolce", "2026-09-25", "2026-09-07")],
            latest,
        )
        self.assertEqual(rows, [])

    def test_paid_raise_starts_at_its_period(self):
        latest = {"Alvarez, Ana": {"effective_date": SEED_DATE, "wage_rate_dollars": 15.0}}
        rows = history_rows_from_earnings(
            [{"employee_id": "Alvarez, Ana", "wage_rate_dollars": 15.5}],
            [_line("Alvarez, Ana", "2026-09-11", "2026-08-24"),
             _line("Alvarez, Ana", "2026-09-25", "2026-09-07"),
             _line("Alvarez, Ana", "2026-09-30", "2026-09-01", desc="Bonus")],
            latest,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["effective_date"], D(2026, 9, 7))
        self.assertEqual(rows[0]["source"], "earnings")

    def test_unchanged_or_unpaid_writes_nothing(self):
        latest = {"Alvarez, Ana": {"effective_date": SEED_DATE, "wage_rate_dollars": 15.0}}
        self.assertEqual(history_rows_from_earnings(
            [{"employee_id": "Alvarez, Ana", "wage_rate_dollars": 15.0}],
            [_line("Alvarez, Ana", "2026-09-25", "2026-09-07")], latest), [])
        self.assertEqual(history_rows_from_earnings(
            [{"employee_id": "Roster, Stub", "wage_rate_dollars": None}], [], latest), [])

    def test_first_observation_seeds_from_2000(self):
        rows = history_rows_from_earnings(
            [{"employee_id": "New, Hire", "wage_rate_dollars": 15.25}],
            [_line("New, Hire", "2026-09-25", "2026-09-07")], {},
        )
        self.assertEqual(rows[0]["effective_date"], SEED_DATE)


if __name__ == "__main__":
    unittest.main()
