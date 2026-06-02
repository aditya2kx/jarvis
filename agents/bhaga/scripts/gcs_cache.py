"""GCS file cache for scraped data — eliminates re-scrape on downstream failures.

Cloud Run Jobs have ephemeral filesystems. After a successful scrape (Square CSV,
ADP XLSX), this module uploads the raw files to a GCS bucket keyed by refresh_date.
On re-runs where the Firestore marker says "done" but local files are missing,
the orchestrator downloads from GCS instead of re-scraping — no OTP cost.

Bucket layout:
    gs://bhaga-scrape-cache/{refresh_date}/square/transactions-*.csv
    gs://bhaga-scrape-cache/{refresh_date}/square/transactions-master.csv
    gs://bhaga-scrape-cache/{refresh_date}/square/items-*.csv
    gs://bhaga-scrape-cache/{refresh_date}/square/kds-*.csv
    gs://bhaga-scrape-cache/{refresh_date}/adp/Timecard-*.xlsx
    gs://bhaga-scrape-cache/{refresh_date}/adp/Earnings-*.xlsx

The service account needs roles/storage.objectUser on the bucket.
"""

from __future__ import annotations

import datetime
import os
import pathlib

try:
    from google.cloud import storage as _gcs
except ImportError:
    _gcs = None

BUCKET_NAME = os.environ.get("BHAGA_GCS_CACHE_BUCKET", "bhaga-scrape-cache")

# The canonical PRODUCTION cache bucket. Sandbox/staging runs may READ from it
# (read-only replay) but must NEVER write to it — see the sandbox-isolation
# invariant in .cursor/rules/bhaga-principles.md. Writes in staging mode must be
# diverted to BHAGA_GCS_CACHE_WRITE_BUCKET (a sandbox bucket).
_PROD_CACHE_BUCKET = "bhaga-scrape-cache"

_CATEGORY_MAP = {
    "square": ["transactions-*.csv", "items-*.csv", "kds-*.csv"],
    "adp": ["Timecard-*.xlsx", "Earnings-*.xlsx"],
}


def _get_client():
    if _gcs is None:
        raise ImportError(
            "google-cloud-storage is not installed. "
            "Install it with: pip install google-cloud-storage"
        )
    return _gcs.Client()


def _bucket(client):
    """Bucket used for READS. May point at the prod cache even in sandbox mode
    (reading prod data from a sandbox run is allowed; writing it is not)."""
    return client.bucket(BUCKET_NAME)


def _write_bucket_name() -> str:
    """Bucket that WRITES (cache uploads, evidence) go to.

    Defaults to the read bucket, but a sandbox/staging run sets
    BHAGA_GCS_CACHE_WRITE_BUCKET to its own bucket so it never mutates the prod
    cache. Reads may still come from the prod bucket via BUCKET_NAME.
    """
    return os.environ.get("BHAGA_GCS_CACHE_WRITE_BUCKET", BUCKET_NAME)


def _assert_sandbox_write_isolation(bucket_name: str) -> None:
    """Hard guard: in staging/sandbox mode, block any WRITE to the prod cache.

    Mirrors ``core.config_loader._assert_not_production_sheet`` for GCS. Sandbox
    runs may read prod data sources but must never write to them (caches or
    sheets) — see .cursor/rules/bhaga-principles.md (sandbox isolation).
    """
    if os.environ.get("BHAGA_SHEET_MODE", "").lower() != "staging":
        return
    if bucket_name == _PROD_CACHE_BUCKET:
        raise RuntimeError(
            f"BLOCKED: a sandbox/staging run attempted to WRITE to the production "
            f"GCS cache bucket '{bucket_name}'. Set BHAGA_GCS_CACHE_WRITE_BUCKET to a "
            f"sandbox bucket. Sandbox runs may READ prod data but must NEVER write it "
            f"(see .cursor/rules/bhaga-principles.md — sandbox isolation)."
        )


def _write_bucket(client):
    """Bucket for WRITES, guarded so a sandbox run can never touch the prod cache."""
    name = _write_bucket_name()
    _assert_sandbox_write_isolation(name)
    return client.bucket(name)


