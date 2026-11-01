"""ADP Team Schedule writes — Issue #337.

Creates shifts as *drafts* (Actions › Create shift / Create open shift › Save as
draft) and publishes a week's drafts (toolbar "Publish drafts (N)"). Drafts stay
invisible to employees until published, which is why the console splits the two
into separate confirmed clicks.

Every call site is an operator click behind ``FEATURES.adpScheduleWrite``. The
only commit buttons pressed are ``Save as draft`` / ``Save draft``, the
publish dialog's ``Publish``, Assign Open Shift's ``Assign`` (Issue #412) and,
for console shift changes (Issue #434), one shift's own wizard ``Publish``, its
details pane ``Edit`` › ``Publish`` (else ``Save``) and per-shift ``Delete`` ›
confirm. Never "Delete all schedules"; manual changes never press "Publish drafts".
``dry_run=True`` walks each wizard to its final step and backs out without
saving (selector check against live ADP).

Wizard shape (read-only spikes 2026-09-27, week of Sep 28 / Oct 5):
  Create shift       step 1: date picker + "Unscheduled employees" radio list
                     (``input[name=employeeGroup]``, ``aria-labelledby`` →
                     ``[data-e2e=pwc-person-name]``) › Next
                     step 2: Start / End time (``hh:mm a.m.`` masked), note,
                     Visible to employee › Save as draft | Publish
  Create open shift  step 1: count, date, Start / End time, expiration › Next
                     step 2: eligibility summary › Previous | Save draft | Publish
                     after Save draft: "An open shift … has been created" pane
                     (Edit | Publish; header Back closes it) — live 2026-10-17
  Assign open shift  Open-shift day pane › shift tile › Shift details (Eligible
                     employees; Edit | Assign) › Assign Open Shift: one
                     ``sdf-checkbox`` per eligible employee › Assign — read-only
                     probe 2026-10-19 (week of Oct 26)
"""

from __future__ import annotations

import datetime as dt
import re
import time
from collections import Counter

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


def parse_clock(text: str) -> int:
    """'1:30 PM' → 810 (inverse of fmt_time)."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*([AP]M)\s*", text or "", re.I)
    if not m:
        raise ValueError(f"unrecognised time: {text!r}")
    h, mm = int(m.group(1)) % 12, int(m.group(2))
    return (h + (12 if m.group(3).upper() == "PM" else 0)) * 60 + mm


def open_shift_counts(cells: list[dict], week_start: dt.date) -> Counter:
    """Open-shift pane reads → Counter of (date_iso, start_min, end_min).

    ``cells`` as returned by runner._scrape_open_shifts: heading 'Open Shifts on
    Saturday, Oct 31', shifts [{'range': '1:30 PM - 8:30 PM'}].
    """
    out: Counter = Counter()
    days = {f"{d:%b} {d.day}": d for d in (week_start + dt.timedelta(i) for i in range(7))}
    for cell in cells:
        m = re.search(r"([A-Z][a-z]{2})\w*\s+(\d{1,2})\s*$", cell.get("heading") or "")
        day = days.get(f"{m.group(1)} {int(m.group(2))}") if m else None
        if day is None:
            raise ScheduleWriteError(f"open-shift pane heading not in week {week_start}: {cell.get('heading')!r}")
        for s in cell.get("shifts") or []:
            start, end = (parse_clock(t) for t in s["range"].split(" - "))
            out[(day.isoformat(), start, end)] += 1
    return out


def read_open_shifts(page, frame, week_start: dt.date) -> Counter:
    """Read-only: the shown week's open shifts as ADP lists them. Raises if unreadable."""
    from skills.adp_run_automation import runner as r

    _back_out(frame, page)
    info = r._scrape_open_shifts(page, frame, week_label=str(week_start))
    if info.get("open_shifts_error"):
        raise ScheduleWriteError(f"could not read open shifts: {info['open_shifts_error']}")
    return open_shift_counts(info.get("open_shift_cells") or [], week_start)


