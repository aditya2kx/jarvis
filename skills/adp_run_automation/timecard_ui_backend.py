#!/usr/bin/env python3
"""skills/adp_run_automation/timecard_ui_backend - parse the ADP Timecards month view.

Source: ADP RUN > Time > Timecards (iframe ``timePartnerFrame``), one employee
per page. The Timecard XLSX export (shift_backend) drops any entry without an
End Work, so this view is the only place a forgotten clock-out is visible
(Issue #356). Each day cell lists its scheduled ranges and every time entry,
with an open entry rendered as ``09:00 AM - Blank``.

The input is Playwright's ``locator.ariaSnapshot()`` of the iframe body, not
HTML: it is far more stable than ADP's generated class names and carries the
same text a screen reader gets. Pure module — no network, no IO.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass

from skills.adp_run_automation.employee_aliases import derive_canonical
from skills.bhaga_labor.punch_gaps import (
    TimecardEntry,
    classify_day,
    parse_ampm,
    parse_range,
    to_minutes,
)

TIMECARDS_ANCHOR_ID = "TEMPUS_VIEW_TIME_CARDS"
TIMECARDS_FRAME_NAME = "timePartnerFrame"
DOWNLOADS_GLOB = "TimecardsUI-*.json"

_CELL = re.compile(r'^(\s*)- cell "(.*)":?\s*$')
_CELL_DATE = re.compile(
    r"^(?:Today,\s*)?[A-Za-z]+,\s*([A-Za-z]{3})[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)\b"
)
_EMPLOYEE = re.compile(r'^- button "([^"]+, [^"]+)"\s*$', re.MULTILINE)
_PAY_PERIOD = re.compile(
    r'- button "Pay Period [^"]*?(\d{2}/\d{2}/\d{4}) - (\d{2}/\d{2}/\d{4})"'
)
_TOTAL = re.compile(r"TOTAL TIME (\d+):(\d{2}) Hours")
_SCHEDULE_TEXT = re.compile(r"^\s*- text: Has (?:a schedule|\d+ schedules)\.(.*)$")
_RANGE = re.compile(r"\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M", re.IGNORECASE)
_LISTITEM = re.compile(r"^\s*- listitem:\s*(.+?)\s*$")
_TIME = r"(\d{1,2}:\d{2}\s*[AP]M|Blank)"
# ADP appends the pay code once one applies, e.g. "… Weekly Overtime (0:26 HRS)".
_ENTRY = re.compile(rf"^{_TIME}\s*-\s*{_TIME}(?:\s+[A-Za-z][^\n]*)?$", re.IGNORECASE)
_MENUITEM = re.compile(r'^\s*- menuitem "([^"]+)"', re.MULTILINE)
_ERROR_STATUS = 'status "There are errors for this day"'

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1
)}


@dataclass(frozen=True)
class DayCell:
    date: datetime.date
    entries: tuple[TimecardEntry, ...]
    scheduled_ranges: tuple[tuple[str, str], ...]
    total_hours: float
    adp_error_flag: bool


@dataclass(frozen=True)
class EmployeeMonth:
    employee_name: str  # ADP display form, "Last, First M"
    pay_period_start: datetime.date
    pay_period_end: datetime.date
    days: tuple[DayCell, ...]


def _mdy(text: str) -> datetime.date:
    return datetime.datetime.strptime(text, "%m/%d/%Y").date()


def _resolve_date(mon: str, day: int, start: datetime.date, end: datetime.date) -> datetime.date:
    """Cells carry no year; pick the one that lands inside the pay period
    (a Dec 28 – Jan 10 period spans two)."""
    month = _MONTHS[mon[:3].title()]
    for year in (start.year, end.year):
        d = datetime.date(year, month, day)
        if start <= d <= end:
            return d
    raise ValueError(f"cell date {mon} {day} outside pay period {start}..{end}")


def _parse_entry(text: str) -> TimecardEntry | None:
    m = _ENTRY.match(text)
    if not m:
        return None
    ins, outs = (None if t.lower() == "blank" else parse_ampm(t) for t in m.groups())
    hours = 0.0
    if ins and outs and to_minutes(outs) > to_minutes(ins):
        hours = round((to_minutes(outs) - to_minutes(ins)) / 60.0, 2)
    return TimecardEntry(in_time=ins, out_time=outs, hours=hours)


def _parse_cell(label: str, body: list[str], start: datetime.date, end: datetime.date) -> DayCell:
    m = _CELL_DATE.match(label)
    if not m:
        raise ValueError(f"unrecognised timecard cell label: {label[:60]!r}")
    date = _resolve_date(m.group(1), int(m.group(2)), start, end)
    total = _TOTAL.search(label)
    ranges: list[tuple[str, str]] = []
    entries: list[TimecardEntry] = []
    for line in body:
        sched = _SCHEDULE_TEXT.match(line)
        if sched:
            ranges.extend(r for r in map(parse_range, _RANGE.findall(sched.group(1))) if r)
            continue
        item = _LISTITEM.match(line)
        if item:
            entry = _parse_entry(item.group(1))
            if entry is None:
                raise ValueError(f"unrecognised time entry on {date}: {item.group(1)!r}")
            entries.append(entry)
    return DayCell(
        date=date,
        entries=tuple(entries),
        scheduled_ranges=tuple(ranges),
        total_hours=round(int(total.group(1)) + int(total.group(2)) / 60.0, 2) if total else 0.0,
        adp_error_flag=any(_ERROR_STATUS in line for line in body),
    )


def parse_month_view(aria_snapshot: str) -> EmployeeMonth:
    """Parse one employee's pay-period month view into per-day cells."""
    emp = _EMPLOYEE.search(aria_snapshot)
    period = _PAY_PERIOD.search(aria_snapshot)
    if not emp or not period:
        raise ValueError("timecard snapshot missing employee or pay-period header")
    start, end = _mdy(period.group(1)), _mdy(period.group(2))

    days: list[DayCell] = []
    lines = aria_snapshot.splitlines()
    i = 0
    while i < len(lines):
        cell = _CELL.match(lines[i])
        if not cell:
            i += 1
            continue
        indent = len(cell.group(1))
        j = i + 1
        while j < len(lines) and len(lines[j]) - len(lines[j].lstrip()) > indent:
            j += 1
        days.append(_parse_cell(cell.group(2), lines[i + 1:j], start, end))
        i = j
    return EmployeeMonth(
        employee_name=emp.group(1),
        pay_period_start=start,
        pay_period_end=end,
        days=tuple(days),
    )


