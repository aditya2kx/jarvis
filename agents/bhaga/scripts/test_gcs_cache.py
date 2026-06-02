"""Tests for gcs_cache — sandbox write-isolation guard, write-bucket routing,
and the deterministic evidence prefix. These exercise pure helpers only (no live
GCS client), so they run anywhere."""

from __future__ import annotations

import datetime

import pytest

from agents.bhaga.scripts import gcs_cache


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for var in (
        "BHAGA_SHEET_MODE",
        "BHAGA_GCS_CACHE_WRITE_BUCKET",
    ):
        monkeypatch.delenv(var, raising=False)


class TestWriteBucketRouting:
    def test_defaults_to_read_bucket(self):
        assert gcs_cache._write_bucket_name() == gcs_cache.BUCKET_NAME

    def test_honors_write_override(self, monkeypatch):
        monkeypatch.setenv("BHAGA_GCS_CACHE_WRITE_BUCKET", "bhaga-scrape-cache-sandbox")
        assert gcs_cache._write_bucket_name() == "bhaga-scrape-cache-sandbox"


class TestSandboxWriteIsolation:
    def test_noop_when_not_staging(self):
        # Prod (no staging) may write the prod cache bucket.
        gcs_cache._assert_sandbox_write_isolation(gcs_cache._PROD_CACHE_BUCKET)

    def test_blocks_prod_bucket_in_staging(self, monkeypatch):
        monkeypatch.setenv("BHAGA_SHEET_MODE", "staging")
        with pytest.raises(RuntimeError, match="production"):
            gcs_cache._assert_sandbox_write_isolation(gcs_cache._PROD_CACHE_BUCKET)

    def test_allows_sandbox_bucket_in_staging(self, monkeypatch):
        monkeypatch.setenv("BHAGA_SHEET_MODE", "staging")
        gcs_cache._assert_sandbox_write_isolation("bhaga-scrape-cache-sandbox")

    def test_write_bucket_factory_guards_in_staging(self, monkeypatch):
        monkeypatch.setenv("BHAGA_SHEET_MODE", "staging")
        # No write-bucket override → resolves to the prod bucket → must raise
        # before any client call.
        with pytest.raises(RuntimeError):
            gcs_cache._write_bucket(object())


class TestEvidencePrefix:
    def test_prefix_shape_default_bucket(self):
        d = datetime.date(2026, 5, 31)
        prefix = gcs_cache.evidence_prefix(d)
        assert prefix == f"gs://{gcs_cache.BUCKET_NAME}/2026-05-31/evidence/"

    def test_prefix_uses_write_bucket_in_sandbox(self, monkeypatch):
        monkeypatch.setenv("BHAGA_SHEET_MODE", "staging")
        monkeypatch.setenv("BHAGA_GCS_CACHE_WRITE_BUCKET", "bhaga-scrape-cache-sandbox")
        prefix = gcs_cache.evidence_prefix(datetime.date(2026, 6, 1))
        assert prefix == "gs://bhaga-scrape-cache-sandbox/2026-06-01/evidence/"
