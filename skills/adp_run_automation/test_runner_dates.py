#!/usr/bin/env python3
"""Unit tests for skills.adp_run_automation.runner — date-related regexes.

Run:
    python3 skills/adp_run_automation/test_runner_dates.py

Covers Layer D of the seamless_bhaga_refresh fix: the
``_CURRENT_PAY_PERIOD_RE`` regex used to find ADP's "Current Pay
Period" dropdown option when no closed pay period contains
``target_date`` (in-flight payroll case).

We only test the regex match contract; the Playwright click landing
correctly on the option is verified by tonight's live cron run.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation.runner import _CURRENT_PAY_PERIOD_RE


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


if __name__ == "__main__":
    unittest.main()
