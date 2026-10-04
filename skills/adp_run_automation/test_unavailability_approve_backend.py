"""Approve-card matching (Issue #337) — card text mirrors the 2026-09-27 ADP spike."""
from __future__ import annotations

import datetime
import unittest

from skills.adp_run_automation import unavailability_approve_backend as ab

ON = datetime.date(2026, 9, 27)
SAT = """AB
Doe, Alex
Sat, Oct 3,
6:00 AM - 10:00 AM
4.00 HRS
Repeats
Every Saturday until Nov 1, 2026
Request expires
 Oct 2, 2026 9:00 PM
 APPROVE
 REJECT"""
SUN = """Unavailability update
AB
Doe, Alex
Sun, Oct 4,
12:00 AM - 12:00 AM
24.00 HRS
 APPROVE
 REJECT"""


def target(first_date, from_time=None, to_time=None, name="Doe,  Alex"):
    return {"raw_employee_name": name, "first_date": first_date, "from_time": from_time, "to_time": to_time}


class TestMatchCard(unittest.TestCase):
    cards = [{"index": 0, "text": SAT}, {"index": 1, "text": SUN}]

    def test_matches_window_card_without_header(self):
        self.assertEqual(ab.match_card(self.cards, target("2026-10-03", "06:00", "10:00"), scraped_on=ON), 0)

    def test_matches_all_day_card(self):
        self.assertEqual(ab.match_card(self.cards, target("2026-10-04"), scraped_on=ON), 1)

    def test_no_match_never_guesses(self):
        with self.assertRaisesRegex(ab.ApproveError, "no pending ADP card"):
            ab.match_card(self.cards, target("2026-10-03", "07:00", "10:00"), scraped_on=ON)

    def test_duplicate_cards_are_ambiguous(self):
        dup = self.cards + [{"index": 2, "text": SUN}]
        with self.assertRaisesRegex(ab.ApproveError, "ambiguous"):
            ab.match_card(dup, target("2026-10-04"), scraped_on=ON)


if __name__ == "__main__":
    unittest.main()
