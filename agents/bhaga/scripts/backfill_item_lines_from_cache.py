#!/usr/bin/env python3
"""Replay cached Square Item Sales CSVs into BHAGA Square Raw > item_lines.

No browser scrape / OTP — by default reads only from GCS ``bhaga-scrape-cache``
(cloud-primary). Never touches ``extracted/downloads/`` unless you pass
``--local-only`` (unit tests / offline dev only).

Usage:
    python3 -m agents.bhaga.scripts.backfill_item_lines_from_cache --store palmetto
    python3 -m agents.bhaga.scripts.backfill_item_lines_from_cache --store palmetto --dry-run
    python3 -m agents.bhaga.scripts.backfill_item_lines_from_cache --store palmetto --local-only

See RUNBOOK.md § "Run a one-off backfill against prod" and
.cursor/rules/bhaga.mdc § Operational rules.
"""

from __future__ import annotations

import argparse
import atexit
import datetime
import json
import os
import pathlib
import shutil
import sys
import tempfile
from typing import Iterator

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core.config_loader import project_dir, resolve_sheet_id
from skills.square_tips import transactions_backend
from skills.tip_ledger_writer.writer import write_raw_square_item_lines

STORE_PROFILE_DIR = (
    pathlib.Path(project_dir()) / "agents" / "bhaga" / "knowledge-base" / "store-profiles"
)

DOWNLOAD_DIR = pathlib.Path(project_dir()) / "extracted" / "downloads"
BATCH_SIZE = 2000


def load_store_profile(store: str) -> dict:
    path = STORE_PROFILE_DIR / f"{store}.json"
    if not path.exists():
        raise FileNotFoundError(f"Store profile not found: {path}")
    return json.loads(path.read_text())


def _natural_key(rec: dict) -> tuple:
    return (
        rec["transaction_id"],
        rec["item_name"],
        rec["item_sold_at_local"],
        int(rec["line_seq"]),
    )


def _dedupe_records(records: list[dict]) -> list[dict]:
    by_key: dict[tuple, dict] = {}
    for rec in records:
        by_key[_natural_key(rec)] = rec
    return list(by_key.values())


def _iter_local_item_csvs(download_dir: pathlib.Path) -> Iterator[pathlib.Path]:
    yield from sorted(download_dir.glob("items-*.csv"))


def _iter_gcs_item_csvs(*, required: bool) -> Iterator[pathlib.Path]:
    try:
        from google.cloud import storage
    except ImportError as exc:
        if required:
            raise SystemExit(
                "ERROR: google-cloud-storage is required for GCS replay "
                "(pip install google-cloud-storage). Use --local-only only for "
                "offline dev/tests."
            ) from exc
        print("WARN: google-cloud-storage not installed — skipping GCS")
        return

    bucket_name = os.environ.get("BHAGA_GCS_CACHE_BUCKET", "bhaga-scrape-cache")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    seen_names: set[str] = set()
    # Cache layout is {refresh_date}/square/items-*.csv — date is the FIRST path
    # segment, so there's no single prefix that captures every day's items; we
    # list the (scrape-cache-only) bucket and filter by the /square/ segment.
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="bhaga-items-cache-"))
    atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
    print(f"# GCS cache bucket: {bucket_name}")

    for blob in bucket.list_blobs():
        name = blob.name
        if "/square/" not in name:
            continue
        filename = name.split("/")[-1]
        if not filename.startswith("items-") or not filename.endswith(".csv"):
            continue
        if filename in seen_names:
            continue
        seen_names.add(filename)
        local_path = tmp_dir / filename
        try:
            blob.download_to_filename(str(local_path))
            yield local_path
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN: failed to download {name}: {exc}")


def _parse_csv_paths(
    paths: list[pathlib.Path],
    *,
    shop_tz: str,
) -> tuple[list[dict], set[str]]:
    all_records: list[dict] = []
    dates_seen: set[str] = set()
    for path in paths:
        print(f"# parsing {path.name}")
        recs = transactions_backend.parse_item_sales_csv(path, shop_tz=shop_tz)
        for r in recs:
            dates_seen.add(r["date_local"])
        all_records.extend(recs)
    return _dedupe_records(all_records), dates_seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", default="palmetto")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--local-only",
        action="store_true",
        help="DEV/TESTS ONLY: read extracted/downloads instead of GCS. "
             "Never use for prod sheet writes.",
    )
    ap.add_argument(
        "--download-dir",
        type=pathlib.Path,
        default=DOWNLOAD_DIR,
        help="With --local-only: directory containing items-*.csv",
    )
    args = ap.parse_args()

    profile = load_store_profile(args.store)
    shop_tz = profile.get("timezone", {}).get("shop_tz", "America/Chicago")

    paths: list[pathlib.Path] = []
    if args.local_only:
        paths.extend(list(_iter_local_item_csvs(args.download_dir)))
        source = f"local {args.download_dir}"
    else:
        paths.extend(list(_iter_gcs_item_csvs(required=True)))
        source = "GCS bhaga-scrape-cache"

    if not paths:
        print(f"ERROR: no items-*.csv found in {source}")
        return 1

    print(f"# source: {source}")
    print(f"# found {len(paths)} item sales CSV file(s)")
    records, dates_seen = _parse_csv_paths(paths, shop_tz=shop_tz)
    if not records:
        print("ERROR: parsed zero item lines")
        return 1

    first_date = min(dates_seen)
    last_date = max(dates_seen)
    print(f"# parsed {len(records)} unique item lines")
    print(f"# date coverage: {first_date} .. {last_date} ({len(dates_seen)} days)")

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "files": len(paths),
            "unique_lines": len(records),
            "first_date_covered": first_date,
            "last_date_covered": last_date,
            "days_covered": len(dates_seen),
        }, indent=2))
        return 0

    square_raw_sid = resolve_sheet_id("bhaga_square_raw", profile)
    summaries: list[dict] = []
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        s = write_raw_square_item_lines(square_raw_sid, batch, account=args.store)
        summaries.append(s)
        print(
            f"  batch {i // BATCH_SIZE + 1}: +{s['inserted']} new, "
            f"{s['updated']} updated, {s['total_after']} total"
        )

    print()
    print("SUMMARY")
    print(json.dumps({
        "first_date_covered": first_date,
        "last_date_covered": last_date,
        "unique_lines": len(records),
        "batches": len(summaries),
        "last_batch": summaries[-1] if summaries else {},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
