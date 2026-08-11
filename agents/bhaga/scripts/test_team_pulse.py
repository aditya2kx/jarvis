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

    def test_greeting_follows_chicago_hour(self):
        import datetime
        from zoneinfo import ZoneInfo

        afternoon = datetime.datetime(2026, 8, 11, 13, 0, tzinfo=ZoneInfo("America/Chicago"))
        self.assertEqual(tp.time_of_day_greeting(afternoon), "Good Afternoon")
        out = tp.compose_message(
            "Good Morning Team ! Sharing x.\n\n{leaderboard}",
            "*   **Ada** leading with $10.",
            now=afternoon,
        )
        self.assertTrue(out.startswith("Good Afternoon Team"))
        self.assertNotIn("Good Morning", out)

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


    def test_rejects_recorded_2026_08_08_multi_draft_structure(self):
        """Scrubbed mirror of ClickUp message_id 80170041046292 (3 drafts + ---)."""
        lb = (
            "*   **Alex Example** and **Sam Sample** leading with $70 each.\n"
            "*   **Pat Placeholder** at $30.\n"
            "*   **Jordan Quotient** at $10.\n"
            "*   **Casey Decimal** and **Riley Remainder** at $6.67 each.\n"
            "*   **Morgan Fraction** at $6.66."
        )
        content = (
            "Good morning, team! Here's the latest Google Review Bonus "
            "leaderboard for the current pay cycle.\n\n"
            f"{lb}\n\n"
            "Keep up the amazing work, everyone! Let's continue creating "
            "great experiences for our customers.\n\n"
            "---\n\n"
            "Hi team, sharing a quick update on our Google Review Bonus "
            "leaderboard.\n\n"
            f"{lb}\n\n"
            "Fantastic effort from everyone. Let's keep that momentum "
            "strong as a team!\n\n"
            "---\n\n"
            "Hey everyone! Time for our Google Review Bonus leaderboard "
            "check-in for this pay cycle.\n\n"
            f"{lb}\n\n"
            "Every contribution counts. Let's keep up the collaborative "
            "spirit and aim for more positive reviews!"
        )
        out, ok = tp.accept_varied_copy(content, lb)
        self.assertFalse(ok)
        self.assertEqual(out, "")
        self.assertEqual(content.count(lb), 3)

    def test_rejects_repeated_leaderboard(self):
        text = f"A\n\n{self.LB}\n\nB\n\n{self.LB}\n\nC"
        out, ok = tp.accept_varied_copy(text, self.LB)
        self.assertFalse(ok)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
