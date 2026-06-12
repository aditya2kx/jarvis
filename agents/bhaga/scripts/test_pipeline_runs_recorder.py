"""Unit tests for the pipeline_runs recorder in daily_refresh.

Tests cover _record_pipeline_run() and its integration inside main().
All tests mock core.datastore.load_rows where it is imported inside the
recorder (via the lazy `from core.datastore import load_rows` inside the
function body) and manipulate _RUN_SUMMARY directly.
"""
from __future__ import annotations

import datetime
import importlib
import sys
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

import agents.bhaga.scripts.daily_refresh as dr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REF_DATE = datetime.date(2026, 6, 12)
_STARTED = datetime.datetime(2026, 6, 12, 2, 0, 0, tzinfo=datetime.timezone.utc)


def _reset_summary(**kwargs: Any) -> None:
    """Clear _RUN_SUMMARY and optionally seed it."""
    dr._RUN_SUMMARY.clear()
    dr._RUN_SUMMARY.update(kwargs)


def _make_started() -> datetime.datetime:
    return _STARTED


# ---------------------------------------------------------------------------
# Scenario 1: Happy path — exit_code=0 → status="success"
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_success_row_written(self, monkeypatch):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        captured: list[dict] = []

        def fake_load_rows(table, rows, **kw):
            captured.extend(rows)
            return len(rows)

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0)

        assert len(captured) == 1
        row = captured[0]
        assert row["status"] == "success"
        assert row["failed_step"] is None
        assert row["exit_code"] == 0
        assert row["run_date"] == _REF_DATE.isoformat()
        assert row["store"] == "palmetto"


# ---------------------------------------------------------------------------
# Scenario 2: Failure — _record_failure sets failed_step; setdefault on
#             second call must NOT overwrite the first.
# ---------------------------------------------------------------------------


class TestFailurePath:
    def test_failed_step_captured(self, monkeypatch):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        # Simulate _record_failure being called for the first failure
        exc = RuntimeError("bq write failed")
        # Call _record_failure with a mocked adapter so it doesn't actually hit Firestore
        with patch("agents.bhaga.scripts.daily_refresh._adapter_record_step_failure"):
            with patch("agents.bhaga.scripts.daily_refresh.evidence_prefix", return_value=None):
                dr._record_failure(_REF_DATE, "load_raw_bigquery", exc)

        assert dr._RUN_SUMMARY.get("failed_step") == "load_raw_bigquery"

        # Second call must NOT overwrite (setdefault semantics)
        with patch("agents.bhaga.scripts.daily_refresh._adapter_record_step_failure"):
            with patch("agents.bhaga.scripts.daily_refresh.evidence_prefix", return_value=None):
                dr._record_failure(_REF_DATE, "render_raw_sheets", RuntimeError("second"))

        assert dr._RUN_SUMMARY.get("failed_step") == "load_raw_bigquery"

        # Recorder writes the correct status
        captured: list[dict] = []

        def fake_load_rows(table, rows, **kw):
            captured.extend(rows)
            return len(rows)

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=1)

        assert captured[0]["status"] == "failed"
        assert captured[0]["failed_step"] == "load_raw_bigquery"


# ---------------------------------------------------------------------------
# Scenario 3: OTP pending — status_override="otp_pending", exit_code=0
# ---------------------------------------------------------------------------


class TestOtpPending:
    def test_otp_pending_status(self, monkeypatch):
        _reset_summary(
            refresh_date=_REF_DATE, store="palmetto", dry_run=False,
            status_override="otp_pending",
        )
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        captured: list[dict] = []

        def fake_load_rows(table, rows, **kw):
            captured.extend(rows)
            return len(rows)

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0)

        assert captured[0]["status"] == "otp_pending"
        assert captured[0]["exit_code"] == 0


# ---------------------------------------------------------------------------
# Scenario 4: Halted — exit_code=EXIT_HALTED (3) → status="halted"
# ---------------------------------------------------------------------------


class TestHalted:
    def test_halted_status(self, monkeypatch):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        captured: list[dict] = []

        def fake_load_rows(table, rows, **kw):
            captured.extend(rows)
            return len(rows)

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(
                started_at_utc=_make_started(), exit_code=dr.EXIT_HALTED
            )

        assert captured[0]["status"] == "halted"
        assert captured[0]["exit_code"] == dr.EXIT_HALTED


# ---------------------------------------------------------------------------
# Scenario 5: Gating — load_rows must NOT be called when:
#   a) BHAGA_DATASTORE != "bigquery"
#   b) dry_run=True
#   c) refresh_date absent in _RUN_SUMMARY
# ---------------------------------------------------------------------------


class TestGating:
    def test_no_bq_datastore(self, monkeypatch):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.delenv("BHAGA_DATASTORE", raising=False)

        mock_load = MagicMock()
        with patch("core.datastore.load_rows", mock_load):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0)

        mock_load.assert_not_called()

    def test_dry_run(self, monkeypatch):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=True)
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        mock_load = MagicMock()
        with patch("core.datastore.load_rows", mock_load):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0)

        mock_load.assert_not_called()

    def test_no_refresh_date(self, monkeypatch):
        _reset_summary()  # empty — no refresh_date
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        mock_load = MagicMock()
        with patch("core.datastore.load_rows", mock_load):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0)

        mock_load.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 6: Never raises — load_rows raises; recorder swallows; main()
#             still returns _run_refresh()'s rc.
# ---------------------------------------------------------------------------


class TestNeverRaises:
    def test_load_rows_exception_swallowed(self, monkeypatch):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        def exploding_load_rows(table, rows, **kw):
            raise RuntimeError("BQ connection failed")

        # Must not raise
        with patch("core.datastore.load_rows", exploding_load_rows):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0)

    def test_main_returns_run_refresh_rc(self, monkeypatch):
        """main() must propagate _run_refresh()'s return code."""
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        # Patch _run_refresh to return 0 cleanly
        with patch.object(dr, "_run_refresh", return_value=0):
            with patch.object(dr, "_record_pipeline_run"):
                rc = dr.main()

        assert rc == 0

    def test_main_returns_run_refresh_rc_failure(self, monkeypatch):
        """main() propagates non-zero rc from _run_refresh()."""
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        with patch.object(dr, "_run_refresh", return_value=1):
            with patch.object(dr, "_record_pipeline_run"):
                rc = dr.main()

        assert rc == 1
