"""Classify ADP timecard days with missing punches and suggest a fair close.

ADP's Timecard Excel export silently omits an entry that has a clock-in and no
clock-out, so a forgotten punch-out looks exactly like a day nobody worked. The
Timecards UI still shows the open entry ("Out Time Blank · Missing Out Punch");
this module turns one employee-day from that UI plus the Team Schedule into a
gap record and a suggestion the operator accepts, edits or rejects (Issue #356).

Pure module — no network, no IO, no clock (``now_ct`` is passed in), same
contract as solo_shift.py. Rules that are easy to get backwards:

  * A suggestion only ever closes an OPEN entry. It never edits a completed
    one, so accepting it can add hours but never remove punched time.
  * A day with no entry at all gets no suggestion. A callout or a departed
    employee still on the schedule looks identical, and paying them would be
    inventing hours.
  * Today's open entry is "in progress", not a gap, until the day is over —
    otherwise every evening someone still on shift shows up as a mistake.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Optional

MISSING_OUT = "missing_out"
MISSING_OUT_AFTER_BREAK = "missing_out_after_break"
IN_PROGRESS = "in_progress"
NO_ENTRY = "no_entry"
MISSING_IN = "missing_in"

RULE_SCHEDULED_END = "close_at_scheduled_end"
RULE_SHOP_CLOSE = "close_at_shop_close"
RULE_SCHEDULED_HOURS = "pay_scheduled_hours"
RULE_SCHEDULED_SHIFT = "scheduled_shift"

_AMPM = re.compile(r"(\d{1,2}):(\d{2})\s*([AP])\.?M\.?", re.IGNORECASE)
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class TimecardEntry:
    in_time: Optional[str]  # "HH:MM" 24h, shop-local
    out_time: Optional[str]
    hours: float = 0.0

    @property
    def is_open(self) -> bool:
        return bool(self.in_time) and not self.out_time


@dataclass(frozen=True)
class PunchGap:
    kind: str
    open_entry_index: Optional[int]
    suggested_out: Optional[str]
    suggested_hours: Optional[float]
    rule: Optional[str]
    suggested_in: Optional[str] = None


def parse_ampm(text: str) -> str:
    """'09:00 AM' / '1:30 PM' → '09:00' / '13:30'. Raises on anything else."""
    m = _AMPM.search(text or "")
    if not m:
        raise ValueError(f"not an AM/PM time: {text!r}")
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if not (1 <= h <= 12 and 0 <= mi <= 59):
        raise ValueError(f"not an AM/PM time: {text!r}")
    h = h % 12 + (12 if ap == "P" else 0)
    return f"{h:02d}:{mi:02d}"


def parse_range(text: str) -> Optional[tuple[str, str]]:
    """'09:00 AM - 03:00 PM' (or 'OVERTIME 11:00 AM - 3:00 PM') → ('09:00', '15:00')."""
    times = _AMPM.findall(text or "")
    if len(times) < 2:
        return None
    start = parse_ampm(f"{times[0][0]}:{times[0][1]} {times[0][2]}M")
    end = parse_ampm(f"{times[1][0]}:{times[1][1]} {times[1][2]}M")
    return start, end


def to_minutes(hhmm: str) -> int:
    m = _HHMM.match(hhmm or "")
    if not m:
        raise ValueError(f"not HH:MM: {hhmm!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"not HH:MM: {hhmm!r}")
    return h * 60 + mi


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def merge_ranges(ranges: list[tuple[str, str]]) -> list[tuple[int, int]]:
    """Sort and merge touching/overlapping ranges (ADP splits 6:30–1:30 + 1:30–2:30)."""
    spans = sorted(
        (to_minutes(a), to_minutes(b)) for a, b in ranges if to_minutes(b) > to_minutes(a)
    )
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def day_is_over(
    *,
    date: datetime.date,
    scheduled_ranges: list[tuple[str, str]],
    shop_close: str,
    now_ct: datetime.datetime,
    grace_minutes: int,
) -> bool:
    """A past date is over; today is over once its last scheduled end (or shop
    close when unscheduled) plus ``grace_minutes`` has passed in CT."""
    today = now_ct.date()
    if date < today:
        return True
    if date > today:
        return False
    merged = merge_ranges(scheduled_ranges)
    last_end = merged[-1][1] if merged else to_minutes(shop_close)
    now_min = now_ct.hour * 60 + now_ct.minute
    return now_min >= last_end + grace_minutes


def suggest_close(
    *,
    open_in: str,
    scheduled_ranges: list[tuple[str, str]],
    shop_close: str,
    worked_minutes: int = 0,
) -> tuple[Optional[str], Optional[str]]:
    """(suggested_out, rule) for an open entry, or (None, None) when there is no
    scheduled shift to anchor on or the anchor is not after the clock-in.

    The day pays at least its scheduled hours: when a late clock-in would make
    the scheduled end short, the close moves to clock-in + the scheduled hours
    still owed (``worked_minutes`` = completed entries that day). Scheduled time
    after shop close is not owed (a schedule past close is a data error), but a
    late clock-in may push the guarantee past the scheduled end or shop close —
    erring toward paying the employee is the deliberate side of the liability.
    """
    start = to_minutes(open_in)
    close = to_minutes(shop_close)
    merged = merge_ranges(scheduled_ranges)
    anchor: Optional[tuple[int, str]] = None
    for _, end in merged:
        if end <= start:
            continue
        if end > close:
            anchor = (close, RULE_SHOP_CLOSE) if close > start else None
        else:
            anchor = (end, RULE_SCHEDULED_END)
        break
    if anchor is None:
        return None, None
    owed = sum(max(0, min(b, close) - a) for a, b in merged) - worked_minutes
    guarantee = min(start + owed, 23 * 60 + 59)
    if guarantee > anchor[0]:
        return _fmt(guarantee), RULE_SCHEDULED_HOURS
    return _fmt(anchor[0]), anchor[1]


def suggest_entry(
    *, scheduled_ranges: list[tuple[str, str]], shop_close: str,
) -> tuple[Optional[str], Optional[str], Optional[float]]:
    """(in, out, hours) for a scheduled day with no punch: one entry from the
    first scheduled start lasting the scheduled hours (a split schedule pays its
    total in one span; time after shop close is not owed)."""
    close = to_minutes(shop_close)
    merged = merge_ranges(scheduled_ranges)
    owed = sum(max(0, min(b, close) - a) for a, b in merged)
    if not merged or owed <= 0:
        return None, None, None
    start = merged[0][0]
    return _fmt(start), _fmt(min(start + owed, 23 * 60 + 59)), round(owed / 60.0, 2)


def classify_day(
    *,
    date: datetime.date,
    entries: list[TimecardEntry],
    scheduled_ranges: list[tuple[str, str]],
    shop_close: str,
    now_ct: datetime.datetime,
    grace_minutes: int = 60,
) -> Optional[PunchGap]:
    """Return the gap for one employee-day, or None when the day is complete."""
    open_idx = next((i for i, e in enumerate(entries) if e.is_open), None)
    missing_in_idx = next(
        (i for i, e in enumerate(entries) if e.out_time and not e.in_time), None
    )
    if missing_in_idx is not None:
        return PunchGap(MISSING_IN, missing_in_idx, None, None, None)
    if not entries or all(not e.in_time for e in entries):
        if not scheduled_ranges:
            return None
        if not day_is_over(
            date=date, scheduled_ranges=scheduled_ranges, shop_close=shop_close,
            now_ct=now_ct, grace_minutes=grace_minutes,
        ):
            return None
        sin, sout, hours = suggest_entry(scheduled_ranges=scheduled_ranges, shop_close=shop_close)
        return PunchGap(NO_ENTRY, None, sout, hours, RULE_SCHEDULED_SHIFT if sin else None, sin)
    if open_idx is None:
        return None
    if not day_is_over(
        date=date, scheduled_ranges=scheduled_ranges, shop_close=shop_close,
        now_ct=now_ct, grace_minutes=grace_minutes,
    ):
        return PunchGap(IN_PROGRESS, open_idx, None, None, None)
    kind = MISSING_OUT if open_idx == 0 else MISSING_OUT_AFTER_BREAK
    open_in = entries[open_idx].in_time or ""
    worked = sum(
        to_minutes(e.out_time) - to_minutes(e.in_time)
        for e in entries
        if e.in_time and e.out_time
    )
    out, rule = suggest_close(
        open_in=open_in, scheduled_ranges=scheduled_ranges, shop_close=shop_close,
        worked_minutes=worked,
    )
    hours = round((to_minutes(out) - to_minutes(open_in)) / 60.0, 2) if out else None
    return PunchGap(kind, open_idx, out, hours, rule)
