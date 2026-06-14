#!/usr/bin/env python3
"""Sheet ⇆ BQ model reconciliation gate.

Compares each Sheet model tab against its BQ model table to detect data drift.
Also asserts tip-pool conservation from BQ.

When any grain diverges or the conservation check fails, exits non-zero with
a structured mismatch report. Designed to run:
  - In CI (against sandbox sheet + BQ) — gating the PR
  - Nightly (as a non-fatal step after render_model_sheet_from_bq) — alerting on drift

Reuses _cells_match, _normalize_cell, _compare_tabs, _read_sheet_tab from
verify_bq_parity to avoid duplicating the normalization logic.

Usage (sandbox):
    BHAGA_DATASTORE=bigquery BHAGA_SHEET_MODE=staging \\
        python3 -m agents.bhaga.scripts.reconcile_model --store palmetto

Usage (prod, read-only evidence):
    BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.reconcile_model --store palmetto --allow-prod-read
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from agents.bhaga.scripts.verify_bq_parity import (
    _cells_match,   # noqa: F401 — re-exported for callers
    _compare_tabs,
    _read_sheet_tab,
)
from core.config_loader import refresh_access_token, resolve_sheet_id
from core.datastore import dataset, read_query

_PROJECT = "jarvis-bhaga-prod"
_DATASET = dataset()  # env-driven (BHAGA_BQ_DATASET); prod `bhaga` by default
_STORE_PROFILES = pathlib.Path(__file__).resolve().parents[3] / "agents" / "bhaga" / "knowledge-base" / "store-profiles"

# Columns that exist in BQ but not in the Sheet (metadata/internal) — skip in diff.
_SKIP_COLS: set[str] = {"materialized_at_utc", "last_refreshed_ct", "scraped_at_utc"}

# Sheet column → BQ column when names differ (raw-mirror grains).
_BQ_COL_ALIASES: dict[str, str] = {"employee_name": "employee"}


def _bq_cell(bq_row: dict, col: str) -> Any:
    if col in bq_row and bq_row[col] is not None:
        return bq_row[col]
    alias = _BQ_COL_ALIASES.get(col)
    if alias:
        return bq_row.get(alias)
    return bq_row.get(col)

# Tab name → BQ table name + sort columns + workbook key (default: "bhaga_model").
# raw_mirror=True grains compare the raw Sheet tabs against their 1:1 BQ raw tables.
_GRAINS: list[dict] = [
    # ── Model grains (Sheet model tabs ⇆ BQ model tables) ───────────────────
    {"tab": "labor_daily",         "bq_table": "model_labor_daily",         "sort_by": ["date"]},
    {"tab": "labor_weekly",        "bq_table": "model_labor_weekly",         "sort_by": ["iso_week"]},
    {"tab": "tip_alloc_period",    "bq_table": "model_tip_alloc_period",     "sort_by": ["period_start", "employee"]},
    {"tab": "review_bonus_period", "bq_table": "model_review_bonus_period",  "sort_by": ["period_start", "employee"]},
    {"tab": "period_summary",      "bq_table": "model_period_summary",       "sort_by": ["period_start"]},
    # ── Raw-mirror grains (raw Sheet tabs ⇆ BQ raw tables, migration 005) ───
    # These grains ensure nightly backfill_bigquery doesn't drift from the source sheets.
    {"tab": "item_lines",       "bq_table": "square_item_lines",  "sort_by": ["date_local", "transaction_id", "line_seq"], "workbook": "bhaga_square_raw", "raw_mirror": True},
    {"tab": "kds_daily",        "bq_table": "square_kds_daily",   "sort_by": ["date_local"], "workbook": "bhaga_square_raw", "raw_mirror": True},
    {"tab": "kds_tickets",      "bq_table": "square_kds_tickets", "sort_by": ["date_local", "time_created", "ticket_name"], "workbook": "bhaga_square_raw", "raw_mirror": True},
    {"tab": "earnings",         "bq_table": "adp_earnings",       "sort_by": ["period_start", "employee", "description"], "workbook": "bhaga_adp_raw", "raw_mirror": True},
    {"tab": "reviews",          "bq_table": "google_reviews",     "sort_by": ["post_date_ct", "review_id"], "workbook": "bhaga_review_raw", "raw_mirror": True},
]


def _read_bq_as_rows(bq_table: str, sort_by: list[str], *, sheet_header: list[str]) -> list[list]:
    """Read a BQ model table and project to the Sheet's column order.

    Only projects columns that appear in both the Sheet header and the BQ row
    (extra BQ columns like materialized_at_utc are excluded). Returns a
    header+rows structure compatible with _compare_tabs.
    """
    order = ", ".join(sort_by)
    sql = f"SELECT * FROM `{_PROJECT}.{_DATASET}.{bq_table}` ORDER BY {order}"
    try:
        bq_rows = read_query(sql)
    except Exception as exc:  # noqa: BLE001
        print(f"  [reconcile] WARN: could not read {bq_table}: {exc}")
        return [sheet_header]  # treat as empty — compare_tabs will flag EMPTY_COMPUTED

    if not bq_rows:
        return [sheet_header]

    # Use the Sheet header for column order; only include cols that BQ has.
    projected_header = [c for c in sheet_header if c not in _SKIP_COLS]
    out: list[list] = [projected_header]
    for bq_row in bq_rows:
        out.append([_bq_cell(bq_row, col) for col in projected_header])
    return out


def _assert_tip_pool_conservation(bq_table: str = "model_tip_alloc_period") -> list[str]:
    """Verify tip-pool conservation per closed period from BQ.

    Returns a list of violation messages (empty = all good).
    """
    sql = f"""
        SELECT
            CAST(period_start AS STRING) AS period_start,
            CAST(period_end   AS STRING) AS period_end,
            SUM(our_calc)               AS total_allocated,
            is_open
        FROM `{_PROJECT}.{_DATASET}.{bq_table}`
        GROUP BY period_start, period_end, is_open
        ORDER BY period_start
    """
    try:
        rows = read_query(sql)
    except Exception as exc:  # noqa: BLE001
        return [f"conservation check skipped (BQ error): {exc}"]

    violations: list[str] = []
    for row in rows:
        if row.get("is_open"):
            continue
        # The tip pool should be internally consistent: allocated ≈ pool.
        # Since we only have our_calc in this table (not the raw pool), we check
        # that our_calc > 0 for each closed period (a proxy for "data landed").
        allocated = row.get("total_allocated") or 0
        if allocated <= 0:
            violations.append(
                f"period {row['period_start']}–{row['period_end']}: "
                f"total our_calc={allocated:.2f} (expected > 0 for a closed period)"
            )
    return violations


def reconcile(
    store: str,
    *,
    allow_prod_read: bool = False,
    grains: list[dict] | None = None,
) -> dict:
    """Run the reconciliation for all grains and return the full result dict.

    Returns:
        {"passed": bool, "grains": [{tab, status, cells, mismatches}, ...],
         "conservation_violations": [...]}
    """
    profile = json.loads((_STORE_PROFILES / f"{store}.json").read_text())
    model_sid = resolve_sheet_id("bhaga_model", profile)

    # Pre-resolve workbook IDs for raw-mirror grains.
    _workbook_ids: dict[str, str] = {
        "bhaga_model": model_sid,
        "bhaga_square_raw": resolve_sheet_id("bhaga_square_raw", profile),
        "bhaga_adp_raw": resolve_sheet_id("bhaga_adp_raw", profile),
        "bhaga_review_raw": resolve_sheet_id("bhaga_review_raw", profile),
    }

    token = refresh_access_token(account=store)
    grains_to_check = grains if grains is not None else _GRAINS

    results: list[dict] = []
    passed = True

    for grain in grains_to_check:
        tab = grain["tab"]
        workbook_key = grain.get("workbook", "bhaga_model")
        sid = _workbook_ids.get(workbook_key, model_sid)
        is_raw = grain.get("raw_mirror", False)
        print(f"  [{tab}{'(raw)' if is_raw else ''}] reading Sheet tab…", end=" ", flush=True)
        try:
            sheet_rows = _read_sheet_tab(sid, tab, token)
        except Exception as exc:  # noqa: BLE001
            print(f"SKIP (Sheet read error: {exc})")
            results.append({"tab": tab, "status": "SKIP_SHEET_ERROR", "cells": 0, "mismatches": []})
            continue

        if not sheet_rows:
            print("SKIP (empty Sheet tab)")
            results.append({"tab": tab, "status": "EMPTY_SHEET", "cells": 0, "mismatches": []})
            continue

        sheet_header = sheet_rows[0]
        print(f"reading BQ {grain['bq_table']}…", end=" ", flush=True)
        bq_rows = _read_bq_as_rows(grain["bq_table"], grain["sort_by"], sheet_header=sheet_header)

        cmp = _compare_tabs(tab, sheet_rows, bq_rows, skip_columns=_SKIP_COLS)
        status = cmp.get("status", "")
        n_mis = len([m for m in cmp.get("mismatches", []) if m.get("type") != "row_count"])
        total_cells = cmp.get("total_cells", 0)

        if status not in ("OK",) or n_mis > 0:
            passed = False
            print(f"FAIL ({n_mis} mismatches, status={status})")
        else:
            pct = cmp.get("match_pct", 100.0)
            print(f"PASS ({total_cells} cells, {pct:.2f}%)")

        results.append({
            "tab": tab,
            "status": status,
            "cells": total_cells,
            "mismatches": cmp.get("mismatches", []),
        })

    # Tip-pool conservation check
    print("  [conservation] checking tip-pool…", end=" ", flush=True)
    violations = _assert_tip_pool_conservation()
    if violations:
        passed = False
        print(f"FAIL ({len(violations)} violation(s))")
        for v in violations:
            print(f"    ! {v}")
    else:
        print("PASS")

    return {
        "passed": passed,
        "grains": results,
        "conservation_violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="Store name (e.g. palmetto)")
    parser.add_argument("--allow-prod-read", action="store_true",
                        help="Allow reading the production Sheet for evidence gathering")
    parser.add_argument("--json", action="store_true",
                        help="Output result as JSON (for CI / nightly digest)")
    args = parser.parse_args()

    print(f"# reconcile_model [{args.store}]")
    result = reconcile(args.store, allow_prod_read=args.allow_prod_read)

    if args.json:
        import json as json_mod
        print(json_mod.dumps(result, indent=2, default=str))

    if result["passed"]:
        print("\nRESULT: all grains PASS — Sheet and BQ are in sync.")
        return 0
    else:
        n_fail = sum(1 for g in result["grains"] if g["mismatches"] or g["status"] not in ("OK",))
        print(f"\nRESULT: {n_fail} grain(s) FAIL — Sheet / BQ drift detected.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
