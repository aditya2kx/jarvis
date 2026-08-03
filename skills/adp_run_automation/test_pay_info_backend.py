"""Unit tests for pay_info_backend parse helpers (Issue #213)."""

from __future__ import annotations

import unittest

from skills.adp_run_automation.pay_info_backend import (
    directory_search_name,
    parse_hourly_pay_rate,
    rate_record,
)


class TestPayInfoParse(unittest.TestCase):
    def test_directory_search_adds_comma(self):
        self.assertEqual(directory_search_name("Willingham Brooke"), "Willingham, Brooke")
        self.assertEqual(directory_search_name("Willingham, Brooke"), "Willingham, Brooke")

    def test_parse_hourly_rate_brooke_shape(self):
        body = (
            "Payroll info Hourly pay rate $15.2500 Overtime Eligible "
            "Added on 06/18/2026 Something else"
        )
        parsed = parse_hourly_pay_rate(body)
        self.assertEqual(parsed["wage_rate_dollars"], 15.25)
        self.assertEqual(parsed["added_on"], "2026-06-18")

    def test_parse_from_input_value(self):
        parsed = parse_hourly_pay_rate(
            "Pay rate section",
            input_values=["Hourly pay rate $15.2500"],
        )
        self.assertEqual(parsed["wage_rate_dollars"], 15.25)

    def test_rate_record_source(self):
        rec = rate_record("Willingham, Brooke", wage_rate_dollars=15.25, added_on="2026-06-18")
        self.assertEqual(rec["rate_source"], "pay_info")
        self.assertEqual(rec["wage_rate_dollars"], 15.25)
        self.assertEqual(rec["rate_history"][0]["source"], "pay_info")


if __name__ == "__main__":
    unittest.main()
