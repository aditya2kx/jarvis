"""Unit tests for pay_info_backend parse helpers (Issue #213)."""

from __future__ import annotations

import unittest

from skills.adp_run_automation.pay_info_backend import (
    _PEOPLE_SEARCH_PLACEHOLDER_RE,
    directory_search_name,
    parse_hourly_pay_rate,
    prepare_pay_info_writes,
    rate_record,
)


class TestPayInfoParse(unittest.TestCase):
    def test_directory_search_adds_comma(self):
        self.assertEqual(directory_search_name("Willingham Brooke"), "Willingham, Brooke")
        self.assertEqual(directory_search_name("Willingham, Brooke"), "Willingham, Brooke")

    def test_people_search_placeholder_covers_2026_hub(self):
        self.assertTrue(_PEOPLE_SEARCH_PLACEHOLDER_RE.search("Search people"))
        self.assertTrue(
            _PEOPLE_SEARCH_PLACEHOLDER_RE.search("Search for an employee's name")
        )
        self.assertFalse(_PEOPLE_SEARCH_PLACEHOLDER_RE.search("Search Shortcuts"))

    def test_parse_hourly_rate_brooke_shape(self):
        body = (
            "Payroll info Hourly pay rate $15.2500 Overtime Eligible "
            "Added on 06/18/2026 Something else"
        )
        parsed = parse_hourly_pay_rate(body)
        self.assertEqual(parsed["wage_rate_dollars"], 15.25)
        self.assertEqual(parsed["added_on"], "2026-06-18")

    def test_parse_shadow_split_label_and_value(self):
        """sdf-input keeps $15.2500 in shadow; walker concatenates label + value."""
        parsed = parse_hourly_pay_rate(
            "Hourly pay rate Added on 07/20/2026",
            input_values=["Hourly pay rate $15.2500"],
        )
        self.assertEqual(parsed["wage_rate_dollars"], 15.25)
        self.assertEqual(parsed["added_on"], "2026-07-20")

    def test_rate_record_source(self):
        rec = rate_record("Willingham, Brooke", wage_rate_dollars=15.25, added_on="2026-06-18")
        self.assertEqual(rec["rate_source"], "pay_info")
        self.assertEqual(rec["wage_rate_dollars"], 15.25)
        self.assertEqual(rec["rate_history"][0]["source"], "pay_info")

    def test_prepare_writes_updates_rate_and_preserves_ot(self):
        incoming = [
            rate_record("Perales, Elizabeth", wage_rate_dollars=16.00),
        ]
        existing = {
            "Perales, Elizabeth": {
                "wage_rate_dollars": 15.25,
                "ot_rate_dollars": 22.875,
                "is_salaried": False,
                "multi_rate": False,
            }
        }
        fills, changes = prepare_pay_info_writes(incoming, existing)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["ot_rate_dollars"], 22.875)
        self.assertEqual(changes[0]["old"], 15.25)
        self.assertEqual(changes[0]["new"], 16.00)

    def test_prepare_writes_preserves_salaried_flag(self):
        incoming = [rate_record("Krause, Lindsay", wage_rate_dollars=25.0)]
        existing = {
            "Krause, Lindsay": {
                "wage_rate_dollars": 25.0,
                "ot_rate_dollars": 37.5,
                "is_salaried": True,
                "multi_rate": False,
            }
        }
        fills, changes = prepare_pay_info_writes(incoming, existing)
        self.assertEqual(fills[0]["is_salaried"], True)
        self.assertEqual(fills[0]["ot_rate_dollars"], 37.5)
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main()
