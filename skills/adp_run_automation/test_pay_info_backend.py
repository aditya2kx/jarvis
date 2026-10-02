"""Unit tests for pay_info_backend parse helpers (Issue #213)."""

from __future__ import annotations

import pathlib
import unittest
from html.parser import HTMLParser

from unittest import mock

from skills.adp_run_automation import pay_info_backend as pib
from skills.adp_run_automation.pay_info_backend import (
    _PEOPLE_SEARCH_PLACEHOLDER_RE,
    AmbiguousEmployeeError,
    accepted_directory_names,
    directory_search_name,
    dismiss_blocking_modals,
    parse_hourly_pay_rate,
    parse_pay_rate_cards,
    parse_payroll_info,
    prepare_pay_info_writes,
    rate_record,
    report_pay_info_issues,
    select_directory_match,
    status_filter_clicks,
)

_TESTDATA = pathlib.Path(__file__).parent / "testdata"


class _CardHTML(HTMLParser):
    """Mirror of _PAYROLL_CARDS_JS over a saved ADP 'Pay rates' fragment."""

    _FIELDS = {
        "current-pay-rate": "rate_text",
        "default-rate-label": "label",
        "default-rate-date-message": "date_text",
    }

    def __init__(self):
        super().__init__()
        self.cards: list[dict] = []
        self._field = None

    def handle_starttag(self, tag, attrs):
        tid = dict(attrs).get("data-test-id") or ""
        if tid.startswith("pay-rate-card-"):
            self.cards.append({"rate_text": "", "label": "", "date_text": ""})
        elif tid in self._FIELDS and self.cards:
            self._field = self._FIELDS[tid]

    def handle_endtag(self, tag):
        if tag == "div":
            self._field = None

    def handle_data(self, data):
        if self._field:
            self.cards[-1][self._field] += data.strip()


def _fixture_cards(name: str) -> list[dict]:
    p = _CardHTML()
    p.feed((_TESTDATA / f"pay_info_cards_{name}.html").read_text())
    return p.cards


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


class TestParsePayRateCards(unittest.TestCase):
    """ADP's 2026-09-29 'Pay rates' cards, from live fixtures (spike 2026-10-01)."""

    def test_single_card_added_on(self):
        parsed = parse_pay_rate_cards(_fixture_cards("single_added_on"))
        self.assertEqual(parsed["wage_rate_dollars"], 25.0)
        self.assertEqual(parsed["added_on"], "2025-09-25")
        self.assertTrue(parsed["default_card"])

    def test_last_changed_on_is_the_date(self):
        parsed = parse_pay_rate_cards(_fixture_cards("last_changed_on"))
        self.assertEqual(parsed["wage_rate_dollars"], 18.0)
        self.assertEqual(parsed["added_on"], "2026-09-25")

    def test_default_card_beats_rate2_card(self):
        """10 of 17 records carry an unlabelled $16.25 rate-2 card (invariant 12)."""
        cards = _fixture_cards("default_plus_rate2")
        self.assertEqual(len(cards), 2)
        for order in (cards, list(reversed(cards))):
            parsed = parse_pay_rate_cards(order)
            self.assertEqual(parsed["wage_rate_dollars"], 15.25)
            self.assertEqual(parsed["added_on"], "2026-02-16")
            self.assertTrue(parsed["default_card"])

    def test_no_default_label_takes_lowest_and_says_so(self):
        cards = [{"rate_text": "$16.2500"}, {"rate_text": "$15.2500"}]
        parsed = parse_pay_rate_cards(cards)
        self.assertEqual(parsed["wage_rate_dollars"], 15.25)
        self.assertFalse(parsed["default_card"])

    def test_card_without_rate_text_is_not_ready(self):
        """A card renders ~0.5 s before its rate (Pascone)."""
        self.assertIsNone(parse_pay_rate_cards([{"rate_text": "", "label": "Default rate"}]))
        self.assertIsNone(parse_pay_rate_cards([]))

    def test_thousands_separator(self):
        parsed = parse_pay_rate_cards([{"rate_text": "$1,234.5000", "label": "Default rate"}])
        self.assertEqual(parsed["wage_rate_dollars"], 1234.5)

    def test_implausible_rate_is_returned_for_the_writer_to_refuse(self):
        parsed = parse_pay_rate_cards([{"rate_text": "$1.2500", "label": "Default rate"}])
        self.assertEqual(parsed["wage_rate_dollars"], 1.25)


