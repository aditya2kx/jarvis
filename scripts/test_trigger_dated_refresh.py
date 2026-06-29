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
    assert env["BHAGA_IGNORE_HALT"] == "1"


def test_recompute_env_injects_force_model_recompute():
    """recompute_only must inject BHAGA_FORCE_MODEL_RECOMPUTE so daily_refresh clears markers."""
    env = dict(t._build_env_overrides("2026-06-13", recompute_only=True))
    assert env.get("BHAGA_FORCE_MODEL_RECOMPUTE") == "1", (
        "BHAGA_FORCE_MODEL_RECOMPUTE must be '1' in recompute-only mode"
    )


def test_scrape_env_does_not_inject_force_model_recompute():
    """Full-scrape mode must NOT inject BHAGA_FORCE_MODEL_RECOMPUTE."""
    env = dict(t._build_env_overrides("2026-06-14", recompute_only=False))
    assert "BHAGA_FORCE_MODEL_RECOMPUTE" not in env, (
        "BHAGA_FORCE_MODEL_RECOMPUTE must not be set for full-scrape runs"
    )


def test_scrape_env_starts_inline_no_force_request():
    # Full-scrape dates start inline (no READY handshake) — BHAGA_OTP_FORCE_REQUEST
    # is no longer injected; the default gate mode handles the OTP automatically.
    env = dict(t._build_env_overrides("2026-06-14", recompute_only=False))
    assert env["REFRESH_DATE"] == "2026-06-14"
    assert "BHAGA_OTP_FORCE_REQUEST" not in env
    assert "BHAGA_SKIP_SQUARE" not in env
    assert env["BHAGA_IGNORE_HALT"] == "1"


def test_recompute_env_has_no_force_flag():
    env = dict(t._build_env_overrides("2026-06-13", recompute_only=True))
    assert "BHAGA_OTP_FORCE_REQUEST" not in env


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


# ---------------------------------------------------------------------------
# B1: ADP-aware coverage logic
# ---------------------------------------------------------------------------



def _make_covered_client(monkeypatch, sq_max, adp_max):
    """Monkeypatch _max_date_in_table and bigquery.Client import."""
    import datetime as _dt

    def fake_max(client, table, date_col):
        if table == "square_daily_rollup":
            return sq_max
        if table == "adp_shifts":
            return adp_max
        return None

    monkeypatch.setattr(t, "_max_date_in_table", fake_max)


def test_both_covered_is_recompute_only(monkeypatch):
    """Square and ADP both cover 2026-06-28 → recompute-only."""
    import datetime

    _make_covered_client(
        monkeypatch,
        sq_max=datetime.date(2026, 6, 28),
        adp_max=datetime.date(2026, 6, 28),
    )

    class _FakeBQModule:
        class Client:
            def __init__(self, **kw): pass

    import unittest.mock as mock
    with mock.patch.dict("sys.modules", {"google.cloud.bigquery": _FakeBQModule}):
        result = t._date_is_covered("2026-06-28")
    assert result is True, "Both covered → recompute-only"


def test_square_covered_adp_missing_is_full_scrape(monkeypatch):
    """Square covers 2026-06-28 but ADP is missing → full scrape (tonight's bug)."""
    import datetime

    _make_covered_client(
        monkeypatch,
        sq_max=datetime.date(2026, 6, 28),
        adp_max=datetime.date(2026, 6, 27),  # one day behind
    )

    class _FakeBQModule:
        class Client:
            def __init__(self, **kw): pass

    import unittest.mock as mock
    with mock.patch.dict("sys.modules", {"google.cloud.bigquery": _FakeBQModule}):
        result = t._date_is_covered("2026-06-28")
    assert result is False, "ADP missing → full scrape (not recompute-only)"


def test_neither_covered_is_full_scrape(monkeypatch):
    """Neither Square nor ADP covers the date → full scrape."""
    import datetime

    _make_covered_client(
        monkeypatch,
        sq_max=datetime.date(2026, 6, 27),
        adp_max=datetime.date(2026, 6, 27),
    )

    class _FakeBQModule:
        class Client:
            def __init__(self, **kw): pass

    import unittest.mock as mock
    with mock.patch.dict("sys.modules", {"google.cloud.bigquery": _FakeBQModule}):
        result = t._date_is_covered("2026-06-28")
    assert result is False, "Neither covered → full scrape"


def test_adp_covered_square_missing_is_full_scrape(monkeypatch):
    """ADP covers but Square doesn't → full scrape."""
    import datetime

    _make_covered_client(
        monkeypatch,
        sq_max=datetime.date(2026, 6, 27),
        adp_max=datetime.date(2026, 6, 28),
    )

    class _FakeBQModule:
        class Client:
            def __init__(self, **kw): pass

    import unittest.mock as mock
    with mock.patch.dict("sys.modules", {"google.cloud.bigquery": _FakeBQModule}):
        result = t._date_is_covered("2026-06-28")
    assert result is False, "Square missing → full scrape"


def test_bq_probe_error_fails_open_to_full_scrape(monkeypatch):
    """If _max_date_in_table returns None (BQ error), fails open → full scrape."""
    _make_covered_client(monkeypatch, sq_max=None, adp_max=None)

    class _FakeBQModule:
        class Client:
            def __init__(self, **kw): pass

    import unittest.mock as mock
    with mock.patch.dict("sys.modules", {"google.cloud.bigquery": _FakeBQModule}):
        result = t._date_is_covered("2026-06-28")
    assert result is False, "BQ error → fail-open to full scrape"


def test_decide_uses_adp_aware_coverage(monkeypatch):
    """_decide_recompute delegates to _date_is_covered (which is now ADP-aware)."""
    monkeypatch.setattr(t, "_date_is_covered", lambda d: False)
    assert t._decide_recompute("2026-06-28", force_recompute=False, force_scrape=False) is False

    monkeypatch.setattr(t, "_date_is_covered", lambda d: True)
    assert t._decide_recompute("2026-06-28", force_recompute=False, force_scrape=False) is True
