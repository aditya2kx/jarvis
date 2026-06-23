#!/usr/bin/env python3
"""skills/square_api/ingest — Square API -> BigQuery, no downloads.

Fetches Payments + Refunds + Orders from the Square REST API, builds
in-memory rows using the same column layout that the browser-scraped CSVs
used (reusing export.py helpers), passes them through the calibrated parsers
(transactions_backend.parse_transaction_rows / parse_item_rows), applies the
same BQ mappers as backfill_bigquery, and loads directly into BigQuery via
core.datastore.load_rows. No CSV files are written; no extracted/downloads/
directory is touched.

Timezone discipline: identical to export.py — Square's UTC created_at is
converted to the account display TZ (Eastern), the matching label is emitted,
and parse_transaction_rows converts ET -> shop_tz (Central) to derive
date_local. This replicates the scrape exactly so parity holds.

Usage (CLI):
    BHAGA_SECRETS_BACKEND=gcp python3 -m skills.square_api.ingest \\
        --store palmetto --start 2026-06-01 --end 2026-06-01

    # Sandbox isolation (writes to bhaga_sandbox, reads prod Square API):
    BHAGA_SECRETS_BACKEND=gcp BHAGA_BQ_DATASET=bhaga_sandbox \\
        python3 -m skills.square_api.ingest --store palmetto \\
        --start 2026-06-01 --end 2026-06-01
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_api.client import SquareClient
from skills.square_api.export import (
    TXN_HEADER, ITEM_HEADER, DEFAULT_DISPLAY_TZ,
    _resolve_location, _team_member_map, _category_map,
    _rfc3339_day_bounds, _tz_label,
    _build_transaction_rows, _build_refund_rows,
    _build_item_rows, _build_refund_item_rows,
)


def _persist_location_id(store: str, location_id: str) -> None:
    """Write the resolved location_id back to palmetto.json if it was empty."""
    try:
        from core.config_loader import project_dir
        profile_path = (
            pathlib.Path(project_dir())
            / "agents/bhaga/knowledge-base/store-profiles"
            / f"{store.lower()}.json"
        )
        data = json.loads(profile_path.read_text())
        if not data.get("square", {}).get("location_id"):
            data.setdefault("square", {})["location_id"] = location_id
            profile_path.write_text(json.dumps(data, indent=2) + "\n")
            print(f"[ingest] persisted location_id={location_id!r} -> {profile_path.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"[ingest] WARN: could not persist location_id: {exc}", file=sys.stderr)


def ingest_window(
    *,
    start_date: datetime.date,
    end_date: datetime.date,
    store: str = "palmetto",
    client: SquareClient | None = None,
    profile: dict | None = None,
) -> dict[str, int]:
    """Fetch Payments + Refunds + Orders for [start_date, end_date] and load BQ.

    Returns row counts per table:
      {"square_transactions": N, "square_daily_rollup": N,
       "square_item_lines": N, "square_item_daily": N}
    """
    from agents.bhaga.scripts.backfill_bigquery import (
        load_store_profile, map_square_transaction, map_square_daily_rollup,
        map_square_item_line, map_square_item_daily,
    )
    from agents.bhaga.scripts.backfill_from_downloads import aggregate_square_daily, _TS_TYPES
    from skills.square_tips import transactions_backend as tb
    from core.datastore import load_rows

    profile = profile or load_store_profile(store)
    display_tz = profile.get("timezone", {}).get("square_account_display_tz", DEFAULT_DISPLAY_TZ)
    shop_tz = profile.get("timezone", {}).get("shop_tz", "America/Chicago")
    client = client or SquareClient(store)

    location_id, location_name = _resolve_location(client, profile)
    print(f"[ingest] location_id={location_id!r} location_name={location_name!r}")
    _persist_location_id(store, location_id)

    begin_time, end_time = _rfc3339_day_bounds(start_date, end_date, display_tz)
    tz_label = _tz_label(display_tz)

    payments = client.get_paginated("/v2/payments", params={
        "begin_time": begin_time, "end_time": end_time,
        "location_id": location_id, "limit": 100, "sort_order": "ASC",
    }, items_key="payments")
    # Keep only COMPLETED payments — FAILED/CANCELED payment attempts share the
    # same order_id as the eventual successful payment and would otherwise
    # create duplicate rows or corrupt amounts.
    payments = [p for p in payments if p.get("status") == "COMPLETED"]
    print(f"[ingest] fetched {len(payments)} payments")

    refunds = client.get_paginated("/v2/refunds", params={
        "begin_time": begin_time, "end_time": end_time,
        "location_id": location_id, "limit": 100, "sort_order": "ASC",
    }, items_key="refunds")
    print(f"[ingest] fetched {len(refunds)} refunds")

    order_ids = sorted(
        {p.get("order_id") for p in payments if p.get("order_id")}
        # Also include refund order_ids so we can look up their source for the
        # transaction row (refund order source → "Register" if empty).
        | {r.get("order_id") for r in refunds if r.get("order_id")}
    )
    orders_by_id: dict[str, dict] = {}
    for i in range(0, len(order_ids), 100):
        chunk = order_ids[i:i + 100]
        resp = client.post("/v2/orders/batch-retrieve", body={
            "location_id": location_id, "order_ids": chunk,
        })
        for o in (resp.get("orders") or []):
            orders_by_id[o.get("id")] = o

    # Filter out canceled orders — the dashboard CSV excludes them.
    canceled_order_ids = {oid for oid, o in orders_by_id.items() if o.get("state") == "CANCELED"}
    if canceled_order_ids:
        print(f"[ingest] skipping {len(canceled_order_ids)} canceled orders: {canceled_order_ids}")
        payments = [p for p in payments if p.get("order_id") not in canceled_order_ids]
        refunds = [r for r in refunds if r.get("order_id") not in canceled_order_ids]
        for oid in canceled_order_ids:
            orders_by_id.pop(oid, None)
    print(f"[ingest] fetched {len(orders_by_id)} non-canceled orders")

    team = _team_member_map(client)
    categories = _category_map(client, list(orders_by_id.values()))

    txn_rows = _build_transaction_rows(payments, orders_by_id, team, location_name,
                                       display_tz, tz_label)
    txn_rows += _build_refund_rows(refunds, orders_by_id, team, location_name,
                                   display_tz, tz_label, client=client)
    item_rows = _build_item_rows(payments, orders_by_id, team, categories, location_name,
                                 display_tz, tz_label)
    item_rows += _build_refund_item_rows(refunds, orders_by_id, team, categories, location_name,
                                         display_tz, tz_label, client=client)

    txns = tb.parse_transaction_rows([TXN_HEADER] + txn_rows, shop_tz=shop_tz)
    txns = [t for t in txns
            if start_date.isoformat() <= t["date_local"] <= end_date.isoformat()]
    items = tb.parse_item_rows([ITEM_HEADER] + item_rows, shop_tz=shop_tz)
    items = [r for r in items
             if start_date.isoformat() <= r["date_local"] <= end_date.isoformat()]
    print(f"[ingest] parsed {len(txns)} txns, {len(items)} item lines (after date filter)")

    counts: dict[str, int] = {}

    bq_txns = [map_square_transaction(r) for r in txns]
    bq_txns = [r for r in bq_txns if r.get("date_local")]
    counts["square_transactions"] = load_rows(
        "square_transactions", bq_txns,
        merge_keys=["transaction_id"],
        column_bq_types=_TS_TYPES,
    )

    roll = [map_square_daily_rollup(r) for r in aggregate_square_daily(txns)]
    roll = [r for r in roll if r.get("date_local")]
    counts["square_daily_rollup"] = load_rows(
        "square_daily_rollup", roll,
        merge_keys=["date_local"],
        column_bq_types=_TS_TYPES,
    )

    bq_lines = [map_square_item_line(r) for r in items]
    bq_lines = [r for r in bq_lines if r.get("date_local")]
    counts["square_item_lines"] = load_rows(
        "square_item_lines", bq_lines,
        merge_keys=["transaction_id", "item_name", "item_sold_at_local", "line_seq"],
        column_bq_types=_TS_TYPES,
    )

    bq_idaily = [map_square_item_daily(r) for r in tb.aggregate_daily_item_stats(items)]
    bq_idaily = [r for r in bq_idaily if r.get("date_local")]
    counts["square_item_daily"] = load_rows(
        "square_item_daily", bq_idaily,
        merge_keys=["date_local"],
        column_bq_types=_TS_TYPES,
    )

    print(f"[ingest] BQ row counts: {counts}")
    return counts


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Square API -> BigQuery ingest (no downloads)")
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--start", required=True, help="YYYY-MM-DD")
    cli.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = cli.parse_args()

    result = ingest_window(
        start_date=datetime.date.fromisoformat(args.start),
        end_date=datetime.date.fromisoformat(args.end),
        store=args.store,
    )
    print(json.dumps(result, indent=2))
