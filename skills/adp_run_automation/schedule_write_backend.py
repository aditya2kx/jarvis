"""ADP Team Schedule writes — Issue #337.

Creates shifts as *drafts* (Actions › Create shift / Create open shift › Save as
draft) and publishes a week's drafts (toolbar "Publish drafts (N)"). Drafts stay
invisible to employees until published, which is why the console splits the two
into separate confirmed clicks.

Every call site is an operator click behind ``FEATURES.adpScheduleWrite``. The
only commit buttons pressed are ``Save as draft`` / ``Save draft`` and the
publish dialog's ``Publish``. ``dry_run=True`` walks each wizard to its final
step and backs out without saving (selector check against live ADP).

Wizard shape (read-only spikes 2026-09-27, week of Sep 28 / Oct 5):
  Create shift       step 1: date picker + "Unscheduled employees" radio list
                     (``input[name=employeeGroup]``, ``aria-labelledby`` →
                     ``[data-e2e=pwc-person-name]``) › Next
                     step 2: Start / End time (``hh:mm a.m.`` masked), note,
                     Visible to employee › Save as draft | Publish
  Create open shift  step 1: count, date, Start / End time, expiration › Next
                     step 2: eligibility summary › Previous | Save draft | Publish
"""

from __future__ import annotations

import datetime as dt
import re
import time

TIME_INPUT = "input[placeholder='hh:mm a.m.']"
PUBLISH_DRAFTS_RE = re.compile(r"Publish drafts\s*\((\d+)\)", re.I)
WEEK_RANGE_RE = re.compile(r"Week of\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", re.I)

PICKER_JS = r"""
() => [...document.querySelectorAll('input[name=employeeGroup]')].map(i => {
  const ref = i.getAttribute('aria-labelledby');
  const host = ref && document.getElementById(ref);
  const el = host && (host.querySelector('[data-e2e=pwc-person-name]') || host);
  return { value: i.value, name: el ? el.textContent.replace(/\s+/g, ' ').trim() : '' };
})
"""


class ScheduleWriteError(RuntimeError):
    """A single shift could not be created; the caller records it and moves on."""


class PublishUnconfirmed(ScheduleWriteError):
    """Publish was confirmed in ADP, but the draft count never read 0 afterwards."""

    def __init__(self, msg: str, pending: int):
        super().__init__(msg)
        self.pending = pending


class UnconfirmedSave(ScheduleWriteError):
    """Save was clicked but the draft count never moved — the shift may exist in ADP.

    Callers stop the run: retrying (or continuing) could duplicate shifts if the
    count check itself is what broke.
    """


# ── Pure helpers ──────────────────────────────────────────────────


