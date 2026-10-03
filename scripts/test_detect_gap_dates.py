#!/usr/bin/env python3
"""Unit tests for scripts/detect_gap_dates.py (no GCP calls)."""
from __future__ import annotations

import datetime
import os
import pathlib
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import scripts.detect_gap_dates as g

TODAY = datetime.date(2026, 10, 2)
WEEK = [(TODAY - datetime.timedelta(days=n)).isoformat() for n in range(7, 0, -1)]


def test_healthy_week_has_no_gaps_or_holes():
    status = {d: "success" for d in WEEK}
    assert g.gap_dates(latest_status=status, model_days=set(WEEK), today=TODAY, lookback=7) == []
    assert g.raw_holes(square_days=set(WEEK), adp_days=set(WEEK), today=TODAY, lookback=7) == []


def test_failed_never_ran_and_missing_model_are_gaps():
    status = {d: "success" for d in WEEK}
    status["2026-09-29"] = "failed"
    del status["2026-09-27"]
    model = set(WEEK) - {"2026-09-30"}
    assert g.gap_dates(latest_status=status, model_days=model, today=TODAY, lookback=7) == [
        "2026-09-27", "2026-09-29", "2026-09-30",
    ]


def test_today_is_never_a_gap_or_hole():
    assert TODAY.isoformat() not in g.gap_dates(latest_status={}, model_days=set(),
                                                today=TODAY, lookback=3)
    assert TODAY.isoformat() not in g.raw_holes(square_days=set(), adp_days=set(),
                                                today=TODAY, lookback=3)


def test_hole_behind_latest_date_is_found():
    """One night's ADP load failed and later nights landed: MAX(date) looks fine."""
    adp = set(WEEK) - {"2026-09-28"}
    assert g.raw_holes(square_days=set(WEEK), adp_days=adp, today=TODAY, lookback=7) == [
        "2026-09-28"]


def test_closed_dates_are_not_holes():
    sq = set(WEEK) - {"2026-09-28"}
    adp = set(WEEK) - {"2026-09-28"}
    assert g.raw_holes(square_days=sq, adp_days=adp, today=TODAY, lookback=7,
                       closed=frozenset({"2026-09-28"})) == []


def test_plan_one_scrape_for_all_holes_and_no_recompute_for_them():
    runs = g.plan(gaps=["2026-09-27", "2026-09-29"], holes=["2026-09-27", "2026-09-30"],
                  rebuild_from=None, yesterday="2026-10-01")
    assert runs == [
        ["--date", "2026-09-30", "--force-scrape", "--window-from", "2026-09-27"],
        ["--date", "2026-09-29", "--force-recompute"],
    ]


def test_plan_rebuild_subsumes_later_gaps_and_respects_exclude():
    runs = g.plan(gaps=["2026-09-05", "2026-09-10", "2026-09-29"], holes=[],
                  rebuild_from="2026-09-07", yesterday="2026-10-01", exclude={"2026-09-05"})
    assert runs == [["--date", "2026-10-01", "--force-recompute",
                     "--model-scope-from", "2026-09-07"]]


def test_model_sources_follow_imports():
    src = g.model_sources()
    assert g.MODEL_ENTRY in src
    assert "skills/bhaga_labor/solo_shift.py" in src
    assert "agents/bhaga/scripts/update_model_sheet.py" in src


def test_touches_model():
    src = {"agents/bhaga/scripts/materialize_model_bq.py"}
    assert g.touches_model(["agents/bhaga/scripts/materialize_model_bq.py"], src)
    assert g.touches_model([g.STORE_PROFILES + "palmetto.json"], src)
    assert not g.touches_model(["agents/bhaga/scripts/test_materialize_model_bq.py"], src)
    assert not g.touches_model(["RUNBOOK.md", "cloud/pup_watch/app.py"], src)


def test_closed_period_start_is_the_period_before_today():
    profile = {"adp_run": {"pay_periods_anchor_end_date": "2026-05-17"}}
    assert g.closed_period_start(profile, TODAY) == "2026-09-07"
    assert g.closed_period_start({}, TODAY) is None


def test_plan_rate_refresh_scrapes_yesterday_once():
    assert g.plan(gaps=[], holes=[], rebuild_from=None, yesterday="2026-10-01",
                  refresh_rates=True) == [["--date", "2026-10-01", "--force-scrape"]]
    runs = g.plan(gaps=[], holes=["2026-09-30"], rebuild_from=None,
                  yesterday="2026-10-01", refresh_rates=True)
    assert runs == [["--date", "2026-09-30", "--force-scrape", "--window-from", "2026-09-30"]]


def _run_main(data, argv, excluded=()):
    healthy = {"status": {d: "success" for d in WEEK}, "model": set(WEEK),
               "square": set(WEEK), "adp": set(WEEK), "unrated": []}
    with mock.patch.object(g, "_fetch", return_value={**healthy, **data}), \
         mock.patch("skills.store_profile.load_exclusions",
                    return_value={"permanent": list(excluded)}), \
         mock.patch.object(g, "datetime") as dt:
        dt.datetime.now.return_value = datetime.datetime(2026, 10, 2, 14, 0)
        dt.timedelta = datetime.timedelta
        dt.date = datetime.date
        assert g.main(["--lookback", "7", *argv]) == 0


def test_main_prints_plan_lines(capsys, tmp_path):
    changed = tmp_path / "files.txt"
    changed.write_text("skills/bhaga_labor/solo_shift.py\n")
    _run_main({"adp": set(WEEK) - {"2026-09-30"}}, ["--changed-files", str(changed)])
    assert capsys.readouterr().out.splitlines() == [
        "--date 2026-10-01 --force-scrape --window-from 2026-09-30 --model-scope-from 2026-09-07",
    ]


def test_plan_folded_rebuild_runs_through_yesterday():
    runs = g.plan(gaps=[], holes=["2026-09-25", "2026-09-27"], rebuild_from="2026-09-07",
                  yesterday="2026-10-01")
    assert runs == [["--date", "2026-10-01", "--force-scrape", "--window-from", "2026-09-25",
                     "--model-scope-from", "2026-09-07"]]


def test_main_new_hire_without_rate_triggers_scrape(capsys):
    _run_main({"unrated": ["New, Hire"]}, [])
    out = capsys.readouterr()
    assert out.out.splitlines() == ["--date 2026-10-01 --force-scrape"]
    assert "unrated=['New, Hire']" in out.err


def test_main_excluded_unrated_is_ignored(capsys):
    _run_main({"unrated": ["Owner, Salaried"]}, [], excluded=["Owner, Salaried"])
    assert capsys.readouterr().out == ""


def test_main_rate_scraper_change_triggers_scrape(capsys, tmp_path):
    changed = tmp_path / "files.txt"
    changed.write_text("skills/adp_run_automation/pay_info_backend.py\n")
    _run_main({}, ["--changed-files", str(changed)])
    out = capsys.readouterr()
    assert out.out.splitlines() == [
        "--date 2026-10-01 --force-scrape --model-scope-from 2026-09-07"]
    assert "rate_scraper_changed=True" in out.err


def test_bq_error_prints_nothing_and_exits_zero(capsys):
    with mock.patch.object(g, "_fetch", side_effect=RuntimeError("no creds")):
        assert g.main([]) == 0
    assert capsys.readouterr().out == ""


def test_deploy_step_runs_the_plan():
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/deploy.yml").read_text()
    assert "detect_gap_dates.py --changed-files changed-files.txt" in wf
    assert "pulls/$PR/files" in wf
    assert "python3 scripts/trigger_dated_refresh.py $ARGS" in wf
