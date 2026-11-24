"""A store's opening hours from its Square location (``business_hours``).

The nightly copies them into ``square_location_hours`` so the Operator Console has a
fallback when Square can't be reached, and a history of when the hours changed.
"""

from __future__ import annotations

import datetime

from core import datastore
from skills.square_api.client import SquareClient

TABLE = "square_location_hours"
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _minute(hhmmss: str) -> int:
    h, m, *_ = (int(x) for x in hhmmss.split(":"))
    return h * 60 + m


def week_hours(location: dict) -> list[tuple[int, int] | None]:
    """(open, close) minute of day per weekday, Mon first; None when Square lists no hours.

    Split hours on one day (e.g. 7–11 and 12–8) count as one span, earliest open to
    latest close: the staffed window covers the break too. A close of midnight is 24:00.
    """
    spans: list[tuple[int, int] | None] = [None] * 7
    for p in (location.get("business_hours") or {}).get("periods") or []:
        if p.get("day_of_week") not in DAYS:
            continue
        d = DAYS.index(p["day_of_week"])
        start, end = _minute(p["start_local_time"]), _minute(p["end_local_time"])
        if end <= start:
            end = 24 * 60
        cur = spans[d]
        spans[d] = (start, end) if cur is None else (min(cur[0], start), max(cur[1], end))
    return spans


def snapshot(store: str, location_id: str, as_of: datetime.date, client: SquareClient | None = None) -> int:
    """Copy the location's hours into BQ for ``as_of`` (idempotent per store and day)."""
    client = client or SquareClient(store)
    location = client.get(f"/v2/locations/{location_id}").get("location") or {}
    spans = week_hours(location)
    if not any(spans):
        raise ValueError(f"Square location {location_id} has no business_hours")
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = [{"store": store, "as_of_date": as_of, "day_of_week": d,
             "open_min": s[0] if s else None, "close_min": s[1] if s else None,
             "location_id": location_id, "synced_at": now}
            for d, s in enumerate(spans)]
    return datastore.load_rows(TABLE, rows, merge_keys=["store", "as_of_date", "day_of_week"],
                               column_bq_types={"open_min": "INT64", "close_min": "INT64"})