def _blob_prefix(refresh_date: datetime.date, category: str) -> str:
    return f"{refresh_date.isoformat()}/{category}/"


def upload_file(
    local_path: pathlib.Path,
    *,
    refresh_date: datetime.date,
    category: str,
) -> str:
    """Upload a single file to the GCS cache. Returns the gs:// URI.

    Writes go to ``_write_bucket_name()`` and are blocked by
    ``_assert_sandbox_write_isolation`` from ever touching the prod cache when
    ``BHAGA_SHEET_MODE=staging``.
    """
    client = _get_client()
    blob_name = f"{_blob_prefix(refresh_date, category)}{local_path.name}"
    blob = _write_bucket(client).blob(blob_name)
    blob.upload_from_filename(str(local_path))
    uri = f"gs://{_write_bucket_name()}/{blob_name}"
    print(f"  [gcs_cache] uploaded {local_path.name} → {uri}")
    return uri


def evidence_prefix(refresh_date: datetime.date) -> str:
    """Deterministic ``gs://`` prefix where this run's failure evidence lives.

    Surfaced verbatim into the Slack failure DM and the Firestore ``runs/<date>``
    document so a postmortem has a durable anchor (screenshot / DOM / meta) WITHOUT
    a rerun or a directory listing. Honors the sandbox write bucket, so a staging
    run points at its own bucket, never the prod cache.
    """
    return f"gs://{_write_bucket_name()}/{_blob_prefix(refresh_date, 'evidence')}"


def upload_evidence(local_path: pathlib.Path, *, refresh_date: datetime.date) -> str:
    """Upload a failure-evidence artifact (screenshot / DOM / meta) under
    ``<refresh_date>/evidence/``. Returns the gs:// URI.

    Durable counterpart to the ephemeral container screenshot dir: in a Cloud
    Run Job the local ``~/.bhaga/state/screenshots`` path is discarded when the
    execution exits, so a browser failure must be reconstructable from
    ``gs://<bucket>/<date>/evidence/`` + Firestore + Cloud Run logs ALONE,
    without a rerun (see ``.cursor/rules/bhaga-principles.md`` — observability).
    """
    return upload_file(local_path, refresh_date=refresh_date, category="evidence")


_SESSION_PREFIX = "_session/"


def _session_blob_name(portal: str, store: str) -> str:
    return f"{_SESSION_PREFIX}{portal}-{store}.json"


def upload_session(local_path: pathlib.Path, *, portal: str, store: str) -> str:
    """Persist a portal browser session (Playwright ``storage_state`` cookies) so a
    later run is a 'trusted device' and skips 2FA. Returns the gs:// URI.

    Stored in the run's OWN write bucket (sandbox → sandbox bucket), so a sandbox
    run never writes its session into the prod cache and never reuses prod's live
    session — it maintains its own. Guarded by ``_assert_sandbox_write_isolation``.
    """
    client = _get_client()
    name = _session_blob_name(portal, store)
    blob = _write_bucket(client).blob(name)
    blob.upload_from_filename(str(local_path))
    uri = f"gs://{_write_bucket_name()}/{name}"
    print(f"  [gcs_cache] persisted {portal} session → {uri}")
    return uri


def download_session(dest_path: pathlib.Path, *, portal: str, store: str) -> bool:
    """Restore a persisted portal session into ``dest_path``. True on hit, False on miss.

    Reads from the run's OWN bucket (the write bucket), so a sandbox reuses its own
    trusted session — not prod's. Never raises (a miss/error just means full login).
    """
    try:
        client = _get_client()
        bucket = client.bucket(_write_bucket_name())
        blob = bucket.blob(_session_blob_name(portal, store))
        if not blob.exists():
            return False
        blob.download_to_filename(str(dest_path))
        print(f"  [gcs_cache] restored {portal} session ← "
              f"gs://{_write_bucket_name()}/{_session_blob_name(portal, store)}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [gcs_cache] WARN: {portal} session restore failed (will full-login): {exc}")
        return False


