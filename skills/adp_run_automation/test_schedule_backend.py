"""Unit tests for schedule_backend (ADP Team Schedule parser).

Pure-Python; no browser. Sample strings are the exact innerText shapes captured
live from Palmetto Superfoods on 2026-06-10 (see schedule_backend docstring).
"""
from __future__ import annotations

import datetime
import json

import pytest

from skills.adp_run_automation import schedule_backend as sb


# ── HH:MM → decimal ───────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("1:30 PM - 8:30 PM", 7.0),
    ("6:30 AM - 1:30 PM", 7.0),
    ("9:00 AM - 3:00 PM", 6.0),
    ("11:00 AM - 5:00 PM", 6.0),
    ("", 0.0),
    (None, 0.0),
])
def test_parse_shift_range_hours(raw, expected):
    assert sb.parse_shift_range_hours(raw) == pytest.approx(expected)


def test_build_employee_schedule_records_maps_header_index_to_date():
    weeks = [{
        "week_label": "Week of Jul 13, 2026 - Jul 19, 2026",
        "employee_rows": [{
            "name": "Garcia, Jacob",
            "days": [
                {"header_index": 0, "ranges": ["1:30 PM - 8:30 PM"]},
                {"header_index": 5, "ranges": ["9:00 AM - 3:00 PM", "bad"]},
            ],
        }],
    }]
    recs = sb.build_employee_schedule_records(weeks)
    assert [(r["date"], r["scheduled_hours"]) for r in recs] == [
        ("2026-07-13", 7.0),
        ("2026-07-18", 6.0),
    ]
    assert recs[0]["employee_id"] == "Garcia, Jacob"


def test_cap_days_to_week_total_trims_grid_over_attribution():
    """Tina/Ximena bug: shared-grid climb attached every shift; week total is truth."""
    days = [
        {"header_index": 0, "ranges": ["1:30 PM - 8:30 PM"]},  # 7
        {"header_index": 2, "ranges": ["1:30 PM - 8:30 PM"]},  # 7
        {"header_index": 5, "ranges": ["9:00 AM - 3:00 PM"]},  # 6 → 20 ≈ 19:00
        {"header_index": 0, "ranges": ["6:30 AM - 1:30 PM"]},  # pollution
        {"header_index": 1, "ranges": ["1:30 PM - 8:30 PM"]},
    ]
    kept = sb.cap_days_to_week_total(days, week_total_hours=19.0)
    assert len(kept) == 3
    assert [d["header_index"] for d in kept] == [0, 2, 5]


def test_build_employee_schedule_records_caps_over_attributed_payload():
    weeks = [{
        "week_label": "Week of Jul 13, 2026 - Jul 19, 2026",
        "employee_rows": [{
            "name": "Majdinasab, Tina",
            "week_total_text": "19:00 Hrs",
            "days": [
                {"header_index": 0, "ranges": ["1:30 PM - 8:30 PM"]},
                {"header_index": 2, "ranges": ["1:30 PM - 8:30 PM"]},
                {"header_index": 5, "ranges": ["9:00 AM - 3:00 PM"]},
                {"header_index": 0, "ranges": ["6:30 AM - 1:30 PM"]},
                {"header_index": 1, "ranges": ["1:30 PM - 8:30 PM"]},
            ],
        }],
    }]
    recs = sb.build_employee_schedule_records(weeks)
    assert sum(r["scheduled_hours"] for r in recs) == pytest.approx(20.0)
    assert len(recs) == 3


@pytest.mark.parametrize("raw,expected", [
    ("7 Employees 46:45 Hrs", 7),
    ("13 Employees 291:30 Hrs", 13),
    ("46:45 Hrs", 0),   # per-employee total has no "Employees" token
    (None, 0),
])
def test_parse_employee_count(raw, expected):
    assert sb.parse_employee_count(raw) == expected


