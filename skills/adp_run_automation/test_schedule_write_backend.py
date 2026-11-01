"""ADP Team Schedule write helpers (Issue #337)."""
from __future__ import annotations

import contextlib
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

    WIZARD = "1. SHIFT DETAILS 2. ELIGIBILITY Shift details 5:00 AM Eligibility 4 employees are eligible"
    CONFIRM = WIZARD + " PreviousSave draftPublish Publish to employees When published, an employee can claim"

    def _publish(self, panes, *, confirm_buttons=("Cancel", "Publish"), marked=1):
        """``panes``: _pane_text reads in order (the last repeats)."""
        calls, frame, seq = [], mock.MagicMock(), list(panes)
        frame.evaluate.side_effect = lambda js, *_a: (
            list(confirm_buttons) if js == wb.MARK_CONFIRM_JS else ["Previous", "Save draft", "Publish [off]"])
        frame.locator.return_value.count.return_value = marked
        frame.locator.return_value.first.click.side_effect = lambda **_k: calls.append("confirm")
        with mock.patch.object(wb, "_open_action"), \
             mock.patch.object(wb, "_fill_times"), \
             mock.patch.object(wb, "_click", side_effect=lambda _f, _p, what: calls.append(what)), \
             mock.patch.object(wb, "_visible_button", return_value=object()), \
             mock.patch.object(wb, "_pane_text", side_effect=lambda _f: seq.pop(0) if len(seq) > 1 else seq[0]), \
             mock.patch.object(wb, "_wait_for", side_effect=lambda _p, check, *_a: check()), \
             mock.patch.object(wb, "_back_out", side_effect=lambda _f, _p: calls.append("back_out")):
            shown = wb.create_open_shift(frame, mock.Mock(), date_iso="2026-12-14",
                                         start_min=300, end_min=360, publish=True)
        return calls, shown

    def test_publish_presses_the_publish_to_employees_confirmation(self):
        # 2026-11-01: backing out of this confirmation cancelled both live open-adds.
        calls, shown = self._publish([self.WIZARD, self.CONFIRM, self.CONFIRM, "Open shift published"])
        self.assertEqual(calls, ["Next", "Publish", "confirm", "back_out"])
        self.assertEqual(shown, "Open shift published")

    def test_unrecognised_confirmation_backs_out_and_says_which_buttons(self):
        with self.assertRaises(wb.ScheduleWriteError) as ctx:
            self._publish([self.WIZARD, self.CONFIRM], confirm_buttons=("Cancel", "Send"), marked=0)
        self.assertIn("['Cancel', 'Send']", str(ctx.exception))

    def test_publish_without_a_confirmation_returns_what_adp_showed_next(self):
        calls, shown = self._publish([self.WIZARD, "Open shift published"])
        self.assertEqual(calls, ["Next", "Publish", "back_out"])
        self.assertEqual(shown, "Open shift published")

    def test_edit_presses_the_publish_confirmation_before_backing_out(self):
        calls = []
        commit = mock.Mock()
        commit.click.side_effect = lambda **_k: calls.append("Publish")
        with mock.patch.object(wb, "_click"), \
             mock.patch.object(wb, "_fill_times"), \
             mock.patch.object(wb, "_visible_button", return_value=commit), \
             mock.patch.object(wb, "_wait_for", side_effect=lambda _p, check, *_a: check()), \
             mock.patch.object(wb, "_pane_text", return_value="Open shift updated"), \
             mock.patch.object(wb, "_press_publish_confirmation",
                               side_effect=lambda _f, _p: calls.append("confirm") or "Publish to employees"), \
             mock.patch.object(wb, "_back_out", side_effect=lambda _f, _p: calls.append("back_out")):
            self.assertTrue(wb._edit_times(mock.MagicMock(), mock.Mock(), start_min=360, end_min=420, dry_run=False))
        self.assertEqual(calls, ["Publish", "confirm", "back_out"])

    def _edit_open(self, after_next):
        """Edit whose times step offers only Next; ``after_next``: the commit button shown after it."""
        calls, nexted = [], []
        frame = mock.MagicMock()
        frame.evaluate.return_value = ["Previous", "Save draft"]

        def visible(_f, pattern):
            if "Next" in pattern:
                return object()
            return after_next if nexted else None

        with mock.patch.object(wb, "_click", side_effect=lambda _f, _p, what: calls.append(what) or (
                 what == "Next" and nexted.append(1))), \
             mock.patch.object(wb, "_fill_times"), \
             mock.patch.object(wb, "_visible_button", side_effect=visible), \
             mock.patch.object(wb, "_wait_for", side_effect=lambda _p, check, *_a: check()), \
             mock.patch.object(wb, "_pane_text", return_value="Eligibility 4 employees are eligible"), \
             mock.patch.object(wb, "_press_publish_confirmation", return_value=None), \
             mock.patch.object(wb, "_back_out"):
            return calls, wb._edit_times(frame, mock.Mock(), start_min=360, end_min=420, dry_run=False)

    def test_open_shift_edit_goes_through_next_to_publish(self):
        commit = mock.Mock()
        calls, ok = self._edit_open(commit)
        self.assertTrue(ok)
        self.assertEqual(calls, ["Edit", "Next"])
        commit.click.assert_called_once()

    def test_edit_without_a_commit_button_says_what_adp_showed(self):
        with self.assertRaises(wb.ScheduleWriteError) as ctx:
            self._edit_open(None)
        self.assertIn("buttons: Previous, Save draft; pane: Eligibility 4 employees are eligible", str(ctx.exception))

    def test_publish_that_stays_on_the_wizard_says_which_buttons_were_off(self):
        _calls, shown = self._publish([self.WIZARD])
        self.assertEqual(
            shown,
            "still on the wizard: Eligibility 4 employees are eligible | buttons: Previous, Save draft, Publish [off]",
        )


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


