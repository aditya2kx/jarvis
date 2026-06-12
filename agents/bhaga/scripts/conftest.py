"""Shared fixtures for agents/bhaga/scripts tests."""
import pytest


@pytest.fixture(autouse=True)
def _stub_pipeline_recorder(request, monkeypatch):
    """Prevent any test that reaches daily_refresh.main() from writing real
    rows to prod BQ pipeline_runs/source_pulls. status.py sets
    BHAGA_DATASTORE=bigquery at import (process-wide), so the recorder's env
    gate is not a safe test barrier. test_pipeline_runs_recorder is exempt —
    it tests the recorder itself with core.datastore.load_rows mocked."""
    import agents.bhaga.scripts.daily_refresh as dr
    dr._RUN_SUMMARY.clear()
    if request.module.__name__.split(".")[-1] != "test_pipeline_runs_recorder":
        monkeypatch.setattr(dr, "_record_pipeline_run", lambda **kw: None)
    yield
