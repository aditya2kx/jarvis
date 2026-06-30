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
        monkeypatch.setenv("BHAGA_RECORD_PIPELINE_RUN", "1")

        captured: list[dict] = []

        def fake_load_rows(table, rows, **kw):
            captured.extend(rows)
            return len(rows)

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0, run_id="testrun123")

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
        monkeypatch.setenv("BHAGA_RECORD_PIPELINE_RUN", "1")

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
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=1, run_id="testrun123")

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
        monkeypatch.setenv("BHAGA_RECORD_PIPELINE_RUN", "1")

        captured: list[dict] = []

        def fake_load_rows(table, rows, **kw):
            captured.extend(rows)
            return len(rows)

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0, run_id="testrun123")

        assert captured[0]["status"] == "otp_pending"
        assert captured[0]["exit_code"] == 0


# ---------------------------------------------------------------------------
# Scenario 4: Halted — exit_code=EXIT_HALTED (3) → status="halted"
# ---------------------------------------------------------------------------


class TestHalted:
    def test_halted_status(self, monkeypatch):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.setenv("BHAGA_RECORD_PIPELINE_RUN", "1")

        captured: list[dict] = []

        def fake_load_rows(table, rows, **kw):
            captured.extend(rows)
            return len(rows)

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(
                started_at_utc=_make_started(), exit_code=dr.EXIT_HALTED,
                run_id="testrun123"
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
        monkeypatch.delenv("BHAGA_SECRETS_BACKEND", raising=False)

        mock_load = MagicMock()
        with patch("core.datastore.load_rows", mock_load):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0, run_id="testrun123")

        mock_load.assert_not_called()

    def test_dry_run(self, monkeypatch):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=True)
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        mock_load = MagicMock()
        with patch("core.datastore.load_rows", mock_load):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0, run_id="testrun123")

        mock_load.assert_not_called()

    def test_no_refresh_date(self, monkeypatch):
        _reset_summary()  # empty — no refresh_date
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        mock_load = MagicMock()
        with patch("core.datastore.load_rows", mock_load):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0, run_id="testrun123")

        mock_load.assert_not_called()


# ---------------------------------------------------------------------------
# Cloud parent-process gate (BHAGA_SECRETS_BACKEND=gcp without BHAGA_DATASTORE)
# ---------------------------------------------------------------------------


class TestCloudRecorderGate:
    def test_cloud_gcp_backend_records_without_bhaga_datastore(self, monkeypatch):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.setenv("CLOUD_RUN_JOB", "bhaga-daily-refresh")
        monkeypatch.delenv("BHAGA_DATASTORE", raising=False)

        mock_load = MagicMock(return_value=1)
        with patch("core.datastore.load_rows", mock_load):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0, run_id="testrun123")

        mock_load.assert_called()
        assert mock_load.call_args[0][0] == "pipeline_runs"

    def test_laptop_no_env_still_skips(self, monkeypatch, capsys):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.delenv("BHAGA_DATASTORE", raising=False)
        monkeypatch.delenv("BHAGA_SECRETS_BACKEND", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
        monkeypatch.delenv("BHAGA_RECORD_PIPELINE_RUN", raising=False)

        mock_load = MagicMock()
        with patch("core.datastore.load_rows", mock_load):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0, run_id="testrun123")

        mock_load.assert_not_called()
        assert "skip: not_cloud_run" in capsys.readouterr().err

    def test_cloud_with_source_pulls(self, monkeypatch):
        _reset_summary(
            refresh_date=_REF_DATE, store="palmetto", dry_run=False,
            source_pulls=[{
                "source": "square", "started_at_utc": _STARTED,
                "finished_at_utc": _STARTED, "status": "success", "error": None,
            }],
        )
        monkeypatch.setenv("CLOUD_RUN_JOB", "bhaga-daily-refresh")
        monkeypatch.delenv("BHAGA_DATASTORE", raising=False)
        captured: dict = {}
        kwargs_captured: dict = {}

        def fake_load_rows(table, rows, **kw):
            captured.setdefault(table, []).extend(rows)
            kwargs_captured[table] = kw
            return len(rows)

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_STARTED, exit_code=0, run_id="testrun123")

        assert "pipeline_runs" in captured
        assert "source_pulls" in captured
        assert kwargs_captured["pipeline_runs"]["merge_keys"] == ["run_id"]
        assert kwargs_captured["source_pulls"]["merge_keys"] == ["run_id", "source"]

    def test_staging_sandbox_blocked_from_prod_dataset(self, monkeypatch, capsys):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.setenv("CLOUD_RUN_JOB", "bhaga-daily-refresh")
        monkeypatch.setenv("BHAGA_SHEET_MODE", "staging")
        monkeypatch.delenv("BHAGA_BQ_DATASET", raising=False)
        monkeypatch.delenv("BHAGA_DATASTORE", raising=False)

        def blocked_load_rows(table, rows, **kw):
            raise RuntimeError(
                "BLOCKED: a sandbox/staging run attempted to WRITE to the production "
                "BigQuery dataset 'bhaga'."
            )

        with patch("core.datastore.load_rows", blocked_load_rows):
            dr._record_pipeline_run(started_at_utc=_STARTED, exit_code=0, run_id="testrun123")

        err = capsys.readouterr().err
        assert "WARN: could not record run outcome" in err

    def test_skip_logs_dry_run(self, monkeypatch, capsys):
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=True)
        monkeypatch.setenv("BHAGA_SECRETS_BACKEND", "gcp")

        mock_load = MagicMock()
        with patch("core.datastore.load_rows", mock_load):
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0, run_id="testrun123")

        mock_load.assert_not_called()
        assert "skip: dry_run" in capsys.readouterr().err


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
            dr._record_pipeline_run(started_at_utc=_make_started(), exit_code=0, run_id="testrun123")

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