class PublishDraftsLag(unittest.TestCase):
    WEEK = datetime.date(2026, 10, 26)

    def _publish(self, fresh):
        confirm = mock.Mock()
        with mock.patch.object(wb, "drafts_pending", return_value=1), \
             mock.patch.object(wb, "_click"), \
             mock.patch.object(wb, "_visible_button", return_value=confirm), \
             mock.patch.object(wb, "_wait_drafts", return_value=False), \
             mock.patch.object(wb, "_shown_week", return_value=self.WEEK), \
             mock.patch.object(wb, "_drafts_on_fresh_load", return_value=fresh) as reload:
            try:
                pending = wb.publish_drafts(mock.Mock(), mock.Mock())
            except wb.PublishUnconfirmed as exc:
                pending = exc
        return pending, reload

    def test_fresh_load_showing_zero_confirms_the_publish(self):
        pending, reload = self._publish(0)
        self.assertEqual(pending, 1)
        reload.assert_called_once()

    def test_fresh_load_still_pending_raises(self):
        pending, _reload = self._publish(1)
        self.assertIsInstance(pending, wb.PublishUnconfirmed)

    def test_unreadable_week_raises_without_reloading(self):
        with mock.patch.object(wb, "drafts_pending", return_value=1), \
             mock.patch.object(wb, "_click"), \
             mock.patch.object(wb, "_visible_button", return_value=mock.Mock()), \
             mock.patch.object(wb, "_wait_drafts", return_value=False), \
             mock.patch.object(wb, "_shown_week", return_value=None), \
             mock.patch.object(wb, "_drafts_on_fresh_load") as reload:
            with self.assertRaises(wb.PublishUnconfirmed):
                wb.publish_drafts(mock.Mock(), mock.Mock())
        reload.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class ConsoleShiftChanges(unittest.TestCase):
    """Issue #434: delete / edit any shift, open-shift edit / delete, publish-mode creates."""

    T = {"date_iso": "2026-11-03", "employee": "Lee, Kim", "start_min": 540, "end_min": 900}

    def _patch(self, **over):
        self.calls = []
        grid = over.pop("grid", [True])
        base = {
            "_open_assigned_shift": mock.MagicMock(side_effect=lambda *_a, **k: self.calls.append(("open", k["state"]))),
            "_press_delete": mock.MagicMock(side_effect=lambda *_a, dry_run: self.calls.append(("delete", dry_run)) or not dry_run),
            "_edit_times": mock.MagicMock(side_effect=lambda *_a, dry_run, **_k: self.calls.append(("edit", dry_run)) or not dry_run),
            "shift_in_grid": mock.MagicMock(side_effect=lambda *_a, **k: self.calls.append(("grid", k.get("state", "any"), k["start_min"])) or grid.pop(0)),
            "_wait_for": mock.MagicMock(side_effect=lambda _p, check, *_a: check()),
            "_open_shift_details": mock.MagicMock(side_effect=lambda *_a, **_k: self.calls.append(("open-details",))),
            "_visible_button": mock.MagicMock(return_value=object()),
            **over,
        }
        stack = contextlib.ExitStack()
        for name, fake in base.items():
            stack.enter_context(mock.patch.object(wb, name, fake))
        self.addCleanup(stack.close)

    def test_confirm_texts_and_never_delete_all(self):
        self.assertTrue(wb.DELETE_CONFIRM_RE.search("Are you sure you want to delete this shift?"))
        self.assertTrue(wb.DELETE_CONFIRM_RE.search("Are you sure you want to delete this open shift?"))
        self.assertTrue(wb.CONFIRM_RE.match("Yes, delete"))
        self.assertIsNone(wb.CONFIRM_RE.match("Delete all schedules"))
        self.assertFalse(wb.CANCEL_RE.match("Delete"))

    def test_delete_any_shift_opens_any_state_and_confirms_on_the_grid(self):
        self._patch(grid=[False])
        wb.delete_any_shift(mock.MagicMock(), mock.Mock(), **self.T)
        self.assertEqual(self.calls, [("open", "any"), ("delete", False), ("grid", "any", 540)])

    def test_delete_any_shift_still_on_the_grid_is_unconfirmed(self):
        self._patch(grid=[True])
        with self.assertRaises(wb.UnconfirmedSave):
            wb.delete_any_shift(mock.MagicMock(), mock.Mock(), **self.T)

    def test_delete_any_shift_dry_run_cancels_and_skips_the_read_back(self):
        self._patch()
        wb.delete_any_shift(mock.MagicMock(), mock.Mock(), **self.T, dry_run=True)
        self.assertEqual(self.calls, [("open", "any"), ("delete", True)])

    def test_edit_shift_is_confirmed_live_at_the_new_times(self):
        self._patch(grid=[True])
        wb.edit_shift(mock.MagicMock(), mock.Mock(), **self.T, to_start_min=600, to_end_min=960)
        self.assertEqual(self.calls, [("open", "any"), ("edit", False), ("grid", "published", 600)])

    def test_edit_shift_not_shown_at_the_new_times_is_unconfirmed(self):
        self._patch(grid=[False])
        with self.assertRaises(wb.UnconfirmedSave):
            wb.edit_shift(mock.MagicMock(), mock.Mock(), **self.T, to_start_min=600, to_end_min=960)

    def test_published_open_shift_delete_looks_under_edit(self):
        clicked = []
        self._patch(_delete_shown=mock.MagicMock(side_effect=[False, True]),
                    _click=mock.MagicMock(side_effect=lambda _f, _p, what: clicked.append(what)))
        wb.delete_open_shift(mock.MagicMock(), mock.Mock(), date_iso="2026-12-15", start_min=300, end_min=360)
        self.assertEqual(clicked, ["Edit"])
        self.assertEqual(self.calls, [("open-details",), ("delete", False)])

    def test_open_shift_edit_and_delete_go_through_its_details_pane(self):
        self._patch(_delete_shown=mock.MagicMock(return_value=True))
        times = {"date_iso": "2026-11-03", "start_min": 540, "end_min": 900}
        wb.edit_open_shift(mock.MagicMock(), mock.Mock(), **times, to_start_min=600, to_end_min=900, dry_run=True)
        wb.delete_open_shift(mock.MagicMock(), mock.Mock(), **times)
        self.assertEqual(self.calls, [("open-details",), ("edit", True), ("open-details",), ("delete", False)])

    def test_published_create_presses_publish_never_save_as_draft_and_checks_the_grid(self):
        clicked = []
        self._patch(grid=[True], _open_action=mock.MagicMock(), _fill_times=mock.MagicMock(),
                    _settled_picker=mock.MagicMock(return_value=[{"name": "Lee, Kim", "value": "7"}]),
                    _click=mock.MagicMock(side_effect=lambda _f, _p, what: clicked.append(what)),
                    _back_out=mock.MagicMock(), drafts_pending=mock.MagicMock(side_effect=AssertionError("no count")))
        name = wb.create_shift(mock.MagicMock(), mock.Mock(), **self.T, publish=True)
        self.assertEqual((name, clicked), ("Lee, Kim", ["Next", "Publish"]))
        self.assertEqual(self.calls[-1], ("grid", "published", 540))
