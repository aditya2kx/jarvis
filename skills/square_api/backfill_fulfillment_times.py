#!/usr/bin/env python3
"""Backfill square_transactions fulfillment + ops-clock columns (migration 056).

Pulls existing ``transaction_id`` values from BigQuery, batch-retrieves Square
Orders, extracts fulfillment timestamps via ``skills.square_api.fulfillment``,
and MERGE-updates only the new columns (does not rewrite place-time / money).

Usage:
    BHAGA_SECRETS_BACKEND=gcp python3 -m skills.square_api.backfill_fulfillment_times \\
        --store palmetto

    # Dry-run (fetch + print sample, no BQ write):
    BHAGA_SECRETS_BACKEND=gcp python3 -m skills.square_api.backfill_fulfillment_times \\
        --store palmetto --dry-run --limit 20

    # Only rows still missing ops_at_local_iso:
    BHAGA_SECRETS_BACKEND=gcp python3 -m skills.square_api.backfill_fulfillment_times \\
        --store palmetto --missing-only
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_api.client import SquareClient
from skills.square_api.export import DEFAULT_DISPLAY_TZ, _resolve_location
from skills.square_api.fulfillment import (
    FULFILLMENT_BQ_FIELDS,
    enrich_transaction_record,
)


def _load_profile(store: str) -> dict:
    from agents.bhaga.scripts.backfill_bigquery import load_store_profile
    return load_store_profile(store)


def _fetch_txn_ids(
    *,
    start: datetime.date | None,
    end: datetime.date | None,
    missing_only: bool,
    limit: int | None,
) -> list[dict]:
    from core.datastore import fq, get_client

    client = get_client()
    if client is None:
        raise RuntimeError("BigQuery client unavailable")

    where = ["event_type = 'Payment'"]
    if missing_only:
        where.append("(ops_at_local_iso IS NULL OR ops_at_local_iso = '')")
    if start:
        where.append(f"date_local >= DATE '{start.isoformat()}'")
    if end:
        where.append(f"date_local <= DATE '{end.isoformat()}'")
    where_sql = " AND ".join(where)
    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    sql = f"""
      SELECT transaction_id, created_at_local_iso, date_local
      FROM {fq('square_transactions')}
      WHERE {where_sql}
      ORDER BY date_local, transaction_id
      {limit_sql}
    """
    return [dict(r) for r in client.query(sql).result()]


def _batch_retrieve_orders(
    client: SquareClient,
    location_id: str,
    order_ids: list[str],
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(order_ids), 100):
        chunk = order_ids[i : i + 100]
        resp = client.post("/v2/orders/batch-retrieve", body={
            "location_id": location_id,
            "order_ids": chunk,
        })
        for o in (resp.get("orders") or []):
            if o.get("id"):
                out[o["id"]] = o
        print(f"[backfill] retrieved orders {i + 1}-{i + len(chunk)} / {len(order_ids)}")
    return out


def _parse_date_val(val) -> datetime.date | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
        return val
    if isinstance(val, datetime.datetime):
        return val.date()
    return datetime.date.fromisoformat(str(val)[:10])


def backfill(
    *,
    store: str = "palmetto",
    start: datetime.date | None = None,
    end: datetime.date | None = None,
    missing_only: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    from core.datastore import ensure_schema, load_rows

    applied = ensure_schema()
    if applied:
        print(f"[backfill] applied migrations: {applied}")

    profile = _load_profile(store)
    shop_tz = profile.get("timezone", {}).get("shop_tz", "America/Chicago")
    client = SquareClient(store)
    location_id, location_name = _resolve_location(client, profile)
    print(f"[backfill] store={store} location={location_name!r} ({location_id})")

    rows = _fetch_txn_ids(
        start=start, end=end, missing_only=missing_only, limit=limit,
    )
    print(f"[backfill] {len(rows)} payment rows to enrich")
    if not rows:
        return {"updated": 0, "orders_found": 0, "scheduled": 0}

    order_ids = [r["transaction_id"] for r in rows if r.get("transaction_id")]
    orders_by_id = _batch_retrieve_orders(client, location_id, order_ids)
    print(f"[backfill] {len(orders_by_id)} orders returned from Square")

    updates: list[dict] = []
    scheduled = 0
    for r in rows:
        tid = r["transaction_id"]
        order = orders_by_id.get(tid)
        rec = {
            "transaction_id": tid,
            "created_at_local_iso": r.get("created_at_local_iso") or "",
        }
        enrich_transaction_record(rec, order, shop_tz=shop_tz)
        if (rec.get("schedule_type") or "").upper() == "SCHEDULED":
            scheduled += 1
        update = {"transaction_id": tid}
        for key in FULFILLMENT_BQ_FIELDS:
            val = rec.get(key)
            if key == "ops_date_local":
                update[key] = _parse_date_val(val)
            elif key == "ops_hour_local":
                update[key] = int(val) if val is not None and val != "" else None
            else:
                update[key] = val if val not in ("",) else None
        updates.append(update)

    if dry_run:
        sample = [u for u in updates if (u.get("schedule_type") or "").upper() == "SCHEDULED"][:5]
        if not sample:
            sample = updates[:5]
        print("[backfill] dry-run sample:")
        print(json.dumps(sample, indent=2, default=str))
        return {
            "updated": 0,
            "would_update": len(updates),
            "orders_found": len(orders_by_id),
            "scheduled": scheduled,
            "dry_run": True,
        }

    # MERGE only fulfillment columns — does not touch money / created_at_*.
    n = load_rows(
        "square_transactions",
        updates,
        merge_keys=["transaction_id"],
        column_bq_types={
            "ops_date_local": "DATE",
            "ops_hour_local": "INT64",
        },
    )
    print(f"[backfill] MERGE updated {n} rows ({scheduled} SCHEDULED)")
    return {
        "updated": n,
        "orders_found": len(orders_by_id),
        "scheduled": scheduled,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", default="palmetto")
    p.add_argument("--start", default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--missing-only", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    result = backfill(
        store=args.store,
        start=datetime.date.fromisoformat(args.start) if args.start else None,
        end=datetime.date.fromisoformat(args.end) if args.end else None,
        missing_only=args.missing_only,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
