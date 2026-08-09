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
            "name": "Employee489, Xcc2",
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
    assert recs[0]["employee_id"] == "Employee489, Xcc2"


def test_cap_days_to_week_total_trims_grid_over_attribution():
    """Shared-grid climb attached every shift to mid-list names; week total is truth."""
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
            "name": "Employee489, Xcc2",
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
    # Cap keeps 3 wall-clock days (20h), then scale to paid week total 19h.
    assert sum(r["scheduled_hours"] for r in recs) == pytest.approx(19.0)
    assert len(recs) == 3
    assert recs[0]["employee_id"] == "Employee489, Xcc2"


def test_scale_hours_removes_unpaid_meal_like_lindsay():
    """5× 9:00–5:30 wall (8.5h) with ADP week total 40:00 → 5×8.0 paid."""
    weeks = [{
        "week_label": "Week of Aug 3, 2026 - Aug 9, 2026",
        "employee_rows": [{
            "name": "Krause, Lindsay",
            "week_total_text": "40:00 Hrs",
            "days": [
                {"header_index": i, "ranges": ["9:00 AM - 5:30 PM"]}
                for i in (0, 1, 3, 5, 6)
            ],
        }],
    }]
    recs = sb.build_employee_schedule_records(weeks)
    assert len(recs) == 5
    assert sum(r["scheduled_hours"] for r in recs) == pytest.approx(40.0)
    assert all(r["scheduled_hours"] == pytest.approx(8.0) for r in recs)
    # Ranges stay wall-clock for coverage UI.
    assert json.loads(recs[0]["shift_ranges_json"]) == ["9:00 AM - 5:30 PM"]


def test_scale_hours_to_week_total_unit():
    assert sb.scale_hours_to_week_total([8.5, 8.5, 8.5, 8.5, 8.5], 40.0) == [
        8.0, 8.0, 8.0, 8.0, 8.0,
    ]


def test_scale_hours_never_inflates_sparse_days():
    """2 scraped days + week_total 40 must NOT become 20h/day (concurrent blow-up)."""
    assert sb.scale_hours_to_week_total([8.5, 8.5], 40.0) == [8.5, 8.5]


def test_sparse_week_without_pto_keeps_wall_hours():
    """Incomplete week with empty cells (no PTO text) must not invent hours."""
    weeks = [{
        "week_label": "Week of Aug 10, 2026 - Aug 16, 2026",
        "employee_rows": [{
            "name": "Krause, Lindsay",
            "week_total_text": "40:00 Hrs",
            "days": [
                {"header_index": 0, "ranges": ["8:00 AM - 4:30 PM"]},
                {"header_index": 1, "ranges": ["12:00 PM - 8:30 PM"]},
                {"header_index": 2, "ranges": []},
                {"header_index": 3, "ranges": []},
                {"header_index": 4, "ranges": []},
            ],
        }],
    }]
    recs = sb.build_employee_schedule_records(weeks)
    assert len(recs) == 2
    assert all(r["scheduled_hours"] == pytest.approx(8.5) for r in recs)
    assert sum(r["scheduled_hours"] for r in recs) == pytest.approx(17.0)


def test_krause_pto_cells_count_toward_week_total():
    """Live Aug 10 bug: PERSONAL Approved Time Off in cell_text, not ranges.

    Wall Mon+Tue 8.5 + three PTO 8:00–4:00 (=8) = 41 → scale to ADP 40 paid.
    Mon/Tue land ~8 paid (unpaid meal), not 8.5 wall.
    """
    weeks = [{
        "week_label": "Week of Aug 10, 2026 - Aug 16, 2026",
        "employee_rows": [{
            "name": "Krause, Lindsay",
            "week_total_text": "40:00 Hrs",
            "days": [
                {
                    "header_index": 0,
                    "ranges": ["8:00 AM - 4:30 PM"],
                    "cell_text": "8:00 AM - 4:30 PM",
                },
                {
                    "header_index": 1,
                    "ranges": ["12:00 PM - 8:30 PM"],
                    "cell_text": "12:00 PM - 8:30 PM",
                },
                {
                    "header_index": 2,
                    "ranges": [],
                    "cell_text": "PERSONAL Approved Time Off. 8:00 AM - 4:00 PM",
                },
                {
                    "header_index": 3,
                    "ranges": [],
                    "cell_text": "PERSONAL Approved Time Off. 8:00 AM - 4:00 PM",
                },
                {
                    "header_index": 4,
                    "ranges": [],
                    "cell_text": "PERSONAL Approved Time Off. 8:00 AM - 4:00 PM",
                },
            ],
        }],
    }]
    recs = sb.build_employee_schedule_records(weeks)
    assert len(recs) == 5
    assert sum(r["scheduled_hours"] for r in recs) == pytest.approx(40.0)
    by_date = {r["date"]: r for r in recs}
    assert by_date["2026-08-10"]["hour_kind"] == "shift"
    assert by_date["2026-08-12"]["hour_kind"] == "pto"
    # Paid after scale (wall 41 → 40): Mon/Tue ~8.29, PTO ~7.80 — not wall 8.5
    assert by_date["2026-08-10"]["scheduled_hours"] == pytest.approx(8.5 * 40 / 41, abs=0.02)
    assert by_date["2026-08-12"]["scheduled_hours"] == pytest.approx(8.0 * 40 / 41, abs=0.02)


def test_parse_day_cell_hours_pto():
    hours, ranges, kind = sb.parse_day_cell_hours({
        "ranges": [],
        "cell_text": "PERSONAL Approved Time Off. 8:00 AM - 4:00 PM",
    })
    assert kind == "pto"
    assert hours == pytest.approx(8.0)
    assert ranges and "8:00 AM" in ranges[0]


def test_reconcile_employee_vs_footer_warns_on_gap():
    weeks = [{
        "week_label": "Week of Aug 10, 2026 - Aug 16, 2026",
        "grand": "15 Employees 238:30 Hrs",
        "days": [
            "5 Employees 32:00 Hrs",
            "4 Employees 25:30 Hrs",
            "5 Employees 32:30 Hrs",
            "5 Employees 32:30 Hrs",
            "6 Employees 40:30 Hrs",
            "6 Employees 36:00 Hrs",
            "6 Employees 39:30 Hrs",
        ],
        "employee_rows": [{
            "name": "Krause, Lindsay",
            "week_total_text": "40:00 Hrs",
            "days": [
                {"header_index": 0, "ranges": ["8:00 AM - 4:30 PM"]},
                {"header_index": 1, "ranges": ["12:00 PM - 8:30 PM"]},
            ],
        }],
    }]
    warns = sb.reconcile_employee_vs_footer(weeks, tolerance_hours=0.5)
    assert warns and "gap=" in warns[0]


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


def test_default_weeks_horizon_covers_manager_planning():
    """Issue #230: sync must look beyond current+next week."""
    assert sb.DEFAULT_WEEKS == 8
    assert sb.MAX_SCHEDULE_WEEKS == 8
    assert sb.DEFAULT_WEEKS <= sb.MAX_SCHEDULE_WEEKS


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