def parse_employee_menu(aria_snapshot: str) -> list[str]:
    """Names in the expanded employee picker (``- menuitem "Last, First"``)."""
    return _MENUITEM.findall(aria_snapshot)


def _employee_id(raw: str, aliases: dict) -> str:
    return aliases.get(raw) or aliases.get(raw.replace(",", "")) or derive_canonical(raw)


def _ranges_json(ranges) -> str:
    return json.dumps([list(r) for r in ranges])


def build_gap_records(
    employees: list[dict],
    *,
    store: str,
    aliases: dict,
    shop_close: str,
    now_ct: datetime.datetime,
) -> tuple[list[dict], list[datetime.date]]:
    """(gap rows for ``adp_timecard_gaps``, pay_period_starts seen).

    One row per employee-day ``classify_day`` flags. Raises on any snapshot it
    cannot parse — a partial set must not be loaded as if it were complete.
    """
    rows: list[dict] = []
    periods: set[datetime.date] = set()
    for emp in employees:
        month = parse_month_view(emp["aria"])
        periods.add(month.pay_period_start)
        emp_id = _employee_id(month.employee_name, aliases)
        for day in month.days:
            gap = classify_day(
                date=day.date,
                entries=list(day.entries),
                scheduled_ranges=list(day.scheduled_ranges),
                shop_close=shop_close,
                now_ct=now_ct,
            )
            if gap is None:
                continue
            rows.append({
                "store": store,
                "date": day.date.isoformat(),
                "employee_id": emp_id,
                "raw_employee_name": month.employee_name,
                "pay_period_start": month.pay_period_start.isoformat(),
                "kind": gap.kind,
                "entries_json": json.dumps([
                    {"in": e.in_time, "out": e.out_time, "hours": e.hours}
                    for e in day.entries
                ]),
                "scheduled_ranges_json": _ranges_json(day.scheduled_ranges),
                "open_entry_index": gap.open_entry_index,
                "suggested_in": gap.suggested_in,
                "suggested_out": gap.suggested_out,
                "suggested_hours": gap.suggested_hours,
                "rule": gap.rule,
                "adp_error_flag": day.adp_error_flag,
            })
    return rows, sorted(periods)
