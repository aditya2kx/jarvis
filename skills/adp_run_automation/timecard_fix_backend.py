#!/usr/bin/env python3
"""skills/adp_run_automation/timecard_fix_backend - write an operator-approved
clock-out into an open ADP timecard entry (Issue #356).

The ONLY write this module makes: fill the Out Time of an entry ADP shows as
open ("Out Time Blank · Missing Out Punch"), attach a comment, Save once. It
never edits a completed entry, never deletes, never touches payroll.

Selectors calibrated live 2026-10-03 against RUN > Time > Timecards: the day
panel (``Show details for Monday, September 21st``) lists each entry with an
``Actions`` menu (Edit / Delete / Add new comment); Edit turns the entry into
``In Time *`` / ``Out Time`` textboxes (``hh:mm AM``); ``Add new comment``
opens an ``Add New Comment`` dialog; ``Save`` commits; ``Back`` discards.

Page driving sits behind ``TimecardUi`` so ``apply_out_punch``'s safety rules
(re-read before writing, one Save, verify by read-back, no retry) are unit
tested against a fake.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Optional, Protocol

from skills.adp_run_automation import timecard_ui_backend as tub
from skills.bhaga_labor.punch_gaps import parse_ampm, to_minutes

APPLIED = "applied"
ALREADY_RESOLVED = "already_resolved"
FAILED = "failed"

COMMENT_MAX = 512

_DIALOG_ENTRY = re.compile(
    r"In Time (\d{1,2}:\d{2}\s*[AP]M|Blank) Out Time (\d{1,2}:\d{2}\s*[AP]M|Blank)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ApplyResult:
    status: str  # applied | already_resolved | failed
    error: Optional[str] = None
    evidence_path: Optional[str] = None


class TimecardUi(Protocol):
    def open_day(self, label: str) -> str: ...  # returns the day panel snapshot
    def day_snapshot(self) -> str: ...
    def edit_entry(self, index: int) -> None: ...
    def fill_out_time(self, ampm: str) -> None: ...
    def open_add_entry(self, date: datetime.date) -> Optional[str]: ...  # None: day has entries
    def fill_in_time(self, ampm: str) -> None: ...
    def add_comment(self, text: str) -> None: ...
    def save(self) -> None: ...
    def back(self) -> None: ...
    def evidence(self, tag: str) -> Optional[str]: ...


def day_button_label(date: datetime.date) -> str:
    """2026-09-21 → 'Monday, September 21st' (the day panel's button name)."""
    d = date.day
    suffix = "th" if 11 <= d % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return f"{date.strftime('%A')}, {date.strftime('%B')} {d}{suffix}"


def add_entry_label(date: datetime.date) -> str:
    """2026-10-03 → 'Saturday, Oct 3rd' (the month view's 'Add new entry for …' button)."""
    return f"{date.strftime('%A')}, {date.strftime('%b')} {day_button_label(date).split()[-1]}"


def to_ampm(hhmm: str) -> str:
    """'15:38' → '03:38 PM' (the Out Time textbox format)."""
    m = to_minutes(hhmm)
    h, mi = divmod(m, 60)
    return f"{(h % 12) or 12:02d}:{mi:02d} {'PM' if h >= 12 else 'AM'}"


def parse_day_panel(snapshot: str) -> list[tuple[Optional[str], Optional[str]]]:
    """[(in, out)] as 'HH:MM' / None for each read-only entry in the day panel."""
    out = []
    for a, b in _DIALOG_ENTRY.findall(snapshot or ""):
        out.append((
            None if a.lower() == "blank" else parse_ampm(a),
            None if b.lower() == "blank" else parse_ampm(b),
        ))
    return out


def comment_text(
    *, out_time: str, decided_by: str, rule: Optional[str], in_time: Optional[str] = None,
) -> str:
    why = {
        "pay_scheduled_hours": "pays the full scheduled hours",
        "close_at_scheduled_end": "closes at the scheduled end",
        "close_at_shop_close": "closes at shop close",
        "scheduled_shift": "the scheduled shift",
    }.get(rule or "", "operator-entered time")
    what = (
        f"Missing punch entered as {to_ampm(in_time)} - {to_ampm(out_time)}" if in_time
        else f"Missing clock-out set to {to_ampm(out_time)}"
    )
    text = f"{what} via BHAGA Operator Console ({why}); approved by {decided_by}."
    return text[:COMMENT_MAX]


def apply_out_punch(
    ui: TimecardUi,
    *,
    date: datetime.date,
    open_entry_index: int,
    expect_in: str,
    out_time: str,
    comment: str,
) -> ApplyResult:
    """Close one open entry. Saves at most once and never retries.

    Re-reads the day first: an entry ADP already closed is ``already_resolved``
    with no write, so a repeat click (or a fix made by hand in ADP) cannot
    double-edit. ``applied`` only when a fresh read shows the requested out.
    """
    label = day_button_label(date)
    try:
        entries = parse_day_panel(ui.open_day(label))
    except Exception as exc:  # noqa: BLE001 — day not visible (other pay period, etc.)
        return ApplyResult(FAILED, f"open-day: {type(exc).__name__}: {exc}"[:300],
                           ui.evidence("open-day"))
    if open_entry_index >= len(entries):
        ui.back()
        return ApplyResult(ALREADY_RESOLVED, f"entry {open_entry_index} no longer in ADP")
    in_t, out_t = entries[open_entry_index]
    if out_t is not None:
        ui.back()
        return ApplyResult(ALREADY_RESOLVED, f"ADP already shows out {out_t}")
    if in_t != expect_in:
        ev = ui.evidence("in-mismatch")
        ui.back()
        return ApplyResult(FAILED, f"ADP clock-in is {in_t}, expected {expect_in}", ev)
    if to_minutes(out_time) <= to_minutes(expect_in):
        ui.back()
        return ApplyResult(FAILED, f"out {out_time} is not after clock-in {expect_in}")

    step = "edit"
    try:
        ui.edit_entry(open_entry_index)
        step = "fill-out"
        ui.fill_out_time(to_ampm(out_time))
        step = "comment"
        ui.add_comment(comment)
    except Exception as exc:  # noqa: BLE001 — nothing saved yet; discard the edit
        ev = ui.evidence(step)
        ui.back()
        return ApplyResult(FAILED, f"{step}: {type(exc).__name__}: {exc}"[:300], ev)

    try:
        ui.save()
    except Exception as exc:  # noqa: BLE001 — Save may or may not have landed
        ev = ui.evidence("save")
        return ApplyResult(FAILED, f"save: {type(exc).__name__}: {exc}"[:300], ev)

    try:
        after = parse_day_panel(ui.day_snapshot())
    except Exception as exc:  # noqa: BLE001
        after = []
        verify_err = f"{type(exc).__name__}: {exc}"
    else:
        verify_err = None
    got = after[open_entry_index] if open_entry_index < len(after) else None
    if got == (expect_in, out_time):
        ui.back()
        return ApplyResult(APPLIED)
    ev = ui.evidence("verify")
    ui.back()
    return ApplyResult(
        FAILED,
        f"verify: ADP shows {got} after Save, wanted {(expect_in, out_time)}"
        + (f" ({verify_err})" if verify_err else ""),
        ev,
    )


def apply_new_entry(
    ui: TimecardUi,
    *,
    date: datetime.date,
    in_time: str,
    out_time: str,
    comment: str,
) -> ApplyResult:
    """Add one entry on a scheduled day ADP shows with no punch. Same rules as
    ``apply_out_punch``: re-read first (a day that now has any entry is
    ``already_resolved``), Save once, never retry, ``applied`` only on read-back."""
    if to_minutes(out_time) <= to_minutes(in_time):
        return ApplyResult(FAILED, f"out {out_time} is not after in {in_time}")
    try:
        existing = ui.open_add_entry(date)
    except Exception as exc:  # noqa: BLE001 — day not visible (other pay period, etc.)
        return ApplyResult(FAILED, f"open-add: {type(exc).__name__}: {exc}"[:300],
                           ui.evidence("open-add"))
    if existing is not None:
        entries = parse_day_panel(existing)
        ui.back()
        return ApplyResult(ALREADY_RESOLVED, f"ADP already has entries {entries}")

    step = "fill-in"
    try:
        ui.fill_in_time(to_ampm(in_time))
        step = "fill-out"
        ui.fill_out_time(to_ampm(out_time))
        step = "comment"
        ui.add_comment(comment)
    except Exception as exc:  # noqa: BLE001 — nothing saved yet; discard the form
        ev = ui.evidence(step)
        ui.back()
        return ApplyResult(FAILED, f"{step}: {type(exc).__name__}: {exc}"[:300], ev)

    try:
        ui.save()
    except Exception as exc:  # noqa: BLE001 — Save may or may not have landed
        ev = ui.evidence("save")
        return ApplyResult(FAILED, f"save: {type(exc).__name__}: {exc}"[:300], ev)

    try:
        after = parse_day_panel(ui.day_snapshot())
        verify_err = None
    except Exception as exc:  # noqa: BLE001
        after, verify_err = [], f"{type(exc).__name__}: {exc}"
    if (in_time, out_time) in after:
        ui.back()
        return ApplyResult(APPLIED)
    ev = ui.evidence("verify")
    ui.back()
    return ApplyResult(
        FAILED,
        f"verify: ADP shows {after} after Save, wanted {[(in_time, out_time)]}"
        + (f" ({verify_err})" if verify_err else ""),
        ev,
    )


class PlaywrightTimecardUi:
    """``TimecardUi`` over the live Timecards iframe for one employee."""

    def __init__(self, page, *, evidence_dir):
        self.page = page
        self.frame = page.frame_locator(f'iframe[name="{tub.TIMECARDS_FRAME_NAME}"]')
        self.evidence_dir = evidence_dir

    def _snap(self) -> str:
        return self.frame.locator("body").aria_snapshot(timeout=10_000)

    def _wait_panel(self, *, editing: bool = False) -> str:
        for _ in range(20):
            self.page.wait_for_timeout(500)
            snap = self._snap()
            if "- dialog " in snap and ("textbox" in snap) == editing:
                return snap
        raise RuntimeError("day panel never settled")

    def select_employee(self, name: str, current: str) -> str:
        from skills.adp_run_automation import runner

        if current != name:
            self.frame.get_by_role("button", name=current, exact=True).click()
            self.page.wait_for_timeout(800)
            self.frame.get_by_role("menuitem", name=name, exact=True).click()
        return runner._wait_timecards_employee(self.page, name)

    def open_day(self, label: str) -> str:
        self._label = label
        self.frame.get_by_role("button", name=f"Show details for {label}").click(timeout=10_000)
        return self._wait_panel()

    def day_snapshot(self) -> str:
        """Read-only panel after Save; reopens the day if Save closed it."""
        try:
            return self._wait_panel()
        except RuntimeError:
            self.back()
            return self.open_day(self._label)

    def edit_entry(self, index: int) -> None:
        self.frame.get_by_role("button", name="Actions").nth(index).click(timeout=10_000)
        self.page.wait_for_timeout(600)
        self.frame.get_by_role("menuitem", name="Edit", exact=True).click(timeout=10_000)
        self._wait_panel(editing=True)

    def open_add_entry(self, date: datetime.date) -> Optional[str]:
        """Open the add-entry form; if the day already has entries, open its
        details instead and return that snapshot (nothing to add)."""
        self._label = day_button_label(date)
        add = self.frame.get_by_role("button", name=f"Add new entry for {add_entry_label(date)}")
        if add.count():
            add.first.click(timeout=10_000)
            self._wait_panel(editing=True)
            return None
        return self.open_day(self._label)

    def _fill_time(self, name: str, ampm: str) -> None:
        box = self.frame.get_by_role("textbox", name=name, exact=True)
        box.fill(ampm, timeout=10_000)
        box.press("Tab")
        self.page.wait_for_timeout(400)
        shown = box.input_value().strip()
        if not shown or parse_ampm(shown) != parse_ampm(ampm):
            raise RuntimeError(f"{name} box shows {box.input_value()!r}")

    def fill_in_time(self, ampm: str) -> None:
        self._fill_time("In Time *", ampm)

    def fill_out_time(self, ampm: str) -> None:
        self._fill_time("Out Time", ampm)

    def add_comment(self, text: str) -> None:
        region = self.frame.get_by_role("region", name="Time Entry")
        region.get_by_role("button", name="Add new comment").click(timeout=10_000)
        # sdf-textarea keeps its <textarea> in a shadow root; role lookup misses it headless.
        note = self.frame.locator('[data-e2e="comment-note"] textarea')
        note.fill(text, timeout=10_000)
        if note.input_value() != text:
            raise RuntimeError("comment box did not take the text")
        submit = self.frame.locator('sdf-button[data-e2e="slidein-submit"]:visible',
                                    has_text="Add new comment")
        submit.click(timeout=10_000)
        self.page.wait_for_timeout(800)
        # The added comment stays on screen inline in the entry; only the dialog goes away.
        if submit.count():
            raise RuntimeError("comment dialog did not close")

    def save(self) -> None:
        self.frame.locator('sdf-button[data-e2e="save-btn"]:visible').click(timeout=10_000)
        self.page.wait_for_timeout(2500)

    def back(self) -> None:
        try:
            self.frame.get_by_role("button", name="Back").first.click(timeout=5_000)
            self.page.wait_for_timeout(800)
        except Exception:  # noqa: BLE001 — already back on the month view
            pass

    def evidence(self, tag: str) -> Optional[str]:
        try:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
            path = self.evidence_dir / f"punch-fix-{tag}-{stamp}.png"
            self.page.screenshot(path=str(path))
            return str(path)
        except Exception:  # noqa: BLE001
            return None
