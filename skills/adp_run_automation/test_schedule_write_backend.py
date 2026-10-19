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


class OpenShiftReads(unittest.TestCase):
    W = datetime.date(2026, 10, 26)

    def test_parse_clock_inverts_fmt_time(self):
        for m in (0, 390, 720, 810, 1230, 1439):
            self.assertEqual(wb.parse_clock(wb.fmt_time(m)), m)
        with self.assertRaises(ValueError):
            wb.parse_clock("13:00")

    def test_counts_by_day_and_range(self):
        # As read live 2026-10-17 (adp_schedule_write --inspect --week-start 2026-10-26).
        cells = [
            {"heading": "Open Shifts on Tuesday, Oct 27", "shifts": [{"range": "4:30 PM - 8:30 PM"}]},
            {"heading": "Open Shifts on Saturday, Oct 31",
             "shifts": [{"range": "1:30 PM - 8:30 PM"}, {"range": "1:30 PM - 8:30 PM"}]},
        ]
        self.assertEqual(wb.open_shift_counts(cells, self.W), {
            ("2026-10-27", 990, 1230): 1, ("2026-10-31", 810, 1230): 2,
        })

    def test_week_crossing_a_month(self):
        cells = [{"heading": "Open Shifts on Sunday, Nov 1", "shifts": [{"range": "9:00 AM - 4:30 PM"}]}]
        self.assertEqual(wb.open_shift_counts(cells, self.W), {("2026-11-01", 540, 990): 1})

    def test_heading_outside_the_week_raises(self):
        cells = [{"heading": "Open Shifts on Monday, Nov 2", "shifts": [{"range": "9:00 AM - 4:30 PM"}]}]
        with self.assertRaises(wb.ScheduleWriteError):
            wb.open_shift_counts(cells, self.W)


class CreateOpenShiftConfirm(unittest.TestCase):
    CREATED = ("An open shift with 1 shift has been created and added to the schedule as a draft shift. "
               "Shift details 1 of 1 available 3:00 PM - 8:30 PM EditPublish")

    def _create(self, pane_text, counts_match):
        calls = []
        with mock.patch.object(wb, "_open_action"), \
             mock.patch.object(wb, "_fill_times"), \
             mock.patch.object(wb, "_click", side_effect=lambda _f, _p, what: calls.append(what)), \
             mock.patch.object(wb, "_visible_button", return_value=object()), \
             mock.patch.object(wb, "_pane_text", return_value=pane_text), \
             mock.patch.object(wb, "_wait_for", side_effect=lambda _p, check, _t: check()), \
             mock.patch.object(wb, "_back_out", side_effect=lambda _f, _p: calls.append("back_out")), \
             mock.patch.object(wb, "_wait_drafts", side_effect=lambda *_a, **_k: calls.append("count") or counts_match):
            wb.create_open_shift(mock.MagicMock(), mock.Mock(), date_iso="2026-10-31",
                                 start_min=900, end_min=1230, before=25)
        return calls

    def test_created_pane_is_closed_before_the_count_is_read(self):
        # 2026-10-17: the pane hid the toolbar, so the count never read 26 and the job stopped.
        calls = self._create(self.CREATED, counts_match=True)
        self.assertEqual(calls, ["Next", "Save draft", "back_out", "count"])
        self.assertNotIn("Publish", calls)

    def test_created_pane_confirms_a_lagging_count(self):
        self._create(self.CREATED, counts_match=False)

    def test_no_created_pane_and_no_count_is_unconfirmed(self):
        with self.assertRaises(wb.UnconfirmedSave):
            self._create("Shift details Eligibility Previous Save draft Publish", counts_match=False)


class FakeAssignFrame:
    """The Assign Open Shift pane as read live 2026-10-19: one checkbox per eligible employee."""

    def __init__(self, names):
        self.names, self.picked, self.checked, self.open, self.clicked = names, None, False, True, []

    def evaluate(self, js, arg=None):
        if js is not wb.ASSIGN_PANE_JS:
            raise AssertionError("unexpected script")
        if arg is not None:
            self.picked = arg
        return {"pane": self.open, "names": self.names, "checked": self.checked}

    def locator(self, sel):
        if sel == "[data-jarvis-assign='box']":
            box = mock.MagicMock()
            box.first.click.side_effect = lambda **_k: setattr(self, "checked", True)
            return box
        btn = mock.MagicMock()
        btn.is_visible.return_value = True
        btn.is_enabled.side_effect = lambda: self.checked
        btn.click.side_effect = lambda **_k: (self.clicked.append(self.names[self.picked]), setattr(self, "open", False))
        buttons = mock.MagicMock()
        buttons.count.return_value = 1
        buttons.nth.return_value = btn
        pane = mock.MagicMock()
        pane.get_by_role.return_value = buttons
        return pane


class AssignOpenShift(unittest.TestCase):
    ELIGIBLE = ["Denton, Cimino R", "Huynh, Hillary", "Orourke, Elizabeth"]

    def _assign(self, employee, dry_run=False):
        frame = FakeAssignFrame(self.ELIGIBLE)
        backs = []
        with mock.patch.object(wb, "_open_shift_details") as details, \
             mock.patch.object(wb, "_click") as click, \
             mock.patch.object(wb, "_visible_button", return_value=object()), \
             mock.patch.object(wb, "_pane_text", return_value=""), \
             mock.patch.object(wb, "_wait_for", side_effect=lambda _p, check, _t: check()), \
             mock.patch.object(wb, "_back_out", side_effect=lambda _f, _p: backs.append(1)):
            try:
                name = wb.assign_open_shift(frame, mock.Mock(), date_iso="2026-10-27", employee=employee,
                                            start_min=990, end_min=1230, dry_run=dry_run)
            except wb.ScheduleWriteError as exc:
                name = exc
        details.assert_called_once_with(frame, mock.ANY, date_iso="2026-10-27", start_min=990, end_min=1230)
        click.assert_called_once_with(frame, r"^\s*Assign\s*$", what="Assign")
        return name, frame, backs

    def test_ticks_the_eligible_person_and_assigns(self):
        name, frame, backs = self._assign("Denton, Cimino")
        self.assertEqual(name, "Denton, Cimino R")
        self.assertEqual(frame.clicked, ["Denton, Cimino R"])
        self.assertEqual(backs, [1])

    def test_person_adp_does_not_list_fails_without_assigning(self):
        err, frame, backs = self._assign("Berding, Kenya")
        self.assertIsInstance(err, wb.ScheduleWriteError)
        self.assertIn("not eligible", str(err))
        self.assertIn("Huynh, Hillary", str(err))
        self.assertEqual(frame.clicked, [])
        self.assertEqual(backs, [1])

    def test_dry_run_reaches_an_enabled_assign_and_backs_out(self):
        name, frame, backs = self._assign("Huynh, Hillary", dry_run=True)
        self.assertEqual(name, "Huynh, Hillary")
        self.assertTrue(frame.checked)
        self.assertEqual(frame.clicked, [])
        self.assertEqual(backs, [1])

    def test_a_day_without_open_shifts_raises(self):
        frame = mock.MagicMock()
        frame.evaluate.return_value = {"cells": [{"index": 0, "header_index": 5, "summary": "Open Shifts (5)"}]}
        with mock.patch.object(wb, "_back_out"), self.assertRaises(wb.ScheduleWriteError) as ctx:
            wb._open_shift_details(frame, mock.Mock(), date_iso="2026-10-27", start_min=990, end_min=1230)
        self.assertIn("no open shifts on 2026-10-27", str(ctx.exception))
        frame.locator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
