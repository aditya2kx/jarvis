"""Load taxonomy + category rules into BQ from a *private* seed directory (Issue #160).

Real merchant / brand match patterns MUST NOT live in git. Point
``PLAID_TAXONOMY_SEED_DIR`` at a local (gitignored) or ops-managed folder that
contains:

  - category_taxonomy.csv
  - transaction_rule_seed.csv
  - extension_rules.csv   (optional corpus extensions)

Idempotent MERGE on node/rule id. Prod already has live rules in BQ; this module
is for bootstrap / re-seed only.
"""
from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT = "jarvis-bhaga-prod"
_DATASET = "bhaga"

_TAXONOMY_NAME = "category_taxonomy.csv"
_RULES_NAME = "transaction_rule_seed.csv"
_EXTENSIONS_NAME = "extension_rules.csv"


def _slug(label: str) -> str:
    s = label.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:80] or "unknown"


def _fq(table: str) -> str:
    return f"`{_PROJECT}.{_DATASET}.{table}`"


def _bq():
    from google.cloud import bigquery

    return bigquery.Client(project=_PROJECT)


def _seed_dir() -> Path:
    """Resolve private seed dir — never a tracked repo path with live brands."""
    env = (os.environ.get("PLAID_TAXONOMY_SEED_DIR") or "").strip()
    if env:
        d = Path(env).expanduser().resolve()
        if (d / _TAXONOMY_NAME).is_file():
            return d
        raise FileNotFoundError(
            f"PLAID_TAXONOMY_SEED_DIR={d} missing {_TAXONOMY_NAME}"
        )
    # Dev convenience: worktree-local gitignored folder (see .gitignore `local/`)
    repo_local = (
        Path(__file__).resolve().parents[2] / "local" / "plaid-taxonomy-seed"
    )
    if (repo_local / _TAXONOMY_NAME).is_file():
        return repo_local
    raise FileNotFoundError(
        "Private taxonomy seed not found. Set PLAID_TAXONOMY_SEED_DIR to a "
        f"directory containing {_TAXONOMY_NAME} + {_RULES_NAME} "
        "(live merchant patterns must not be committed). "
        f"Optional default: {repo_local}"
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _build_nodes(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Build parent + child taxonomy nodes from taxonomy CSV only (no brand extras)."""
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
    return list(parents.values()) + children


def _resolve_ids(
    category: str, subcategory: str, nodes: list[dict[str, Any]]
) -> tuple[str, str | None]:
    pid = _slug(category)
    sid = f"{pid}__{_slug(subcategory)}" if subcategory else None
    if sid and not any(n["id"] == sid for n in nodes):
        for n in nodes:
            if n.get("parent_id") == pid and n.get("slug") == _slug(subcategory):
                sid = n["id"]
                break
    return pid, sid


def _ensure_nodes(
    category: str,
    subcategory: str,
    nodes: list[dict[str, Any]],
    now: str,
) -> tuple[str, str | None]:
    cid, sid = _resolve_ids(category, subcategory, nodes)
    if not any(n["id"] == cid for n in nodes):
        nodes.append(
            {
                "id": cid,
                "parent_id": None,
                "slug": cid,
                "label": category,
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
                "slug": _slug(subcategory),
                "label": subcategory,
                "definition": None,
                "default_pnl_treatment": None,
                "sort_order": 999,
                "enabled": True,
                "updated_at": now,
            }
        )
    return cid, sid


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
        cid, sid = _ensure_nodes(cat, sub, nodes, now)
        field = (r.get("match_field") or "name").strip()
        if field in ("name", "merchant_name", "name_or_merchant"):
            match_field = (
                "name_or_merchant" if field == "name" else field
            )
        else:
            match_field = "name_or_merchant"
        op = (r.get("match_operator") or "contains").strip()
        # Prefer rule_note only — never june_context (may carry PII / amounts).
        notes = (r.get("rule_note") or "").strip() or None
        out.append(
            {
                "id": (r.get("rule_id") or "").strip(),
                "priority": int(r.get("priority") or 9999),
                "match_field": match_field,
                "match_operator": op,
                "match_pattern": (r.get("match_pattern") or "").strip(),
                "amount_sign": (r.get("amount_sign") or "any").strip() or "any",
                "category_id": cid,
                "subcategory_id": sid,
                "confidence": (r.get("confidence") or "medium").strip(),
                "enabled": True,
                "notes": notes,
                "updated_at": now,
            }
        )
    return [r for r in out if r["id"]]


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
    tax_rows = _read_csv(seed / _TAXONOMY_NAME)
    rule_rows = _read_csv(seed / _RULES_NAME)
    nodes = _build_nodes(tax_rows)
    rules = _build_rules(rule_rows, nodes)
    bq = None if dry_run else _bq()
    if dry_run:
        return {
            "dry_run": True,
            "seed_dir": str(seed),
            "nodes": len(nodes),
            "rules": len(rules),
        }
    n_nodes = _merge_nodes(bq, nodes, dry_run=False)
    n_rules = _merge_rules(bq, rules, dry_run=False)
    return {
        "dry_run": False,
        "seed_dir": str(seed),
        "nodes": n_nodes,
        "rules": n_rules,
    }


def extend_corpus_rules(*, dry_run: bool = True) -> dict[str, Any]:
    """Load optional extension_rules.csv from the private seed dir (no hardcoded brands)."""
    seed = _seed_dir()
    tax_rows = _read_csv(seed / _TAXONOMY_NAME)
    nodes = _build_nodes(tax_rows)
    # Ensure rule-derived nodes exist before extensions
    rule_path = seed / _RULES_NAME
    if rule_path.is_file():
        _build_rules(_read_csv(rule_path), nodes)
    ext_path = seed / _EXTENSIONS_NAME
    if not ext_path.is_file():
        return {
            "dry_run": dry_run,
            "nodes": len(nodes),
            "extension_rules": 0,
            "skipped": f"missing {_EXTENSIONS_NAME}",
        }
    ext = _build_rules(_read_csv(ext_path), nodes)
    if dry_run:
        return {
            "dry_run": True,
            "nodes": len(nodes),
            "extension_rules": len(ext),
        }
    bq = _bq()
    n_nodes = _merge_nodes(bq, nodes, dry_run=False)
    n_rules = _merge_rules(bq, ext, dry_run=False)
    return {"dry_run": False, "nodes": n_nodes, "extension_rules": n_rules}