def _mark_shift(frame, page, *, date_iso: str, employee: str, start_min: int, end_min: int,
                state: str = "draft") -> dict:
    """Mark ``employee``'s shift at exactly this time on this day with data-jarvis-del (FIND_SHIFT_JS)."""
    _back_out(frame, page)
    d = dt.date.fromisoformat(date_iso)
    rng = f"{fmt_time(start_min)} - {fmt_time(end_min)}"
    info: dict = {}
    for _ in range(20):
        info = frame.evaluate(FIND_SHIFT_JS, [employee, d.day, rng, state])
        if info.get("found") or not info.get("scroll"):
            break
        page.wait_for_timeout(400)
    return info


def shift_in_grid(frame, page, *, date_iso: str, employee: str, start_min: int, end_min: int,
                  state: str = "any") -> bool:
    """Read-only: whether the grid shows ``employee``'s shift (in ``state``) at exactly this time."""
    info = _mark_shift(frame, page, date_iso=date_iso, employee=employee,
                       start_min=start_min, end_min=end_min, state=state)
    frame.evaluate("() => document.querySelectorAll('[data-jarvis-del]').forEach(e => e.removeAttribute('data-jarvis-del'))")
    return bool(info.get("found"))


def has_draft_shift(frame, page, *, date_iso: str, employee: str, start_min: int, end_min: int) -> bool:
    """Read-only: whether the grid shows ``employee``'s draft at exactly this time on this day."""
    return shift_in_grid(frame, page, date_iso=date_iso, employee=employee,
                         start_min=start_min, end_min=end_min, state="draft")


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


def _shown_week(frame) -> dt.date | None:
    """The week the grid says it is showing, or None when the label can't be read."""
    try:
        text = frame.get_by_text(re.compile(r"Week of")).first.inner_text(timeout=3000)
        return week_label_start(" ".join(text.split()))
    except Exception:
        return None


def _drafts_on_fresh_load(page, week_start: dt.date) -> int | None:
    """Publish drafts (N) after a full reload. The open grid keeps the pre-publish count.

    2026-10-19: Publish of one recreated open shift left the toolbar at 1 for the whole
    30s wait, then a fresh Team Schedule load showed 0 drafts and the shift live.
    """
    from skills.adp_run_automation import runner as r

    try:
        page.reload(wait_until="domcontentloaded")
        frame = r._open_team_schedule(page)
        page.wait_for_timeout(2500)
        goto_week(frame, page, week_start)
        page.wait_for_timeout(2000)
        return drafts_pending(frame)
    except Exception as exc:  # noqa: BLE001
        print(f"[schedule_write] fresh load unreadable: {type(exc).__name__}: {exc}"[:300])
        return None


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


OPEN_SHIFT_CREATED_RE = re.compile(r"open shift with \d+ shifts? has been created and added to the schedule as a draft", re.I)


def _pane_text(frame) -> str:
    return frame.evaluate(PANE_TEXT_JS) or ""


def _wait_for(page, check, timeout_s: float, step_ms: int = 250) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if check():
            return True
        if time.monotonic() > deadline:
            return False
        page.wait_for_timeout(step_ms)


