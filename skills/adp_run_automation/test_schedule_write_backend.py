"""ADP Team Schedule write helpers (Issue #337)."""
from __future__ import annotations

import datetime
import unittest
from unittest import mock

from skills.adp_run_automation import runner
from skills.adp_run_automation import schedule_write_backend as wb


class Helpers(unittest.TestCase):
    def test_fmt_time(self):
        self.assertEqual(wb.fmt_time(390), "6:30 AM")
        self.assertEqual(wb.fmt_time(0), "12:00 AM")
        self.assertEqual(wb.fmt_time(720), "12:00 PM")
        self.assertEqual(wb.fmt_time(1260), "9:00 PM")
        with self.assertRaises(ValueError):
            wb.fmt_time(1440)

    def test_fmt_date(self):
        self.assertEqual(wb.fmt_date("2026-10-04"), "10/04/2026")

    def test_week_label_start(self):
        self.assertEqual(
            wb.week_label_start("Week of Sep 28, 2026 - Oct 4, 2026"), datetime.date(2026, 9, 28)
        )
        with self.assertRaises(ValueError):
            wb.week_label_start("Monthly Schedule")

    def test_match_employee(self):
        names = ["Doe, Alex J", "Doe, Alexis", "Roe, Sam"]
        self.assertEqual(wb.match_employee(names, "Doe, Alex"), "Doe, Alex J")
        self.assertEqual(wb.match_employee(names, "roe,  sam"), "Roe, Sam")
        self.assertIsNone(wb.match_employee(names, "Poe, Kim"))
        # Two middle-initial variants → ambiguous, never guess.
        self.assertIsNone(wb.match_employee(["Doe, Alex J", "Doe, Alex K"], "Doe, Alex"))

    def test_publish_count(self):
        self.assertEqual(wb.publish_count(" Publish drafts (3) "), 3)
        self.assertIsNone(wb.publish_count("Publish"))


def _label(start: datetime.date) -> str:
    end = start + datetime.timedelta(days=6)
    return f"Week of {start:%b} {start.day}, {start.year} - {end:%b} {end.day}, {end.year}"


class GotoWeek(unittest.TestCase):
    """The grid may sit on a later week (just published); goto_week steps back (Issue #381)."""

    def _grid(self, shown: datetime.date):
        state = {"shown": shown, "prev_clicks": 0}
        label = mock.Mock()
        label.inner_text.side_effect = lambda **_: _label(state["shown"])
        frame = mock.Mock()
        frame.get_by_text.return_value.first = label

        def prev(*_a, **_k):
            state["prev_clicks"] += 1
            state["shown"] -= datetime.timedelta(days=7)

        frame.locator.return_value.first.evaluate.side_effect = prev
        return frame, label, state

    def test_steps_back_to_an_earlier_week(self):
        frame, label, state = self._grid(datetime.date(2026, 10, 19))
        page = mock.Mock()
        with mock.patch.object(runner, "_wait_week_label_change",
                               side_effect=lambda _p, lbl, _b, _s: lbl.inner_text()):
            wb.goto_week(frame, page, datetime.date(2026, 10, 5))
        self.assertEqual(state["prev_clicks"], 2)
        frame.locator.assert_called_with('[aria-label="Select previous week"]')

    def test_previous_click_that_does_nothing_raises(self):
        frame, _label_, _state = self._grid(datetime.date(2026, 10, 19))
        frame.locator.return_value.first.evaluate.side_effect = None
        with mock.patch.object(runner, "_wait_week_label_change", return_value=None), \
                self.assertRaises(wb.ScheduleWriteError):
            wb.goto_week(frame, mock.Mock(), datetime.date(2026, 10, 12))

    def test_forward_still_uses_next_week(self):
        frame, _label_, state = self._grid(datetime.date(2026, 10, 5))

        def nxt(_page, _frame):
            state["shown"] += datetime.timedelta(days=7)

        with mock.patch.object(runner, "_goto_next_week", side_effect=nxt) as go:
            wb.goto_week(frame, mock.Mock(), datetime.date(2026, 10, 19))
        self.assertEqual(go.call_count, 2)


if __name__ == "__main__":
    unittest.main()
