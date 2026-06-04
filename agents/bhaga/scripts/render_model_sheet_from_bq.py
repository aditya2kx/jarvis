#!/usr/bin/env python3
"""Render BQ model tables → Google Sheet model tabs.

This is the Sheet-side sink for the BQ-canonical pipeline path
(BHAGA_SHEET_FROM_BQ=1). It reads each BQ model table, projects columns
into the exact build_* header order, converts values to Sheet-compatible
representations, then calls clear_and_write_tab + the existing formatters
from update_model_sheet.py.

Operator INPUT tabs (config, training_excluded, training_shifts) are never
written by this projector — only read by the compute step (materialize_model_bq).

Usage (flag on):
    BHAGA_SHEET_FROM_BQ=1 BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.render_model_sheet_from_bq --store palmetto

Usage (dry-run):
    BHAGA_SHEET_FROM_BQ=1 BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.render_model_sheet_from_bq --store palmetto --dry-run
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from core.datastore import read_query
from core.config_loader import refresh_access_token, resolve_sheet_id
from agents.bhaga.scripts.update_model_sheet import (
    clear_and_write_tab,
    format_currency_columns,
    format_number_columns,
    bold_header_row,
    add_sheet_if_missing,
)
from skills.bhaga_config.dates import _iso_date_for_sheet_cell

_PROJECT = "jarvis-bhaga-prod"
_DATASET = "bhaga"
_STORE_PROFILES = pathlib.Path(__file__).resolve().parents[3] / "agents" / "bhaga" / "knowledge-base" / "store-profiles"

# Boolean columns that the build_* functions write as "yes" / "no" (NOT "TRUE"/"FALSE").
# All other BOOL columns are written as Python bools ("TRUE"/"FALSE" in the Sheet).
_YES_NO_BOOL_COLS: frozenset[str] = frozenset({
    "is_open",
})

# Boolean columns written as plain string "Y"/"N" (labor_period, labor_weekly).
_Y_N_BOOL_COLS: frozenset[str] = frozenset({
    "is_partial",
})

# --------------------------------------------------------------------------
# Tab specs: each entry describes one Sheet tab ↔ BQ table mapping.
# header:        the exact column list that build_* writes (defines projection order).
# bq_table:      the BQ model_* table to read from.
# sort_by:       ORDER BY columns for deterministic row order matching build_* output.
# currency_cols: 0-indexed column positions for format_currency_columns.
# number_cols:   0-indexed column positions for format_number_columns (non-currency numerics).
# --------------------------------------------------------------------------

_DAILY_HEADER = [
    "date", "dow",
    "gross_sales", "tip_pool", "tips_pct_of_sales",
    "team_hours_eligible", "team_hours_total",
    "pool_per_hour", "txn_count",
]

_LABOR_DAILY_HEADER = [
    "date", "dow",
    "gross_sales", "discounts", "net_sales", "tip_pool", "net_sales_plus_tips",
    "orders",
    "hourly_hours", "hourly_labor_cost",
    "fulltime_hours", "fulltime_labor_cost", "total_labor_cost",
    "hourly_pct_of_net_sales", "hourly_pct_of_net_sales_plus_tips",
    "fulltime_pct_of_net_sales", "fulltime_pct_of_net_sales_plus_tips",
    "total_labor_pct_of_net_sales", "total_labor_pct_of_net_sales_plus_tips",
    "tips_pct_of_net_sales",
    "all_in_cost_pct_of_net_sales_plus_tips",
    "hourly_labor_per_order", "fulltime_labor_per_order", "total_labor_per_order",
    "orders_per_labor_hour", "peak_hour_orders_per_labor_hour",
    "over_saturation",
    "hours_per_order", "avg_order_price", "avg_net_sales_plus_tips_per_order",
    "items_sold", "avg_items_per_order", "hours_per_item", "avg_item_price",
    "hourly_hours_per_order", "fulltime_hours_per_order",
    "hourly_hours_per_item", "fulltime_hours_per_item",
    "kds_completed_tickets", "kds_completed_items",
    "kds_median_time_per_item_sec", "kds_p90_time_per_item_sec",
    "kds_p95_time_per_item_sec", "kds_p99_time_per_item_sec",
    "kds_pct_items_over_goal", "kds_pct_tickets_late",
    "outlier_flag", "forecast_exclude",
    "outlier_reason", "forecast_exclude_reason",
]

_LABOR_WEEKLY_HEADER = [
    "iso_week", "week_start", "week_end", "is_partial", "days_covered",
    "gross_sales", "discounts", "net_sales", "tip_pool", "net_sales_plus_tips",
    "orders",
    "hourly_hours", "hourly_labor_cost",
    "fulltime_hours", "fulltime_labor_cost", "total_labor_cost",
    "hourly_pct_of_net_sales", "hourly_pct_of_net_sales_plus_tips",
    "fulltime_pct_of_net_sales", "fulltime_pct_of_net_sales_plus_tips",
    "total_labor_pct_of_net_sales", "total_labor_pct_of_net_sales_plus_tips",
    "tips_pct_of_net_sales",
    "all_in_cost_pct_of_net_sales_plus_tips",
    "hourly_labor_per_order", "fulltime_labor_per_order", "total_labor_per_order",
    "orders_per_labor_hour", "peak_hour_orders_per_labor_hour",
    "over_saturation",
    "hours_per_order", "avg_order_price", "avg_net_sales_plus_tips_per_order",
    "items_sold", "avg_items_per_order", "hours_per_item", "avg_item_price",
    "hourly_hours_per_order", "fulltime_hours_per_order",
    "hourly_hours_per_item", "fulltime_hours_per_item",
    "kds_completed_tickets", "kds_completed_items",
    "kds_median_time_per_item_sec", "kds_p90_time_per_item_sec",
    "kds_p95_time_per_item_sec", "kds_p99_time_per_item_sec",
    "kds_pct_items_over_goal", "kds_pct_tickets_late",
]

_LABOR_PERIOD_HEADER = [
    "pay_period_start", "pay_period_end", "is_open", "days_covered",
    "gross_sales", "discounts", "net_sales", "tip_pool", "net_sales_plus_tips",
    "orders",
    "hourly_hours", "hourly_labor_cost",
    "fulltime_hours", "fulltime_labor_cost", "total_labor_cost",
    "hourly_pct_of_net_sales", "hourly_pct_of_net_sales_plus_tips",
    "fulltime_pct_of_net_sales", "fulltime_pct_of_net_sales_plus_tips",
    "total_labor_pct_of_net_sales", "total_labor_pct_of_net_sales_plus_tips",
    "tips_pct_of_net_sales",
    "all_in_cost_pct_of_net_sales_plus_tips",
    "hourly_labor_per_order", "fulltime_labor_per_order", "total_labor_per_order",
    "orders_per_labor_hour", "peak_hour_orders_per_labor_hour",
    "over_saturation",
    "hours_per_order", "avg_order_price", "avg_net_sales_plus_tips_per_order",
    "items_sold", "avg_items_per_order", "hours_per_item", "avg_item_price",
    "hourly_hours_per_order", "fulltime_hours_per_order",
    "hourly_hours_per_item", "fulltime_hours_per_item",
    "kds_completed_tickets", "kds_completed_items",
    "kds_median_time_per_item_sec", "kds_p90_time_per_item_sec",
    "kds_p95_time_per_item_sec", "kds_p99_time_per_item_sec",
    "kds_pct_items_over_goal", "kds_pct_tickets_late",
]

_TIP_ALLOC_PERIOD_HEADER = [
    "period_start", "period_end", "coverage", "is_open",
    "employee", "hours_worked",
    "our_calc", "adp_paid", "diff", "diff_pct",
    "our_per_hour", "adp_per_hour", "likely_reason",
]

_TIP_ALLOC_DAILY_HEADER = [
    "date", "dow", "period_start", "period_end",
    "employee", "hours_worked",
    "day_pool", "team_hours_eligible", "pct_of_day_hours", "our_share",
]

_PERIOD_SUMMARY_HEADER = [
    "period_start", "period_end", "coverage", "is_open", "check_dates",
    "employees_count", "team_hours", "tip_pool",
    "our_total_allocated", "adp_total_paid", "total_diff",
    "employees_with_diff_over_1usd",
]

_REVIEW_BONUS_PERIOD_HEADER = [
    "period_start", "period_end", "is_open", "employee",
    "reviews_credited", "named_count",
    "base_dollars", "named_dollars", "total_bonus", "likely_reason",
]

# currency_cols are 0-indexed matching the tab payloads in update_model_sheet main().
_TAB_SPECS: list[dict] = [
    {
        "tab": "daily",
        "bq_table": "model_daily",
        "sort_by": ["date"],
        "header": _DAILY_HEADER,
        "currency_cols": [2, 3, 7],
        "number_cols": [4, 5, 6, 8],
    },
    {
        "tab": "labor_daily",
        "bq_table": "model_labor_daily",
        "sort_by": ["date"],
        "header": _LABOR_DAILY_HEADER,
        "currency_cols": [2, 3, 4, 5, 6, 9, 11, 12, 21, 22, 23, 28, 29, 33],
        "number_cols": [7, 8, 10, 13, 14, 15, 16, 17, 18, 19, 20, 24, 25, 27, 30, 31, 32, 34, 35, 36, 37],
    },
    {
        "tab": "labor_weekly",
        "bq_table": "model_labor_weekly",
        "sort_by": ["iso_week"],
        "header": _LABOR_WEEKLY_HEADER,
        "currency_cols": [5, 6, 7, 8, 9, 12, 14, 15, 24, 25, 26, 31, 32, 36],
        "number_cols": [10, 11, 13, 16, 17, 18, 19, 20, 21, 22, 23, 27, 28, 30, 33, 34, 35, 37, 38],
    },
    {
        "tab": "labor_period",
        "bq_table": "model_labor_period",
        "sort_by": ["pay_period_start"],
        "header": _LABOR_PERIOD_HEADER,
        "currency_cols": [4, 5, 6, 7, 8, 11, 13, 14, 23, 24, 25, 30, 31, 35],
        "number_cols": [9, 10, 12, 15, 16, 17, 18, 19, 20, 21, 22, 26, 27, 29, 32, 33, 34, 36, 37],
    },
    {
        "tab": "tip_alloc_period",
        "bq_table": "model_tip_alloc_period",
        "sort_by": ["period_start", "employee"],
        "header": _TIP_ALLOC_PERIOD_HEADER,
        "currency_cols": [6, 7, 8, 10, 11],
        "number_cols": [5, 9],
    },
    {
        "tab": "tip_alloc_daily",
        "bq_table": "model_tip_alloc_daily",
        "sort_by": ["date", "employee"],
        "header": _TIP_ALLOC_DAILY_HEADER,
        "currency_cols": [6, 9],
        "number_cols": [5, 7, 8],
    },
    {
        "tab": "period_summary",
        "bq_table": "model_period_summary",
        "sort_by": ["period_start"],
        "header": _PERIOD_SUMMARY_HEADER,
        "currency_cols": [7, 8, 9, 10],
        "number_cols": [5, 6, 11],
    },
    {
        "tab": "review_bonus_period",
        "bq_table": "model_review_bonus_period",
        "sort_by": ["period_start", "employee"],
        "header": _REVIEW_BONUS_PERIOD_HEADER,
        "currency_cols": [6, 7, 8],
        "number_cols": [4, 5],
    },
]


def _render_cell(col_name: str, value) -> object:
    """Convert a BQ-typed value to the Sheet cell representation.

    Rules (inverse of materialize_model_bq._coerce):
    - None → ""
    - datetime.date → "'YYYY-MM-DD" (apostrophe prefix so Sheet keeps as text)
    - is_open (yes/no bool) → "yes" / "no"
    - is_partial (Y/N bool) → "Y" / "N"
    - other bool → "TRUE" / "FALSE"
    - float → round to 6 significant digits (Sheet handles display formatting)
    - int → leave as int
    - str → leave as-is
    """
    if value is None:
        return ""
    if isinstance(value, (datetime.date, datetime.datetime)) and not isinstance(value, datetime.datetime):
        return _iso_date_for_sheet_cell(value.isoformat())
    if isinstance(value, bool):
        if col_name in _YES_NO_BOOL_COLS:
            return "yes" if value else "no"
        if col_name in _Y_N_BOOL_COLS:
            return "Y" if value else "N"
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        # Keep 6 significant digits to avoid float noise; Sheet formats via column format.
        if value == int(value):
            return int(value)
        return round(value, 6)
    return value


def _read_bq_tab(spec: dict) -> list[list]:
    """Read a BQ model table and project to the tab header order."""
    table = f"`{_PROJECT}.{_DATASET}.{spec['bq_table']}`"
    order_cols = ", ".join(spec["sort_by"])
    sql = f"SELECT * FROM {table} ORDER BY {order_cols}"
    try:
        rows = read_query(sql)
    except Exception as exc:  # noqa: BLE001
        print(f"  [render] WARN: could not read {spec['bq_table']}: {exc}")
        return [spec["header"]]  # header-only on error

    header = spec["header"]
    out: list[list] = [header]
    for bq_row in rows:
        out.append([_render_cell(col, bq_row.get(col)) for col in header])
    return out


def render(store: str, *, dry_run: bool = False) -> None:
    """Read each BQ model table and write the corresponding Sheet model tab.

    Operator input tabs (config, training_excluded, training_shifts,
    labor_daily_forecast) are never written — only read by the compute step.
    Skips the write if BHAGA_SHEET_FROM_BQ is not set (called only when enabled).
    """
    profile = json.loads((_STORE_PROFILES / f"{store}.json").read_text())
    model_sid = resolve_sheet_id("bhaga_model", profile)

    if not dry_run:
        token = refresh_access_token(account=store)
    else:
        token = None

    print(f"# render_model_sheet_from_bq [{store}] dry_run={dry_run} sheet={model_sid}")

    for spec in _TAB_SPECS:
        rows = _read_bq_tab(spec)
        n_data = len(rows) - 1
        print(f"  {spec['tab']:<24} {n_data:>5} rows from {spec['bq_table']}")

        if dry_run:
            continue

        sheet_id = add_sheet_if_missing(model_sid, token, tab_name=spec["tab"], column_count=len(spec["header"]) + 2)
        clear_and_write_tab(model_sid, token, tab_name=spec["tab"], values=rows)
        bold_header_row(model_sid, token, sheet_id=sheet_id)

        if spec["currency_cols"]:
            format_currency_columns(model_sid, token, sheet_id=sheet_id, column_indices=spec["currency_cols"])
        if spec.get("number_cols"):
            format_number_columns(model_sid, token, sheet_id=sheet_id, column_indices=spec["number_cols"])

    print("# render_model_sheet_from_bq done.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="Store name (e.g. palmetto)")
    parser.add_argument("--dry-run", action="store_true", help="Print row counts without writing to Sheet")
    args = parser.parse_args()
    render(args.store, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
