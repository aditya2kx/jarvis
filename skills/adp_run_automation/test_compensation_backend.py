"""Wage-rate inference tests, focused on the solo-premium feedback loop (#309).

Solo-shift pay decides eligibility by comparing an employee's base rate to
$15.25, and it pays the premium as a second Regular earnings line on the same
check. So the base rate that inference reports is an input to the feature *and*
downstream of its output — the one shape where a plausible-looking rule silently
turns the feature off.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation.compensation_backend import infer_wage_rates


def earning(
    employee: str,
    *,
    check_date: str,
    rate: float,
    hours: float = 8.0,
    description: str = "Regular",
) -> dict:
    """One parsed Earnings & Hours line, matching parse_xlsx's output keys."""
    return {
        "employee_name": employee,
        "raw_employee_name": employee.upper(),
        "check_date": check_date,
        "hours": hours,
        "hourly_rate": rate,
        "amount": round(rate * hours, 2),
        "description": description,
    }


def rate_for(records: list[dict], employee: str) -> dict:
    return next(r for r in records if r["employee_name"] == employee)


class TestBaseRateIsTheFloorOfTheNewestCheck(unittest.TestCase):
    NAME = "Willingham, Brooke"

    def test_solo_premium_line_does_not_raise_the_base_rate(self):
        """The premium must not lift the base rate it is measured against.

        Team hours at $15.25 and solo hours at $16.25 land on one check. If
        $16.25 became the base, the next cycle would read the employee as
        ineligible and quietly stop paying the premium.
        """
        records = infer_wage_rates([
            earning(self.NAME, check_date="2026-09-25", rate=15.25, hours=30.0),
            earning(self.NAME, check_date="2026-09-25", rate=16.25, hours=4.75),
        ])
        self.assertEqual(rate_for(records, self.NAME)["wage_rate_dollars"], 15.25)

    def test_base_rate_is_independent_of_earnings_line_order(self):
        """Rows sharing a check_date must not let XLSX ordering pick the rate."""
        lines = [
            earning(self.NAME, check_date="2026-09-25", rate=15.25, hours=30.0),
            earning(self.NAME, check_date="2026-09-25", rate=16.25, hours=4.75),
        ]
        forward = infer_wage_rates(lines)
        reversed_ = infer_wage_rates(list(reversed(lines)))
        self.assertEqual(
            rate_for(forward, self.NAME)["wage_rate_dollars"],
            rate_for(reversed_, self.NAME)["wage_rate_dollars"],
        )

    def test_multi_rate_still_flags_the_two_rate_check(self):
        """Eligibility is preserved, but the two-rate check stays visible."""
        records = infer_wage_rates([
            earning(self.NAME, check_date="2026-09-25", rate=15.25, hours=30.0),
            earning(self.NAME, check_date="2026-09-25", rate=16.25, hours=4.75),
        ])
        self.assertTrue(rate_for(records, self.NAME)["multi_rate"])

    def test_a_real_raise_is_picked_up_from_the_next_check(self):
        """A raise wins once it owns a check outright — newest check, its floor."""
        records = infer_wage_rates([
            earning(self.NAME, check_date="2026-09-11", rate=15.25),
            earning(self.NAME, check_date="2026-09-25", rate=17.00),
        ])
        self.assertEqual(rate_for(records, self.NAME)["wage_rate_dollars"], 17.00)

    def test_single_rate_employee_is_unchanged(self):
        records = infer_wage_rates([
            earning(self.NAME, check_date="2026-09-11", rate=15.25),
            earning(self.NAME, check_date="2026-09-25", rate=15.25),
        ])
        record = rate_for(records, self.NAME)
        self.assertEqual(record["wage_rate_dollars"], 15.25)
        self.assertFalse(record["multi_rate"])

    def test_overtime_lines_never_become_the_base_rate(self):
        records = infer_wage_rates([
            earning(self.NAME, check_date="2026-09-25", rate=15.25, hours=40.0),
            earning(
                self.NAME,
                check_date="2026-09-25",
                rate=22.875,
                hours=2.0,
                description="Overtime",
            ),
        ])
        record = rate_for(records, self.NAME)
        self.assertEqual(record["wage_rate_dollars"], 15.25)
        self.assertEqual(record["ot_rate_dollars"], 22.875)

    def test_salaried_employee_has_no_regular_hourly_rate(self):
        records = infer_wage_rates([
            earning(
                "Krause, Lindsay",
                check_date="2026-09-25",
                rate=0.0,
                hours=0.0,
                description="Salary",
            ),
        ])
        record = rate_for(records, "Krause, Lindsay")
        self.assertIsNone(record["wage_rate_dollars"])
        self.assertTrue(record["is_salaried"])


if __name__ == "__main__":
    unittest.main()
