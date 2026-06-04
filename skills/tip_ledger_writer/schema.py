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
            "tab_name": "earnings",
            "natural_key_columns": (
                "period_start", "period_end", "employee_name", "description", "check_date",
            ),
            "header": [
                "period_start", "period_end", "check_date",
                "employee_name", "raw_employee_name", "description",
                "hours", "hourly_rate", "amount", "scraped_at_utc",
            ],
            "notes": (
                "Per earning-line from ADP Earnings & Hours XLSX. One row per "
                "employee per pay period per earning code. amount can be negative "
                "(voids/reissues). Codes: Regular, Overtime, Bonus, "
                "Credit Card Tips Owed, Misc reimbursement non-taxable. "
                "Source: compensation_backend.parse_xlsx."
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
        {
            "tab_name": "item_daily_rollup",
            "natural_key_columns": ("date_local",),
            "header": [
                "date_local", "items_sold", "units_sold",
                "gross_sales_cents", "avg_item_price_cents",
                "scraped_at_utc",
            ],
            "notes": (
                "Per-shop-local-day item-level rollup. Source: "
                "skills/square_tips/transactions_backend.aggregate_daily_item_stats. "
                "Natural key: (date_local,). items_sold = count of item line items; "
                "units_sold = sum of qty; avg_item_price_cents = gross_sales / items_sold."
            ),
        },
        {
            "tab_name": "item_lines",
            "natural_key_columns": (
                "transaction_id", "item_name", "item_sold_at_local", "line_seq",
            ),
            "header": [
                "date_local", "item_sold_at_local", "item_name", "category",
                "qty_sold", "gross_sales_cents", "discount_cents", "net_sales_cents",
                "event_type", "transaction_id", "payment_id", "location", "channel",
                "line_seq", "scraped_at_utc",
            ],
            "notes": (
                "One row per Square Item Sales Detail line. Source: "
                "transactions_backend.parse_item_sales_csv. Natural key: "
                "(transaction_id, item_name, item_sold_at_local, line_seq). "
                "line_seq is the 0-based index AMONG lines sharing that same "
                "(transaction_id, item_name, item_sold_at_local) — a per-group "
                "counter (NOT a file-global index), so the key is stable across "
                "differently-windowed re-exports and replay never duplicates a line."
            ),
        },
        {
            "tab_name": "kds_daily",
            "natural_key_columns": ("date_local",),
            "header": [
                "date_local", "completed_tickets", "completed_items",
                "median_time_per_item_sec",
                "p90_time_per_item_sec", "p95_time_per_item_sec",
                "p99_time_per_item_sec",
                "pct_tickets_late",
                "shift_start", "shift_end",
                "late_tickets", "due_tickets",
                "per_item_times_json",
                "scraped_at_utc",
            ],
            "notes": (
                "Per-shop-local-day KDS OPERATIONAL-EFFICIENCY aggregates. Source: "
                "skills/square_tips/transactions_backend.aggregate_daily_kds_stats. "
                "Natural key: (date_local,). Only filter is the 15s lower floor "
                "(KDS cleared without actual prep) — NO upper cap, the full tail is "
                "surfaced (p90/p95/p99). per_item_times_json is the item-weighted "
                "per-item-seconds distribution so weekly/period rollups pool it for "
                "EXACT percentiles + kds_pct_items_over_goal. avg_time_per_item_sec "
                "was removed (percentiles + median replace it)."
            ),
        },
        {
            "tab_name": "kds_tickets",
            "natural_key_columns": ("date_local", "time_created", "ticket_name"),
            "header": [
                "date_local", "device_name", "ticket_name", "order_source",
                "num_items", "items_in_ticket", "completion_time_sec",
                "time_created", "time_completed", "time_due", "scraped_at_utc",
            ],
            "notes": (
                "Per-ticket KDS rows — NEW grain. Needed for the 'Slow Items — "
                "Investigation' Grafana panel. Per-item minutes = "
                "completion_time_sec / num_items. Natural key: "
                "(date_local, time_created, ticket_name). Source: "
                "transactions_backend.parse_kds_csv."
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
        {
            "tab_name": "item_operations",
            "natural_key_columns": (
                "transaction_id", "item_name", "item_sold_at_local", "line_seq",
            ),
            "header": [
                "date_local", "item_sold_at_local", "dow_label", "item_name",
                "category", "qty_sold", "gross_sales_dollars", "discount_dollars",
                "net_sales_dollars", "event_type", "transaction_id",
                "staff_punched_in_hourly_count", "staff_punched_in_fulltime_count",
                "staff_punched_in_total_count", "line_seq",
            ],
            "notes": (
                "Item-level operations view: one row per Square item line with "
                "staff punched-in headcounts at item_sold_at_local. Upserted "
                "incrementally (not full-tab rewrite). Source: item_lines + punches."
            ),
        },
    ],
}


WORKBOOK_SCHEMAS["BHAGA Review Raw"] = [
    {
        "tab_name": "reviews",
        "natural_key_columns": ("review_id",),
        "header": [
            "review_id", "post_ts_ct", "post_date_ct", "rating", "reviewer",
            "comment", "named_baristas", "named_status", "shift_date_credited",
            "shift_assignment_reason", "shift_members", "trainees_on_shift",
            "named_credit_each", "base_credit_each", "total_bonus",
            "review_url", "clickup_message_id", "ingested_at_utc",
        ],
        "notes": (
            "Per-review audit trail. review_id is a sha1 content hash (stable "
            "across re-ingestion). Source: process_reviews.py; header matches "
            "REVIEW_HEADER_ROW. BQ mirror: google_reviews (clickup_message_id "
            "is Sheet-only — not in the BQ table)."
        ),
    },
]


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
