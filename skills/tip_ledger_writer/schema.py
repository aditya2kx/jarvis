#!/usr/bin/env python3
"""Single source of truth for BHAGA's three-workbook schema.

Imported by:
    * `agents/bhaga/scripts/bootstrap_sheets.py` — one-time workbook creation,
      seeds tabs and header rows.
    * `skills/tip_ledger_writer/writer.py` — runtime idempotent writes, uses
      header order to map record dicts to row tuples.

When you add a column here, both producers stay in sync automatically.
"""

from __future__ import annotations


# Header columns are the contract between the source skill (e.g.
# adp_run_automation.shift_backend.daily_shifts) and the destination tab.
# Order matters — that's the order columns appear in the sheet.
#
# `notes` is dropped into the sheet itself (column N+2 of row 1) by the
# bootstrap script, so a human opening the sheet sees the usage hint inline.
WORKBOOK_SCHEMAS: dict[str, list[dict]] = {
    "BHAGA ADP Raw": [
        {
            "tab_name": "shifts",
            "natural_key_columns": ("date", "employee_id"),
            "header": [
                "date", "employee_id", "employee_name", "raw_employee_name",
                "in_time", "out_time", "regular_hours", "ot_hours",
                "doubletime_hours", "total_hours", "punch_count",
                "pay_period", "scraped_at_utc",
            ],
            "notes": (
                "One row per (employee, date). Source: skills/adp_run_automation/shift_backend.daily_shifts. "
                "Natural key: (date, employee_id). Idempotent upserts on re-write."
            ),
        },
        {
            "tab_name": "punches",
            "natural_key_columns": ("date", "employee_id", "punch_idx_in_day"),
            "header": [
                "date", "employee_id", "employee_name", "raw_employee_name",
                "punch_idx_in_day", "in_time", "out_time",
                "regular_hours", "ot_hours", "doubletime_hours",
                "pay_period", "scraped_at_utc",
            ],
            "notes": (
                "Per-punch granularity (split shifts emit multiple rows). Source: "
                "skills/adp_run_automation/shift_backend.raw_punches. Natural key: "
                "(date, employee_id, punch_idx_in_day)."
            ),
        },
        {
            "tab_name": "wage_rates",
            "natural_key_columns": ("employee_id",),
            "header": [
                "employee_id", "employee_name", "wage_rate_dollars",
                "ot_rate_dollars", "is_salaried", "multi_rate",
                "excluded_from_labor_pct", "rate_history_json",
                "raw_employee_names_json", "scraped_at_utc",
            ],
            "notes": (
                "One row per employee. Source: skills/adp_run_automation/compensation_backend.compensation. "
                "wage_rate_dollars is the most recent Regular rate; rate_history_json is the "
                "full audit trail. excluded_from_labor_pct mirrors store_profile."
            ),
        },
    ],
    "BHAGA Square Raw": [
        {
            "tab_name": "transactions",
            "natural_key_columns": ("transaction_id",),
            "header": [
                "transaction_id", "event_type",
                "created_at_src_iso", "created_at_local_iso",
                "date_local", "hour_local", "dow_local",
                "gross_sales_cents", "discount_cents", "tip_cents",
                "net_total_cents", "total_collected_cents",
                "source", "staff_name", "location",
                "raw_date_csv", "raw_time_csv", "raw_tz_csv",
                "scraped_at_utc",
            ],
            "notes": (
                "One row per Square transaction. Source: skills/square_tips/transactions_backend.parse_csv. "
                "Natural key: transaction_id. created_at_local_iso is shop-local (America/Chicago); "
                "raw account TZ (ET) preserved in created_at_src_iso plus raw_date_csv/raw_time_csv/raw_tz_csv for audit."
            ),
        },
        {
            "tab_name": "daily_rollup",
            "natural_key_columns": ("date_local",),
            "header": [
                "date_local", "txn_count", "gross_sales_cents",
                "tip_cents", "net_sales_cents", "refund_cents",
                "scraped_at_utc",
            ],
            "notes": (
                "Per-shop-local-day rollup. Derived from transactions tab. Convenience "
                "snapshot so the model doesn't need to re-aggregate every refresh."
            ),
        },
    ],
    "BHAGA Model": [
        {
            "tab_name": "config",
            "natural_key_columns": ("key",),
            "header": ["key", "value", "notes"],
            "notes": (
                "Hand-edited configuration that the model sheet references. Examples: "
                "raw_adp_sheet_id, raw_square_sheet_id, store_tz, store_close_time, "
                "excluded_from_labor_pct (comma-sep), employee_aliases_json. Avoids "
                "hardcoding IDs in formulas."
            ),
        },
        {
            "tab_name": "daily",
            "natural_key_columns": ("date_local",),
            "header": [
                "date_local", "dow_label",
                "hours_total", "hours_eligible_for_tip_pool",
                "labor_cost_dollars", "sales_dollars", "labor_pct",
                "tips_dollars", "tips_per_hour",
                "transaction_count", "avg_ticket_dollars",
            ],
            "notes": (
                "One row per shop-local-day. Hours/labor exclude employees in store_profile "
                "excluded_from_labor_pct. Built via IMPORTRANGE + SUMPRODUCT in M2."
            ),
        },
        {
            "tab_name": "tip_alloc_daily",
            "natural_key_columns": ("date_local", "employee_id"),
            "header": [
                "date_local", "employee_id", "employee_name",
                "hours_worked", "share_of_day_hours_pct",
                "tip_pool_dollars", "tip_allocation_dollars",
            ],
            "notes": (
                "Pool-by-day fair-share output. One row per (date, employee). "
                "Source: skills/tip_pool_allocation.allocate, with manager excluded. M2."
            ),
        },
        {
            "tab_name": "dow_hour",
            "natural_key_columns": ("dow_local", "hour_local"),
            "header": [
                "dow_local", "dow_label", "hour_local",
                "transaction_count_28d", "sales_dollars_28d", "tips_dollars_28d",
                "avg_sales_per_day", "avg_tips_per_day",
            ],
            "notes": (
                "Trailing-28-day day-of-week x hour aggregation for the heatmap chart. "
                "168 rows (7 days x 24 hours). Refreshed daily by formula. M2."
            ),
        },
        {
            "tab_name": "period_summary",
            "natural_key_columns": ("pay_period",),
            "header": [
                "pay_period", "period_start", "period_end",
                "hours_total", "labor_cost_dollars",
                "sales_dollars", "labor_pct",
                "tips_dollars",
            ],
            "notes": (
                "Per-pay-period rollup for the pay-period close workflow (M4). "
                "Boundaries from store_profile.pay_period_definition."
            ),
        },
    ],
}


def get_tab_spec(workbook_title: str, tab_name: str) -> dict:
    """Return the tab spec for a given workbook+tab. Raises KeyError if not found."""
    if workbook_title not in WORKBOOK_SCHEMAS:
        raise KeyError(
            f"Unknown workbook '{workbook_title}'. Known: {list(WORKBOOK_SCHEMAS)}"
        )
    for spec in WORKBOOK_SCHEMAS[workbook_title]:
        if spec["tab_name"] == tab_name:
            return spec
    raise KeyError(
        f"Unknown tab '{tab_name}' in workbook '{workbook_title}'. "
        f"Known: {[s['tab_name'] for s in WORKBOOK_SCHEMAS[workbook_title]]}"
    )
