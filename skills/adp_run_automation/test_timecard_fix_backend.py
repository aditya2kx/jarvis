"""Tests for timecard_fix_backend — the one ADP timecard write (Issue #356)."""

from __future__ import annotations

import datetime

import pytest

from skills.adp_run_automation import timecard_fix_backend as tfb

D = datetime.date(2026, 9, 21)

# Day panel as captured live 2026-10-03 (Emp A, Mon Sep 21), read-only mode.
OPEN_PANEL = """- dialog "Monday, September 21, 2026":
  - heading "Monday, September 21, 2026" [level=2]
  - button "Back"
  - text: "Your scheduled shifts: 09:00 AM - 03:00 PM"
  - heading "Time Entry" [level=3]
  - button "Actions"
  - text: In Time 09:00 AM Out Time Blank Total Hours 0:00HRS Missing Out Punch
  - button "Save"
"""
CLOSED_PANEL = OPEN_PANEL.replace(
    "Out Time Blank Total Hours 0:00HRS Missing Out Punch", "Out Time 03:00 PM Total Hours 6:00HRS"
)


class FakeUi:
    def __init__(self, panel, *, after_save=None, fail_at=None):
        self.panel = panel
        self.after_save = after_save if after_save is not None else CLOSED_PANEL
        self.fail_at = fail_at
        self.calls: list[str] = []

    def _step(self, name):
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError(f"boom at {name}")

    def open_day(self, label):
        self._step(f"open:{label}")
        return self.panel

    def day_snapshot(self):
        self._step("read")
        return self.after_save

    def edit_entry(self, index):
        self._step(f"edit:{index}")

    def fill_out_time(self, ampm):
        self._step(f"fill:{ampm}")

    def open_add_entry(self, date):
        self._step(f"add:{date}")
        return self.panel

    def fill_in_time(self, ampm):
        self._step(f"fill-in:{ampm}")

    def add_comment(self, text):
        self._step("comment")

    def save(self):
        self._step("save")

    def back(self):
        self.calls.append("back")

    def evidence(self, tag):
        return f"/tmp/{tag}.png"


def apply(ui, out="15:00", expect_in="09:00", idx=0):
    return tfb.apply_out_punch(
        ui, date=D, open_entry_index=idx, expect_in=expect_in, out_time=out, comment="c",
    )


def test_happy_path_saves_once_and_verifies_by_read_back():
    ui = FakeUi(OPEN_PANEL)
    assert apply(ui) == tfb.ApplyResult(tfb.APPLIED)
    assert ui.calls == [
        "open:Monday, September 21st", "edit:0", "fill:03:00 PM", "comment", "save", "read", "back",
    ]


def test_entry_already_closed_in_adp_writes_nothing():
    ui = FakeUi(CLOSED_PANEL)
    res = apply(ui)
    assert res.status == tfb.ALREADY_RESOLVED
    assert "save" not in ui.calls and not any(c.startswith("edit") for c in ui.calls)


def test_clock_in_changed_in_adp_fails_without_writing():
    res = apply(FakeUi(OPEN_PANEL), expect_in="09:05")
    assert res.status == tfb.FAILED and "expected 09:05" in res.error


def test_failure_before_save_discards_the_edit():
    ui = FakeUi(OPEN_PANEL, fail_at="comment")
    res = apply(ui)
    assert res.status == tfb.FAILED and res.error.startswith("comment:")
    assert "save" not in ui.calls and ui.calls[-1] == "back"
    assert res.evidence_path == "/tmp/comment.png"


def test_save_error_is_failed_and_never_retried():
    ui = FakeUi(OPEN_PANEL, fail_at="save")
    res = apply(ui)
    assert res.status == tfb.FAILED
    assert ui.calls.count("save") == 1


def test_read_back_mismatch_is_failed():
    res = apply(FakeUi(OPEN_PANEL, after_save=OPEN_PANEL))
    assert res.status == tfb.FAILED and res.error.startswith("verify:")