def _press_publish_confirmation(frame, page, timeout_s: float = 5.0) -> str | None:
    """After a Publish click: press the "Publish to employees" confirmation's own Publish.

    2026-11-01: an open shift's Publish opens this inside the pane; closing it (as
    _back_out does) cancelled both live open-adds. Returns the pane text from just
    before the press (None when ADP asked nothing); raises when its buttons are unknown.
    """
    if not _wait_for(page, lambda: bool(PUBLISH_CONFIRM_RE.search(_pane_text(frame))), timeout_s, 500):
        return None
    buttons = frame.evaluate(MARK_CONFIRM_JS, [PUBLISH_CONFIRM_RE.pattern, PUBLISH_YES_RE.pattern])
    print(f"[schedule_write] publish confirm buttons {buttons}")
    marked = frame.locator("[data-jarvis-confirm]")
    if marked.count() != 1:
        raise ScheduleWriteError(f"publish confirmation buttons not recognised: {buttons}")
    shown = _pane_text(frame)
    marked.first.click(timeout=5000)
    return shown


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
    """Step the grid back or forward until it shows ``week_start``."""
    from skills.adp_run_automation import runner as r

    for _ in range(10):
        label = frame.get_by_text(re.compile(r"Week of")).first
        before = label.inner_text(timeout=5000).strip()
        shown = week_label_start(before)
        if shown == week_start:
            return
        if shown < week_start:
            r._goto_next_week(page, frame)
            continue
        # A coordinate click on the ‹ chevron does nothing; the DOM click does.
        frame.locator('[aria-label="Select previous week"]').first.evaluate("el => el.click()", timeout=5000)
        now = r._wait_week_label_change(page, label, before, 8.0)
        if now is None:
            raise ScheduleWriteError(f"previous-week click did not leave {before!r}")
        print(f"[adp_schedule] step=previous-week {before!r} -> {now!r}")
        page.wait_for_timeout(1500)
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
    before: int | None = None, dry_run: bool = False, publish: bool = False,
) -> str:
    """Create one assigned draft shift. Returns the ADP display name used.

    ``before`` is the draft count the caller expects ADP to show now; a lagging
    toolbar count must not let the previous save "confirm" this one.
    ``publish=True`` presses step 2's Publish (that one shift, never the week's
    drafts) and is confirmed when the grid shows the shift live.
    """
    commit = r"^\s*Publish\s*$" if publish else r"^\s*Save as draft\s*$"
    if before is None and not publish:
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
            if _visible_button(frame, commit) is None:
                raise ScheduleWriteError(f"{'Publish' if publish else 'Save as draft'} not found on step 2")
            _back_out(frame, page)
            return name
        _click(frame, commit, what="Publish" if publish else "Save as draft")
    except Exception:
        _back_out(frame, page)
        raise
    if publish:
        page.wait_for_timeout(1500)
        _back_out(frame, page)
        if not _wait_for(page, lambda: shift_in_grid(frame, page, date_iso=date_iso, employee=employee,
                                                     start_min=start_min, end_min=end_min,
                                                     state="published"), 15.0, 1000):
            raise UnconfirmedSave(f"ADP does not show {employee}'s {date_iso} shift live — check ADP before re-adding")
        return name
    if not _wait_drafts(frame, page, before + 1):
        raise UnconfirmedSave(
            f"ADP did not confirm the draft for {employee} on {date_iso} — check ADP before re-saving"
        )
    return name


def create_open_shift(
    frame, page, *, date_iso: str, start_min: int, end_min: int,
    before: int | None = None, dry_run: bool = False, publish: bool = False,
) -> str | None:
    """Create one unassigned draft open shift (default eligibility). ``before`` as in create_shift.

    ``publish=True`` presses the eligibility step's Publish instead; the caller
    confirms from the week's open-shift counts. Returns the dialog text ADP showed
    after Publish, so an unconfirmed publish says what ADP said.
    """
    if publish:
        _open_action(frame, page, "Create open shift")
        try:
            date_box = frame.locator("sdf-focus-pane sdf-date-picker input").first
            date_box.fill(fmt_date(date_iso), timeout=5000)
            date_box.press("Tab")
            _fill_times(frame, page, start_min, end_min)
            _click(frame, r"^\s*Next\b", what="Next")
            _wait_for(page, lambda: _visible_button(frame, r"^\s*Publish\s*$") is not None, 8.0)
            if _visible_button(frame, r"^\s*Publish\s*$") is None:
                raise ScheduleWriteError("Publish not found on the eligibility step")
            if dry_run:
                _back_out(frame, page)
                return
            wizard = _pane_text(frame)
            _click(frame, r"^\s*Publish\s*$", what="Publish")
            wizard = _press_publish_confirmation(frame, page) or wizard
        except Exception:
            _back_out(frame, page)
            raise
        # Closing the wizard discards it, so give ADP time to leave the eligibility step.
        _wait_for(page, lambda: _pane_text(frame) != wizard, 15.0, 500)
        shown = _pane_text(frame)
        if shown == wizard:
            step = shown[shown.lower().rfind("eligibility"):] if "eligibility" in shown.lower() else shown
            shown = f"still on the wizard: {step[:160]} | buttons: {', '.join(frame.evaluate(PANE_BUTTONS_JS))}"
        print(f"[schedule_write] open shift {date_iso} after Publish: {shown[:500]!r}")
        _back_out(frame, page)
        return shown
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
    # ADP swaps the wizard for a "has been created … as a draft shift" pane that
    # hides the toolbar count and carries its own Publish button; Back closes it.
    created = _wait_for(page, lambda: bool(OPEN_SHIFT_CREATED_RE.search(_pane_text(frame))), 15.0)
    _back_out(frame, page)
    if _wait_drafts(frame, page, before + 1):
        return
    if created:
        print(f"[schedule_write] open shift {date_iso} confirmed by ADP's created pane; draft count lagging")
        return
    raise UnconfirmedSave(
        f"ADP did not confirm the open-shift draft on {date_iso} — check ADP before re-saving"
    )


