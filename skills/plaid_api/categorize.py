"""Apply category rules to plaid_transactions (Issue #160).

Never overwrites operator overrides. Idempotent reapply.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from skills.plaid_api.category_rules import CategoryRule, evaluate_rules

_PROJECT = "jarvis-bhaga-prod"
_DATASET = "bhaga"

# BigQuery aborts one of two DML statements that touch the same table at the same time
# ("Could not serialize access … due to concurrent update"); the webhook fires two syncs
# per Plaid update, so a categorize MERGE racing the other sync is routine.
_SERIALIZE_MARKERS = ("could not serialize access", "concurrent update")
_RETRY_DELAYS_S = (2, 5, 10)


def _fq(table: str) -> str:
    return f"`{_PROJECT}.{_DATASET}.{table}`"


def is_serialization_conflict(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _SERIALIZE_MARKERS)


def _with_serialize_retry(fn, *, label: str, sleep=time.sleep):
    for attempt, delay in enumerate((*_RETRY_DELAYS_S, None), start=1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if delay is None or not is_serialization_conflict(exc):
                raise
            print(
                f"[plaid categorize] {label} serialization conflict attempt={attempt} "
                f"retry_in={delay}s"
            )
            sleep(delay)
    return None


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


def plan_updates(rows: list[dict[str, Any]], rules: list[CategoryRule]) -> dict[str, Any]:
    """Pure: which rows change under ``rules`` (overrides are never touched)."""
    updates: list[dict[str, Any]] = []
    unchanged = 0
    skipped_override = 0
    for d in rows:
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
    return {"updates": updates, "unchanged": unchanged, "skipped_override": skipped_override}


def _merge_updates(bq, updates: list[dict[str, Any]]) -> None:
    """One MERGE from an array-of-STRUCT param — no shared staging table to race on."""
    from google.cloud import bigquery

    structs = [
        bigquery.StructQueryParameter(
            None,
            bigquery.ScalarQueryParameter("transaction_id", "STRING", u["transaction_id"]),
            bigquery.ScalarQueryParameter("category_id", "STRING", u["category_id"]),
            bigquery.ScalarQueryParameter("subcategory_id", "STRING", u["subcategory_id"]),
            bigquery.ScalarQueryParameter("rule_id", "STRING", u["rule_id"]),
            bigquery.ScalarQueryParameter("is_internal", "BOOL", u["is_internal"]),
        )
        for u in updates
    ]
    sql = f"""
        MERGE {_fq("plaid_transactions")} T
        USING (SELECT * FROM UNNEST(@rows)) S
        ON T.transaction_id = S.transaction_id
        WHEN MATCHED AND T.override_category_id IS NULL THEN UPDATE SET
          category_id = S.category_id,
          subcategory_id = S.subcategory_id,
          rule_id = S.rule_id,
          is_internal = S.is_internal,
          categorized_at = CURRENT_TIMESTAMP(),
          updated_at = CURRENT_TIMESTAMP()
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("rows", "STRUCT", structs)]
    )
    _with_serialize_retry(
        lambda: bq.query(sql, job_config=job_config).result(),
        label=f"merge rows={len(updates)}",
    )


def reapply_categories(
    bq,
    *,
    item_id: Optional[str] = None,
    transaction_ids: Optional[list[str]] = None,
    include_uncategorized: bool = False,
) -> dict[str, Any]:
    """Evaluate rules for txns without overrides; UPDATE only when changed.

    ``include_uncategorized`` also sweeps every row never categorized
    (``categorized_at IS NULL``, no override) so a sync whose categorize step
    failed heals on the next sync instead of waiting for a manual Reapply.
    """
    rules = load_rules(bq)
    params = []
    from google.cloud import bigquery

    where_t = ["1=1"]
    if item_id:
        where_t.append("t.item_id = @item_id")
        params.append(bigquery.ScalarQueryParameter("item_id", "STRING", item_id))
    scope = []
    if transaction_ids:
        scope.append("t.transaction_id IN UNNEST(@ids)")
        params.append(bigquery.ArrayQueryParameter("ids", "STRING", transaction_ids))
    if include_uncategorized:
        scope.append("(t.categorized_at IS NULL AND t.override_category_id IS NULL)")
    if scope:
        where_t.append("(" + " OR ".join(scope) + ")")

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
    rows = [dict(r) for r in bq.query(sql, job_config=job_config).result()]

    plan = plan_updates(rows, rules)
    if plan["updates"]:
        _merge_updates(bq, plan["updates"])

    return {
        "updated": len(plan["updates"]),
        "unchanged": plan["unchanged"],
        "skipped_override": plan["skipped_override"],
        "rules": len(rules),
        "scanned": len(rows),
    }


def categorize_upserted(
    bq, transaction_ids: list[str], *, item_id: Optional[str] = None
) -> dict[str, Any]:
    """Post-sync categorize: the rows this sync upserted plus any never-categorized row."""
    return reapply_categories(
        bq,
        item_id=item_id,
        transaction_ids=transaction_ids or None,
        include_uncategorized=True,
    )
