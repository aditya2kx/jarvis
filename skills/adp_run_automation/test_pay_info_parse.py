"""Unit tests for wage-rate parse strictness and the plausibility band (Issue #305)."""

from __future__ import annotations

import unittest

from skills.adp_run_automation.pay_info_backend import (
    parse_hourly_pay_rate,
    prepare_pay_info_writes,
    rate_record,
)

# The 2026-09-15 page shape: no "Hourly pay rate" label rendered, but 1.25 sits
# in the prose as an overtime multiplier and was the first decimal on the page.
OT_MULTIPLIER_BODY = (
    "Payroll info Overtime multiplier 1.25 Pay frequency Biweekly "
    "Employee pay type Hourly Added on 06/18/2026"
)


class TestParseHourlyPayRateStrictness(unittest.TestCase):
    def test_ot_multiplier_without_label_is_refused(self):
        with self.assertRaises(ValueError):
            parse_hourly_pay_rate(OT_MULTIPLIER_BODY)

    def test_labelled_rate_is_parsed(self):
        body = (
            "Payroll info Hourly pay rate $15.2500 Overtime Eligible "
            "Pay frequency Biweekly"
        )
        self.assertEqual(parse_hourly_pay_rate(body)["wage_rate_dollars"], 15.25)

    def test_bare_input_value_is_parsed(self):
        parsed = parse_hourly_pay_rate("", input_values=["15.25"])
        self.assertEqual(parsed["wage_rate_dollars"], 15.25)

    def test_unlabelled_prose_figure_is_refused(self):
        body = (
            "Your last pay statement totaled 1,250.75 across 80.00 hours worked. "
            "Contact your payroll administrator with questions."
        )
        with self.assertRaises(ValueError):
            parse_hourly_pay_rate(body)

    def test_added_on_still_returned(self):
        parsed = parse_hourly_pay_rate(
            "Hourly pay rate $15.2500 Added on 06/18/2026"
        )
        self.assertEqual(parsed["added_on"], "2026-06-18")


class TestRatePlausibilityBand(unittest.TestCase):
    NAME = "Browning, Aiden"

    def _writes(self, old: float, new: float) -> tuple[list, list]:
        return prepare_pay_info_writes(
            [rate_record(self.NAME, wage_rate_dollars=new)],
            {
                self.NAME: {
                    "wage_rate_dollars": old,
                    "ot_rate_dollars": None,
                    "is_salaried": False,
                    "multi_rate": False,
                }
            },
        )

    def test_refuses_page_artifact(self):
        self.assertEqual(self._writes(15.25, 1.25), ([], []))

    def test_refuses_more_than_doubling(self):
        self.assertEqual(self._writes(15.25, 40.00), ([], []))

    def test_refuses_below_floor(self):
        self.assertEqual(self._writes(15.25, 5.00), ([], []))

    def test_accepts_a_real_raise(self):
        fills, changes = self._writes(15.25, 16.25)
        self.assertEqual(fills[0]["wage_rate_dollars"], 16.25)
        self.assertEqual(changes, [{"employee_name": self.NAME, "old": 15.25, "new": 16.25}])


if __name__ == "__main__":
    unittest.main()
