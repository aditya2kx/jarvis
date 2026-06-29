#!/usr/bin/env python3
"""Parse ADP RUN scheduled-maintenance banners into a window-end timestamp.

ADP's sign-in SPA shows a banner like:

    "Planned RUN Maintenance on Sun, Jun 28th from 10pm ET to Mon, Jun 29th at 2am ET."

When a login lands on sorry.adp.com *after* valid credentials during such a
window, BHAGA schedules a smart retry shortly after the window closes rather
than waiting ~24h for the next nightly. This module turns the banner text into a
timezone-aware UTC end timestamp.

Timezone handling is DST-aware via ``zoneinfo`` ("ET" = ``America/New_York``,
which resolves to EDT/UTC-4 in summer and EST/UTC-5 in winter). There is NO
fixed-offset fallback — a fixed offset would be an hour wrong half the year and
could schedule the retry into the maintenance window. Pure + unit-testable:
``now`` is injected for year inference and (when the banner omits the end date)
end-date resolution.
"""

from __future__ import annotations

import datetime
import re
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = datetime.timezone.utc

DEFAULT_RETRY_BUFFER_MINUTES = 7  # middle of the operator's 5–10 min window

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        start=1,
    )
}
_MONTHS_FULL = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"],
        start=1,
    )
}

# The END clause of the banner: "... to [Weekday,] [<Month> <day>] at <h>[:mm] <am/pm> ET".
# Month/day are optional (some banners only state the end time). The leading
# weekday (e.g. "Mon,") is consumed but ignored.
_END_RE = re.compile(
    r"\bto\s+"
    r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"      # optional weekday "Mon," / "Monday"
    r"(?:([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+)?"  # optional "Jun 29th"
    r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\s*"   # "[at] 2am" / "2:00 a.m."
    r"(?:ET|EST|EDT|eastern)\b",
    re.IGNORECASE,
)

_HAS_BANNER_RE = re.compile(r"planned\s+run\s+maintenance|run\s+maintenance", re.IGNORECASE)


def _month_num(name: str) -> int | None:
    n = name.strip().lower().rstrip(".")
    return _MONTHS_FULL.get(n) or _MONTHS.get(n[:3])


def parse_maintenance_end(text: str | None, *, now: datetime.datetime) -> datetime.datetime | None:
    """Return the maintenance-window END as a UTC-aware datetime, or None.

    ``now`` MUST be timezone-aware (used to infer the year, and the end date when
    the banner omits it). Returns None when no maintenance end can be confidently
    parsed — the caller then falls back to the next nightly / Retry-Dates.
    """
    if not text or now.tzinfo is None:
        return None
    flat = re.sub(r"\s+", " ", text)
    if not _HAS_BANNER_RE.search(flat):
        return None
    m = _END_RE.search(flat)
    if not m:
        return None

    month_s, day_s, hour_s, min_s, ap = m.groups()
    hour = int(hour_s) % 12
    if ap.lower() == "p":
        hour += 12
    minute = int(min_s) if min_s else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    now_et = now.astimezone(ET)

    if month_s and day_s:
        month = _month_num(month_s)
        if not month:
            return None
        day = int(day_s)
        try:
            cand = datetime.datetime(now_et.year, month, day, hour, minute, tzinfo=ET)
        except ValueError:
            return None
        # Year inference around the Dec/Jan boundary: if the parsed end is far in
        # the past relative to now, the banner means next year.
        if cand < now_et - datetime.timedelta(days=180):
            try:
                cand = cand.replace(year=now_et.year + 1)
            except ValueError:
                return None
    else:
        # No explicit end date — pick today/tomorrow (ET) so end >= now.
        cand = now_et.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cand < now_et:
            cand += datetime.timedelta(days=1)

    return cand.astimezone(UTC)


def compute_retry_at(
    end_utc: datetime.datetime,
    *,
    buffer_minutes: int = DEFAULT_RETRY_BUFFER_MINUTES,
) -> datetime.datetime:
    """Window end + buffer (default 7 min) → the smart-retry time, UTC-aware."""
    return end_utc + datetime.timedelta(minutes=buffer_minutes)
