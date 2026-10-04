"""Tests for skills/bhaga_labor/punch_gaps.py (Issue #356).

Cases mirror the 2026-09-21 → 10-04 pay period as seen in the ADP Timecards UI.
"""

from __future__ import annotations

import datetime

import pytest

from skills.bhaga_labor import punch_gaps as pg
from skills.bhaga_labor.punch_gaps import TimecardEntry as E

CLOSE = "21:00"
# Late evening after the shop closed on 2026-10-02.
NIGHT = datetime.datetime(2026, 10, 2, 23, 30)
D = datetime.date


def classify(date, entries, ranges, now=NIGHT, close=CLOSE):
    return pg.classify_day(
        date=date, entries=entries, scheduled_ranges=ranges, shop_close=close, now_ct=now,
    )


def test_missing_out_only_entry_closes_at_scheduled_end():
    gap = classify(D(2026, 9, 21), [E("09:00", None)], [("09:00", "15:00")])
    assert gap == pg.PunchGap(pg.MISSING_OUT, 0, "15:00", 6.0, pg.RULE_SCHEDULED_END)


def test_missing_out_after_break_keeps_first_entry_and_closes_second():
    entries = [E("06:28", "11:10", 4.7), E("11:30", None)]
    gap = classify(D(2026, 9, 25), entries, [("06:30", "13:30")])
    assert gap.kind == pg.MISSING_OUT_AFTER_BREAK
    assert gap.open_entry_index == 1
    # 7h scheduled − 4h42 already worked = 2h18 still owed after the break.
    assert gap.suggested_out == "13:48"
    assert gap.suggested_hours == 2.3
    assert gap.rule == pg.RULE_SCHEDULED_HOURS


def test_split_schedule_ranges_are_merged_before_choosing_the_end():
    ranges = [("06:30", "13:30"), ("13:30", "14:30")]
    gap = classify(D(2026, 10, 1), [E("06:30", None)], ranges)
    assert gap.suggested_out == "14:30"


def test_open_entry_after_the_first_shift_uses_the_next_range():
    ranges = [("09:00", "16:00"), ("17:30", "18:30")]
    gap = classify(D(2026, 9, 28), [E("08:50", "16:00"), E("17:30", None)], ranges)
    assert gap.suggested_out == "18:30"


def test_no_entry_on_a_scheduled_day_suggests_the_scheduled_shift():
    gap = classify(D(2026, 9, 28), [], [("06:30", "13:30")])
    assert gap == pg.PunchGap(pg.NO_ENTRY, None, "13:30", 7.0, pg.RULE_SCHEDULED_SHIFT, "06:30")


def test_no_entry_split_schedule_pays_the_total_in_one_entry():
    gap = classify(D(2026, 9, 28), [], [("06:30", "11:00"), ("15:00", "18:00")])
    assert (gap.suggested_in, gap.suggested_out, gap.suggested_hours) == ("06:30", "14:00", 7.5)


def test_no_entry_does_not_owe_time_after_shop_close():
    gap = classify(D(2026, 9, 28), [], [("17:00", "22:00")])
    assert (gap.suggested_in, gap.suggested_out, gap.suggested_hours) == ("17:00", "21:00", 4.0)


def test_unscheduled_day_without_entries_is_not_a_gap():
    assert classify(D(2026, 9, 28), [], []) is None


def test_complete_day_is_not_a_gap():
    assert classify(D(2026, 9, 29), [E("06:32", "15:13", 8.68)], [("06:30", "13:30")]) is None


def test_open_entry_today_is_in_progress_until_the_day_is_over():
    evening = datetime.datetime(2026, 10, 2, 21, 0)
    gap = classify(D(2026, 10, 2), [E("07:30", None)], [("07:30", "20:30")], now=evening)
    assert gap.kind == pg.IN_PROGRESS
    assert gap.suggested_out is None


def test_open_entry_today_becomes_a_gap_after_grace():
    gap = classify(D(2026, 10, 2), [E("07:30", None)], [("07:30", "20:30")])
    assert gap.kind == pg.MISSING_OUT
    assert gap.suggested_out == "20:30"


def test_no_entry_today_is_not_flagged_before_the_shift_could_have_ended():
    morning = datetime.datetime(2026, 10, 2, 10, 0)
    assert classify(D(2026, 10, 2), [], [("13:30", "20:30")], now=morning) is None


def test_suggestion_is_capped_at_shop_close():
    gap = classify(D(2026, 9, 26), [E("13:30", None)], [("13:30", "22:30")])
    assert gap.suggested_out == "21:00"
    assert gap.rule == pg.RULE_SHOP_CLOSE


def test_late_clock_in_still_pays_the_scheduled_hours():
    gap = classify(D(2026, 9, 23), [E("09:08", None)], [("09:00", "15:30")])
    assert gap == pg.PunchGap(pg.MISSING_OUT, 0, "15:38", 6.5, pg.RULE_SCHEDULED_HOURS)


def test_early_clock_in_keeps_the_scheduled_end():
    gap = classify(D(2026, 9, 24), [E("13:27", None)], [("13:30", "20:30")])
    assert gap == pg.PunchGap(pg.MISSING_OUT, 0, "20:30", 7.05, pg.RULE_SCHEDULED_END)


def test_late_clock_in_guarantee_may_pass_shop_close():
    gap = classify(D(2026, 10, 2), [E("14:10", None)], [("14:00", "21:00")])
    assert gap.suggested_out == "21:10"
    assert gap.suggested_hours == 7.0


def test_split_schedule_late_clock_in_pays_the_merged_total():
    ranges = [("06:30", "13:30"), ("13:30", "14:30")]
    gap = classify(D(2026, 10, 1), [E("06:32", None)], ranges)
    assert gap.suggested_out == "14:32"
    assert gap.suggested_hours == 8.0


def test_no_schedule_means_no_suggestion():
    gap = classify(D(2026, 9, 23), [E("10:00", None)], [])
    assert gap.kind == pg.MISSING_OUT
    assert gap.suggested_out is None and gap.suggested_hours is None


def test_clock_in_after_the_scheduled_end_gets_no_suggestion():
    gap = classify(D(2026, 9, 23), [E("16:00", None)], [("09:00", "15:00")])
    assert gap.suggested_out is None


def test_suggestion_never_removes_punched_time():
    entries = [E("06:28", "11:10", 4.7), E("11:30", None)]
    gap = classify(D(2026, 9, 25), entries, [("06:30", "13:30")])
    assert pg.to_minutes(gap.suggested_out) > pg.to_minutes(entries[1].in_time)
    assert entries[0].out_time == "11:10"


def test_out_without_in_is_missing_in():
    gap = classify(D(2026, 9, 23), [E(None, "15:00")], [("09:00", "15:00")])
    assert gap.kind == pg.MISSING_IN
    assert gap.suggested_out is None


@pytest.mark.parametrize(
    "text, want",
    [("09:00 AM", "09:00"), ("12:00 PM", "12:00"), ("12:15 AM", "00:15"), ("1:30 PM", "13:30")],
)
def test_parse_ampm(text, want):
    assert pg.parse_ampm(text) == want


def test_parse_range_tolerates_prefix():
    assert pg.parse_range("OVERTIME 11:00 AM - 3:00 PM") == ("11:00", "15:00")
    assert pg.parse_range("nothing") is None


def test_parse_ampm_rejects_garbage():
    with pytest.raises(ValueError):
        pg.parse_ampm("Blank")
