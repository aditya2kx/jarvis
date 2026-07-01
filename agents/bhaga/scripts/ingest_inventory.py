"""Ingest ClickUp closing-form inventory data into bhaga.inventory_closing_daily.

Fetches tasks from the "Closing" ClickUp list, parses per-field inventory
quantities via skills/inventory_parse/parse.py, and upserts rows into
bhaga.inventory_closing_daily (idempotent MERGE on natural key).

Usage:
    # Backfill from a specific date:
    BHAGA_DATASTORE=bigquery BHAGA_BQ_DATASET=bhaga_sandbox \\
        python3 agents/bhaga/scripts/ingest_inventory.py \\
        --store palmetto --backfill-from 2026-03-20

    # Incremental (high-water mark from BQ, or today):
    BHAGA_DATASTORE=bigquery \\
        python3 agents/bhaga/scripts/ingest_inventory.py --store palmetto

    # Single date re-ingest:
    BHAGA_DATASTORE=bigquery BHAGA_BQ_DATASET=bhaga_sandbox \\
        python3 agents/bhaga/scripts/ingest_inventory.py \\
        --store palmetto --date 2026-03-27

    # Dry run (counts rows, no BQ writes):
    python3 agents/bhaga/scripts/ingest_inventory.py \\
        --store palmetto --backfill-from 2026-03-20 --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import re
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from skills.clickup_tasks.runner import LIST_CLOSING, list_tasks
from skills.inventory_parse.parse import FIELD_REGISTRY, parse_qty
from core.datastore import load_rows, get_client

# ClickUp list hosting all closing-form submissions for Palmetto Austin.
_LIST_CLOSING = LIST_CLOSING

# Natural key for idempotent MERGE upsert.
_MERGE_KEYS = ["store", "source_task_id", "field_id"]

# Regex to extract a tz-aware ISO timestamp from task names like:
#   'Form Submission - #2026-03-27T19:10:32-05:00'
_TASK_NAME_RE = re.compile(
    r"Form Submission\s*-\s*#(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2})"
)

# Timezone for submitted_date boundary (America/Chicago = business date)
_CHICAGO_UTC_OFFSET = datetime.timezone(datetime.timedelta(hours=-5))  # CST (safe default)


# ---------------------------------------------------------------------------
# Row extraction
# ---------------------------------------------------------------------------

def _parse_submitted_ts(task_name: str) -> datetime.datetime | None:
    """Extract submission timestamp from task name, return as UTC datetime or None."""
    m = _TASK_NAME_RE.search(task_name)
    if not m:
        return None
    try:
        dt = datetime.datetime.fromisoformat(m.group(1))
        return dt.astimezone(datetime.timezone.utc)
    except ValueError:
        return None


def rows_from_task(task: dict, store: str, *, run_id: str) -> list[dict]:
    """Extract inventory rows from a single ClickUp closing-form task.

    Returns one row per registered FIELD_REGISTRY entry found in the task.
    Duplicate display-name fields (e.g. two 'Mango' fields) are kept as
    separate rows distinguished by field_id — the natural key includes field_id
    so MERGE correctly deduplicates by identity, not display name.

    Fields not in FIELD_REGISTRY are silently skipped (scalable: new fields
    appear by adding to FIELD_REGISTRY, no code change here).

    Args:
        task: ClickUp task dict with 'custom_fields', 'id', 'name'.
        store: e.g. 'palmetto'.
        run_id: Pipeline run lineage string.

    Returns:
        List of row dicts ready for load_rows().
    """
    task_id = task.get("id", "")
    task_name = task.get("name", "")

    submitted_ts_utc = _parse_submitted_ts(task_name)
    if submitted_ts_utc is None:
        return []  # Not a closing form submission

    # submitted_date in America/Chicago time
    # (use -05:00 as conservative offset; accurate for CST; CDT is -06 but
    # boundary difference is negligible for nightly closing submissions)
    ct_dt = submitted_ts_utc.astimezone(datetime.timezone(datetime.timedelta(hours=-5)))
    submitted_date = ct_dt.date()

    scraped_at = datetime.datetime.now(datetime.timezone.utc)
    rows: list[dict] = []

    for field in task.get("custom_fields") or []:
        field_name = (field.get("name") or "").strip()
        if field_name not in FIELD_REGISTRY:
            continue

        category, unit = FIELD_REGISTRY[field_name]
        field_id = field.get("id") or field_name  # fallback to name if id absent

        raw_value = field.get("value")
        # ClickUp returns number fields as numeric, text fields as string
        if raw_value is None:
            raw_text = None
            qty = None
            ok = False
        elif isinstance(raw_value, (int, float)):
            raw_text = str(raw_value)
            qty = float(raw_value)
            ok = True
        else:
            raw_text = str(raw_value).strip() or None
            qty = parse_qty(raw_text)
            ok = qty is not None

        rows.append({
            "store":          store,
            "submitted_date": submitted_date.isoformat(),
            "submitted_ts":   submitted_ts_utc.isoformat(),
            "source_task_id": task_id,
            "category":       category,
            "item":           field_name,
            "field_id":       field_id,
            "field_name":     field_name,
            "raw_text":       raw_text,
            "quantity_units": qty,
            "unit":           unit,
            "parse_ok":       ok,
            "run_id":         run_id,
            "scraped_at_utc": scraped_at.isoformat(),
        })

    return rows


# ---------------------------------------------------------------------------
# High-water mark (incremental mode)
# ---------------------------------------------------------------------------

def _get_high_water_ts_ms(store: str) -> int | None:
    """Return epoch-ms of the latest submitted_ts for this store from BQ.

    Returns None if the table is empty or BQ is unavailable (triggers full
    backfill from the first day of operations).
    """
    client = get_client()
    if client is None:
        return None
    try:
        sql = (
            "SELECT UNIX_MILLIS(MAX(submitted_ts)) AS hw "
            "FROM `jarvis-bhaga-prod.{dataset}.inventory_closing_daily` "
            "WHERE store = @store"
        ).format(dataset=os.environ.get("BHAGA_BQ_DATASET", "bhaga"))
        from google.cloud import bigquery as _bq
        job_config = _bq.QueryJobConfig(
            query_parameters=[_bq.ScalarQueryParameter("store", "STRING", store)]
        )
        rows = list(client.query(sql, job_config=job_config).result())
        if rows and rows[0]["hw"] is not None:
            return int(rows[0]["hw"])
    except Exception as e:
        print(f"[ingest_inventory] Could not read high-water mark: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Main ingest function
# ---------------------------------------------------------------------------

def ingest(
    store: str = "palmetto",
    *,
    backfill_from: datetime.date | None = None,
    only_date: datetime.date | None = None,
    dry_run: bool = False,
    run_id: str | None = None,
) -> int:
    """Fetch ClickUp closing-form tasks and upsert into inventory_closing_daily.

    Args:
        store: Store identifier (default 'palmetto').
        backfill_from: If set, fetch all tasks from this date onward (epoch-ms
            derived from the date at midnight CT).  Overrides high-water.
        only_date: If set, fetch only tasks for this specific date window
            (from 00:00 CT to 23:59 CT of that date).
        dry_run: Count rows without writing to BQ.
        run_id: Pipeline lineage string; auto-generated if omitted.

    Returns:
        Number of rows upserted (or counted in dry-run mode).
    """
    if run_id is None:
        run_id = os.environ.get("BHAGA_RUN_ID") or uuid.uuid4().hex

    # Determine since_ts_ms for ClickUp API filter
    since_ts_ms: int | None = None
    if only_date is not None:
        # Fetch tasks created on or after midnight CT of only_date
        ct = datetime.timezone(datetime.timedelta(hours=-5))
        since_ts_ms = int(
            datetime.datetime(only_date.year, only_date.month, only_date.day,
                              tzinfo=ct).timestamp() * 1000
        )
    elif backfill_from is not None:
        ct = datetime.timezone(datetime.timedelta(hours=-5))
        since_ts_ms = int(
            datetime.datetime(backfill_from.year, backfill_from.month,
                              backfill_from.day, tzinfo=ct).timestamp() * 1000
        )
    else:
        since_ts_ms = _get_high_water_ts_ms(store)
        if since_ts_ms is None:
            print(
                "[ingest_inventory] No high-water mark found — performing full backfill.",
                file=sys.stderr,
            )

    print(f"[ingest_inventory] store={store} since_ts_ms={since_ts_ms} "
          f"dry_run={dry_run} run_id={run_id[:8]}...")

    tasks = list_tasks(_LIST_CLOSING, since_ts_ms=since_ts_ms)
    print(f"[ingest_inventory] Fetched {len(tasks)} task(s) from ClickUp.")

    all_rows: list[dict] = []
    skipped = 0
    for task in tasks:
        task_rows = rows_from_task(task, store, run_id=run_id)
        if not task_rows:
            skipped += 1
            continue
        # Filter to only_date if specified
        if only_date is not None:
            task_rows = [r for r in task_rows if r["submitted_date"] == only_date.isoformat()]
        all_rows.extend(task_rows)

    print(
        f"[ingest_inventory] Parsed {len(all_rows)} rows "
        f"({skipped} non-form tasks skipped)."
    )

    if dry_run:
        print("[ingest_inventory] DRY RUN — no BQ writes.")
        return len(all_rows)

    if not all_rows:
        print("[ingest_inventory] Nothing to write.")
        return 0

    n = load_rows(
        "inventory_closing_daily",
        all_rows,
        merge_keys=_MERGE_KEYS,
        column_bq_types={
            "submitted_date": "DATE",
            "submitted_ts":   "TIMESTAMP",
            "scraped_at_utc": "TIMESTAMP",
            "quantity_units": "FLOAT64",
            "parse_ok":       "BOOL",
        },
    )
    print(f"[ingest_inventory] Upserted {n} row(s) into inventory_closing_daily.")
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="palmetto",
                        help="Store identifier (default: palmetto)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--backfill-from", type=_parse_date, metavar="YYYY-MM-DD",
                       help="Backfill from this date onward")
    group.add_argument("--date", type=_parse_date, metavar="YYYY-MM-DD",
                       help="Re-ingest a specific date only")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count rows without writing to BQ")
    parser.add_argument("--run-id", default=None,
                        help="Pipeline run ID (auto-generated if omitted)")
    args = parser.parse_args()

    n = ingest(
        store=args.store,
        backfill_from=args.backfill_from,
        only_date=args.date,
        dry_run=args.dry_run,
        run_id=args.run_id,
    )
    print(f"[ingest_inventory] Done. rows={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