# Marks the one shift of ``name`` on ``day`` (day-of-month) spanning ``rng``
# ("2:00 PM - 8:30 PM") with data-jarvis-del. ``state``: 'draft' (default) |
# 'published' | 'any' — drafts carry a "Draft" tag. Day columns are matched to cells by x.
FIND_SHIFT_JS = r"""
([name, day, rng, state]) => {
  state = state || 'draft';
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
  // Rows only render days that have something, so the nearest cell can be a neighbour's.
  if (!cell || bd > hb.width / 2) return { found: false, why: 'no day cell' };
  const shifts = [...cell.querySelectorAll('schedule-calendar-shifts')]
    .filter(s => norm(s).includes(rng))
    .filter(s => state === 'any' || (state === 'draft') === /draft/i.test(norm(s)));
  if (shifts.length !== 1) return { found: false, why: 'matching ' + state + ' shifts ' + shifts.length, cell: norm(cell) };
  (shifts[0].querySelector('schedule-shift-range') || shifts[0]).setAttribute('data-jarvis-del', '1');
  return { found: true, cell: norm(cell) };
}
"""

PANE_TEXT_JS = r"""
() => [...document.querySelectorAll('sdf-focus-pane')]
  .filter(p => { const b = p.getBoundingClientRect(); return b.width > 0 && b.height > 0; })
  .map(p => (p.innerText || '').replace(/\s+/g, ' ').trim()).join(' | ')
"""

# Visible buttons of the open pane, "[off]" when disabled: why a click did nothing.
PANE_BUTTONS_JS = r"""
() => [...document.querySelectorAll('sdf-focus-pane')]
  .filter(p => { const b = p.getBoundingClientRect(); return b.width > 0 && b.height > 0; })
  .flatMap(p => [...p.querySelectorAll('sdf-button, button')])
  .filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
  .map(b => (b.innerText || '').replace(/\s+/g, ' ').trim()
    + (b.disabled || b.hasAttribute('disabled') || b.getAttribute('aria-disabled') === 'true' ? ' [off]' : ''))
  .filter(t => t)
"""

PUBLISH_CONFIRM_RE = re.compile(r"Publish to employees", re.I)
PUBLISH_YES_RE = re.compile(r"^(Publish|Yes\b.*|Confirm|OK|Continue)$", re.I)

