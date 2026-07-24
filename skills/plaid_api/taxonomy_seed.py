"""Load Copilot June taxonomy + rules into BQ (Issue #160).

Idempotent MERGE on node/rule id. Also installs corpus-extension rules for
non-June high-dollar patterns (BOA, rent, Qualifrac, Homebase, capital).
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT = "jarvis-bhaga-prod"
_DATASET = "bhaga"

# Prefer console seed path; fall back to skills-local copy if present.
_SEED_DIRS = [
    Path(__file__).resolve().parents[2]
    / "apps"
    / "operator-console"
    / "lib"
    / "plaid"
    / "taxonomy"
    / "seed",
    Path(__file__).resolve().parent / "taxonomy" / "seed",
]


def _seed_dir() -> Path:
    for d in _SEED_DIRS:
        if (d / "palmetto_category_taxonomy.csv").exists():
            return d
    raise FileNotFoundError(
        "palmetto_category_taxonomy.csv not found under "
        + ", ".join(str(d) for d in _SEED_DIRS)
    )


def _slug(label: str) -> str:
    s = label.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:80] or "unknown"


def _fq(table: str) -> str:
    return f"`{_PROJECT}.{_DATASET}.{table}`"


def _bq():
    from google.cloud import bigquery

    return bigquery.Client(project=_PROJECT)


def _read_taxonomy(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _read_rules(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _build_nodes(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Build parent + child taxonomy nodes from taxonomy CSV + rule-only subs."""
    now = datetime.now(timezone.utc).isoformat()
    parents: dict[str, dict[str, Any]] = {}
    children: list[dict[str, Any]] = []
    sort_p = 0
    for r in rows:
        cat = (r.get("category") or "").strip()
        sub = (r.get("subcategory") or "").strip()
        if not cat:
            continue
        pid = _slug(cat)
        if pid not in parents:
            sort_p += 10
            parents[pid] = {
                "id": pid,
                "parent_id": None,
                "slug": pid,
                "label": cat,
                "definition": (r.get("notes") or "").strip() or None,
                "default_pnl_treatment": (r.get("default_pnl_treatment") or "").strip()
                or None,
                "sort_order": sort_p,
                "enabled": True,
                "updated_at": now,
            }
        if sub:
            sid = f"{pid}__{_slug(sub)}"
            children.append(
                {
                    "id": sid,
                    "parent_id": pid,
                    "slug": _slug(sub),
                    "label": sub,
                    "definition": (r.get("notes") or "").strip() or None,
                    "default_pnl_treatment": (r.get("default_pnl_treatment") or "").strip()
                    or None,
                    "sort_order": len(children) + 1,
                    "enabled": True,
                    "updated_at": now,
                }
            )
    # Extra parents/subs referenced by rules but missing from taxonomy CSV
    extras = [
        ("Payroll / labor", "ADP payroll fees"),
        ("Payroll / labor", "ADP 401k / benefits"),
        ("Inventory / food / supplies", "Palmetto ACH purchase"),
        ("Inventory / food / supplies", "Unpaid Palmetto bases invoice"),
        ("Inventory / food / supplies", "Terrasoul wholesale"),
        ("Inventory / food / supplies", "HEB / Favor grocery"),
        ("Inventory / food / supplies", "HEB grocery"),
        ("Logistics / facilities / store ops", "Echo freight"),
        ("Logistics / facilities / store ops", "Restaurant supplies"),
        ("Logistics / facilities / store ops", "Uline supplies"),
        ("Logistics / facilities / store ops", "Vehicle / auto finance"),
        ("Logistics / facilities / store ops", "Tesla charging / vehicle"),
        ("Logistics / facilities / store ops", "Tolls"),
        ("Logistics / facilities / store ops", "Plumbing repair"),
        ("Logistics / facilities / store ops", "Junk removal"),
        ("Logistics / facilities / store ops", "Internet / utility"),
        ("Logistics / facilities / store ops", "SWC misc store ops"),
        ("Logistics / facilities / store ops", "Bank service charge"),
        ("Logistics / facilities / store ops", "Rent / landlord"),
        ("Marketing + software tools", "Paid digital ads"),
        ("Marketing + software tools", "Wainscot local magazine"),
        ("Marketing + software tools", "Digital signage"),
        ("Marketing + software tools", "Cursor"),
        ("Marketing + software tools", "ClickUp"),
        ("Marketing + software tools", "Anthropic"),
        ("Marketing + software tools", "MarketMan"),
        ("Marketing + software tools", "Betterteam"),
        ("Marketing + software tools", "Lovable"),
        ("Marketing + software tools", "Adobe"),
        ("Logistics / facilities / store ops", "Franchise / filing tax"),
        ("Marketing + software tools", "Professional services"),
        ("One-off / review", "Gift card / promo / misc"),
        ("Other inflow / owner transfer", "Zelle / reimbursement"),
        ("Other inflow / owner transfer", "BOA owner transfer"),
        ("Other inflow / owner transfer", "Owner capital inflow"),
        ("Contra expense / refund", "Amazon refund"),
        ("Review / possible owner purchase", "Palmetto store purchase"),
    ]
    existing_ids = {n["id"] for n in list(parents.values()) + children}
    for cat, sub in extras:
        pid = _slug(cat)
        if pid not in parents:
            sort_p += 10
            parents[pid] = {
                "id": pid,
                "parent_id": None,
                "slug": pid,
                "label": cat,
                "definition": None,
                "default_pnl_treatment": None,
                "sort_order": sort_p,
                "enabled": True,
                "updated_at": now,
            }
        sid = f"{pid}__{_slug(sub)}"
        if sid not in existing_ids:
            children.append(
                {
                    "id": sid,
                    "parent_id": pid,
                    "slug": _slug(sub),
                    "label": sub,
                    "definition": None,
                    "default_pnl_treatment": None,
                    "sort_order": len(children) + 1,
                    "enabled": True,
                    "updated_at": now,
                }
            )
            existing_ids.add(sid)
    return list(parents.values()) + children


