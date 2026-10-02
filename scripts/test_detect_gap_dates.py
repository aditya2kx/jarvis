#!/usr/bin/env python3
"""Unit tests for scripts/detect_gap_dates.py (no GCP calls)."""
from __future__ import annotations

import datetime
import os
import pathlib
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scripts.detect_gap_dates as g

TODAY = datetime.date(2026, 10, 2)


def _ok(days):
    return {(TODAY - datetime.timedelta(days=n)).isoformat(): "success" for n in days}


def test_healthy_week_has_no_gaps():
    status = _ok(range(1, 8))
    assert g.gap_dates(latest_status=status, model_days=set(status), today=TODAY, lookback=7) == []


def test_failed_never_ran_and_missing_model_are_gaps():
    status = _ok(range(1, 8))
    status["2026-09-29"] = "failed"
    del status["2026-09-27"]
    model = set(status) - {"2026-09-30"}
    assert g.gap_dates(latest_status=status, model_days=model, today=TODAY, lookback=7) == [
        "2026-09-27", "2026-09-29", "2026-09-30",
    ]


def test_today_is_never_a_gap():
    """Tonight's nightly hasn't run yet; it is not a gap."""
    assert TODAY.isoformat() not in g.gap_dates(latest_status={}, model_days=set(),
                                                today=TODAY, lookback=3)


def test_only_covered_dates_are_printed(capsys):
    """Uncovered nights need a scrape (OTP) — left to the nightly, never printed."""
    with mock.patch.object(g, "_fetch", return_value=({"2026-10-01": "failed"}, set())), \
         mock.patch("trigger_dated_refresh._date_is_covered", side_effect=lambda d: d == "2026-10-01"), \
         mock.patch.object(g, "datetime") as dt:
        dt.datetime.now.return_value = datetime.datetime(2026, 10, 2, 14, 0)
        dt.timedelta = datetime.timedelta
        assert g.main(["--lookback", "2"]) == 0
    assert capsys.readouterr().out.split() == ["2026-10-01"]


def test_bq_error_prints_nothing_and_exits_zero(capsys):
    with mock.patch.object(g, "_fetch", side_effect=RuntimeError("no creds")):
        assert g.main([]) == 0
    assert capsys.readouterr().out == ""


def test_deploy_step_reruns_detected_gaps_recompute_only():
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/deploy.yml").read_text()
    assert 'GAPS="$(python3 scripts/detect_gap_dates.py || true)"' in wf
    assert 'trigger_dated_refresh.py --date "$d" --force-recompute' in wf