# The innermost visible element holding the confirmation text and buttons (not a
# nested focus pane, unlike delete's). Marks its one button matching yesRe with
# data-jarvis-confirm; returns every button label for the breadcrumb.
MARK_CONFIRM_JS = r"""
([textRe, yesRe]) => {
  const text = new RegExp(textRe, 'i'), yes = new RegExp(yesRe, 'i');
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const norm = e => (e.innerText || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  document.querySelectorAll('[data-jarvis-confirm]').forEach(e => e.removeAttribute('data-jarvis-confirm'));
  const btnsOf = box => [...box.querySelectorAll('sdf-button, button')]
    .filter(b => vis(b) && !(b.tagName === 'BUTTON' && b.parentElement && b.parentElement.closest('sdf-button')));
  const boxes = [...document.querySelectorAll('sdf-focus-pane *')]
    .filter(e => vis(e) && text.test(e.innerText || '') && btnsOf(e).length);
  const inner = boxes.filter(b => !boxes.some(o => o !== b && b.contains(o)));
  if (inner.length !== 1) return ['dialogs: ' + inner.length];
  const btns = btnsOf(inner[0]);
  const hits = btns.filter(b => yes.test(norm(b)));
  if (hits.length === 1) hits[0].setAttribute('data-jarvis-confirm', '1');
  return btns.map(norm);
}
"""

DELETE_CONFIRM_RE = re.compile(r"Are you sure you want to delete this (open )?shift", re.I)
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
    _open_assigned_shift(frame, page, date_iso=date_iso, employee=employee,
                         start_min=start_min, end_min=end_min, state="draft")
    if not _press_delete(frame, page, dry_run=dry_run):
        if not _wait_drafts(frame, page, before, timeout_s=8.0):
            raise UnconfirmedSave(f"dry-run delete changed the draft count (expected {before})")
        return
    if not _wait_drafts(frame, page, before - 1):
        raise UnconfirmedSave(
            f"ADP did not confirm deleting {employee} on {date_iso} {fmt_time(start_min)} - {fmt_time(end_min)}"
        )


def _open_assigned_shift(frame, page, *, date_iso: str, employee: str, start_min: int, end_min: int,
                         state: str) -> None:
    """From the grid (on the right week), open the Shift details pane of one assigned shift."""
    d = dt.date.fromisoformat(date_iso)
    rng = f"{fmt_time(start_min)} - {fmt_time(end_min)}"
    info = _mark_shift(frame, page, date_iso=date_iso, employee=employee,
                       start_min=start_min, end_min=end_min, state=state)
    if not info.get("found"):
        raise ScheduleWriteError(f"{state} shift {employee} {date_iso} {rng} not found in grid: {info.get('why')}")
    frame.locator("[data-jarvis-del='1']").first.click(timeout=8000)
    if not _wait_for(page, lambda: _pane_open(frame), 8.0):
        raise ScheduleWriteError("shift details pane did not open")
    page.wait_for_timeout(500)
    pane = frame.evaluate(PANE_TEXT_JS)
    expect = (f"{d:%b} {d.day}, {d.year}", rng) + (("Draft",) if state == "draft" else ())
    if not all(s in pane for s in expect) or employee.split(",")[0] not in pane:
        _back_out(frame, page)
        raise ScheduleWriteError(f"details pane is not {employee} {date_iso} {rng}: {pane[:200]!r}")


def _press_delete(frame, page, *, dry_run: bool) -> bool:
    """In an open Shift details pane: per-shift Delete, then the confirmation's Yes.

    Returns True when Yes was clicked; a dry run cancels at the confirmation and
    returns False. Leaves the grid with every pane closed.
    """
    dels = frame.locator("sdf-focus-pane sdf-button[aria-label=' Delete ']")
    if dels.count() != 1:
        alt = frame.locator("sdf-focus-pane").get_by_role("button", name=re.compile(r"^\s*Delete\s*$", re.I))
        visible = [alt.nth(i) for i in range(alt.count()) if alt.nth(i).is_visible()]
        if len(visible) != 1:
            seen = f"buttons: {', '.join(frame.evaluate(PANE_BUTTONS_JS))}; pane: {_pane_text(frame)[-150:]}"
            _back_out(frame, page)
            raise ScheduleWriteError(
                f"expected one per-shift Delete in the pane, found {dels.count()} / {len(visible)} ({seen})"
            )
        visible[0].click(timeout=5000)
    else:
        dels.first.click(timeout=5000)
    # The confirmation is a nested focus pane; its buttons live in shadow DOM,
    # which role locators pierce. Scope to the innermost pane so nothing in the
    # details pane (notably "Delete all schedules") can be matched.
    confirm_pane = frame.locator("sdf-focus-pane").filter(has_text=DELETE_CONFIRM_RE).last
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
        return False
    buttons.nth(yes[0]).click(timeout=5000)
    page.wait_for_timeout(1000)
    _back_out(frame, page)
    return True


