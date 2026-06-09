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


class _FakeBlob:
    def __init__(self, name):
        self.name = name
        self.downloaded_to = None

    def download_to_filename(self, path):
        self.downloaded_to = path


class _FakeBucket:
    def __init__(self, blobs):
        self._blobs = blobs

    def list_blobs(self, prefix=None, max_results=None):
        return [b for b in self._blobs if b.name.startswith(prefix or "")]


class _FakeSessionBlob:
    def __init__(self, name, *, present):
        self.name = name
        self._present = present
        self.deleted = False

    def exists(self):
        return self._present

    def delete(self):
        if not self._present:
            raise AssertionError("delete() called on a missing blob")
        self.deleted = True


class _FakeSessionBucket:
    def __init__(self, blob):
        self._blob = blob

    def blob(self, name):
        assert name == self._blob.name, f"unexpected blob {name!r}"
        return self._blob


class TestDeleteSession:
    """delete_session drops a poisoned trusted-device session so the next login
    starts fresh. Idempotent (missing blob = no-op) and never raises."""

    def test_deletes_present_session(self, monkeypatch):
        blob = _FakeSessionBlob("_session/square-palmetto.json", present=True)
        monkeypatch.setattr(gcs_cache, "_get_client", lambda: object())
        monkeypatch.setattr(gcs_cache, "_write_bucket", lambda client: _FakeSessionBucket(blob))
        assert gcs_cache.delete_session(portal="square", store="palmetto") is True
        assert blob.deleted is True

    def test_noop_when_absent(self, monkeypatch):
        blob = _FakeSessionBlob("_session/square-palmetto.json", present=False)
        monkeypatch.setattr(gcs_cache, "_get_client", lambda: object())
        monkeypatch.setattr(gcs_cache, "_write_bucket", lambda client: _FakeSessionBucket(blob))
        assert gcs_cache.delete_session(portal="square", store="palmetto") is False
        assert blob.deleted is False

    def test_never_raises_on_error(self, monkeypatch):
        def _boom():
            raise RuntimeError("GCS down")

        monkeypatch.setattr(gcs_cache, "_get_client", _boom)
        # Swallows the error and reports "nothing deleted" rather than masking the
        # real login failure with a GCS exception.
        assert gcs_cache.delete_session(portal="square", store="palmetto") is False


class TestDownloadCachedFilesNameFilter:
    """The name_contains filter must actually restrict WHICH blobs are
    downloaded (the per-call bandwidth bound the earnings loader relies on),
    not merely be accepted as a kwarg."""

    def _patch_client(self, monkeypatch, blobs):
        bucket = _FakeBucket(blobs)
        monkeypatch.setattr(gcs_cache, "_get_client", lambda: object())
        monkeypatch.setattr(gcs_cache, "_bucket", lambda client: bucket)
        return blobs

    def test_only_matching_blobs_downloaded(self, monkeypatch, tmp_path):
        d = datetime.date(2026, 6, 1)
        blobs = self._patch_client(monkeypatch, [
            _FakeBlob("2026-06-01/adp/Earnings-and-Hours-V1-2026-06-02.xlsx"),
            _FakeBlob("2026-06-01/adp/Timecard-2026-06-02.xlsx"),
        ])
        restored = gcs_cache.download_cached_files(
            refresh_date=d, download_dir=tmp_path, name_contains="Earnings",
        )
        # Only the Earnings blob is restored; the Timecard is never downloaded.
        assert list(restored) == ["2026-06-01/adp/Earnings-and-Hours-V1-2026-06-02.xlsx"]
        assert blobs[0].downloaded_to is not None
        assert blobs[1].downloaded_to is None

    def test_no_filter_downloads_all(self, monkeypatch, tmp_path):
        d = datetime.date(2026, 6, 1)
        blobs = self._patch_client(monkeypatch, [
            _FakeBlob("2026-06-01/adp/Earnings-1.xlsx"),
            _FakeBlob("2026-06-01/adp/Timecard-1.xlsx"),
        ])
        restored = gcs_cache.download_cached_files(refresh_date=d, download_dir=tmp_path)
        assert len(restored) == 2
        assert all(b.downloaded_to is not None for b in blobs)
