#!/usr/bin/env python3
"""Unit tests for scripts/trigger_dated_refresh.py (no GCP calls)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import scripts.trigger_dated_refresh as t


def test_recompute_env_has_skip_flags():
    env = dict(t._build_env_overrides("2026-06-13", recompute_only=True))
    assert env["REFRESH_DATE"] == "2026-06-13"
    assert env["BHAGA_SKIP_SQUARE"] == "1"
    assert env["BHAGA_SKIP_ADP"] == "1"
    assert env["BHAGA_SKIP_KDS"] == "1"


def test_scrape_env_is_date_only():
    env = dict(t._build_env_overrides("2026-06-14", recompute_only=False))
    assert env == {"REFRESH_DATE": "2026-06-14"}


def test_decide_force_flags_win():
    assert t._decide_recompute("2026-06-13", force_recompute=True, force_scrape=False) is True
    assert t._decide_recompute("2026-06-13", force_recompute=False, force_scrape=True) is False


def test_decide_auto_uses_coverage(monkeypatch):
    monkeypatch.setattr(t, "_date_is_covered", lambda d: True)
    assert t._decide_recompute("2026-06-13", force_recompute=False, force_scrape=False) is True
    monkeypatch.setattr(t, "_date_is_covered", lambda d: False)
    assert t._decide_recompute("2026-06-14", force_recompute=False, force_scrape=False) is False


def test_main_dry_run_covered(monkeypatch, capsys):
    monkeypatch.setattr(t, "_date_is_covered", lambda d: True)
    rc = t.main(["--date", "2026-06-13", "--dry-run"])
    assert rc == 0
    assert "recompute-only" in capsys.readouterr().out


def test_main_rejects_bad_date():
    assert t.main(["--date", "not-a-date", "--dry-run"]) == 2