EDIT_COMMIT_RE = r"^\s*(Publish|Save|Save changes|Update)\s*$"


def _edit_times(frame, page, *, start_min: int, end_min: int, dry_run: bool) -> bool:
    """In an open Shift details pane: Edit › new Start / End › Publish (else Save).

    Returns True when the commit button was clicked; a dry run backs out at it.
    """
    _wait_for(page, lambda: _visible_button(frame, r"^\s*Edit\s*$") is not None, 8.0)
    _click(frame, r"^\s*Edit\s*$", what="Edit")
    try:
        frame.locator(f"sdf-focus-pane {TIME_INPUT}").first.wait_for(state="visible", timeout=8000)
        _fill_times(frame, page, start_min, end_min)
        _wait_for(page, lambda: _visible_button(frame, EDIT_COMMIT_RE) is not None, 8.0)
        # An open shift's Edit reopens the create wizard: times, then Next to the eligibility step.
        if _visible_button(frame, EDIT_COMMIT_RE) is None and _visible_button(frame, r"^\s*Next\b") is not None:
            _click(frame, r"^\s*Next\b", what="Next")
            _wait_for(page, lambda: _visible_button(frame, EDIT_COMMIT_RE) is not None, 8.0)
        commit = _visible_button(frame, r"^\s*Publish\s*$") or _visible_button(frame, EDIT_COMMIT_RE)
        if commit is None:
            raise ScheduleWriteError(
                f"no Publish / Save button after editing the times (buttons: {', '.join(frame.evaluate(PANE_BUTTONS_JS))};"
                f" pane: {_pane_text(frame)[-150:]})"
            )
        if dry_run:
            _back_out(frame, page)
            return False
        commit.click(timeout=8000)
        confirmed = _press_publish_confirmation(frame, page)
    except Exception:
        _back_out(frame, page)
        raise
    if confirmed:
        _wait_for(page, lambda: _pane_text(frame) != confirmed, 15.0, 500)
    page.wait_for_timeout(1500)
    _back_out(frame, page)
    return True


def delete_any_shift(
    frame, page, *, date_iso: str, employee: str, start_min: int, end_min: int, dry_run: bool = False,
) -> None:
    """Delete one assigned shift, draft or published. Confirmed when the grid no longer shows it."""
    _open_assigned_shift(frame, page, date_iso=date_iso, employee=employee,
                         start_min=start_min, end_min=end_min, state="any")
    if not _press_delete(frame, page, dry_run=dry_run):
        return
    gone = _wait_for(page, lambda: not shift_in_grid(frame, page, date_iso=date_iso, employee=employee,
                                                     start_min=start_min, end_min=end_min), 15.0, 1000)
    if not gone:
        raise UnconfirmedSave(f"ADP still shows {employee}'s {date_iso} shift after Delete — check ADP")


def edit_shift(
    frame, page, *, date_iso: str, employee: str, start_min: int, end_min: int,
    to_start_min: int, to_end_min: int, dry_run: bool = False,
) -> None:
    """Change one assigned shift's times and publish it. Confirmed when the grid shows the new times live."""
    _open_assigned_shift(frame, page, date_iso=date_iso, employee=employee,
                         start_min=start_min, end_min=end_min, state="any")
    if not _edit_times(frame, page, start_min=to_start_min, end_min=to_end_min, dry_run=dry_run):
        return
    moved = _wait_for(page, lambda: shift_in_grid(frame, page, date_iso=date_iso, employee=employee,
                                                  start_min=to_start_min, end_min=to_end_min,
                                                  state="published"), 15.0, 1000)
    if not moved:
        raise UnconfirmedSave(f"ADP does not show {employee}'s {date_iso} shift at the new times — check ADP")


