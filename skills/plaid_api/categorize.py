"""Apply category rules to plaid_transactions (Issue #160).

Never overwrites operator overrides. Idempotent reapply.
"""
from __future__ import annotations

from typing import Any, Optional

from skills.plaid_api.category_rules import CategoryRule, evaluate_rules

_PROJECT = "jarvis-bhaga-prod"
_DATASET = "bhaga"


def _fq(table: str) -> str:
    return f"`{_PROJECT}.{_DATASET}.{table}`"


def load_rules(bq) -> list[CategoryRule]:
    rows = list(
        bq.query(
            f"""
            SELECT id, priority, match_field, match_operator, match_pattern,
                   amount_sign, category_id, subcategory_id, enabled, account_mask,
                   from_mask, to_mask
            FROM {_fq("plaid_category_rules")}
            WHERE IFNULL(enabled, TRUE) IS TRUE
            ORDER BY priority, id
            """
        ).result()
    )
    out: list[CategoryRule] = []
    for r in rows:
        out.append(
            CategoryRule(
                id=r["id"],
                priority=int(r["priority"]),
                match_field=r["match_field"] or "name_or_merchant",
                match_operator=r["match_operator"] or "contains",
                match_pattern=r["match_pattern"] or "",
                amount_sign=r["amount_sign"],
                category_id=r["category_id"],
                subcategory_id=r["subcategory_id"],
                enabled=bool(r["enabled"]) if r["enabled"] is not None else True,
                account_mask=r["account_mask"],
                from_mask=r.get("from_mask"),
                to_mask=r.get("to_mask"),
            )
        )
    return out


def reapply_categories(
    bq,
    *,
    item_id: Optional[str] = None,
    transaction_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Evaluate rules for txns without overrides; UPDATE only when changed."""
    rules = load_rules(bq)
    params = []
    from google.cloud import bigquery

    where_t = ["1=1"]
    if item_id:
        where_t.append("t.item_id = @item_id")
        params.append(bigquery.ScalarQueryParameter("item_id", "STRING", item_id))
    if transaction_ids:
        where_t.append("t.transaction_id IN UNNEST(@ids)")
        params.append(bigquery.ArrayQueryParameter("ids", "STRING", transaction_ids))

    sql = f"""
        SELECT t.transaction_id, t.name, t.merchant_name, t.amount,
               a.mask AS account_mask,
               JSON_VALUE(t.raw_json, '$.counterparties[0].name') AS counterparty_name,
               t.category_id, t.subcategory_id, t.rule_id,
               t.override_category_id, t.override_subcategory_id,
               IFNULL(t.is_internal, FALSE) AS is_internal
        FROM {_fq("plaid_transactions")} t
        LEFT JOIN {_fq("plaid_accounts")} a ON a.account_id = t.account_id
        WHERE {" AND ".join(where_t)}
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY t.transaction_id
          ORDER BY t.updated_at DESC NULLS LAST
        ) = 1
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
    rows = list(bq.query(sql, job_config=job_config).result())

    updated = 0
    unchanged = 0
    skipped_override = 0

    # Batch updates via staging table for speed
    updates: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        if d.get("override_category_id"):
            skipped_override += 1
            unchanged += 1
            continue
        match = evaluate_rules(d, rules)
        new_cat = match.category_id if match else None
        new_sub = match.subcategory_id if match else None
        new_rule = match.rule_id if match else None
        new_internal = True if new_cat == "internal_transfers" else bool(d.get("is_internal"))
        if (
            (d.get("category_id") or None) == new_cat
            and (d.get("subcategory_id") or None) == new_sub
            and (d.get("rule_id") or None) == new_rule
            and bool(d.get("is_internal")) == new_internal
        ):
            unchanged += 1
            continue
        updates.append(
            {
                "transaction_id": d["transaction_id"],
                "category_id": new_cat,
                "subcategory_id": new_sub,
                "rule_id": new_rule,
                "is_internal": new_internal,
            }
        )
        updated += 1

    if updates:
        table = f"{_PROJECT}.{_DATASET}._plaid_cat_staging"
        job = bq.load_table_from_json(
            updates,
            table,
            job_config=bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            ),
        )
        job.result()
        bq.query(
            f"""
            MERGE {_fq("plaid_transactions")} T
            USING `{table}` S
            ON T.transaction_id = S.transaction_id
            WHEN MATCHED AND T.override_category_id IS NULL THEN UPDATE SET
              category_id = S.category_id,
              subcategory_id = S.subcategory_id,
              rule_id = S.rule_id,
              is_internal = S.is_internal,
              categorized_at = CURRENT_TIMESTAMP(),
              updated_at = CURRENT_TIMESTAMP()
            """
        ).result()

    return {
        "updated": updated,
        "unchanged": unchanged,
        "skipped_override": skipped_override,
        "rules": len(rules),
        "scanned": len(rows),
    }


def categorize_upserted(bq, transaction_ids: list[str]) -> dict[str, Any]:
    if not transaction_ids:
        return {"updated": 0, "unchanged": 0, "skipped_override": 0, "rules": 0, "scanned": 0}
    return reapply_categories(bq, transaction_ids=transaction_ids)