# ---------------------------------------------------------------------------
# Scenario 7: source_pulls rows written alongside pipeline_runs row.
# ---------------------------------------------------------------------------


class TestSourcePulls:
    def _fake_load_rows_per_table(self):
        captured: dict[str, list] = {}

        kwargs_captured: dict = {}

        def fake_load_rows(table, rows, **kw):
            captured.setdefault(table, []).extend(rows)
            kwargs_captured[table] = kw
            return len(rows)

        return captured, fake_load_rows, kwargs_captured

    def test_source_pulls_rows_written(self, monkeypatch):
        _reset_summary(
            refresh_date=_REF_DATE, store="palmetto", dry_run=False,
            source_pulls=[{
                "source": "square", "started_at_utc": _STARTED,
                "finished_at_utc": _STARTED, "status": "success", "error": None,
            }],
        )
        monkeypatch.setenv("BHAGA_RECORD_PIPELINE_RUN", "1")
        captured, fake_load_rows, kwargs_captured = self._fake_load_rows_per_table()

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_STARTED, exit_code=0, run_id="testrun123")

        assert len(captured.get("pipeline_runs", [])) == 1
        assert len(captured.get("source_pulls", [])) == 1
        run_row = captured["pipeline_runs"][0]
        assert run_row["run_id"] == "testrun123"
        pull = captured["source_pulls"][0]
        assert pull["run_id"] == "testrun123"
        assert pull["source"] == "square"
        assert pull["status"] == "success"
        assert pull["error"] is None
        assert pull["run_date"] == _REF_DATE.isoformat()
        assert kwargs_captured["pipeline_runs"]["merge_keys"] == ["run_id"]
        assert kwargs_captured["source_pulls"]["merge_keys"] == ["run_id", "source"]

    def test_failed_pull_error_string(self, monkeypatch):
        _reset_summary(
            refresh_date=_REF_DATE, store="palmetto", dry_run=False,
            source_pulls=[{
                "source": "adp", "started_at_utc": _STARTED,
                "finished_at_utc": _STARTED, "status": "failed",
                "error": "RuntimeError: scrape blew up",
            }],
        )
        monkeypatch.setenv("BHAGA_RECORD_PIPELINE_RUN", "1")
        captured, fake_load_rows, kwargs_captured = self._fake_load_rows_per_table()

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_STARTED, exit_code=1, run_id="testrun123")

        pull = captured["source_pulls"][0]
        assert pull["status"] == "failed"
        assert pull["error"] == "RuntimeError: scrape blew up"

    def test_no_pulls_no_second_insert(self, monkeypatch):
        """When source_pulls absent, only pipeline_runs is inserted."""
        _reset_summary(refresh_date=_REF_DATE, store="palmetto", dry_run=False)
        monkeypatch.setenv("BHAGA_RECORD_PIPELINE_RUN", "1")
        captured, fake_load_rows, kwargs_captured = self._fake_load_rows_per_table()

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_STARTED, exit_code=0, run_id="testrun123")

        assert "pipeline_runs" in captured
        assert "source_pulls" not in captured

    def test_review_fetch_source_google_reviews_round_trips(self, monkeypatch):
        """A pull with source='google_reviews' round-trips through the recorder."""
        _reset_summary(
            refresh_date=_REF_DATE, store="palmetto", dry_run=False,
            source_pulls=[{
                "source": "google_reviews", "started_at_utc": _STARTED,
                "finished_at_utc": _STARTED, "status": "success", "error": None,
            }],
        )
        monkeypatch.setenv("BHAGA_RECORD_PIPELINE_RUN", "1")
        captured, fake_load_rows, kwargs_captured = self._fake_load_rows_per_table()

        with patch("core.datastore.load_rows", fake_load_rows):
            dr._record_pipeline_run(started_at_utc=_STARTED, exit_code=0, run_id="testrun123")

        assert captured["source_pulls"][0]["source"] == "google_reviews"

    def test_capture_timestamps_success(self):
        """_execute_pipelines stamps started/finished on a successful pipeline."""
        result = dr._execute_pipelines(
            {"square": lambda: dr.PipelineResult(name="square", success=True)},
            serialize_otp=False,
        )
        pr = result["square"]
        assert pr.started_at_utc is not None
        assert pr.finished_at_utc is not None
        assert pr.started_at_utc <= pr.finished_at_utc
        assert pr.success is True

    def test_capture_timestamps_exception(self):
        """_execute_pipelines stamps timestamps even when the pipeline raises."""
        def _raises():
            raise RuntimeError("boom")

        result = dr._execute_pipelines({"adp": _raises}, serialize_otp=False)
        pr = result["adp"]
        assert pr.started_at_utc is not None
        assert pr.finished_at_utc is not None
        assert pr.success is False
        assert isinstance(pr.error, RuntimeError)

    def test_recorder_never_raises_with_pulls(self, monkeypatch):
        """Recorder swallows exceptions even when source_pulls are present."""
        _reset_summary(
            refresh_date=_REF_DATE, store="palmetto", dry_run=False,
            source_pulls=[{
                "source": "square", "started_at_utc": _STARTED,
                "finished_at_utc": _STARTED, "status": "success", "error": None,
            }],
        )
        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        def exploding_load_rows(table, rows, **kw):
            raise RuntimeError("BQ connection failed")

        # Must not raise
        with patch("core.datastore.load_rows", exploding_load_rows):
            dr._record_pipeline_run(started_at_utc=_STARTED, exit_code=0, run_id="testrun123")


# ---------------------------------------------------------------------------
# Verify main() generates a unique 32-char hex run_id per invocation
# ---------------------------------------------------------------------------

class TestMainRunId:
    def test_main_generates_unique_run_id(self, monkeypatch):
        """main() must generate a distinct 32-char hex run_id on each call."""
        from unittest.mock import MagicMock, patch

        monkeypatch.setenv("BHAGA_DATASTORE", "bigquery")

        received_ids: list[str] = []
        mock_recorder = MagicMock(side_effect=lambda **kw: received_ids.append(kw["run_id"]))

        with (
            patch.object(dr, "_run_refresh", return_value=0),
            patch.object(dr, "_record_pipeline_run", mock_recorder),
        ):
            dr.main()
            dr.main()

        assert len(received_ids) == 2
        for rid in received_ids:
            assert len(rid) == 32
            assert rid.isalnum()
        assert received_ids[0] != received_ids[1]