def upload_scrape_artifacts(
    *,
    refresh_date: datetime.date,
    download_dir: pathlib.Path,
    square_csv: pathlib.Path | None = None,
    master_csv: pathlib.Path | None = None,
    item_sales_csv: pathlib.Path | None = None,
    kds_csv: pathlib.Path | None = None,
    adp_timecard_xlsx: pathlib.Path | None = None,
    adp_earnings_xlsx: pathlib.Path | None = None,
) -> list[str]:
    """Upload all available scrape artifacts for a refresh_date. Returns list of gs:// URIs."""
    uploaded: list[str] = []

    for path, category in [
        (square_csv, "square"),
        (master_csv, "square"),
        (item_sales_csv, "square"),
        (kds_csv, "square"),
        (adp_timecard_xlsx, "adp"),
        (adp_earnings_xlsx, "adp"),
    ]:
        if path is not None and path.exists():
            try:
                uri = upload_file(path, refresh_date=refresh_date, category=category)
                uploaded.append(uri)
            except Exception as exc:  # noqa: BLE001
                print(f"  [gcs_cache] WARN: failed to upload {path.name}: {exc}")

    return uploaded


def download_cached_files(
    *,
    refresh_date: datetime.date,
    download_dir: pathlib.Path,
    name_contains: str | None = None,
) -> dict[str, pathlib.Path]:
    """Download all cached files for a refresh_date into the local download dir.

    Returns a dict mapping category/filename to local path for files successfully
    downloaded. Silently skips missing blobs (cache miss is not an error).

    ``name_contains`` (optional): when set, only blobs whose filename contains
    this substring are downloaded (e.g. ``"Earnings"`` to fetch just the ADP
    earnings export and skip the Timecard). Keeps bandwidth bounded for callers
    that need a single artifact rather than the whole date prefix.
    """
    client = _get_client()
    bucket = _bucket(client)
    prefix = f"{refresh_date.isoformat()}/"
    download_dir.mkdir(parents=True, exist_ok=True)

    restored: dict[str, pathlib.Path] = {}

    blobs = list(bucket.list_blobs(prefix=prefix))
    if not blobs:
        print(f"  [gcs_cache] no cached files for {refresh_date.isoformat()}")
        return restored

    for blob in blobs:
        filename = blob.name.split("/")[-1]
        if not filename:
            continue
        if name_contains is not None and name_contains not in filename:
            continue
        local_path = download_dir / filename
        if local_path.exists():
            print(f"  [gcs_cache] {filename} already on disk — skip download")
            restored[blob.name] = local_path
            continue
        try:
            blob.download_to_filename(str(local_path))
            restored[blob.name] = local_path
            print(f"  [gcs_cache] restored {blob.name} → {local_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [gcs_cache] WARN: failed to download {blob.name}: {exc}")

    return restored


def list_cached_dates() -> list[datetime.date]:
    """Return the sorted list of refresh_dates that have any cached artifacts.

    Reads the top-level ``YYYY-MM-DD/`` prefixes in the cache bucket. Used by
    the sandbox e2e to auto-select a recent, definitely-cached window so CI
    never depends on a hardcoded date range that may have aged out.
    """
    client = _get_client()
    bucket = _bucket(client)
    iterator = client.list_blobs(bucket, delimiter="/")
    # Consume the iterator so .prefixes is populated.
    for _ in iterator:
        pass
    dates: list[datetime.date] = []
    for prefix in getattr(iterator, "prefixes", []) or []:
        try:
            dates.append(datetime.date.fromisoformat(prefix.rstrip("/")))
        except ValueError:
            continue
    return sorted(dates)


def has_cached_files(refresh_date: datetime.date) -> bool:
    """Quick check: does the GCS cache have any files for this refresh_date?"""
    try:
        client = _get_client()
        bucket = _bucket(client)
        prefix = f"{refresh_date.isoformat()}/"
        blobs = list(bucket.list_blobs(prefix=prefix, max_results=1))
        return len(blobs) > 0
    except Exception:  # noqa: BLE001
        return False
