"""GCS file cache for scraped data — eliminates re-scrape on downstream failures.

Cloud Run Jobs have ephemeral filesystems. After a successful scrape (Square CSV,
ADP XLSX), this module uploads the raw files to a GCS bucket keyed by refresh_date.
On re-runs where the Firestore marker says "done" but local files are missing,
the orchestrator downloads from GCS instead of re-scraping — no OTP cost.

Bucket layout:
    gs://bhaga-scrape-cache/{refresh_date}/square/transactions-*.csv
    gs://bhaga-scrape-cache/{refresh_date}/square/transactions-master.csv
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

_CATEGORY_MAP = {
    "square": ["transactions-*.csv"],
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
    return client.bucket(BUCKET_NAME)


def _blob_prefix(refresh_date: datetime.date, category: str) -> str:
    return f"{refresh_date.isoformat()}/{category}/"


def upload_file(
    local_path: pathlib.Path,
    *,
    refresh_date: datetime.date,
    category: str,
) -> str:
    """Upload a single file to the GCS cache. Returns the gs:// URI."""
    client = _get_client()
    blob_name = f"{_blob_prefix(refresh_date, category)}{local_path.name}"
    blob = _bucket(client).blob(blob_name)
    blob.upload_from_filename(str(local_path))
    uri = f"gs://{BUCKET_NAME}/{blob_name}"
    print(f"  [gcs_cache] uploaded {local_path.name} → {uri}")
    return uri


def upload_scrape_artifacts(
    *,
    refresh_date: datetime.date,
    download_dir: pathlib.Path,
    square_csv: pathlib.Path | None = None,
    master_csv: pathlib.Path | None = None,
    adp_timecard_xlsx: pathlib.Path | None = None,
    adp_earnings_xlsx: pathlib.Path | None = None,
) -> list[str]:
    """Upload all available scrape artifacts for a refresh_date. Returns list of gs:// URIs."""
    uploaded: list[str] = []

    for path, category in [
        (square_csv, "square"),
        (master_csv, "square"),
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
) -> dict[str, pathlib.Path]:
    """Download all cached files for a refresh_date into the local download dir.

    Returns a dict mapping category/filename to local path for files successfully
    downloaded. Silently skips missing blobs (cache miss is not an error).
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
