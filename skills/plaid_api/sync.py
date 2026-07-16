#!/usr/bin/env python3
"""skills/plaid_api/sync — /transactions/sync → BigQuery upsert/delete.

Idempotent on transaction_id. Updates plaid_items.cursor after a full page
drain. Access token is never written to BQ.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.plaid_api.auth import get_access_token
from skills.plaid_api.client import PlaidClient

_PROJECT = os.environ.get("BQ_PROJECT", "jarvis-bhaga-prod")
_DATASET = os.environ.get("BQ_DATASET", "bhaga")


def _fq(table: str) -> str:
    return f"`{_PROJECT}.{_DATASET}.{table}`"


@dataclass
class SyncResult:
    item_id: str
    added: int = 0
    modified: int = 0
    removed: int = 0
    cursor: str = ""
    pages: int = 0
    errors: list[str] = field(default_factory=list)


def _pfc(txn: dict) -> tuple[str | None, str | None]:
    pfc = txn.get("personal_finance_category") or {}
    if not isinstance(pfc, dict):
        return None, None
    return pfc.get("primary"), pfc.get("detailed")


def _row_from_txn(txn: dict, item_id: str) -> dict[str, Any]:
    primary, detailed = _pfc(txn)
    return {
        "transaction_id": txn["transaction_id"],
        "item_id": item_id,
        "account_id": txn.get("account_id"),
        "date": txn.get("date"),
        "name": txn.get("name"),
        "merchant_name": txn.get("merchant_name"),
        "amount": txn.get("amount"),
        "iso_currency": txn.get("iso_currency_code") or txn.get("unofficial_currency_code"),
        "pending": bool(txn.get("pending")),
        "pfc_primary": primary,
        "pfc_detailed": detailed,
        "raw_json": json.dumps(txn, default=str)[:10000],
    }


def _bq_client():
    from google.cloud import bigquery

    return bigquery.Client(project=_PROJECT)


def _upsert_transactions(bq, rows: list[dict]) -> None:
    if not rows:
        return
    from google.cloud import bigquery

    # MERGE via temp table load — batch-friendly and idempotent.
    table_id = f"{_PROJECT}.{_DATASET}._plaid_txn_staging"
    job = bq.load_table_from_json(
        rows,
        table_id,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=[
                bigquery.SchemaField("transaction_id", "STRING"),
                bigquery.SchemaField("item_id", "STRING"),
                bigquery.SchemaField("account_id", "STRING"),
                bigquery.SchemaField("date", "STRING"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("merchant_name", "STRING"),
                bigquery.SchemaField("amount", "FLOAT"),
                bigquery.SchemaField("iso_currency", "STRING"),
                bigquery.SchemaField("pending", "BOOLEAN"),
                bigquery.SchemaField("pfc_primary", "STRING"),
                bigquery.SchemaField("pfc_detailed", "STRING"),
                bigquery.SchemaField("raw_json", "STRING"),
            ],
        ),
    )
    job.result()
    bq.query(
        f"""
        MERGE {_fq("plaid_transactions")} T
        USING `{table_id}` S
        ON T.transaction_id = S.transaction_id
        WHEN MATCHED THEN UPDATE SET
          item_id = S.item_id,
          account_id = S.account_id,
          date = SAFE.PARSE_DATE('%Y-%m-%d', S.date),
          name = S.name,
          merchant_name = S.merchant_name,
          amount = S.amount,
          iso_currency = S.iso_currency,
          pending = S.pending,
          pfc_primary = S.pfc_primary,
          pfc_detailed = S.pfc_detailed,
          raw_json = S.raw_json,
          updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
          transaction_id, item_id, account_id, date, name, merchant_name,
          amount, iso_currency, pending, pfc_primary, pfc_detailed, raw_json, updated_at
        ) VALUES (
          S.transaction_id, S.item_id, S.account_id,
          SAFE.PARSE_DATE('%Y-%m-%d', S.date), S.name, S.merchant_name,
          S.amount, S.iso_currency, S.pending, S.pfc_primary, S.pfc_detailed,
          S.raw_json, CURRENT_TIMESTAMP()
        )
        """
    ).result()


def _delete_transactions(bq, removed_ids: list[str]) -> None:
    if not removed_ids:
        return
    from google.cloud import bigquery

    bq.query(
        f"DELETE FROM {_fq('plaid_transactions')} WHERE transaction_id IN UNNEST(@ids)",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("ids", "STRING", removed_ids),
            ]
        ),
    ).result()


def _save_cursor(bq, store: str, item_id: str, cursor: str) -> None:
    from google.cloud import bigquery

    bq.query(
        f"""
        UPDATE {_fq("plaid_items")}
        SET cursor = @cursor, last_synced_at = CURRENT_TIMESTAMP()
        WHERE store = @store AND item_id = @item_id
        """,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("cursor", "STRING", cursor),
                bigquery.ScalarQueryParameter("store", "STRING", store),
                bigquery.ScalarQueryParameter("item_id", "STRING", item_id),
            ]
        ),
    ).result()


def sync_item(store: str, item_id: str, *, cursor: str | None = None) -> SyncResult:
    """Drain /transactions/sync for one Item and persist to BQ."""
    result = SyncResult(item_id=item_id)
    client = PlaidClient()
    access_token = get_access_token(item_id)
    bq = _bq_client()

    if cursor is None:
        from google.cloud import bigquery

        rows = list(
            bq.query(
                f"SELECT cursor FROM {_fq('plaid_items')} WHERE store=@store AND item_id=@item_id",
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("store", "STRING", store),
                        bigquery.ScalarQueryParameter("item_id", "STRING", item_id),
                    ]
                ),
            ).result()
        )
        cursor = (rows[0].cursor if rows else "") or ""

    next_cursor = cursor or ""
    # Preserve start cursor for pagination mutation restart.
    while True:
        page_cursor = next_cursor
        try:
            data = client.transactions_sync(access_token, page_cursor or None)
        except Exception as exc:  # noqa: BLE001
            # TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION → restart from page_cursor
            msg = str(exc)
            result.errors.append(msg)
            if "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" in msg:
                next_cursor = page_cursor
                continue
            raise

        added = data.get("added") or []
        modified = data.get("modified") or []
        removed = data.get("removed") or []
        next_cursor = data.get("next_cursor") or page_cursor
        result.pages += 1
        result.added += len(added)
        result.modified += len(modified)
        result.removed += len(removed)

        upsert_rows = [_row_from_txn(t, item_id) for t in added + modified]
        _upsert_transactions(bq, upsert_rows)
        _delete_transactions(bq, [r["transaction_id"] for r in removed if r.get("transaction_id")])

        if not data.get("has_more"):
            break

    result.cursor = next_cursor
    _save_cursor(bq, store, item_id, next_cursor)
    return result


def list_linked_items(store: str) -> list[dict]:
    bq = _bq_client()
    from google.cloud import bigquery

    rows = bq.query(
        f"SELECT store, item_id, institution_name, cursor, linked_at, linked_by, last_synced_at "
        f"FROM {_fq('plaid_items')} WHERE store=@store",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("store", "STRING", store)]
        ),
    ).result()
    return [dict(r) for r in rows]


def purge_item(store: str, item_id: str, *, dry_run: bool = True) -> dict:
    """Delete all plaid_transactions for item_id, then the plaid_items row.

    Used to retire sandbox Platypus evidence before production Chase Link
    (Issue #168). Does not call Plaid ``/item/remove`` (sandbox Item is
    disposable). Access-token SM cleanup is a separate ops step.
    """
    from google.cloud import bigquery

    bq = _bq_client()
    count_rows = list(
        bq.query(
            f"SELECT COUNT(*) AS n FROM {_fq('plaid_transactions')} WHERE item_id=@item_id",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("item_id", "STRING", item_id),
                ]
            ),
        ).result()
    )
    txn_n = int(count_rows[0].n) if count_rows else 0
    item_rows = list(
        bq.query(
            f"SELECT COUNT(*) AS n FROM {_fq('plaid_items')} "
            f"WHERE store=@store AND item_id=@item_id",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("store", "STRING", store),
                    bigquery.ScalarQueryParameter("item_id", "STRING", item_id),
                ]
            ),
        ).result()
    )
    item_exists = bool(item_rows and int(item_rows[0].n) > 0)
    if dry_run:
        return {
            "transactions_deleted": txn_n,
            "item_deleted": item_exists,
            "dry_run": True,
        }

    bq.query(
        f"DELETE FROM {_fq('plaid_transactions')} WHERE item_id=@item_id",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("item_id", "STRING", item_id),
            ]
        ),
    ).result()
    bq.query(
        f"DELETE FROM {_fq('plaid_items')} WHERE store=@store AND item_id=@item_id",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("store", "STRING", store),
                bigquery.ScalarQueryParameter("item_id", "STRING", item_id),
            ]
        ),
    ).result()
    return {
        "transactions_deleted": txn_n,
        "item_deleted": item_exists,
        "dry_run": False,
    }
