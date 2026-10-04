import datetime
import json
from pathlib import Path

import pytest

from skills.adp_run_automation.timecard_ui_backend import (
    build_gap_records,
    parse_employee_menu,
    parse_month_view,
)
from skills.bhaga_labor.punch_gaps import (
    MISSING_OUT,
    MISSING_OUT_AFTER_BREAK,
    TimecardEntry,
    classify_day,
)

FIXTURE = Path(__file__).parent / "testdata" / "timecards_ui" / "month_after_break.aria.yaml"
D = datetime.date

_HEADER = """- button "Roe, Sam"
- button "Pay Period Current Pay Period, 12/28/2026 - 01/10/2027": Current Pay Period, 12/28/2026 - 01/10/2027
- table:
  - rowgroup:
    - row:
"""


def _days():
    return {d.date: d for d in parse_month_view(FIXTURE.read_text()).days}


def test_header_parsed():
    month = parse_month_view(FIXTURE.read_text())
    assert month.employee_name == "Doe, Jane"
    assert (month.pay_period_start, month.pay_period_end) == (D(2026, 9, 21), D(2026, 10, 4))
    assert len(month.days) == 14


def test_open_entry_after_break_and_split_schedule():
    day = _days()[D(2026, 9, 30)]
    assert day.entries == (
        TimecardEntry("09:10", "09:25", 0.25),
        TimecardEntry("11:10", None, 0.0),
    )
    assert day.scheduled_ranges == (("11:30", "13:30"), ("13:30", "20:30"))
    assert day.total_hours == 0.25
    assert day.adp_error_flag is False


def test_complete_days_and_error_flag():
    days = _days()
    assert days[D(2026, 9, 22)].adp_error_flag is True
    assert len(days[D(2026, 9, 22)].entries) == 2
    assert days[D(2026, 9, 21)].total_hours == pytest.approx(7.83)
    assert days[D(2026, 9, 25)].scheduled_ranges == ()


def test_today_prefix_and_empty_days():
    day = _days()[D(2026, 10, 3)]
    assert day.entries == () and day.scheduled_ranges == ()


def test_fixture_feeds_classifier():
    day = _days()[D(2026, 9, 30)]
    gap = classify_day(
        date=day.date, entries=list(day.entries), scheduled_ranges=list(day.scheduled_ranges),
        shop_close="21:00", now_ct=datetime.datetime(2026, 10, 3, 9, 0),
    )
    assert gap.kind == MISSING_OUT_AFTER_BREAK
    assert (gap.open_entry_index, gap.suggested_out) == (1, "20:30")


def test_single_open_entry_with_scheduled_no_entry_day():
    snap = _HEADER + """      - cell "Monday, Dec 28th This day has a timecard entry. TOTAL TIME 0:00 Hours":
        - text: 28 This day has a timecard entry. TOTAL TIME 0:00 Hours
        - status "There are errors for this day"
        - text: Has a schedule. 09:00 AM - 03:00 PM
        - list "Time Entries":
          - listitem: 09:00 AM - Blank
      - cell "Tuesday, Jan 5th Add new entry for Tuesday, Jan 5th Has a schedule.":
        - text: "5"
        - text: Has a schedule. 01:30 PM - 08:30 PM
"""
    days = {d.date: d for d in parse_month_view(snap).days}
    dec = days[D(2026, 12, 28)]
    assert dec.adp_error_flag and dec.entries == (TimecardEntry("09:00", None, 0.0),)
    gap = classify_day(
        date=dec.date, entries=list(dec.entries), scheduled_ranges=list(dec.scheduled_ranges),
        shop_close="21:00", now_ct=datetime.datetime(2027, 1, 6, 9, 0),
    )
    assert (gap.kind, gap.suggested_out, gap.suggested_hours) == (MISSING_OUT, "15:00", 6.0)
    jan = days[D(2027, 1, 5)]
    assert jan.entries == () and jan.scheduled_ranges == (("13:30", "20:30"),)


def test_missing_in_entry():
    snap = _HEADER + """      - cell "Monday, Dec 28th":
        - list "Time Entries":
          - listitem: Blank - 03:00 PM
"""
    (day,) = parse_month_view(snap).days
    assert day.entries == (TimecardEntry(None, "15:00", 0.0),)


def test_unknown_entry_text_fails_loudly():
    snap = _HEADER + """      - cell "Monday, Dec 28th":
        - list "Time Entries":
          - listitem: 9am to 3pm
"""
    with pytest.raises(ValueError, match="unrecognised time entry"):
        parse_month_view(snap)


def test_employee_menu():
    snap = '- button "Doe, Jane" [expanded]\n- menu:\n  - menuitem "Doe, Jane"\n  - menuitem "Roe, Sam R"\n'
    assert parse_employee_menu(snap) == ["Doe, Jane", "Roe, Sam R"]


def test_build_gap_records_uses_aliases_and_skips_complete_days():
    rows, periods = build_gap_records(
        [{"employee": "Doe, Jane", "aria": FIXTURE.read_text()}],
        store="palmetto",
        aliases={"Doe Jane": "Doe, Jane Q"},
        shop_close="21:00",
        now_ct=datetime.datetime(2026, 10, 3, 9, 0),
    )
    assert periods == [D(2026, 9, 21)]
    assert [(r["date"], r["kind"]) for r in rows] == [("2026-09-30", MISSING_OUT_AFTER_BREAK)]
    (row,) = rows
    assert row["employee_id"] == "Doe, Jane Q"
    assert row["raw_employee_name"] == "Doe, Jane"
    assert (row["open_entry_index"], row["suggested_out"], row["suggested_hours"]) == (1, "20:30", 9.33)
    assert json.loads(row["entries_json"])[1] == {"in": "11:10", "out": None, "hours": 0.0}
    assert json.loads(row["scheduled_ranges_json"]) == [["11:30", "13:30"], ["13:30", "20:30"]]


def test_missing_header_fails_loudly():
    with pytest.raises(ValueError, match="header"):
        parse_month_view("- table:\n")


def test_entry_with_overtime_pay_code_suffix_parses():
    from skills.adp_run_automation.timecard_ui_backend import _parse_entry

    entry = _parse_entry("06:30 AM - 05:00 PM Weekly Overtime (0:26 HRS)")
    assert entry == TimecardEntry("06:30", "17:00", 10.5)
    assert _parse_entry("06:30 AM - Blank Weekly Overtime (0:26 HRS)").out_time is None
    assert _parse_entry("06:30 AM - 05:00 PM 12:00 PM") is None
