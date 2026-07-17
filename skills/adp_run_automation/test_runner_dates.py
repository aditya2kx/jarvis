#!/usr/bin/env python3
"""Unit tests for skills.adp_run_automation.runner — date-related helpers.

Run:
    python3 skills/adp_run_automation/test_runner_dates.py

Covers:
- ``_CURRENT_PAY_PERIOD_RE`` / ``_is_current_pay_period_label`` for ADP's
  in-flight payroll dropdown option (including date-suffixed labels).
- ``_biweekly_period_bounds`` cadence matching store-profile anchor math.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation.runner import (
    _CURRENT_PAY_PERIOD_RE,
    _biweekly_period_bounds,
    _is_current_pay_period_label,
    _parse_pay_period_range,
)


def _matches(text: str) -> bool:
    # Mirror how Playwright's get_by_role(..., name=re) consumes the regex
    # (it uses .search by default, but we anchored the pattern with ^…$ so
    # search == fullmatch here). Strip leading/trailing whitespace because
    # ADP's accessible names sometimes include nbsp-ish padding.
    return bool(_CURRENT_PAY_PERIOD_RE.match(text.strip()))


class CurrentPayPeriodRegexPositiveTests(unittest.TestCase):
    def test_canonical_three_words(self):
        self.assertTrue(_matches("Current Pay Period"))

    def test_all_lowercase(self):
        self.assertTrue(_matches("current pay period"))

    def test_one_word_current(self):
        self.assertTrue(_matches("Current"))

    def test_this_pay_period(self):
        self.assertTrue(_matches("This Pay Period"))

    def test_lowercase_one_word(self):
        self.assertTrue(_matches("current"))

    def test_uppercase_padded(self):
        # Padded with leading/trailing whitespace — our matcher strips
        # before testing the regex.
        self.assertTrue(_matches("  CURRENT  "))

    def test_current_with_date_suffix(self):
        self.assertTrue(
            _is_current_pay_period_label(
                "Current Pay Period (07/13/2026 - 07/26/2026)"
            )
        )

    def test_current_with_bracket_suffix(self):
        self.assertTrue(
            _is_current_pay_period_label("Current Pay Period [in progress]")
        )


class CurrentPayPeriodRegexNegativeTests(unittest.TestCase):
    def test_closed_date_range(self):
        self.assertFalse(_matches("05/05/2026 - 05/18/2026"))

    def test_numbered_pay_period(self):
        self.assertFalse(_matches("Pay Period 5"))

    def test_last_pay_period(self):
        # We must NOT accidentally pick last period — wrong window entirely.
        self.assertFalse(_matches("Last Pay Period"))

    def test_unrelated_phrase_with_current(self):
        # Tax period is a different ADP concept; the prefix shouldn't drag
        # us into matching it.
        self.assertFalse(_matches("Current Tax Period"))
        self.assertFalse(_is_current_pay_period_label("Current Tax Period"))


class BiweeklyPeriodBoundsTests(unittest.TestCase):
    def test_july_15_open_period(self):
        # Anchor end 2026-05-17 → period containing 2026-07-15 is 7/13–7/26.
        start, end = _biweekly_period_bounds(
            datetime.date(2026, 7, 15),
            anchor_end=datetime.date(2026, 5, 17),
        )
        self.assertEqual(start, datetime.date(2026, 7, 13))
        self.assertEqual(end, datetime.date(2026, 7, 26))

    def test_closed_period_end_on_anchor(self):
        start, end = _biweekly_period_bounds(
            datetime.date(2026, 5, 17),
            anchor_end=datetime.date(2026, 5, 17),
        )
        self.assertEqual(start, datetime.date(2026, 5, 4))
        self.assertEqual(end, datetime.date(2026, 5, 17))

    def test_parse_pay_period_range(self):
        bounds = _parse_pay_period_range("Pay Period 07/13/2026 - 07/26/2026")
        self.assertEqual(bounds, (datetime.date(2026, 7, 13), datetime.date(2026, 7, 26)))


if __name__ == "__main__":
    unittest.main()