def edit_open_shift(
    frame, page, *, date_iso: str, start_min: int, end_min: int,
    to_start_min: int, to_end_min: int, dry_run: bool = False,
) -> None:
    """Change one open shift's times. The caller confirms from the week's open-shift counts."""
    _open_shift_details(frame, page, date_iso=date_iso, start_min=start_min, end_min=end_min)
    _edit_times(frame, page, start_min=to_start_min, end_min=to_end_min, dry_run=dry_run)


def delete_open_shift(frame, page, *, date_iso: str, start_min: int, end_min: int, dry_run: bool = False) -> None:
    """Delete one open shift. The caller confirms from the week's open-shift counts."""
    _open_shift_details(frame, page, date_iso=date_iso, start_min=start_min, end_min=end_min)
    _wait_for(page, lambda: _visible_button(frame, r"^\s*(Edit|Assign)\s*$") is not None, 8.0)
    _press_delete(frame, page, dry_run=dry_run)


# The Assign Open Shift pane is nested in the Shift details pane, so the last visible
# match is the innermost. Marks it, lists its checkbox names, and marks box ``pick``.
ASSIGN_PANE_JS = r"""
(pick) => {
  const vis = e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0; };
  document.querySelectorAll('[data-jarvis-assign]').forEach(e => e.removeAttribute('data-jarvis-assign'));
  const panes = [...document.querySelectorAll('sdf-focus-pane')]
    .filter(p => vis(p) && /Assign Open Shift/i.test(p.innerText || ''));
  const pane = panes[panes.length - 1];
  if (!pane) return { pane: false, names: [] };
  pane.setAttribute('data-jarvis-assign', 'pane');
  const deep = (root, out = []) => {
    root.querySelectorAll('*').forEach(e => {
      if (e.tagName === 'SDF-CHECKBOX') out.push(e);
      if (e.shadowRoot) deep(e.shadowRoot, out);
    });
    return out;
  };
  const boxes = [...new Set(deep(pane))].filter(vis);
  const labelOf = b => {
    const ref = b.getAttribute('aria-labelledby');
    const root = b.getRootNode();
    const host = ref && ((root.getElementById && root.getElementById(ref)) || document.getElementById(ref));
    return (host && host.textContent) || b.getAttribute('label') || b.getAttribute('aria-label')
      || b.innerText || (b.parentElement && b.parentElement.innerText) || '';
  };
  const names = boxes.map(b => labelOf(b).replace(/\s+/g, ' ').trim());
  if (pick != null && boxes[pick]) boxes[pick].setAttribute('data-jarvis-assign', 'box');
  const box = pick != null ? boxes[pick] : null;
  const checked = !!box && (box.checked === true || box.getAttribute('aria-checked') === 'true'
    || box.hasAttribute('checked'));
  return { pane: true, names, checked };
}
"""


def _open_shift_details(frame, page, *, date_iso: str, start_min: int, end_min: int) -> None:
    """From the grid (on the right week), open the Shift details pane of one open shift."""
    from skills.adp_run_automation import schedule_backend as sb

    _back_out(frame, page)
    d = dt.date.fromisoformat(date_iso)
    week = d - dt.timedelta(days=d.weekday())
    cells: list[dict] = []
    for _ in range(30):
        cells = frame.evaluate(sb.OPEN_SHIFT_CELLS_JS).get("cells") or []
        if cells:
            break
        page.wait_for_timeout(500)
    cell = next((c for c in cells if c.get("header_index") is not None
                 and week + dt.timedelta(days=int(c["header_index"])) == d), None)
    if cell is None:
        raise ScheduleWriteError(f"ADP lists no open shifts on {date_iso}")
    frame.locator(".open-shift-count").nth(int(cell["index"])).click(timeout=8000)
    want = f"{d:%b} {d.day}"
    if not _wait_for(page, lambda: (frame.evaluate(sb.OPEN_SHIFT_PANE_JS).get("heading") or "").endswith(want), 10.0):
        _back_out(frame, page)
        raise ScheduleWriteError(f"open-shift pane for {date_iso} did not open")
    rng = f"{fmt_time(start_min)} - {fmt_time(end_min)}"
    tile = frame.locator("sdf-focus-pane").get_by_role("button", name=f"Select open shift {rng}", exact=True)
    if not tile.count():
        _back_out(frame, page)
        raise ScheduleWriteError(f"ADP lists no {rng} open shift on {date_iso}")
    tile.first.click(timeout=8000)
    expect = (f"{d:%b} {d.day}, {d.year}", rng, "Shift details")
    if not _wait_for(page, lambda: all(s in _pane_text(frame) for s in expect), 10.0):
        _back_out(frame, page)
        raise ScheduleWriteError(f"shift details for {date_iso} {rng} did not open")


