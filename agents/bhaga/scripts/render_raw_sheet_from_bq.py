#!/usr/bin/env python3
"""Render BigQuery raw tables → Google Sheet raw tabs (incremental upsert).

This is the Sheet-projection step of the BQ-primary pipeline. After scrape
data has landed in BigQuery (via backfill_from_downloads or process_reviews),
this script reads each raw BQ table and incrementally upserts the corresponding
Sheet tab using the existing write_raw_* functions — which key by natural_key_columns
so historical rows outside the window are preserved.

Flow:
    BQ raw table  →  inverse-map → Sheet-header dict  →  write_raw_*(upsert)

The render step is NON-FATAL by default: a Sheet projection failure must not
fail the nightly run since BigQuery already holds the data as system of record.

Usage:
    BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.render_raw_sheet_from_bq --store palmetto
    BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.render_raw_sheet_from_bq --store palmetto \\
            --since 2026-05-01 --dry-run
    BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.render_raw_sheet_from_bq --store palmetto \\
            --tabs reviews
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from core.config_loader import resolve_sheet_id
from core.datastore import dataset, read_query
from skills.tip_ledger_writer.writer import (
    write_raw_adp_earnings,
    write_raw_adp_punches,
    write_raw_adp_rates,
    write_raw_adp_shifts,
    write_raw_google_reviews,
    write_raw_kds_daily,
    write_raw_kds_tickets,
    write_raw_square_daily_rollup,
    write_raw_square_item_daily_rollup,
    write_raw_square_item_lines,
    write_raw_square_transactions,
)

_PROJECT = "jarvis-bhaga-prod"
_DATASET = dataset()  # env-driven (BHAGA_BQ_DATASET); prod `bhaga` by default
_DEFAULT_LOOKBACK_DAYS = 120

# ---------------------------------------------------------------------------
# Inverse mappers: BQ row dict → Sheet-header dict
# ---------------------------------------------------------------------------
# Rules:
#   - BQ canonical_name → Sheet employee_name
#   - BQ shift_count → Sheet punch_count (adp_shifts)
#   - BQ punch_index → Sheet punch_idx_in_day (adp_punches)
#   - BQ earnings_json → Sheet rate_history_json (pass under *_json key; writer fallback)
#   - BQ employee → Sheet employee_name (adp_earnings)
#   - JSON columns: pass under the *_json key (already serialized string from BQ)
#   - Drop BQ-only columns absent from the Sheet header
#   - dates/datetimes: str() gives YYYY-MM-DD or ISO strings, which Sheet accepts
# ---------------------------------------------------------------------------


def _str_date(v) -> str:
    """Coerce a BQ date/string/None to YYYY-MM-DD string."""
    if v is None:
        return ""
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()[:10]
    return str(v)


def _str_ts(v) -> str:
    """Coerce a BQ timestamp/string/None to ISO string."""
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        return v.isoformat()
    return str(v)


def _inv_adp_shift(row: dict) -> dict:
    return {
        "date": _str_date(row.get("date")),
        "employee_id": str(row.get("employee_id", "")),
        "employee_name": str(row.get("canonical_name", "")),
        "raw_employee_name": str(row.get("raw_employee_name", "")),
        "in_time": str(row.get("in_time", "")),
        "out_time": str(row.get("out_time", "")),
        "regular_hours": row.get("regular_hours") or 0.0,
        "ot_hours": row.get("ot_hours") or 0.0,
        "doubletime_hours": row.get("doubletime_hours") or 0.0,
        "total_hours": row.get("total_hours") or 0.0,
        "punch_count": row.get("shift_count") or 1,
        "pay_period": "",
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_adp_punch(row: dict) -> dict:
    return {
        "date": _str_date(row.get("date")),
        "employee_id": str(row.get("employee_id", "")),
        "employee_name": str(row.get("canonical_name", "")),
        "raw_employee_name": str(row.get("raw_employee_name", "")),
        "punch_idx_in_day": row.get("punch_index") or 0,
        "in_time": str(row.get("in_time", "")),
        "out_time": str(row.get("out_time", "")),
        "regular_hours": row.get("regular_hours") or 0.0,
        "ot_hours": row.get("ot_hours") or 0.0,
        "doubletime_hours": row.get("doubletime_hours") or 0.0,
        "pay_period": "",
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_adp_wage_rate(row: dict) -> dict:
    return {
        "employee_id": str(row.get("employee_id", "")),
        "employee_name": str(row.get("canonical_name", "")),
        "wage_rate_dollars": row.get("wage_rate_dollars"),
        "ot_rate_dollars": row.get("ot_rate_dollars"),
        "is_salaried": bool(row.get("is_salaried")),
        "multi_rate": bool(row.get("multi_rate")),
        "excluded_from_labor_pct": bool(row.get("excluded_from_labor_pct")),
        # Pass JSON strings under the *_json key so _record_to_row fallback picks them up
        "rate_history_json": str(row.get("earnings_json") or "[]"),
        "raw_employee_names_json": str(row.get("raw_employee_names_json") or "[]"),
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_adp_earnings(row: dict) -> dict:
    return {
        "period_start": _str_date(row.get("period_start")),
        "period_end": _str_date(row.get("period_end")),
        "check_date": _str_date(row.get("check_date")),
        "employee_name": str(row.get("employee", "")),
        "raw_employee_name": str(row.get("raw_employee_name", "")),
        "description": str(row.get("description", "")),
        "hours": row.get("hours") or 0.0,
        "hourly_rate": row.get("hourly_rate") or 0.0,
        "amount": row.get("amount") or 0.0,
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_square_transaction(row: dict) -> dict:
    return {
        "transaction_id": str(row.get("transaction_id", "")),
        "event_type": str(row.get("event_type", "")),
        "created_at_src_iso": str(row.get("created_at_src_iso", "")),
        "created_at_local_iso": str(row.get("created_at_local_iso", "")),
        "date_local": _str_date(row.get("date_local")),
        "hour_local": "",
        "dow_local": "",
        "gross_sales_cents": row.get("gross_sales_cents") or 0,
        "discount_cents": row.get("discount_cents") or 0,
        "tip_cents": row.get("tip_cents") or 0,
        "net_total_cents": row.get("net_total_cents") or 0,
        "total_collected_cents": row.get("total_collected_cents") or 0,
        "source": str(row.get("source", "")),
        "staff_name": str(row.get("staff_name", "")),
        "location": str(row.get("location", "")),
        "raw_date_csv": "",
        "raw_time_csv": "",
        "raw_tz_csv": "",
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_square_daily_rollup(row: dict) -> dict:
    return {
        "date_local": _str_date(row.get("date_local")),
        "txn_count": row.get("txn_count") or 0,
        "gross_sales_cents": row.get("gross_sales_cents") or 0,
        "tip_cents": row.get("tip_cents") or 0,
        "net_sales_cents": row.get("net_sales_cents") or 0,
        "refund_cents": row.get("refund_cents") or 0,
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_square_item_line(row: dict) -> dict:
    return {
        "date_local": _str_date(row.get("date_local")),
        "item_sold_at_local": str(row.get("item_sold_at_local", "")),
        "item_name": str(row.get("item_name", "")),
        "category": str(row.get("category", "")),
        "qty_sold": row.get("qty_sold") or 0,
        "gross_sales_cents": row.get("gross_sales_cents") or 0,
        "discount_cents": row.get("discount_cents") or 0,
        "net_sales_cents": row.get("net_sales_cents") or 0,
        "event_type": str(row.get("event_type", "")),
        "transaction_id": str(row.get("transaction_id", "")),
        "payment_id": str(row.get("payment_id", "")),
        "location": str(row.get("location", "")),
        "channel": str(row.get("channel", "")),
        "line_seq": row.get("line_seq") or 0,
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_square_item_daily(row: dict) -> dict:
    return {
        "date_local": _str_date(row.get("date_local")),
        "items_sold": row.get("items_sold") or 0,
        "units_sold": row.get("units_sold") or 0,
        "gross_sales_cents": row.get("gross_sales_cents") or 0,
        "avg_item_price_cents": row.get("avg_item_price_cents") or 0,
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_square_kds_daily(row: dict) -> dict:
    return {
        "date_local": _str_date(row.get("date_local")),
        "completed_tickets": row.get("completed_tickets") or 0,
        "completed_items": row.get("completed_items") or 0,
        "median_time_per_item_sec": row.get("median_time_per_item_sec") or 0.0,
        "p90_time_per_item_sec": row.get("p90_time_per_item_sec") or 0.0,
        "p95_time_per_item_sec": row.get("p95_time_per_item_sec") or 0.0,
        "p99_time_per_item_sec": row.get("p99_time_per_item_sec") or 0.0,
        "pct_tickets_late": row.get("pct_tickets_late") or 0.0,
        "shift_start": str(row.get("shift_start", "")),
        "shift_end": str(row.get("shift_end", "")),
        "late_tickets": row.get("late_tickets") or 0,
        "due_tickets": row.get("due_tickets") or 0,
        # JSON string already serialized in BQ; pass under *_json key for writer fallback
        "per_item_times_json": str(row.get("per_item_times_json") or "[]"),
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_kds_ticket(row: dict) -> dict:
    return {
        "date_local": _str_date(row.get("date_local")),
        "device_name": str(row.get("device_name", "")),
        "ticket_name": str(row.get("ticket_name", "")),
        "order_source": str(row.get("order_source", "")),
        "num_items": row.get("num_items") or 0,
        "items_in_ticket": str(row.get("items_in_ticket", "")),
        "completion_time_sec": row.get("completion_time_sec") or 0.0,
        "time_created": str(row.get("time_created", "")),
        "time_completed": str(row.get("time_completed", "")),
        "time_due": str(row.get("time_due", "")),
        "scraped_at_utc": _str_ts(row.get("scraped_at_utc")),
    }


def _inv_google_review(row: dict) -> dict:
    """Inverse-map a google_reviews BQ row to the BHAGA Review Raw > reviews Sheet dict.

    Text-protection (leading apostrophe) is applied to post_ts_ct / post_date_ct /
    shift_date_credited so the Sheet doesn't auto-coerce them to date serials.
    clickup_message_id is Sheet-only (absent from BQ); render as blank.
    """
    post_ts = str(row.get("post_ts_ct") or "")
    post_date = _str_date(row.get("post_date_ct"))
    shift_date = str(row.get("shift_date_credited") or "")

    return {
        "review_id": str(row.get("review_id", "")),
        "post_ts_ct": ("'" + post_ts) if post_ts else "",
        "post_date_ct": ("'" + post_date) if post_date else "",
        "rating": row.get("rating") or "",
        "reviewer": str(row.get("reviewer", "")),
        "comment": str(row.get("comment", "")),
        "named_baristas": str(row.get("named_baristas", "")),
        "named_status": str(row.get("named_status", "")),
        "shift_date_credited": ("'" + shift_date) if shift_date else "",
        "shift_assignment_reason": str(row.get("shift_assignment_reason", "")),
        "shift_members": str(row.get("shift_members", "")),
        "trainees_on_shift": str(row.get("trainees_on_shift", "")),
        "named_credit_each": row.get("named_credit_each") or "",
        "base_credit_each": row.get("base_credit_each") or "",
        "total_bonus": row.get("total_bonus") or "",
        "review_url": str(row.get("review_url", "")),
        "clickup_message_id": "",
        "ingested_at_utc": _str_ts(row.get("ingested_at_utc")),
    }


# ---------------------------------------------------------------------------
# Tab specs
# ---------------------------------------------------------------------------
# Each spec describes one BQ raw table → Sheet raw tab mapping.
#
# workbook_key: key in store profile google_sheets dict
# bq_table:     BQ table name (bhaga dataset)
# date_col:     column for windowed WHERE clause; None = select all (wage_rates)
# inv_map_fn:   inverse mapper: BQ row dict → Sheet-header dict
# write_fn:     write_raw_* function to call (upserts by natural key)
# ---------------------------------------------------------------------------

_TAB_SPECS = [
    {
        "label": "adp_shifts",
        "workbook_key": "bhaga_adp_raw",
        "bq_table": "adp_shifts",
        "date_col": "date",
        "inv_map_fn": _inv_adp_shift,
        "write_fn": write_raw_adp_shifts,
    },
    {
        "label": "adp_punches",
        "workbook_key": "bhaga_adp_raw",
        "bq_table": "adp_punches",
        "date_col": "date",
        "inv_map_fn": _inv_adp_punch,
        "write_fn": write_raw_adp_punches,
    },
    {
        "label": "adp_wage_rates",
        "workbook_key": "bhaga_adp_raw",
        "bq_table": "adp_wage_rates",
        "date_col": None,
        "inv_map_fn": _inv_adp_wage_rate,
        "write_fn": write_raw_adp_rates,
    },
    {
        "label": "adp_earnings",
        "workbook_key": "bhaga_adp_raw",
        "bq_table": "adp_earnings",
        "date_col": "period_start",
        "inv_map_fn": _inv_adp_earnings,
        "write_fn": write_raw_adp_earnings,
    },
    {
        "label": "square_transactions",
        "workbook_key": "bhaga_square_raw",
        "bq_table": "square_transactions",
        "date_col": "date_local",
        "inv_map_fn": _inv_square_transaction,
        "write_fn": write_raw_square_transactions,
    },
    {
        "label": "square_daily_rollup",
        "workbook_key": "bhaga_square_raw",
        "bq_table": "square_daily_rollup",
        "date_col": "date_local",
        "inv_map_fn": _inv_square_daily_rollup,
        "write_fn": write_raw_square_daily_rollup,
    },
    {
        "label": "square_item_lines",
        "workbook_key": "bhaga_square_raw",
        "bq_table": "square_item_lines",
        "date_col": "date_local",
        "inv_map_fn": _inv_square_item_line,
        "write_fn": write_raw_square_item_lines,
    },
    {
        "label": "square_item_daily",
        "workbook_key": "bhaga_square_raw",
        "bq_table": "square_item_daily",
        "date_col": "date_local",
        "inv_map_fn": _inv_square_item_daily,
        "write_fn": write_raw_square_item_daily_rollup,
    },
    {
        "label": "square_kds_daily",
        "workbook_key": "bhaga_square_raw",
        "bq_table": "square_kds_daily",
        "date_col": "date_local",
        "inv_map_fn": _inv_square_kds_daily,
        "write_fn": write_raw_kds_daily,
    },
    {
        "label": "square_kds_tickets",
        "workbook_key": "bhaga_square_raw",
        "bq_table": "square_kds_tickets",
        "date_col": "date_local",
        "inv_map_fn": _inv_kds_ticket,
        "write_fn": write_raw_kds_tickets,
    },
    {
        "label": "reviews",
        "workbook_key": "bhaga_review_raw",
        "bq_table": "google_reviews",
        "date_col": "post_date_ct",
        "inv_map_fn": _inv_google_review,
        "write_fn": write_raw_google_reviews,
    },
]

_LABEL_TO_SPEC: dict[str, dict] = {s["label"]: s for s in _TAB_SPECS}


def _build_query(bq_table: str, date_col: str | None, since: datetime.date | None) -> str:
    table = f"`{_PROJECT}.{_DATASET}.{bq_table}`"
    if date_col and since:
        return f"SELECT * FROM {table} WHERE {date_col} >= DATE('{since.isoformat()}')"
    return f"SELECT * FROM {table}"


def render(
    store: str,
    *,
    since: datetime.date | None = None,
    tabs: list[str] | None = None,
    dry_run: bool = False,
    fatal: bool = False,
) -> dict[str, dict]:
    """Read each BQ raw table and incrementally upsert the corresponding Sheet tab.

    Args:
        store:   store name (e.g. "palmetto")
        since:   only pull BQ rows on/after this date (default: _DEFAULT_LOOKBACK_DAYS ago)
        tabs:    list of spec labels to render; None = all 11 tabs
        dry_run: read BQ and print counts, but do NOT write to Sheet
        fatal:   if True, propagate exceptions; if False (default), log and continue

    Returns dict of {label: {"bq_rows": N, "upsert_result": {...}}}
    """
    profile = json.loads(
        (pathlib.Path(__file__).resolve().parents[3]
         / "agents" / "bhaga" / "knowledge-base" / "store-profiles" / f"{store}.json")
        .read_text()
    )
    google_account = profile.get("google_account_key", store)

    if since is None:
        since = datetime.date.today() - datetime.timedelta(days=_DEFAULT_LOOKBACK_DAYS)

    target_specs = [s for s in _TAB_SPECS if tabs is None or s["label"] in (tabs or [])]

    results: dict[str, dict] = {}
    print(f"# render_raw_sheet_from_bq [{store}] since={since} dry_run={dry_run}")

    for spec in target_specs:
        label = spec["label"]
        try:
            sql = _build_query(spec["bq_table"], spec["date_col"], since)
            bq_rows = read_query(sql)
            sheet_recs = [spec["inv_map_fn"](r) for r in bq_rows]
            print(f"  {label:<28} {len(sheet_recs):>6} rows from BQ")

            if dry_run:
                results[label] = {"bq_rows": len(sheet_recs), "upsert_result": None}
                continue

            spreadsheet_id = resolve_sheet_id(spec["workbook_key"], profile)
            upsert_result = spec["write_fn"](
                spreadsheet_id, sheet_recs, account=google_account,
            )
            inserted = upsert_result.get("inserted", 0)
            updated = upsert_result.get("updated", 0)
            total = upsert_result.get("total_after", 0)
            print(f"    +{inserted} inserted  {updated} updated  {total} total")
            results[label] = {"bq_rows": len(sheet_recs), "upsert_result": upsert_result}

        except Exception as exc:  # noqa: BLE001
            print(f"  WARN: {label}: {exc}")
            results[label] = {"bq_rows": 0, "upsert_result": None, "error": str(exc)}
            if fatal:
                raise

    print("# render_raw_sheet_from_bq done.")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="Store name (e.g. palmetto)")
    parser.add_argument(
        "--since", default=None,
        help=f"YYYY-MM-DD; only render rows on/after this date "
             f"(default: {_DEFAULT_LOOKBACK_DAYS}-day lookback). "
             "wage_rates always renders all rows.",
    )
    parser.add_argument(
        "--tabs", default=None,
        help="Comma-separated list of tab labels to render "
             f"(default: all). Known: {', '.join(_LABEL_TO_SPEC)}",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Read BQ and print counts without writing to Sheet")
    parser.add_argument("--fatal", action="store_true",
                        help="Raise on first error instead of logging and continuing")
    args = parser.parse_args()

    since_date = datetime.date.fromisoformat(args.since) if args.since else None
    tabs_list = [t.strip() for t in args.tabs.split(",")] if args.tabs else None

    render(
        args.store,
        since=since_date,
        tabs=tabs_list,
        dry_run=args.dry_run,
        fatal=args.fatal,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