class TestParsePayrollInfo(unittest.TestCase):
    def test_cards_first(self):
        parsed = parse_payroll_info(
            [{"rate_text": "$15.2500", "label": "Default rate"}],
            "Hourly pay rate $99.0000",
        )
        self.assertEqual(parsed["wage_rate_dollars"], 15.25)
        self.assertEqual(parsed["rate_layout"], "cards")

    def test_legacy_label_fallback(self):
        parsed = parse_payroll_info([], "Payroll info Hourly pay rate $15.2500 Added on 06/18/2026")
        self.assertEqual(parsed["wage_rate_dollars"], 15.25)
        self.assertEqual(parsed["rate_layout"], "legacy")

    def test_blank_pane_is_none(self):
        """The 'undefined' heading page: card-less body with stray prices."""
        self.assertIsNone(parse_payroll_info([], "Hillary Huynh Active undefined Cancel Save"))
        self.assertIsNone(parse_payroll_info([], "$15.2500 Hourly", ["Pay rate $15.2500"]))


class TestPayrollPaneRetry(unittest.TestCase):
    """Blank pane: reopen from the Directory, never reload, never edit."""

    BLANK = {"cards": [], "heading_undefined": True, "text": "undefined", "inputs": []}
    READY = {"cards": [{"rate_text": "$15.2500", "label": "Default rate",
                        "date_text": "Added on 08/31/2026"}],
             "heading_undefined": False, "text": "", "inputs": []}

    def _scrape(self, panes):
        page = mock.Mock()
        with mock.patch.object(pib, "_open_profile", return_value="Huynh, Hillary") as op, \
                mock.patch.object(pib, "_read_payroll_info", side_effect=panes):
            try:
                return pib.scrape_one_pay_info(page, "Huynh, Hillary", dashboard_url="u"), op, page
            except ValueError as exc:
                return exc, op, page

    def test_blank_twice_then_ready(self):
        with mock.patch("builtins.print") as out:
            raw, op, page = self._scrape([self.BLANK, self.BLANK, self.READY])
        self.assertEqual(raw["wage_rate_dollars"], 15.25)
        self.assertEqual(raw["rate_layout"], "cards")
        self.assertEqual(op.call_count, 3)
        page.reload.assert_not_called()
        crumbs = [c.args[0] for c in out.call_args_list if "payroll_info_blank" in c.args[0]]
        self.assertEqual(len(crumbs), 2)
        self.assertIn("attempt=2/3", crumbs[1])

    def test_always_blank_raises(self):
        with mock.patch("builtins.print"):
            exc, op, page = self._scrape([self.BLANK] * 3)
        self.assertIsInstance(exc, ValueError)
        self.assertEqual(op.call_count, 3)
        page.reload.assert_not_called()

    def test_card_reader_never_clicks(self):
        """Every card carries an Edit rate button; the reader is text-only."""
        self.assertNotIn("click", pib._PAYROLL_CARDS_JS)


