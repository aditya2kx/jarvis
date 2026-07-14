#!/usr/bin/env python3
"""skills/adp_run_automation/schedule_backend — ADP RUN Team Schedule extractor.

Source: ADP RUN home page → "Team Schedule" quick-action (`<a id=
"TEMPUS_WEEKLY_SCHEDULE">`) → the "Manage Schedules" weekly grid. Unlike the
Timecard report (which exports a clean .xlsx), the schedule has NO structured
export — "Actions → Print schedule" only routes the on-screen grid through the
browser's native print preview. So we scrape the grid DOM directly.

The grid renders inside `iframe[name="timePartnerFrame"]`. It exposes the
per-day SCHEDULED totals we want as light-DOM custom elements
``<team-schedule-total>`` whose innerText is ``"<N> Employees\\n<HH:MM> Hrs"``.
For a given week there are 1 + 7 of them at the bottom of the grid:

    index 0      → grand total for the week     ("13 Employees\\n291:30 Hrs")
    index 1..7   → Mon..Sun day totals          ("7 Employees\\n46:45 Hrs", ...)

(Per-employee weekly totals are ALSO ``<team-schedule-total>`` but read just
``"<HH:MM> Hrs"`` with no "Employees" — we filter those out by requiring the
"Employees" token.)

The week selector label ("Week of Jun 8, 2026 - Jun 14, 2026") and the ‹ ›
chevrons live in **Shadow DOM**, so a raw ``querySelectorAll``/``innerText``
sweep misses them; Playwright text/role locators DO pierce open shadow roots
(that's how the runner navigates weeks).

This module is the PURE, unit-testable half (mirrors shift_backend.py):
    * ``SCHEDULE_EXTRACT_JS``   — the JS the runner evaluates in the grid frame
      to pull one week's raw payload. Kept here so the codified selector logic
      travels with the parser and is documented in one place.
    * ``parse_hhmm_hours``      — "46:45" → 46.75 decimal hours.
    * ``parse_week_start``      — "Week of Jun 8, 2026 - ..." → date(2026, 6, 8).
    * ``parse_total_cell``      — "7 Employees\\n46:45 Hrs" → (7, 46.75).
    * ``build_schedule_records``— list of per-week raw payloads → one record
      per (date): {date, scheduled_hours, employee_count, week_start}.
    * ``daily_schedule``        — public entry: read the newest Schedule-*.json
      the runner wrote to extracted/downloads/ and return records in a window.

Calibration (2026-06-10, Palmetto Superfoods): this week (Jun 8-14) totalled
291:30 Hrs across 13 employees; next week (Jun 15-21) 286:00 — confirming both
the current and next week are planned, which is exactly the forward horizon we
diff against goal hours.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import sys
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import project_dir

_PROJECT = pathlib.Path(project_dir())
DOWNLOADS_DIR = _PROJECT / "extracted" / "downloads"

# Number of weeks to scrape forward (current + next is what ADP keeps planned).
DEFAULT_WEEKS = 2

# JS evaluated inside iframe[name="timePartnerFrame"] to pull ONE week's totals.
# Returns {grand: "<txt>", days: ["<txt>", ...]} where each <txt> is the raw
# innerText of a footer <team-schedule-total> ("N Employees\n HH:MM Hrs").
# Light-DOM only (these custom elements are not inside shadow roots).
SCHEDULE_EXTRACT_JS = r"""
() => {
  const norm = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const totals = [...document.querySelectorAll('team-schedule-total')]
    .map(norm)
    .filter(t => /Employees/i.test(t));   // drop per-employee weekly totals
  // totals[0] is the week grand total; totals[1..7] are Mon..Sun day totals.
  return { grand: totals[0] || null, days: totals.slice(1, 8) };
}
"""

# Per-employee day cells. Empty days often omit <team-schedule-calendar-day>,
# so we align each cell to weekday headers by bounding-box X (not ordinal index).
# See docs/operator-console/adp-forward-labor-spike.md.
SCHEDULE_EMPLOYEE_EXTRACT_JS = r"""
() => {
  const norm = e => (el => (el.innerText || '').replace(/\s+/g, ' ').trim())(e);
  const headers = [...document.querySelectorAll('.day-cell.column-header')]
    .map((el) => {
      const r = el.getBoundingClientRect();
      return { text: norm(el), x: r.x + r.width / 2 };
    })
    .filter(h => h.text && !/Last Name/i.test(h.text))
    .map((h, i) => ({ ...h, i }));  // Mon=0 .. Sun=6

  // Prefer walking .worker-name nodes — CDK row wrappers are sparse
  // (often only Open Shifts + one aggregated row), but each employee has
  // a .worker-name and a nearby tree of team-schedule-calendar-day cells.
  const employees = [];
  for (const nameEl of document.querySelectorAll('.worker-name')) {
    const name = norm(nameEl);
    if (!name || /Open Shifts/i.test(name)) continue;
    // Climb until we find a subtree with day cells (employee row container).
    let row = nameEl.parentElement;
    for (let i = 0; i < 8 && row; i++) {
      if (row.querySelectorAll('team-schedule-calendar-day').length > 0) break;
      row = row.parentElement;
    }
    if (!row) continue;
    const weekTotalEl = row.querySelector('team-schedule-total');
    const week_total_text = weekTotalEl ? norm(weekTotalEl) : null;
    const days = [];
    for (const cell of row.querySelectorAll('team-schedule-calendar-day')) {
      const r = cell.getBoundingClientRect();
      const cx = r.x + r.width / 2;
      let best = null, bestDist = 1e9;
      for (const h of headers) {
        const d = Math.abs(h.x - cx);
        if (d < bestDist) { bestDist = d; best = h; }
      }
      const ranges = [...cell.querySelectorAll('schedule-shift-range')]
        .map(norm).filter(Boolean);
      days.push({
        header_index: best ? best.i : null,
        header_text: best ? best.text : null,
        ranges,
        cell_text: norm(cell).slice(0, 120),
      });
    }
    employees.push({ name, week_total_text, days });
  }
  return { headers: headers.map(h => h.text), employees };
}
"""

# Selector constants the runner uses to navigate (documented here so the flow
# is codified alongside the parser).
TEAM_SCHEDULE_ANCHOR_ID = "TEMPUS_WEEKLY_SCHEDULE"  # home-page quick-action <a>
SCHEDULE_GRID_FRAME_NAME = "timePartnerFrame"        # iframe holding the grid
WEEK_LABEL_TEXT = "Week of"                          # Playwright get_by_text anchor


# ── Field parsing helpers ─────────────────────────────────────────

_HHMM_PATTERN = re.compile(r"(\d+):(\d{2})")
_EMP_PATTERN = re.compile(r"(\d+)\s+Employees", re.IGNORECASE)
# "Week of Jun 8, 2026 - Jun 14, 2026" (dash may be hyphen or en/em dash).
_WEEK_START_PATTERN = re.compile(
    r"Week of\s+([A-Za-z]{3,9})\s+(\d{1,2}),\s+(\d{4})", re.IGNORECASE
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


def parse_hhmm_hours(s: Optional[str]) -> float:
    """'46:45' -> 46.75 decimal hours. Empty/None/unparseable -> 0.0.

    ADP renders scheduled hours as HOURS:MINUTES (NOT decimal). 46:45 means
    46 hours 45 minutes = 46.75, not 46.75... well, 45/60 = 0.75 so 46.75 — but
    e.g. 40:15 = 40.25, NOT 40.15. Same trap as the timecard H:MM fields.
    """
    if not s:
        return 0.0
    m = _HHMM_PATTERN.search(str(s))
    if not m:
        return 0.0
    return int(m.group(1)) + int(m.group(2)) / 60.0


def parse_employee_count(s: Optional[str]) -> int:
    """'7 Employees | 46:45 Hrs' -> 7. Missing -> 0."""
    if not s:
        return 0
    m = _EMP_PATTERN.search(str(s))
    return int(m.group(1)) if m else 0


def parse_total_cell(s: Optional[str]) -> tuple[int, float]:
    """'7 Employees\\n46:45 Hrs' -> (7, 46.75)."""
    return parse_employee_count(s), parse_hhmm_hours(s)


def parse_week_start(week_label: Optional[str]) -> Optional[datetime.date]:
    """'Week of Jun 8, 2026 - Jun 14, 2026' -> date(2026, 6, 8).

    Returns None if the label can't be parsed (caller should skip the week
    rather than guess a date).
    """
    if not week_label:
        return None
    m = _WEEK_START_PATTERN.search(str(week_label))
    if not m:
        return None
    mon = _MONTHS.get(m.group(1)[:3].lower())
    if not mon:
        return None
    try:
        return datetime.date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None


# ── Record assembly ───────────────────────────────────────────────


def build_schedule_records(weeks: list[dict]) -> list[dict]:
    """Turn the runner's per-week raw payloads into per-day records.

    Each input week payload (see SCHEDULE_EXTRACT_JS + the runner) looks like:

        {
            "week_label": "Week of Jun 8, 2026 - Jun 14, 2026",
            "days": ["7 Employees\\n46:45 Hrs", ..., "7 Employees\\n46:15 Hrs"],
            # optional, ignored here but written for audit:
            "grand": "13 Employees\\n291:30 Hrs",
        }

    Output: one dict per scheduled day, sorted by date, de-duplicated on date
    (last week wins if two payloads overlap — they shouldn't):

        {
            "date": "YYYY-MM-DD",
            "scheduled_hours": float,    # decimal
            "employee_count": int,
            "week_start": "YYYY-MM-DD",
        }

    Weeks whose label can't be parsed, or that don't expose 7 day cells, are
    skipped (with the bad week left out rather than shifting dates).
    """
    by_date: dict[str, dict] = {}
    for wk in weeks:
        week_start = parse_week_start(wk.get("week_label"))
        days = wk.get("days") or []
        if week_start is None or len(days) < 7:
            continue
        for i in range(7):
            emp, hours = parse_total_cell(days[i])
            d = (week_start + datetime.timedelta(days=i)).isoformat()
            by_date[d] = {
                "date": d,
                "scheduled_hours": round(hours, 2),
                "employee_count": emp,
                "week_start": week_start.isoformat(),
            }
    return [by_date[d] for d in sorted(by_date)]


_SHIFT_RANGE_RE = re.compile(
    r"(\d{1,2}):(\d{2})\s*(AM|PM)\s*-\s*(\d{1,2}):(\d{2})\s*(AM|PM)",
    re.IGNORECASE,
)


def _to_minutes(h: int, m: int, ampm: str) -> int:
    hh = h % 12
    if ampm.upper() == "PM":
        hh += 12
    return hh * 60 + m


def parse_shift_range_hours(s: Optional[str]) -> float:
    """'1:30 PM - 8:30 PM' -> 7.0. Unparseable -> 0.0."""
    if not s:
        return 0.0
    m = _SHIFT_RANGE_RE.search(str(s))
    if not m:
        return 0.0
    start = _to_minutes(int(m.group(1)), int(m.group(2)), m.group(3))
    end = _to_minutes(int(m.group(4)), int(m.group(5)), m.group(6))
    if end < start:
        end += 24 * 60  # overnight
    return round((end - start) / 60.0, 2)


def build_employee_schedule_records(weeks: list[dict]) -> list[dict]:
    """Per-(date, employee) scheduled hours from employee_rows payloads.

    Week payload (from runner + SCHEDULE_EMPLOYEE_EXTRACT_JS)::

        {
          "week_label": "Week of Jul 13, 2026 - Jul 19, 2026",
          "employee_rows": [
            {
              "name": "Garcia, Jacob",
              "week_total_text": "38:45 Hrs",
              "days": [
                {"header_index": 0, "ranges": ["1:30 PM - 8:30 PM"], ...},
                ...
              ],
            },
            ...
          ],
        }

    ``header_index`` is the Mon=0..Sun=6 column from bounding-box alignment.
    Hours = sum of parsed shift ranges for that day (not the week total).
    """
    from skills.adp_run_automation.employee_aliases import derive_canonical

    by_key: dict[tuple[str, str], dict] = {}
    for wk in weeks:
        week_start = parse_week_start(wk.get("week_label"))
        if week_start is None:
            continue
        for emp in wk.get("employee_rows") or []:
            raw_name = (emp.get("name") or "").strip()
            if not raw_name:
                continue
            canonical = derive_canonical(raw_name)
            for day in emp.get("days") or []:
                idx = day.get("header_index")
                if idx is None:
                    continue
                try:
                    idx_i = int(idx)
                except (TypeError, ValueError):
                    continue
                if idx_i < 0 or idx_i > 6:
                    continue
                ranges = day.get("ranges") or []
                hours = round(sum(parse_shift_range_hours(r) for r in ranges), 2)
                if hours <= 0:
                    continue
                d = (week_start + datetime.timedelta(days=idx_i)).isoformat()
                key = (d, canonical)
                prev = by_key.get(key)
                if prev:
                    prev["scheduled_hours"] = round(prev["scheduled_hours"] + hours, 2)
                    prev_ranges = json.loads(prev.get("shift_ranges_json") or "[]")
                    prev_ranges.extend(ranges)
                    prev["shift_ranges_json"] = json.dumps(prev_ranges)
                else:
                    by_key[key] = {
                        "date": d,
                        "employee_id": canonical,
                        "employee_name": canonical,
                        "scheduled_hours": hours,
                        "shift_ranges_json": json.dumps(list(ranges)),
                        "week_start": week_start.isoformat(),
                    }
    return [by_key[k] for k in sorted(by_key)]


# ── Public entry ──────────────────────────────────────────────────


def _newest_schedule_json(downloads_dir: pathlib.Path = DOWNLOADS_DIR) -> Optional[pathlib.Path]:
    files = sorted(downloads_dir.glob("Schedule-*.json"))
    return files[-1] if files else None


def load_schedule_payload(path: pathlib.Path) -> list[dict]:
    """Read a Schedule-*.json the runner wrote and return its `weeks` list."""
    data = json.loads(path.read_text())
    return data.get("weeks", [])


def daily_schedule(
    *,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
    downloads_dir: pathlib.Path = DOWNLOADS_DIR,
) -> list[dict]:
    """Public high-level entry: parse the newest Schedule-*.json into records.

    Optionally filter to [start_date, end_date] (inclusive). Returns the same
    record shape as build_schedule_records.
    """
    path = _newest_schedule_json(downloads_dir)
    if path is None:
        raise FileNotFoundError(
            f"No Schedule-*.json found in {downloads_dir} — run the schedule scrape first "
            f"(skills.adp_run_automation.runner download_schedule / download_adp_bundle)."
        )
    records = build_schedule_records(load_schedule_payload(path))
    if start_date or end_date:
        lo = start_date.isoformat() if start_date else "0000-00-00"
        hi = end_date.isoformat() if end_date else "9999-99-99"
        records = [r for r in records if lo <= r["date"] <= hi]
    return records