def _resolve_ids(
    category: str, subcategory: str, nodes: list[dict[str, Any]]
) -> tuple[str, str | None]:
    pid = _slug(category)
    if not any(n["id"] == pid for n in nodes):
        # create on the fly
        pass
    sid = f"{pid}__{_slug(subcategory)}" if subcategory else None
    if sid and not any(n["id"] == sid for n in nodes):
        # fuzzy: find child under parent with matching label slug
        for n in nodes:
            if n.get("parent_id") == pid and n.get("slug") == _slug(subcategory):
                sid = n["id"]
                break
    return pid, sid


def _build_rules(
    rows: list[dict[str, str]], nodes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    out: list[dict[str, Any]] = []
    for r in rows:
        cat = (r.get("category") or "").strip()
        sub = (r.get("subcategory") or "").strip()
        if not cat:
            continue
        cid, sid = _resolve_ids(cat, sub, nodes)
        # Ensure parent/child exist
        if not any(n["id"] == cid for n in nodes):
            nodes.append(
                {
                    "id": cid,
                    "parent_id": None,
                    "slug": cid,
                    "label": cat,
                    "definition": None,
                    "default_pnl_treatment": None,
                    "sort_order": 999,
                    "enabled": True,
                    "updated_at": now,
                }
            )
        if sid and not any(n["id"] == sid for n in nodes):
            nodes.append(
                {
                    "id": sid,
                    "parent_id": cid,
                    "slug": _slug(sub),
                    "label": sub,
                    "definition": None,
                    "default_pnl_treatment": None,
                    "sort_order": 999,
                    "enabled": True,
                    "updated_at": now,
                }
            )
        op = (r.get("match_operator") or "contains").strip()
        out.append(
            {
                "id": (r.get("rule_id") or "").strip(),
                "priority": int(r.get("priority") or 9999),
                "match_field": "name_or_merchant",
                "match_operator": op,
                "match_pattern": (r.get("match_pattern") or "").strip(),
                "amount_sign": (r.get("amount_sign") or "any").strip() or "any",
                "category_id": cid,
                "subcategory_id": sid,
                "confidence": (r.get("confidence") or "medium").strip(),
                "enabled": True,
                "notes": (r.get("rule_note") or r.get("june_context") or "").strip()
                or None,
                "updated_at": now,
            }
        )
    return [r for r in out if r["id"]]


def _extension_rules(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Non-June high-dollar patterns from jam corpus analysis."""
    now = datetime.now(timezone.utc).isoformat()
    specs = [
        (15, "income_capital_allianz", "Allianz Life", "negative",
         "Other inflow / owner transfer", "Owner capital inflow", "high", "contains", "name_or_merchant"),
        (16, "income_capital_nwlife", "NW Life", "negative",
         "Other inflow / owner transfer", "Owner capital inflow", "high", "contains", "name_or_merchant"),
        (17, "income_capital_etrade", "E*TRADE", "negative",
         "Other inflow / owner transfer", "Owner capital inflow", "high", "contains", "name_or_merchant"),
        (18, "income_capital_manual_cr", "MANUAL CR-BKRG", "negative",
         "Other inflow / owner transfer", "Owner capital inflow", "high", "contains", "name_or_merchant"),
        (19, "income_capital_mspbna", "MSPBNA", "negative",
         "Other inflow / owner transfer", "Owner capital inflow", "medium", "contains", "name_or_merchant"),
        (45, "transfer_boa_out", "Bank of America", "positive",
         "Other inflow / owner transfer", "BOA owner transfer", "high", "contains", "name_or_merchant"),
        (46, "transfer_boa_in", "Bank of America", "negative",
         "Other inflow / owner transfer", "BOA owner transfer", "high", "contains", "name_or_merchant"),
        (47, "transfer_zelle_out", "Zelle", "positive",
         "Other inflow / owner transfer", "Zelle / reimbursement", "medium", "contains", "name_or_merchant"),
        (115, "payroll_homebase", "PAYROLL-HOMEBASE", "positive",
         "Payroll / labor", "ADP wage pay", "high", "contains", "name_or_merchant"),
        (116, "payroll_basic_online", "BASIC ONLINE PAYROLL", "positive",
         "Payroll / labor", "ADP wage pay", "high", "contains", "name_or_merchant"),
        (205, "opex_qualifrac", "Qualifrac", "positive",
         "Marketing + software tools", "Professional services", "high", "contains", "name_or_merchant"),
        (206, "opex_bend_law", "Bend Law", "positive",
         "Marketing + software tools", "Professional services", "high", "contains", "name_or_merchant"),
        (211, "inventory_palmetto_super_foods", "Palmetto Super Foods", "positive",
         "Inventory / food / supplies", "Palmetto ACH purchase", "high", "contains", "name_or_merchant"),
        (212, "inventory_ak_juicy_merchant", "Ak Juicy Bowls", "positive",
         "Inventory / food / supplies", "Palmetto inventory purchases", "medium", "contains", "merchant_name"),
        (305, "occupancy_nineteen_hundred", "Nineteen Hundred", "positive",
         "Logistics / facilities / store ops", "Rent / landlord", "high", "contains", "name_or_merchant"),
        (306, "occupancy_nineteenhundred", "Nineteenhundred", "positive",
         "Logistics / facilities / store ops", "Rent / landlord", "high", "contains", "name_or_merchant"),
        (307, "occupancy_houston_landlord", "Houston Palmetto Landlord", "positive",
         "Logistics / facilities / store ops", "Rent / landlord", "high", "contains", "name_or_merchant"),
        (308, "occupancy_prepaid_rent", "PREPAID RENT", "positive",
         "Logistics / facilities / store ops", "Rent / landlord", "high", "contains", "name_or_merchant"),
        (501, "tax_webfile", "WEBFILE", "positive",
         "Logistics / facilities / store ops", "Franchise / filing tax", "high", "contains", "name_or_merchant"),
    ]
    out: list[dict[str, Any]] = []
    for pri, rid, pattern, sign, cat, sub, conf, op, field in specs:
        cid, sid = _resolve_ids(cat, sub, nodes)
        if not any(n["id"] == cid for n in nodes):
            nodes.append(
                {
                    "id": cid,
                    "parent_id": None,
                    "slug": cid,
                    "label": cat,
                    "definition": None,
                    "default_pnl_treatment": None,
                    "sort_order": 999,
                    "enabled": True,
                    "updated_at": now,
                }
            )
        if sid and not any(n["id"] == sid for n in nodes):
            nodes.append(
                {
                    "id": sid,
                    "parent_id": cid,
                    "slug": _slug(sub),
                    "label": sub,
                    "definition": None,
                    "default_pnl_treatment": None,
                    "sort_order": 999,
                    "enabled": True,
                    "updated_at": now,
                }
            )
        out.append(
            {
                "id": rid,
                "priority": pri,
                "match_field": field,
                "match_operator": op,
                "match_pattern": pattern,
                "amount_sign": sign,
                "category_id": cid,
                "subcategory_id": sid,
                "confidence": conf,
                "enabled": True,
                "notes": "Corpus extension (jam #160)",
                "updated_at": now,
            }
        )
    return out


def _merge_nodes(bq, rows: list[dict[str, Any]], *, dry_run: bool) -> int:
    from google.cloud import bigquery

    if dry_run:
        return len(rows)
    table = f"{_PROJECT}.{_DATASET}._plaid_taxonomy_staging"
    job = bq.load_table_from_json(
        rows,
        table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    job.result()
    bq.query(
        f"""
        MERGE {_fq("plaid_taxonomy_nodes")} T
        USING `{table}` S
        ON T.id = S.id
        WHEN MATCHED THEN UPDATE SET
          parent_id = S.parent_id, slug = S.slug, label = S.label,
          definition = S.definition, default_pnl_treatment = S.default_pnl_treatment,
          sort_order = S.sort_order, enabled = S.enabled, updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
          id, parent_id, slug, label, definition, default_pnl_treatment,
          sort_order, enabled, updated_at
        ) VALUES (
          S.id, S.parent_id, S.slug, S.label, S.definition, S.default_pnl_treatment,
          S.sort_order, S.enabled, CURRENT_TIMESTAMP()
        )
        """
    ).result()
    return len(rows)


def _merge_rules(bq, rows: list[dict[str, Any]], *, dry_run: bool) -> int:
    from google.cloud import bigquery

    if dry_run:
        return len(rows)
    table = f"{_PROJECT}.{_DATASET}._plaid_rules_staging"
    job = bq.load_table_from_json(
        rows,
        table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    job.result()
    bq.query(
        f"""
        MERGE {_fq("plaid_category_rules")} T
        USING `{table}` S
        ON T.id = S.id
        WHEN MATCHED THEN UPDATE SET
          priority = S.priority, match_field = S.match_field,
          match_operator = S.match_operator, match_pattern = S.match_pattern,
          amount_sign = S.amount_sign, category_id = S.category_id,
          subcategory_id = S.subcategory_id, confidence = S.confidence,
          enabled = S.enabled, notes = S.notes, updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
          id, priority, match_field, match_operator, match_pattern, amount_sign,
          category_id, subcategory_id, confidence, enabled, notes, updated_at
        ) VALUES (
          S.id, S.priority, S.match_field, S.match_operator, S.match_pattern,
          S.amount_sign, S.category_id, S.subcategory_id, S.confidence,
          S.enabled, S.notes, CURRENT_TIMESTAMP()
        )
        """
    ).result()
    return len(rows)


def seed_taxonomy(*, dry_run: bool = True) -> dict[str, Any]:
    seed = _seed_dir()
    tax_rows = _read_taxonomy(seed / "palmetto_category_taxonomy.csv")
    rule_rows = _read_rules(seed / "palmetto_transaction_rule_seed.csv")
    nodes = _build_nodes(tax_rows)
    rules = _build_rules(rule_rows, nodes)
    # rebuild rules after nodes mutated
    rules = _build_rules(rule_rows, nodes)
    bq = None if dry_run else _bq()
    n_nodes = _merge_nodes(bq, nodes, dry_run=dry_run) if bq or dry_run else 0
    n_rules = _merge_rules(bq, rules, dry_run=dry_run) if bq or dry_run else 0
    if dry_run:
        n_nodes, n_rules = len(nodes), len(rules)
    return {
        "dry_run": dry_run,
        "seed_dir": str(seed),
        "nodes": n_nodes,
        "rules": n_rules,
    }


def extend_corpus_rules(*, dry_run: bool = True) -> dict[str, Any]:
    seed = _seed_dir()
    tax_rows = _read_taxonomy(seed / "palmetto_category_taxonomy.csv")
    nodes = _build_nodes(tax_rows)
    # Also include rule-derived nodes
    rule_rows = _read_rules(seed / "palmetto_transaction_rule_seed.csv")
    _build_rules(rule_rows, nodes)
    ext = _extension_rules(nodes)
    bq = None if dry_run else _bq()
    n_nodes = _merge_nodes(bq, nodes, dry_run=dry_run) if bq or dry_run else len(nodes)
    n_rules = _merge_rules(bq, ext, dry_run=dry_run) if bq or dry_run else len(ext)
    if dry_run:
        n_nodes, n_rules = len(nodes), len(ext)
    return {"dry_run": dry_run, "nodes": n_nodes, "extension_rules": n_rules}