class TestStatusFilterClicks(unittest.TestCase):
    T = "aeed-filter-checkbox-Terminated"
    L = "aeed-filter-checkbox-Leave of absence"

    def test_unticked_wanted_boxes_are_clicked(self):
        states = [
            {"test_id": "aeed-filter-checkbox-Active", "aria_checked": "true"},
            {"test_id": self.L, "aria_checked": "false"},
            {"test_id": self.T, "aria_checked": "false"},
        ]
        self.assertEqual(status_filter_clicks(states), [self.L, self.T])

    def test_already_ticked_is_left_alone(self):
        states = [{"test_id": self.T, "aria_checked": "true"},
                  {"test_id": self.L, "aria_checked": "true"}]
        self.assertEqual(status_filter_clicks(states), [])

    def test_aria_checked_is_the_only_state_read(self):
        """sdf-checkbox always has checked="true|false"; the old code read it as ticked."""
        states = [{"test_id": self.T, "checked": "false", "aria_checked": "false"},
                  {"test_id": self.L, "checked": "true", "aria_checked": "true"}]
        self.assertEqual(status_filter_clicks(states), [self.T])

    def test_other_filters_never_clicked(self):
        states = [{"test_id": "aeed-filter-checkbox-MyADP", "aria_checked": "false"},
                  {"test_id": "aeed-filter-checkbox-Paperless payroll", "aria_checked": "false"}]
        self.assertEqual(status_filter_clicks(states), [])


class TestActiveWins(unittest.TestCase):
    """Dolce: Terminated `Johnson, Dolce` $15.25 beside Active `Johnson, Dolce J` $18."""

    TERM = {"name": "Johnson, Dolce", "status": "Terminated"}
    ACTIVE = {"name": "Johnson, Dolce J", "status": "Active"}

    def test_active_record_wins_when_aliased(self):
        self.assertEqual(
            select_directory_match([self.TERM, self.ACTIVE], "Johnson, Dolce",
                                   accepted_names=["Johnson, Dolce J"]),
            "Johnson, Dolce J",
        )

    def test_both_active_refuses(self):
        with self.assertRaises(AmbiguousEmployeeError):
            select_directory_match(
                [{**self.TERM, "status": "Active"}, self.ACTIVE], "Johnson, Dolce",
                accepted_names=["Johnson, Dolce J"],
            )

    def test_both_terminated_refuses(self):
        with self.assertRaises(AmbiguousEmployeeError):
            select_directory_match(
                [self.TERM, {**self.ACTIVE, "status": "Terminated"}], "Johnson, Dolce",
                accepted_names=["Johnson, Dolce J"],
            )

    def test_unaliased_rehire_refuses_instead_of_taking_the_old_rate(self):
        """Aliases failed to load: the exact Terminated match must not win."""
        with self.assertRaises(AmbiguousEmployeeError):
            select_directory_match([self.TERM, self.ACTIVE], "Johnson, Dolce")

    def test_terminated_exact_match_alone_is_taken(self):
        """Alvarez: Terminated, no lookalike — still the right record."""
        self.assertEqual(
            select_directory_match(
                [{"name": "Alvarez, Sebastian", "status": "Terminated"}], "Alvarez, Sebastian"),
            "Alvarez, Sebastian",
        )


class TestPuncherNamesCanonical(unittest.TestCase):
    def test_unaliased_new_hire_is_keyed_canonically(self):
        """Wing Huang, 2026-09-29: scraped as `Huang Wing` before onboarding ran."""
        punches = [{"employee_name": "Huang Wing"}, {"employee_name": "Garcia, Jacob"}]
        xlsx = mock.Mock()
        xlsx.exists.return_value = True
        with mock.patch("skills.adp_run_automation.shift_backend.parse_xlsx",
                        return_value=punches):
            names = pib.puncher_names_from_session_files(timecard_xlsx=xlsx)
        self.assertEqual(names, ["Garcia, Jacob", "Huang, Wing"])


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


def _nights(name, *oks):
    """oks oldest→newest; None = no attempt that night."""
    import datetime as _dt
    start = _dt.date(2026, 9, 26)
    return [
        {"employee_id": name, "night": start + _dt.timedelta(days=i), "ok": ok}
        for i, ok in enumerate(oks) if ok is not None
    ]


