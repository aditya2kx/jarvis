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


class AcceptVariedCopyTests(unittest.TestCase):
    LB = (
        "*   **Alex Example** and **Sam Sample** leading with $40 each.\n"
        "*   **Pat Placeholder** at $20."
    )

    def test_accepts_single_rewrite(self):
        text = f"Good morning, team!\n\n{self.LB}\n\nKeep up the great work."
        out, ok = tp.accept_varied_copy(text, self.LB)
        self.assertTrue(ok)
        self.assertEqual(out, text)

    def test_rejects_multi_draft_dashes(self):
        text = (
            f"Good morning, team!\n\n{self.LB}\n\nKeep going.\n\n---\n\n"
            f"Hi team!\n\n{self.LB}\n\nFantastic effort.\n\n---\n\n"
            f"Hey everyone!\n\n{self.LB}\n\nOne team."
        )
        out, ok = tp.accept_varied_copy(text, self.LB)
        self.assertFalse(ok)
        self.assertEqual(out, "")

    def test_rejects_repeated_leaderboard(self):
        text = f"A\n\n{self.LB}\n\nB\n\n{self.LB}\n\nC"
        out, ok = tp.accept_varied_copy(text, self.LB)
        self.assertFalse(ok)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
