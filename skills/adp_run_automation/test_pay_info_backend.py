"""Unit tests for pay_info_backend parse helpers (Issue #213)."""

from __future__ import annotations

import unittest

from unittest import mock

from skills.adp_run_automation.pay_info_backend import (
    _PEOPLE_SEARCH_PLACEHOLDER_RE,
    AmbiguousEmployeeError,
    accepted_directory_names,
    directory_search_name,
    dismiss_blocking_modals,
    parse_hourly_pay_rate,
    prepare_pay_info_writes,
    rate_record,
    report_pay_info_issues,
    select_directory_match,
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

    def test_prepare_writes_refuses_token_hourly_drop(self):
        incoming = [rate_record("Krause, Lindsay", wage_rate_dollars=1.25)]
        existing = {
            "Krause, Lindsay": {
                "wage_rate_dollars": 25.0,
                "ot_rate_dollars": 37.5,
                "is_salaried": False,
                "multi_rate": False,
            }
        }
        fills, changes = prepare_pay_info_writes(incoming, existing)
        self.assertEqual(fills, [])
        self.assertEqual(changes, [])


class TestSelectDirectoryMatch(unittest.TestCase):
    """A wrong wage rate is worse than a missing one — refuse to guess."""

    ROSTER = ["Johnson, Dolce", "Johnson, Dolce J", "Flores, Juan"]

    def test_exact_match_wins_over_a_longer_name(self):
        self.assertEqual(
            select_directory_match(self.ROSTER, "Johnson, Dolce"), "Johnson, Dolce"
        )

    def test_matches_terminated_employee(self):
        self.assertEqual(
            select_directory_match(self.ROSTER, "Flores, Juan"), "Flores, Juan"
        )

    def test_refuses_a_near_match(self):
        with self.assertRaises(AmbiguousEmployeeError):
            select_directory_match(["Johnson, Dolce J"], "Johnson, Dolce")

    def test_refuses_duplicate_exact_records(self):
        with self.assertRaises(AmbiguousEmployeeError):
            select_directory_match(["Ray, Alex", "ray, alex"], "Ray, Alex")

    def test_absent_name_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            select_directory_match(self.ROSTER, "Nobody, Here")

    def test_match_is_case_and_space_insensitive(self):
        self.assertEqual(
            select_directory_match(["  flores,  Juan  "], "Flores,  Juan"),
            "  flores,  Juan  ",
        )

    # Issue #343: the live Directory lists only `Johnson, Dolce J`; the alias
    # table maps that spelling to the roster's `Johnson, Dolce`.
    ALIASES = {
        "Johnson, Dolce": "Johnson, Dolce",
        "Johnson Dolce J": "Johnson, Dolce",
        "Johnson, Dolce J": "Johnson, Dolce",
        "Johnson, Dolly": "Johnson, Dolly",
    }

    def test_alias_spelling_is_accepted_when_it_is_the_only_record(self):
        accepted = accepted_directory_names("Johnson, Dolce", self.ALIASES)
        self.assertEqual(accepted, ["Johnson, Dolce J"])
        self.assertEqual(
            select_directory_match(["Johnson, Dolce J"], "Johnson, Dolce", accepted_names=accepted),
            "Johnson, Dolce J",
        )

    def test_alias_of_another_employee_is_not_accepted(self):
        accepted = accepted_directory_names("Johnson, Dolce", self.ALIASES)
        with self.assertRaises(AmbiguousEmployeeError):
            select_directory_match(["Johnson, Dolly"], "Johnson, Dol", accepted_names=accepted)

    def test_exact_and_alias_records_together_refuse(self):
        with self.assertRaises(AmbiguousEmployeeError):
            select_directory_match(
                self.ROSTER, "Johnson, Dolce", accepted_names=["Johnson, Dolce J"],
            )

    def test_two_alias_records_refuse(self):
        with self.assertRaises(AmbiguousEmployeeError):
            select_directory_match(
                ["Johnson, Dolce J", "johnson,  dolce j"], "Johnson, Dolce",
                accepted_names=["Johnson, Dolce J"],
            )

    def test_no_aliases_keeps_refusing_the_near_match(self):
        self.assertEqual(accepted_directory_names("Johnson, Dolce", None), [])
        with self.assertRaises(AmbiguousEmployeeError):
            select_directory_match(["Johnson, Dolce J"], "Johnson, Dolce", accepted_names=[])


class TestDismissBlockingModals(unittest.TestCase):
    def test_returns_false_when_page_evaluate_raises(self):
        page = mock.Mock()
        page.evaluate.side_effect = RuntimeError("detached")
        self.assertFalse(dismiss_blocking_modals(page))

    def test_reports_dismissal(self):
        page = mock.Mock()
        page.evaluate.return_value = True
        self.assertTrue(dismiss_blocking_modals(page))

    def test_targets_ok_by_exact_match(self):
        """Cancel signs the session out, so the button match must be exact."""
        page = mock.Mock()
        page.evaluate.return_value = False
        dismiss_blocking_modals(page)
        js = page.evaluate.call_args[0][0]
        self.assertIn("^\\s*ok\\s*$", js)
        self.assertIn("div.message-box-outer", js)


class TestReportPayInfoIssues(unittest.TestCase):
    """Alert on the outcome (no rate anywhere), not on the mechanism."""

    def _alerts(self, **kwargs) -> list:
        sent = []
        fake = mock.Mock()
        fake.wage_rate_flow_alert = lambda **kw: sent.append(kw)
        with mock.patch.dict(
            "sys.modules", {"agents.bhaga.notify": fake}
        ):
            report_pay_info_issues(date="2026-09-13", **kwargs)
        return sent

    def test_scrape_failure_with_no_gap_is_breadcrumb_only(self):
        # Flores + Majdinasab: pay_info failed, earnings already supplied a rate.
        self.assertEqual(
            self._alerts(scrape_errors={"Flores, Juan": "TimeoutError"}), []
        )

    def test_a_real_gap_alerts(self):
        sent = self._alerts(
            scrape_errors={"Flores, Juan": "TimeoutError"},
            remaining_gaps=["Flores, Juan"],
        )
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["remaining_gaps"], ["Flores, Juan"])

    def test_flow_error_alerts_even_with_no_gaps(self):
        self.assertEqual(len(self._alerts(flow_error="login failed")), 1)

    def test_all_clear_is_silent(self):
        self.assertEqual(self._alerts(), [])


if __name__ == "__main__":
    unittest.main()