class TestBlindStreaks(unittest.TestCase):
    def test_three_failed_nights(self):
        self.assertEqual(pib.blind_streaks(_nights("A", False, False, False)), {"A": 3})

    def test_older_success_bounds_the_streak(self):
        self.assertEqual(pib.blind_streaks(_nights("A", True, False, False)), {"A": 2})

    def test_skipped_night_neither_breaks_nor_extends(self):
        self.assertEqual(pib.blind_streaks(_nights("A", False, False, None, False)), {"A": 3})

    def test_latest_ok_is_omitted(self):
        self.assertEqual(pib.blind_streaks(_nights("A", False, False, True)), {})

    def test_night_ok_if_any_attempt_succeeded(self):
        """The query collapses attempts with LOGICAL_OR; mirror one night as ok."""
        rows = _nights("A", False, False) + _nights("A", None, None, True)
        self.assertEqual(pib.blind_streaks(rows), {})


class TestOutcomeRows(unittest.TestCase):
    def test_all_failed_night(self):
        names = [f"N{i}, X" for i in range(16)]
        rows = pib.outcome_rows({
            "scraped_at_utc": "2026-09-30T02:40:00Z", "rates": [],
            "errors": {n: "ValueError: Hourly pay rate not found" for n in names},
            "attempted": names,
        })
        self.assertEqual(len(rows), 16)
        self.assertFalse(any(r["ok"] for r in rows))

    def test_ok_and_failed(self):
        rows = pib.outcome_rows({
            "scraped_at_utc": "t", "rates": [{"employee_name": "A"}],
            "errors": {"B": "x" * 400}, "attempted": ["A", "B", "C"],
        })
        self.assertEqual([(r["employee_id"], r["ok"]) for r in rows], [("A", True), ("B", False)])
        self.assertEqual(len(rows[1]["error"]), 300)

    def test_legacy_payload_without_timestamp_records_nothing(self):
        self.assertEqual(pib.outcome_rows({"rates": [{"employee_name": "A"}]}), [])


class TestReportBlindStreaks(unittest.TestCase):
    """Alert once per streak; tonight's blind set is what is remembered."""

    def setUp(self):
        self.state: dict = {}
        self.sent: list = []
        fake = mock.Mock()
        fake.partition_anomalies = __import__(
            "agents.bhaga.notify", fromlist=["partition_anomalies"]).partition_anomalies
        fake.pay_info_blind_alert = lambda **kw: self.sent.append(kw["names"])
        sa = mock.Mock()
        sa.get_notify_state = lambda k: list(self.state.get(k, []))
        sa.set_notify_state = lambda k, v: self.state.__setitem__(k, list(v))
        self._patch = mock.patch.dict("sys.modules", {
            "agents.bhaga.notify": fake,
            "skills.bhaga_config.state_adapter": sa,
        })
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self._sa = sa

    def _night(self, streaks):
        with mock.patch("builtins.print"):
            return pib.report_blind_streaks(date="d", streaks=streaks)

    def test_two_nights_is_silent(self):
        self.assertEqual(self._night({"A": 2}), [])
        self.assertEqual(self.sent, [])

    def test_third_night_alerts_once(self):
        self.assertEqual(self._night({"A": 3}), ["A"])
        self.assertEqual(self._night({"A": 4}), [])
        self.assertEqual(self.sent, [["A"]])

    def test_recovered_then_blind_again_re_alerts(self):
        self._night({"A": 3})
        self._night({})
        self._night({"A": 3})
        self.assertEqual(self.sent, [["A"], ["A"]])

    def test_state_read_failure_still_alerts(self):
        def boom(_k):
            raise RuntimeError("firestore down")
        self._sa.get_notify_state = boom
        self.assertEqual(self._night({"A": 3, "B": 1}), ["A"])


if __name__ == "__main__":
    unittest.main()