def fmt_time(minutes: int) -> str:
    """Minutes after midnight → ADP's masked input text, e.g. 390 → '6:30 AM'."""
    if not 0 <= minutes < 24 * 60:
        raise ValueError(f"minutes out of range: {minutes}")
    h, m = divmod(minutes, 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def fmt_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def week_label_start(label: str) -> dt.date:
    """'Week of Sep 28, 2026 - Oct 4, 2026' → date(2026, 9, 28)."""
    m = WEEK_RANGE_RE.search(label or "")
    if not m:
        raise ValueError(f"unrecognised week label: {label!r}")
    return dt.datetime.strptime(m.group(1), "%b %d, %Y").date()


def match_employee(names: list[str], canonical: str) -> str | None:
    """Pick the ADP display name for a canonical one ('Johnson, Dolce' ↔ 'Johnson, Dolce J').

    Exact match wins; otherwise a unique name that extends ``canonical`` by a
    space-separated suffix (middle initial). Ambiguity returns None rather than guess.
    """
    want = " ".join(canonical.split()).lower()
    norm = {n: " ".join(n.split()).lower() for n in names}
    exact = [n for n, v in norm.items() if v == want]
    if exact:
        return exact[0]
    prefixed = [n for n, v in norm.items() if v.startswith(want + " ")]
    return prefixed[0] if len(prefixed) == 1 else None


def publish_count(text: str) -> int | None:
    m = PUBLISH_DRAFTS_RE.search(text or "")
    return int(m.group(1)) if m else None


# ── Playwright steps (frame = Team Schedule iframe from runner._open_team_schedule) ──


def _visible_button(frame, pattern: str):
    loc = frame.get_by_role("button", name=re.compile(pattern, re.I))
    for i in range(loc.count()):
        if loc.nth(i).is_visible():
            return loc.nth(i)
    return None


def _click(frame, pattern: str, *, what: str) -> None:
    btn = _visible_button(frame, pattern)
    if btn is None:
        raise ScheduleWriteError(f"button not found: {what}")
    btn.click(timeout=8000)


def drafts_pending(frame) -> int:
    btn = _visible_button(frame, r"^\s*Publish drafts")
    if btn is None:
        raise ScheduleWriteError("Publish drafts button not found")
    n = publish_count(btn.inner_text(timeout=5000))
    if n is None:
        raise ScheduleWriteError("could not read the Publish drafts count")
    return n


def _wait_drafts(frame, page, expect: int, timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if drafts_pending(frame) == expect:
                return True
        except ScheduleWriteError:
            pass
        page.wait_for_timeout(500)
    return False


# Light-DOM panes only: Playwright's shadow-piercing locator also matches inner
# copies that keep a box after the dialog closes.
OPEN_PANE_JS = r"""
() => [...document.querySelectorAll('sdf-focus-pane')].some(p => {
  const b = p.getBoundingClientRect(); return b.width > 0 && b.height > 0;
})
"""


def _pane_open(frame) -> bool:
    return bool(frame.evaluate(OPEN_PANE_JS))


def _wait_for(page, check, timeout_s: float, step_ms: int = 250) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if check():
            return True
        if time.monotonic() > deadline:
            return False
        page.wait_for_timeout(step_ms)


def _open_action(frame, page, item: str) -> None:
    _back_out(frame, page)
    _click(frame, r"^\s*Actions\b", what="Actions")
    frame.get_by_text(re.compile(rf"^\s*{re.escape(item)}\s*$", re.I)).first.click(timeout=5000)
    if not _wait_for(page, lambda: _pane_open(frame), 8.0):
        raise ScheduleWriteError(f"{item} dialog did not open")
    frame.locator("sdf-focus-pane sdf-date-picker input").first.wait_for(state="visible", timeout=8000)


def _back_out(frame, page) -> None:
    """Close any open wizard without saving.

    Create shift: Escape closes an untouched step; with entries filled in, ADP
    asks "Continue without saving?" — its exact "Close" discards (the X "Close
    dialog" and "No, go back" return to the wizard). Create open shift ignores
    Escape; its header "Back" closes it.
    """
    for attempt in range(6):
        if not _pane_open(frame):
            return
        discard = _visible_button(frame, r"^\s*Close\s*$")
        back = _visible_button(frame, r"^\s*Back\s*$") if attempt % 2 else None
        if discard is not None:
            discard.click(timeout=5000)
        elif back is not None:
            back.click(timeout=5000)
        else:
            page.keyboard.press("Escape")
        page.wait_for_timeout(900)
    if _pane_open(frame):
        raise ScheduleWriteError("could not close the ADP schedule dialog")


def _fill_times(frame, page, start_min: int, end_min: int) -> None:
    times = frame.locator(f"sdf-focus-pane {TIME_INPUT}")
    for i, value in enumerate((fmt_time(start_min), fmt_time(end_min))):
        box = times.nth(i)
        box.fill(value, timeout=5000)
        box.press("Tab")
    page.wait_for_timeout(300)


def goto_week(frame, page, week_start: dt.date) -> None:
    """Advance the grid (opens on the current week) until it shows ``week_start``."""
    from skills.adp_run_automation import runner as r

    for _ in range(10):
        label = frame.get_by_text(re.compile(r"Week of")).first.inner_text(timeout=5000)
        shown = week_label_start(label)
        if shown == week_start:
            return
        if shown > week_start:
            raise ScheduleWriteError(f"week {week_start} is before the grid's first week {shown}")
        r._goto_next_week(page, frame)
    raise ScheduleWriteError(f"could not reach week {week_start}")


def _settled_picker(frame, page, timeout_s: float = 8.0) -> list[dict]:
    """ADP's unscheduled-employee list for the date just typed, once it stops changing.

    The list re-renders after the date commits, so wait for two identical reads.
    """
    page.wait_for_timeout(500)
    last: list[dict] | None = None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        now = frame.evaluate(PICKER_JS)
        if now and now == last:
            return now
        last = now
        page.wait_for_timeout(400)
    return last or []


def create_shift(
    frame, page, *, date_iso: str, employee: str, start_min: int, end_min: int,
    before: int | None = None, dry_run: bool = False,
) -> str:
    """Create one assigned draft shift. Returns the ADP display name used.

    ``before`` is the draft count the caller expects ADP to show now; a lagging
    toolbar count must not let the previous save "confirm" this one.
    """
    if before is None:
        before = drafts_pending(frame)
    _open_action(frame, page, "Create shift")
    try:
        date_box = frame.locator("sdf-focus-pane sdf-date-picker input").first
        date_box.fill(fmt_date(date_iso), timeout=5000)
        date_box.press("Tab")
        options = _settled_picker(frame, page)
        name = match_employee([o["name"] for o in options], employee)
        if name is None:
            listed = ", ".join(o["name"] for o in options) or "nobody"
            raise ScheduleWriteError(
                f"{employee} is not in ADP's unscheduled list for {date_iso} (listed: {listed})"
            )
        value = next(o["value"] for o in options if o["name"] == name)
        frame.locator(f"input[name=employeeGroup][value='{value}']").check(force=True, timeout=5000)
        _click(frame, r"^\s*Next\b", what="Next")
        frame.locator(f"sdf-focus-pane {TIME_INPUT}").first.wait_for(state="visible", timeout=8000)
        _fill_times(frame, page, start_min, end_min)
        if dry_run:
            if _visible_button(frame, r"^\s*Save as draft\s*$") is None:
                raise ScheduleWriteError("Save as draft not found on step 2")
            _back_out(frame, page)
            return name
        _click(frame, r"^\s*Save as draft\s*$", what="Save as draft")
    except Exception:
        _back_out(frame, page)
        raise
    if not _wait_drafts(frame, page, before + 1):
        raise UnconfirmedSave(
            f"ADP did not confirm the draft for {employee} on {date_iso} — check ADP before re-saving"
        )
    return name


def create_open_shift(
    frame, page, *, date_iso: str, start_min: int, end_min: int,
    before: int | None = None, dry_run: bool = False,
) -> None:
    """Create one unassigned draft open shift (default eligibility). ``before`` as in create_shift."""
    if before is None:
        before = drafts_pending(frame)
    _open_action(frame, page, "Create open shift")
    try:
        date_box = frame.locator("sdf-focus-pane sdf-date-picker input").first
        date_box.fill(fmt_date(date_iso), timeout=5000)
        date_box.press("Tab")
        _fill_times(frame, page, start_min, end_min)
        _click(frame, r"^\s*Next\b", what="Next")
        _wait_for(page, lambda: _visible_button(frame, r"^\s*Save draft\s*$") is not None, 8.0)
        if dry_run:
            if _visible_button(frame, r"^\s*Save draft\s*$") is None:
                raise ScheduleWriteError("Save draft not found on the eligibility step")
            _back_out(frame, page)
            return
        _click(frame, r"^\s*Save draft\s*$", what="Save draft")
    except Exception:
        _back_out(frame, page)
        raise
    if not _wait_drafts(frame, page, before + 1):
        raise UnconfirmedSave(
            f"ADP did not confirm the open-shift draft on {date_iso} — check ADP before re-saving"
        )


# Marks the one draft shift of ``name`` on ``day`` (day-of-month) spanning ``rng``
# ("2:00 PM - 8:30 PM") with data-jarvis-del. Day columns are matched to cells by x.
FIND_SHIFT_JS = r"""
([name, day, rng]) => {
  const norm = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  document.querySelectorAll('[data-jarvis-del]').forEach(e => e.removeAttribute('data-jarvis-del'));
  const want = name.toLowerCase();
  const dayRe = new RegExp('(^|\\D)' + day + '(\\D|$)');
  const heads = [...document.querySelectorAll('.day-cell.column-header')].filter(h => dayRe.test(norm(h)));
  if (heads.length !== 1) return { found: false, why: 'day header matches ' + heads.length };
  const hb = heads[0].getBoundingClientRect(); const hx = hb.x + hb.width / 2;
  const rows = [...document.querySelectorAll('.calendar-row')].filter(row => {
    const n = row.querySelector('.worker-name'); const t = n ? norm(n).toLowerCase() : '';
    return t === want || t.startsWith(want + ' ');
  });
  if (rows.length > 1) return { found: false, why: 'worker rows ' + rows.length };
  if (!rows.length) {
    const sc = document.querySelector('.calendar-view') || document.querySelector('.team-work-schedules-list');
    if (sc) sc.scrollTop += 400;
    return { found: false, why: 'worker row not rendered', scroll: true };
  }
  let cell = null, bd = 1e9;
  for (const c of rows[0].querySelectorAll('team-schedule-calendar-day')) {
    const b = c.getBoundingClientRect(); const d = Math.abs(b.x + b.width / 2 - hx);
    if (d < bd) { bd = d; cell = c; }
  }
  if (!cell) return { found: false, why: 'no day cell' };
  const shifts = [...cell.querySelectorAll('schedule-calendar-shifts')]
    .filter(s => norm(s).includes(rng) && /draft/i.test(norm(s)));
  if (shifts.length !== 1) return { found: false, why: 'matching draft shifts ' + shifts.length, cell: norm(cell) };
  (shifts[0].querySelector('schedule-shift-range') || shifts[0]).setAttribute('data-jarvis-del', '1');
  return { found: true, cell: norm(cell) };
}
"""

PANE_TEXT_JS = r"""
() => [...document.querySelectorAll('sdf-focus-pane')]
  .filter(p => { const b = p.getBoundingClientRect(); return b.width > 0 && b.height > 0; })
  .map(p => (p.innerText || '').replace(/\s+/g, ' ').trim()).join(' | ')
"""

DELETE_CONFIRM_TEXT = re.compile(r"Are you sure you want to delete this shift", re.I)
CONFIRM_RE = re.compile(r"^(Yes\b.*|Delete|Confirm|OK)$", re.I)
CANCEL_RE = re.compile(r"^(No\b.*|Cancel|Keep\b.*)$", re.I)


def delete_shift(
    frame, page, *, date_iso: str, employee: str, start_min: int, end_min: int,
    before: int, dry_run: bool = False,
) -> None:
    """Delete one assigned *draft* shift via its details pane (never "Delete all schedules").

    ``before`` is the draft count the caller tracks (the toolbar count is hidden
    while the pane is open). Confirmed only when ADP shows ``before - 1``.
    """
    _back_out(frame, page)
    d = dt.date.fromisoformat(date_iso)
    rng = f"{fmt_time(start_min)} - {fmt_time(end_min)}"
    info: dict = {}
    for _ in range(20):
        info = frame.evaluate(FIND_SHIFT_JS, [employee, d.day, rng])
        if info.get("found") or not info.get("scroll"):
            break
        page.wait_for_timeout(400)
    if not info.get("found"):
        raise ScheduleWriteError(f"draft {employee} {date_iso} {rng} not found in grid: {info.get('why')}")
    frame.locator("[data-jarvis-del='1']").first.click(timeout=8000)
    if not _wait_for(page, lambda: _pane_open(frame), 8.0):
        raise ScheduleWriteError("shift details pane did not open")
    page.wait_for_timeout(500)
    pane = frame.evaluate(PANE_TEXT_JS)
    expect = (f"{d:%b} {d.day}, {d.year}", rng, "Draft")
    if not all(s in pane for s in expect) or employee.split(",")[0] not in pane:
        _back_out(frame, page)
        raise ScheduleWriteError(f"details pane is not {employee} {date_iso} {rng}: {pane[:200]!r}")
    dels = frame.locator("sdf-focus-pane sdf-button[aria-label=' Delete ']")
    if dels.count() != 1:
        _back_out(frame, page)
        raise ScheduleWriteError(f"expected one per-shift Delete in the pane, found {dels.count()}")
    dels.first.click(timeout=5000)
    # The confirmation is a nested focus pane; its buttons live in shadow DOM,
    # which role locators pierce. Scope to the innermost pane so nothing in the
    # details pane (notably "Delete all schedules") can be matched.
    confirm_pane = frame.locator("sdf-focus-pane").filter(has_text=DELETE_CONFIRM_TEXT).last
    try:
        confirm_pane.wait_for(state="visible", timeout=8000)
    except Exception:
        _back_out(frame, page)
        raise ScheduleWriteError("delete confirmation did not appear")
    buttons = confirm_pane.get_by_role("button")
    names = [buttons.nth(i).get_attribute("aria-label") or buttons.nth(i).inner_text() for i in range(buttons.count())]
    names = [" ".join((n or "").split()) for n in names]
    print(f"[schedule_write] delete confirm buttons {names}")
    yes = [i for i, n in enumerate(names) if CONFIRM_RE.match(n) and "all" not in n.lower()]
    no = [i for i, n in enumerate(names) if CANCEL_RE.match(n)]
    if dry_run or len(yes) != 1:
        if no:
            buttons.nth(no[0]).click(timeout=5000)
        else:
            page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
        _back_out(frame, page)
        if not dry_run:
            raise ScheduleWriteError(f"delete confirmation buttons not recognised: {names}")
        if not _wait_drafts(frame, page, before, timeout_s=8.0):
            raise UnconfirmedSave(f"dry-run delete changed the draft count (expected {before})")
        return
    buttons.nth(yes[0]).click(timeout=5000)
    page.wait_for_timeout(1000)
    _back_out(frame, page)
    if not _wait_drafts(frame, page, before - 1):
        raise UnconfirmedSave(f"ADP did not confirm deleting {employee} on {date_iso} {rng}")


def publish_drafts(frame, page, *, dry_run: bool = False) -> int:
    """Publish the shown week's drafts. Returns how many were pending."""
    pending = drafts_pending(frame)
    # Dry run stops here: the toolbar button itself may publish without a dialog.
    if pending == 0 or dry_run:
        return pending
    _click(frame, r"^\s*Publish drafts\b", what="Publish drafts")
    page.wait_for_timeout(2000)
    confirm = _visible_button(frame, r"^\s*Publish\s*$")
    if confirm is None:
        if _wait_drafts(frame, page, 0, timeout_s=5.0):
            return pending
        _back_out(frame, page)
        raise ScheduleWriteError("no Publish confirmation button after Publish drafts")
    confirm.click(timeout=8000)
    if not _wait_drafts(frame, page, 0, timeout_s=30.0):
        raise PublishUnconfirmed("ADP still shows unpublished drafts after Publish", pending)
    return pending