def assign_open_shift(
    frame, page, *, date_iso: str, employee: str, start_min: int, end_min: int, dry_run: bool = False,
) -> str:
    """Assign one existing open shift to ``employee`` (Shift details › Assign › tick › Assign).

    ADP offers only the people it lists as eligible for that shift; anyone else
    raises with that list. Returns the ADP display name. The caller confirms by
    re-reading the week's open shifts — this only reports what was clicked.
    """
    _open_shift_details(frame, page, date_iso=date_iso, start_min=start_min, end_min=end_min)
    try:
        # The pane's text paints before its Edit | Assign buttons.
        _wait_for(page, lambda: _visible_button(frame, r"^\s*Assign\s*$") is not None, 8.0)
        _click(frame, r"^\s*Assign\s*$", what="Assign")
        if not _wait_for(page, lambda: frame.evaluate(ASSIGN_PANE_JS, None)["pane"], 8.0):
            raise ScheduleWriteError("Assign Open Shift pane did not open")
        _wait_for(page, lambda: bool(frame.evaluate(ASSIGN_PANE_JS, None)["names"]), 8.0)
        names = frame.evaluate(ASSIGN_PANE_JS, None)["names"]
        name = match_employee(names, employee)
        if name is None:
            raise ScheduleWriteError(
                f"{employee} is not eligible for this open shift in ADP (eligible: {', '.join(names) or 'nobody'})"
            )
        frame.evaluate(ASSIGN_PANE_JS, names.index(name))
        frame.locator("[data-jarvis-assign='box']").first.click(timeout=5000)
        if not _wait_for(page, lambda: frame.evaluate(ASSIGN_PANE_JS, names.index(name))["checked"], 5.0):
            raise ScheduleWriteError(f"could not tick {name} in Assign Open Shift")
        buttons = frame.locator("[data-jarvis-assign='pane']").get_by_role("button", name=re.compile(r"^\s*Assign\s*$", re.I))
        visible = [buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible()]
        if len(visible) != 1:
            raise ScheduleWriteError(f"expected one Assign button in Assign Open Shift, found {len(visible)}")
        if not _wait_for(page, visible[0].is_enabled, 5.0):
            raise ScheduleWriteError("Assign stayed disabled after ticking the employee")
        if dry_run:
            _back_out(frame, page)
            return name
        visible[0].click(timeout=8000)
    except Exception:
        _back_out(frame, page)
        raise
    _wait_for(page, lambda: not frame.evaluate(ASSIGN_PANE_JS, None)["pane"], 15.0)
    print(f"[schedule_write] assign clicked {date_iso} {fmt_time(start_min)}-{fmt_time(end_min)} → {name!r}; pane={_pane_text(frame)[:200]!r}")
    _back_out(frame, page)
    return name


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
        week = _shown_week(frame)
        if week is not None and _drafts_on_fresh_load(page, week) == 0:
            print(f"[schedule_write] publish confirmed on a fresh load week_start={week}")
            return pending
        raise PublishUnconfirmed("ADP still shows unpublished drafts after Publish", pending)
    return pending
