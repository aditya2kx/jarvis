"""Tests for punch_reply — reading staff replies in the missing-punch thread (Issue #397)."""

from __future__ import annotations

import datetime

import pytest

from skills.bhaga_labor import punch_reply as pr

MON = datetime.date(2026, 10, 12)
TUE = datetime.date(2026, 10, 13)

OUT = pr.ReplyItem(MON, "missing_out", (("09:02", None),), 0, (("09:00", "15:00"),),
                   suggested_out="15:00")
IN = pr.ReplyItem(MON, "missing_in", ((None, "20:31"),), 0, (("14:00", "20:30"),),
                  suggested_in="14:00")
NONE = pr.ReplyItem(MON, "no_entry", (), None, (("14:00", "20:30"),),
                    suggested_in="14:00", suggested_out="20:30")


def R(action, in_time, out_time, date=MON):
    return pr.Resolution(date, action, in_time, out_time)


@pytest.mark.parametrize("text, item, want", [
    # Missing clock-out: only the out is filled.
    ("out 3:20", OUT, R("edit", None, "15:20")),
    ("I left at 3:20pm sorry!", OUT, R("edit", None, "15:20")),
    ("left at 3", OUT, R("edit", None, "15:00")),
    ("3:20", OUT, R("edit", None, "15:20")),
    ("9-3:30", OUT, R("edit", None, "15:30")),
    ("yes", OUT, R("accept", None, "15:00")),
    ("Yep that’s right 👍", OUT, R("accept", None, "15:00")),
    # Missing clock-in: only the in is filled; the punched out stays.
    ("in 2:05", IN, R("edit", "14:05", "20:31")),
    ("clocked in at 1:55", IN, R("edit", "13:55", "20:31")),
    ("2:05", IN, R("edit", "14:05", "20:31")),
    ("ok", IN, R("accept", "14:00", "20:31")),
    # No punches: a whole shift, a confirm, or a dismissal.
    ("2:15-8:45", NONE, R("edit", "14:15", "20:45")),
    ("worked 2 to 8:30", NONE, R("edit", "14:00", "20:30")),
    ("in 2 out 8:30", NONE, R("edit", "14:00", "20:30")),
    ("yes I worked", NONE, R("accept", "14:00", "20:30")),
    ("I didn’t work that day", NONE, R("reject", None, None)),
    ("off", NONE, R("reject", None, None)),
    ("called out sick", NONE, R("reject", None, None)),
    # 24h and explicit am/pm win over the nearest-to-schedule guess.
    ("out 14:40", OUT, R("edit", None, "14:40")),
    ("[@Alex](#user_mention#123) out 3:20 pm", OUT, R("edit", None, "15:20")),
])
def test_understood_replies(text, item, want):
    assert pr.parse_reply(text, [item]) == [want]


@pytest.mark.parametrize("text, item", [
    ("thanks!", OUT),
    ("out 8:00 am", OUT),            # before the 9:02 clock-in
    ("didn't work", OUT),            # contradicts the clock-in
    ("in 2:05", OUT),                # wrong side
    ("out 9:10", IN),                # wrong side
    ("3:30", NONE),                  # one time cannot make a shift
    ("in 2 days", OUT),              # a duration, not a time
    ("out 3:20 and out 4:00", OUT),  # two answers
    ("1-2 then 3-4", NONE),          # two ranges for one day
])
def test_unclear_replies_resolve_nothing(text, item):
    assert pr.parse_reply(text, [item]) == []


def test_confirm_without_a_suggestion_resolves_nothing():
    bare = pr.ReplyItem(MON, "missing_out", (("09:02", None),), 0, ())
    assert pr.parse_reply("yes", [bare]) == []


def test_a_date_picks_the_day_among_several():
    tue = pr.ReplyItem(TUE, "missing_out", (("10:00", None),), 0, (("10:00", "16:00"),),
                       suggested_out="16:00")
    assert pr.parse_reply("Tue out 4:10", [OUT, tue]) == [R("edit", None, "16:10", TUE)]
    assert pr.parse_reply("10/12 left 3:05", [OUT, tue]) == [R("edit", None, "15:05")]
    assert pr.parse_reply("yes", [OUT, tue]) == [R("accept", None, "15:00"),
                                                 R("accept", None, "16:00", TUE)]
    assert pr.parse_reply("out 3:20", [OUT, tue]) == []
    assert pr.parse_reply("Wed out 3:20", [OUT, tue]) == []


def test_one_range_per_day_in_date_order():
    tue = pr.ReplyItem(TUE, "no_entry", (), None, (("09:00", "15:00"),),
                       suggested_in="09:00", suggested_out="15:00")
    assert pr.parse_reply("9-3:10 and 9:05-3", [OUT, tue]) == [
        R("edit", None, "15:10"), R("edit", "09:05", "15:00", TUE)]


def test_missing_out_after_break_uses_the_open_entry():
    item = pr.ReplyItem(MON, "missing_out_after_break", (("07:00", "11:00"), ("11:30", None)), 1,
                        (("07:00", "15:00"),), suggested_out="15:00")
    assert pr.parse_reply("out 3:05", [item]) == [R("edit", None, "15:05")]