def test_out_not_after_clock_in_is_refused():
    ui = FakeUi(OPEN_PANEL)
    assert apply(ui, out="08:30").status == tfb.FAILED
    assert "save" not in ui.calls


@pytest.mark.parametrize("hhmm,want", [("15:38", "03:38 PM"), ("09:05", "09:05 AM"),
                                       ("12:00", "12:00 PM"), ("00:30", "12:30 AM")])
def test_to_ampm(hhmm, want):
    assert tfb.to_ampm(hhmm) == want


@pytest.mark.parametrize("d,want", [
    (datetime.date(2026, 9, 21), "Monday, September 21st"),
    (datetime.date(2026, 10, 2), "Friday, October 2nd"),
    (datetime.date(2026, 9, 23), "Wednesday, September 23rd"),
    (datetime.date(2026, 9, 11), "Friday, September 11th"),
])
def test_day_button_label(d, want):
    assert tfb.day_button_label(d) == want


def test_parse_day_panel_reads_every_entry():
    snap = OPEN_PANEL + "  - text: In Time 05:00 PM Out Time 08:30 PM Total Hours 3:30HRS\n"
    assert tfb.parse_day_panel(snap) == [("09:00", None), ("17:00", "20:30")]


def test_comment_fits_adp_limit_and_names_the_approver():
    text = tfb.comment_text(out_time="15:38", decided_by="adi@mypalmetto.co",
                            rule="pay_scheduled_hours")
    assert "03:38 PM" in text and "adi@mypalmetto.co" in text
    assert len(text) <= tfb.COMMENT_MAX


# ── New entry on a scheduled day with no punch ────────────────────
NEW_PANEL = CLOSED_PANEL.replace("09:00 AM", "01:30 PM").replace("03:00 PM", "08:30 PM")


def add(ui, in_time="13:30", out="20:30"):
    return tfb.apply_new_entry(ui, date=D, in_time=in_time, out_time=out, comment="c")


def test_new_entry_fills_both_times_saves_once_and_verifies():
    ui = FakeUi(None, after_save=NEW_PANEL)
    assert add(ui) == tfb.ApplyResult(tfb.APPLIED)
    assert ui.calls == [f"add:{D}", "fill-in:01:30 PM", "fill:08:30 PM", "comment", "save",
                        "read", "back"]


def test_new_entry_day_that_now_has_an_entry_is_already_resolved_without_writing():
    ui = FakeUi(NEW_PANEL)
    res = add(ui)
    assert res.status == tfb.ALREADY_RESOLVED
    assert "save" not in ui.calls and ui.calls[-1] == "back"


@pytest.mark.parametrize("step", ["fill-in:01:30 PM", "fill:08:30 PM", "comment"])
def test_new_entry_failure_before_save_discards_and_never_saves(step):
    ui = FakeUi(None, fail_at=step)
    res = add(ui)
    assert res.status == tfb.FAILED and "save" not in ui.calls and ui.calls[-1] == "back"


def test_new_entry_save_error_is_failed_and_not_retried():
    ui = FakeUi(None, fail_at="save")
    assert add(ui).status == tfb.FAILED
    assert ui.calls.count("save") == 1


def test_new_entry_read_back_mismatch_fails():
    ui = FakeUi(None, after_save=CLOSED_PANEL)
    res = add(ui)
    assert res.status == tfb.FAILED and res.error.startswith("verify")


def test_new_entry_out_not_after_in_never_opens_adp():
    ui = FakeUi(None)
    assert add(ui, in_time="20:30", out="13:30").status == tfb.FAILED
    assert ui.calls == []


def test_add_entry_label_and_new_entry_comment():
    assert tfb.add_entry_label(datetime.date(2026, 10, 3)) == "Saturday, Oct 3rd"
    assert tfb.add_entry_label(datetime.date(2026, 9, 22)) == "Tuesday, Sep 22nd"
    text = tfb.comment_text(out_time="20:30", in_time="13:30", decided_by="a@b", rule="scheduled_shift")
    assert text.startswith("Missing punch entered as 01:30 PM - 08:30 PM")
    assert "the scheduled shift" in text