def test_parse_total_cell():
    assert sb.parse_total_cell("7 Employees 46:45 Hrs") == (7, 46.75)


# ── Week label → start date ───────────────────────────────────────


@pytest.mark.parametrize("label,expected", [
    ("Week of Jun 8, 2026 - Jun 14, 2026", datetime.date(2026, 6, 8)),
    ("Week of Jun 15, 2026 \u2013 Jun 21, 2026", datetime.date(2026, 6, 15)),  # en-dash
    ("Week of December 29, 2025 - January 4, 2026", datetime.date(2025, 12, 29)),
    ("Week of Jan 1, 2027", datetime.date(2027, 1, 1)),
])
def test_parse_week_start(label, expected):
    assert sb.parse_week_start(label) == expected


@pytest.mark.parametrize("label", [None, "", "no week here", "Week of Xyz 8, 2026"])
def test_parse_week_start_bad(label):
    assert sb.parse_week_start(label) is None


# ── Record assembly ───────────────────────────────────────────────


_REAL_WEEK = {
    "week_label": "Week of Jun 8, 2026 - Jun 14, 2026",
    "grand": "13 Employees 291:30 Hrs",
    "days": [
        "7 Employees 46:45 Hrs",
        "6 Employees 39:15 Hrs",
        "5 Employees 33:15 Hrs",
        "6 Employees 40:15 Hrs",
        "6 Employees 38:45 Hrs",
        "6 Employees 47:00 Hrs",
        "7 Employees 46:15 Hrs",
    ],
}


def test_build_records_maps_each_day_to_a_date():
    recs = sb.build_schedule_records([_REAL_WEEK])
    assert [r["date"] for r in recs] == [
        "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11",
        "2026-06-12", "2026-06-13", "2026-06-14",
    ]
    assert recs[0] == {
        "date": "2026-06-08", "scheduled_hours": 46.75,
        "employee_count": 7, "week_start": "2026-06-08",
    }


def test_build_records_day_sum_matches_grand_total():
    recs = sb.build_schedule_records([_REAL_WEEK])
    assert sum(r["scheduled_hours"] for r in recs) == pytest.approx(291.5)


def test_build_records_two_weeks_are_contiguous_and_sorted():
    wk2 = dict(_REAL_WEEK, week_label="Week of Jun 15, 2026 - Jun 21, 2026")
    recs = sb.build_schedule_records([_REAL_WEEK, wk2])
    dates = [r["date"] for r in recs]
    assert dates[0] == "2026-06-08" and dates[-1] == "2026-06-21"
    assert dates == sorted(dates)
    assert len(dates) == 14


def test_build_records_skips_unparseable_week_without_shifting_dates():
    bad = {"week_label": "not a week", "days": _REAL_WEEK["days"]}
    recs = sb.build_schedule_records([bad, _REAL_WEEK])
    # only the good week survives; its dates are correct (not shifted)
    assert [r["date"] for r in recs][0] == "2026-06-08"
    assert len(recs) == 7


def test_build_records_skips_short_week():
    short = {"week_label": _REAL_WEEK["week_label"], "days": _REAL_WEEK["days"][:5]}
    assert sb.build_schedule_records([short]) == []


# ── daily_schedule (fixture file) ─────────────────────────────────


def test_daily_schedule_reads_newest_and_filters(tmp_path):
    payload = {"scraped_at_utc": "2026-06-10T00:00:00Z", "store": "palmetto",
               "weeks": [_REAL_WEEK]}
    (tmp_path / "Schedule-2026-06-10.json").write_text(json.dumps(payload))
    recs = sb.daily_schedule(
        start_date=datetime.date(2026, 6, 10),
        end_date=datetime.date(2026, 6, 12),
        downloads_dir=tmp_path,
    )
    assert [r["date"] for r in recs] == ["2026-06-10", "2026-06-11", "2026-06-12"]


def test_daily_schedule_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        sb.daily_schedule(downloads_dir=tmp_path)
