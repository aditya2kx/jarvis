"""Unit tests for team_pulse compose + day gate (Issue #216)."""

from __future__ import annotations

import unittest

from agents.bhaga.scripts import team_pulse as tp


class ComposeTests(unittest.TestCase):
    def test_compose_replaces_leaderboard(self):
        out = tp.compose_message("Hi\n\n{leaderboard}\n\nBye", "*   **Ada** leading with $10.")
        self.assertIn("**Ada**", out)
        self.assertNotIn("{leaderboard}", out)
        self.assertTrue(out.startswith("Hi"))
        self.assertTrue(out.endswith("Bye"))

    def test_format_leaderboard_groups(self):
        rows = [
            {"employee": "Willingham, Brooke", "total_bonus": 70},
            {"employee": "Priyosha, Jarin", "total_bonus": 70},
            {"employee": "Garcia, Jacob", "total_bonus": 6.67},
            {"employee": "Zero, Person", "total_bonus": 0},
        ]
        md = tp.format_leaderboard(rows)
        self.assertIn("Brooke Willingham", md)
        self.assertIn("Jarin Priyosha", md)
        self.assertIn("$70", md)
        self.assertIn("Jacob Garcia", md)
        self.assertIn("at $6.67", md)
        self.assertNotIn("Zero", md)

    def test_format_leaderboard_empty(self):
        md = tp.format_leaderboard([])
        self.assertIn("No review bonuses", md)


class DayGateTests(unittest.TestCase):
    def test_enabled_matching_day(self):
        self.assertTrue(tp.should_run_today([1, 3, 6], today_weekday=1, enabled=True))

    def test_wrong_day(self):
        self.assertFalse(tp.should_run_today([1, 3, 6], today_weekday=0, enabled=True))

    def test_disabled(self):
        self.assertFalse(tp.should_run_today([1, 3, 6], today_weekday=1, enabled=False))

    def test_parse_days_json(self):
        self.assertEqual(tp.parse_days("[1,3,6]"), [1, 3, 6])


if __name__ == "__main__":
    unittest.main()
