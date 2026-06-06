"""BQ coverage helper — which business days are already present in raw BQ tables.

Used by the gap-resolver in daily_refresh.main() to determine which days still
need to be scraped from upstream, rather than re-scraping days already stored.

API
---
present_days(table, date_col, start, end) -> set[datetime.date]
missing_ranges(table, date_col, start, end) -> list[(start, end)]

SOURCE_COVERAGE maps logical source names to (table, date_col) for callers that
do not want to hard-code table names.
"""

from __future__ import annotations

import datetime

from core.datastore import read_query

_PROJECT = "jarvis-bhaga-prod"
_DS = "bhaga"

# Logical source name -> (bq_table, date_column)
SOURCE_COVERAGE: dict[str, tuple[str, str]] = {
    "square_transactions": ("square_transactions", "date_local"),
    "adp_shifts":          ("adp_shifts",          "date"),
    "square_kds_daily":    ("square_kds_daily",    "date_local"),
    "adp_earnings":        ("adp_earnings",         "period_start"),
    "google_reviews":      ("google_reviews",       "post_date_ct"),
}


def present_days(
    table: str,
    date_col: str,
    start: datetime.date,
    end: datetime.date,
) -> set[datetime.date]:
    """Return the set of distinct dates present in *table* between *start* and *end* inclusive."""
    rows = read_query(
        f"SELECT DISTINCT {date_col} AS d"
        f" FROM `{_PROJECT}.{_DS}.{table}`"
        f" WHERE {date_col} BETWEEN DATE('{start.isoformat()}') AND DATE('{end.isoformat()}')"
    )
    return {r["d"] for r in rows if r.get("d") is not None}


def missing_ranges(
    table: str,
    date_col: str,
    start: datetime.date,
    end: datetime.date,
) -> list[tuple[datetime.date, datetime.date]]:
    """Return contiguous date ranges absent from *table* in [start, end].

    Each tuple is (gap_start, gap_end) inclusive. Returns an empty list when
    every day in the window is already present in BQ.
    """
    have = present_days(table, date_col, start, end)
    out: list[tuple[datetime.date, datetime.date]] = []
    run_start: datetime.date | None = None
    d = start
    while d <= end:
        if d not in have:
            if run_start is None:
                run_start = d
        else:
            if run_start is not None:
                out.append((run_start, d - datetime.timedelta(days=1)))
                run_start = None
        d += datetime.timedelta(days=1)
    if run_start is not None:
        out.append((run_start, end))
    return out
