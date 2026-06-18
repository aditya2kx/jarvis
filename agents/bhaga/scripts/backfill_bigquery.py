#!/usr/bin/env python3
"""One-shot historical backfill: read raw Google Sheets → write BigQuery.

Used to bootstrap BQ raw tables from the existing Sheet history, or as a
repair tool when BQ gets out of sync (e.g. after a schema migration or an
accidental table truncation). This is NOT the nightly pipeline path.

Nightly pipeline (BQ-primary architecture):
    scrape files → backfill_from_downloads (BHAGA_DATASTORE=bigquery) → BQ
    BQ → render_raw_sheet_from_bq → raw Sheets (projection)

This script reads in the opposite direction (Sheet → BQ), so it is useful
only for one-off historical loads. It is safe to re-run: load_rows uses
MERGE (upsert) by natural key, so re-running is idempotent.

Usage:
    BHAGA_DATASTORE=bigquery BHAGA_IMPERSONATE_SA=bhaga-orchestrator@... \\
        python3 -m agents.bhaga.scripts.backfill_bigquery --store palmetto
    python3 -m agents.bhaga.scripts.backfill_bigquery --store palmetto --tables adp_shifts,square_transactions
    python3 -m agents.bhaga.scripts.backfill_bigquery --store palmetto --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from core.config_loader import refresh_access_token, resolve_sheet_id
from skills.tip_ledger_writer.reader import (
    read_raw_adp_earnings,
    read_raw_adp_punches,
    read_raw_adp_rates,
    read_raw_adp_shifts,
    read_raw_google_reviews,
    read_raw_kds_tickets,
    read_raw_square_daily_rollup,
    read_raw_square_item_daily,
    read_raw_square_item_lines,
    read_raw_square_kds_daily,
    read_raw_square_transactions,
)

_PROJECT_DIR = pathlib.Path(__file__).resolve().parents[3]
_STORE_PROFILE_DIR = _PROJECT_DIR / "agents" / "bhaga" / "knowledge-base" / "store-profiles"


def load_store_profile(store: str) -> dict:
    path = _STORE_PROFILE_DIR / f"{store}.json"
    if not path.exists():
        raise FileNotFoundError(f"Store profile not found: {path}")
    return json.loads(path.read_text())


def _parse_date(val) -> datetime.date | None:
    """Coerce a string or date-like value to datetime.date."""
    if isinstance(val, datetime.date):
        return val
    if not val or str(val).strip() == "":
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_int(val, default: int = 0) -> int:
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return default


def _parse_float(val, default: float = 0.0) -> float:
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(str(val))
    except (ValueError, TypeError):
        return default


def _parse_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().upper() == "TRUE"


def _parse_timestamp(val) -> datetime.datetime | None:
    """Parse a timestamp string into a datetime (UTC).

    Handles common formats from Google Sheets and process_reviews.py:
      2026-01-15T08:30:00Z, 2026-01-15T08:30:00.000Z,
      2026-01-15 08:30:00, 2026-01-15T08:30:00+00:00, etc.
    """
    if val is None or str(val).strip() == "":
        return None
    s = str(val).strip()
    # Try stdlib fromisoformat which handles +00:00 and bare ISO variants
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(datetime.timezone.utc).replace(tzinfo=datetime.timezone.utc)
    except (ValueError, AttributeError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Sheet → BigQuery row mappers
# ---------------------------------------------------------------------------

def map_square_transaction(rec: dict) -> dict:
    """Map a Sheet transactions row to the BQ square_transactions schema."""
    net_sales = _parse_int(rec.get("gross_sales_cents")) - abs(_parse_int(rec.get("discount_cents")))
    return {
        "transaction_id": str(rec.get("transaction_id", "")),
        "date_local": _parse_date(rec.get("date_local")),
        "event_type": str(rec.get("event_type", "")),
        "gross_sales_cents": _parse_int(rec.get("gross_sales_cents")),
        "discount_cents": _parse_int(rec.get("discount_cents")),
        "net_sales_cents": net_sales,
        "tip_cents": _parse_int(rec.get("tip_cents")),
        "total_collected_cents": _parse_int(rec.get("total_collected_cents")),
        "net_total_cents": _parse_int(rec.get("net_total_cents")),
        "source": str(rec.get("source", "")),
        "staff_name": str(rec.get("staff_name", "")),
        "location": str(rec.get("location", "")),
        "created_at_src_iso": str(rec.get("created_at_src_iso", "")),
        "created_at_local_iso": str(rec.get("created_at_local_iso", "")),
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_square_daily_rollup(rec: dict) -> dict:
    """Map a Sheet daily_rollup row to the BQ square_daily_rollup schema."""
    return {
        "date_local": _parse_date(rec.get("date_local")),
        "txn_count": _parse_int(rec.get("txn_count")),
        "gross_sales_cents": _parse_int(rec.get("gross_sales_cents")),
        "tip_cents": _parse_int(rec.get("tip_cents")),
        "net_sales_cents": _parse_int(rec.get("net_sales_cents")),
        "refund_cents": _parse_int(rec.get("refund_cents")),
        "order_count": _parse_int(rec.get("order_count")),
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_adp_shift(rec: dict) -> dict:
    """Map a Sheet shifts row to the BQ adp_shifts schema.

    Sheet columns: date, employee_id, employee_name, raw_employee_name,
                   in_time, out_time, regular_hours, ot_hours,
                   doubletime_hours, total_hours, punch_count,
                   pay_period, scraped_at_utc
    """
    return {
        "date": _parse_date(rec.get("date")),
        "employee_id": str(rec.get("employee_id", "")),
        "canonical_name": str(rec.get("employee_name", "")),
        "raw_employee_name": str(rec.get("raw_employee_name", "")),
        "in_time": str(rec.get("in_time", "")),
        "out_time": str(rec.get("out_time", "")),
        "regular_hours": _parse_float(rec.get("regular_hours")),
        "ot_hours": _parse_float(rec.get("ot_hours")),
        "doubletime_hours": _parse_float(rec.get("doubletime_hours")),
        "total_hours": _parse_float(rec.get("total_hours")),
        "shift_count": _parse_int(rec.get("punch_count"), default=1),
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_adp_punch(rec: dict) -> dict:
    """Map a Sheet punches row to the BQ adp_punches schema.

    Sheet columns: date, employee_id, employee_name, raw_employee_name,
                   punch_idx_in_day, in_time, out_time, regular_hours,
                   ot_hours, doubletime_hours, pay_period, scraped_at_utc
    """
    reg = _parse_float(rec.get("regular_hours"))
    ot = _parse_float(rec.get("ot_hours"))
    dt = _parse_float(rec.get("doubletime_hours"))
    return {
        "date": _parse_date(rec.get("date")),
        "employee_id": str(rec.get("employee_id", "")),
        "canonical_name": str(rec.get("employee_name", "")),
        "raw_employee_name": str(rec.get("raw_employee_name", "")),
        "punch_index": _parse_int(rec.get("punch_idx_in_day")),
        "in_time": str(rec.get("in_time", "")),
        "out_time": str(rec.get("out_time", "")),
        "regular_hours": reg,
        "ot_hours": ot,
        "doubletime_hours": dt,
        "total_hours": round(reg + ot + dt, 4),
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_adp_wage_rate(rec: dict, profile: dict) -> dict:
    """Map a Sheet wage_rates row to the BQ adp_wage_rates schema.

    Sheet columns: employee_id, employee_name, wage_rate_dollars,
                   ot_rate_dollars, is_salaried, multi_rate,
                   excluded_from_labor_pct, rate_history_json,
                   raw_employee_names_json, scraped_at_utc
    """
    canonical = str(rec.get("employee_name", ""))
    excluded_tip = canonical in profile.get("employees", {}).get(
        "excluded_from_tip_pool_and_labor_pct", []
    )
    raw_names = rec.get("raw_employee_names_json")
    if isinstance(raw_names, list):
        raw_names = json.dumps(raw_names)
    elif raw_names is None:
        raw_names = "[]"
    else:
        raw_names = str(raw_names)

    earnings = rec.get("rate_history_json")
    if isinstance(earnings, (list, dict)):
        earnings = json.dumps(earnings)
    elif earnings is None:
        earnings = "[]"
    else:
        earnings = str(earnings)

    return {
        "employee_id": str(rec.get("employee_id", "")),
        "canonical_name": canonical,
        "wage_rate_dollars": _parse_float(rec.get("wage_rate_dollars")),
        "ot_rate_dollars": _parse_float(rec.get("ot_rate_dollars")),
        "is_salaried": _parse_bool(rec.get("is_salaried")),
        "multi_rate": _parse_bool(rec.get("multi_rate")),
        "excluded_from_labor_pct": _parse_bool(rec.get("excluded_from_labor_pct")),
        "excluded_from_tip_pool": excluded_tip,
        "raw_employee_names_json": raw_names,
        "earnings_json": earnings,
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_square_item_line(rec: dict) -> dict:
    """Map a Sheet item_lines row to the BQ square_item_lines schema."""
    return {
        "date_local": _parse_date(rec.get("date_local")),
        "item_sold_at_local": str(rec.get("item_sold_at_local", "")),
        "item_name": str(rec.get("item_name", "")),
        "category": str(rec.get("category", "")),
        "qty_sold": _parse_int(rec.get("qty_sold")),
        "gross_sales_cents": _parse_int(rec.get("gross_sales_cents")),
        "discount_cents": _parse_int(rec.get("discount_cents")),
        "net_sales_cents": _parse_int(rec.get("net_sales_cents")),
        "event_type": str(rec.get("event_type", "")),
        "transaction_id": str(rec.get("transaction_id", "")),
        "payment_id": str(rec.get("payment_id", "")),
        "location": str(rec.get("location", "")),
        "channel": str(rec.get("channel", "")),
        "employee": str(rec.get("employee", "")),
        "line_seq": _parse_int(rec.get("line_seq")),
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_square_item_daily(rec: dict) -> dict:
    """Map a Sheet item_daily_rollup row to the BQ square_item_daily schema."""
    return {
        "date_local": _parse_date(rec.get("date_local")),
        "items_sold": _parse_int(rec.get("items_sold")),
        "units_sold": _parse_int(rec.get("units_sold")),
        "gross_sales_cents": _parse_int(rec.get("gross_sales_cents")),
        "avg_item_price_cents": _parse_int(rec.get("avg_item_price_cents")),
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_square_kds_daily(rec: dict) -> dict:
    """Map a kds_daily row to the BQ square_kds_daily schema.

    Accepts rows from two sources: Sheet-sourced rows carry the JSON-encoded
    ``per_item_times_json`` column, while rows straight off
    ``aggregate_daily_kds_stats`` (the from-downloads backfill path) carry the
    raw list under ``per_item_times``. Either populates the BQ column so
    weekly/period rollups can re-pool the per-item distribution.
    """
    pij = rec.get("per_item_times_json")
    if pij is None or pij == "":
        pij = rec.get("per_item_times")
    if isinstance(pij, list):
        pij = json.dumps(pij)
    elif not pij:
        pij = "[]"
    else:
        pij = str(pij)
    return {
        "date_local": _parse_date(rec.get("date_local")),
        "completed_tickets": _parse_int(rec.get("completed_tickets")),
        "completed_items": _parse_int(rec.get("completed_items")),
        "median_time_per_item_sec": _parse_float(rec.get("median_time_per_item_sec")),
        "p90_time_per_item_sec": _parse_float(rec.get("p90_time_per_item_sec")),
        "p95_time_per_item_sec": _parse_float(rec.get("p95_time_per_item_sec")),
        "p99_time_per_item_sec": _parse_float(rec.get("p99_time_per_item_sec")),
        "pct_tickets_late": _parse_float(rec.get("pct_tickets_late")),
        "shift_start": str(rec.get("shift_start", "")),
        "shift_end": str(rec.get("shift_end", "")),
        "late_tickets": _parse_int(rec.get("late_tickets")),
        "due_tickets": _parse_int(rec.get("due_tickets")),
        "per_item_times_json": pij,
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_kds_ticket(rec: dict) -> dict:
    """Map a Sheet kds_tickets row to the BQ square_kds_tickets schema."""
    return {
        "date_local": _parse_date(rec.get("date_local")),
        "device_name": str(rec.get("device_name", "")),
        "ticket_name": str(rec.get("ticket_name", "")),
        "order_source": str(rec.get("order_source", "")),
        "num_items": _parse_int(rec.get("num_items")),
        "items_in_ticket": str(rec.get("items_in_ticket", "")),
        "completion_time_sec": _parse_float(rec.get("completion_time_sec")),
        "time_created": str(rec.get("time_created", "")),
        "time_completed": str(rec.get("time_completed", "")),
        "time_due": str(rec.get("time_due", "")),
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_adp_earnings_row(rec: dict) -> dict:
    """Map a Sheet adp_earnings row to the BQ adp_earnings schema.

    Sheet uses employee_name (canonical); BQ column is employee.
    """
    return {
        "period_start": _parse_date(rec.get("period_start")),
        "period_end": _parse_date(rec.get("period_end")),
        "check_date": _parse_date(rec.get("check_date")),
        "employee": str(rec.get("employee_name", "")),
        "raw_employee_name": str(rec.get("raw_employee_name", "")),
        "description": str(rec.get("description", "")),
        "hours": _parse_float(rec.get("hours")),
        "hourly_rate": _parse_float(rec.get("hourly_rate")),
        "amount": _parse_float(rec.get("amount")),
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
    }


def map_forecast_daily(rec: dict) -> dict:
    """Map a forecast_bq build_forecast_rows output row to the BQ schema.

    Input keys: date (ISO), forecast_orders (int), forecast_items (float),
                forecast_generated_at (ISO string),
                forecast_model_version (str, optional).
    """
    import datetime as _dt
    return {
        "date": _parse_date(rec.get("date")),
        "forecast_orders": _parse_int(rec.get("forecast_orders")),
        "forecast_items": _parse_float(rec.get("forecast_items")),
        "forecast_generated_at": str(rec.get("forecast_generated_at", "")),
        "forecast_model_version": rec.get("forecast_model_version") or None,
        "materialized_at_utc": _dt.datetime.now(_dt.timezone.utc),
    }


def map_forecast_ramp_daily(rec: dict) -> dict:
    """Map a forecast_ramp_bq build_ramp_forecast_rows output row to the BQ schema.

    Input keys: date (ISO), forecast_orders (int), forecast_items (float),
                forecast_generated_at (ISO string),
                forecast_model_version (str).
    """
    import datetime as _dt
    return {
        "date": _parse_date(rec.get("date")),
        "forecast_orders": _parse_int(rec.get("forecast_orders")),
        "forecast_items": _parse_float(rec.get("forecast_items")),
        "forecast_generated_at": str(rec.get("forecast_generated_at", "")),
        "forecast_model_version": rec.get("forecast_model_version") or None,
        "materialized_at_utc": _dt.datetime.now(_dt.timezone.utc),
    }


def map_google_review(rec: dict) -> dict:
    """Map a Sheet reviews row to the BQ google_reviews schema.

    clickup_message_id is Sheet-only — not written to BQ.
    """
    d = _parse_date(rec.get("post_date_ct"))
    return {
        "review_id": str(rec.get("review_id", "")),
        "post_ts_ct": str(rec.get("post_ts_ct", "")),
        "post_date_ct": d,
        "rating": int(rec.get("rating") or 0),
        "reviewer": str(rec.get("reviewer", "")),
        "comment": str(rec.get("comment", "")),
        "named_baristas": str(rec.get("named_baristas", "")),
        "named_status": str(rec.get("named_status", "")),
        "shift_date_credited": str(rec.get("shift_date_credited", "")),
        "shift_assignment_reason": str(rec.get("shift_assignment_reason", "")),
        "shift_members": str(rec.get("shift_members", "")),
        "trainees_on_shift": str(rec.get("trainees_on_shift", "")),
        "named_credit_each": _parse_float(rec.get("named_credit_each")),
        "base_credit_each": _parse_float(rec.get("base_credit_each")),
        "total_bonus": _parse_float(rec.get("total_bonus")),
        "review_url": str(rec.get("review_url", "")),
        "ingested_at_utc": _parse_timestamp(rec.get("ingested_at_utc")),
    }


# ---------------------------------------------------------------------------
# Backfill orchestrator
# ---------------------------------------------------------------------------

def backfill(store: str, *, tables: set[str] | None = None, dry_run: bool = False) -> dict[str, dict]:
    """Run the full backfill. Returns {table: {rows_in_sheet, rows_loaded}}."""
    os.environ["BHAGA_DATASTORE"] = "bigquery"

    from core.datastore import load_rows

    profile = load_store_profile(store)
    account = profile.get("google_account_key", store)

    adp_raw_sid = profile["google_sheets"]["bhaga_adp_raw"]["spreadsheet_id"]
    square_raw_sid = profile["google_sheets"]["bhaga_square_raw"]["spreadsheet_id"]
    review_raw_sid = profile["google_sheets"]["bhaga_review_raw"]["spreadsheet_id"]

    results: dict[str, dict] = {}
    all_tables = {
        "square_transactions", "square_daily_rollup",
        "square_item_lines", "square_item_daily", "square_kds_daily", "square_kds_tickets",
        "adp_shifts", "adp_punches", "adp_wage_rates", "adp_earnings",
        "google_reviews",
    }
    target_tables = tables if tables else all_tables

    if "square_transactions" in target_tables:
        print("Reading Square transactions from Sheet...")
        sheet_rows = read_raw_square_transactions(square_raw_sid, account=account)
        bq_rows = [map_square_transaction(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r["date_local"] is not None]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run:
            loaded = load_rows("square_transactions", bq_rows, merge_keys=["transaction_id"])
            print(f"  Loaded {loaded} rows into square_transactions")
        else:
            loaded = 0
            print("  [DRY RUN] Would load into square_transactions")
        results["square_transactions"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "square_daily_rollup" in target_tables:
        print("Reading Square daily rollup from Sheet...")
        sheet_rows = read_raw_square_daily_rollup(square_raw_sid, account=account)
        bq_rows = [map_square_daily_rollup(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r["date_local"] is not None]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run:
            loaded = load_rows("square_daily_rollup", bq_rows, merge_keys=["date_local"])
            print(f"  Loaded {loaded} rows into square_daily_rollup")
        else:
            loaded = 0
            print("  [DRY RUN] Would load into square_daily_rollup")
        results["square_daily_rollup"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "adp_shifts" in target_tables:
        print("Reading ADP shifts from Sheet...")
        sheet_rows = read_raw_adp_shifts(adp_raw_sid, account=account)
        bq_rows = [map_adp_shift(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r["date"] is not None]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run:
            loaded = load_rows("adp_shifts", bq_rows, merge_keys=["date", "employee_id"])
            print(f"  Loaded {loaded} rows into adp_shifts")
        else:
            loaded = 0
            print("  [DRY RUN] Would load into adp_shifts")
        results["adp_shifts"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "adp_punches" in target_tables:
        print("Reading ADP punches from Sheet...")
        sheet_rows = read_raw_adp_punches(adp_raw_sid, account=account)
        bq_rows = [map_adp_punch(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r["date"] is not None]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run:
            loaded = load_rows("adp_punches", bq_rows, merge_keys=["date", "employee_id", "punch_index"])
            print(f"  Loaded {loaded} rows into adp_punches")
        else:
            loaded = 0
            print("  [DRY RUN] Would load into adp_punches")
        results["adp_punches"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "adp_wage_rates" in target_tables:
        print("Reading ADP wage rates from Sheet...")
        sheet_rows = read_raw_adp_rates(adp_raw_sid, account=account)
        bq_rows = [map_adp_wage_rate(r, profile) for r in sheet_rows]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run:
            loaded = load_rows("adp_wage_rates", bq_rows, merge_keys=["employee_id"])
            print(f"  Loaded {loaded} rows into adp_wage_rates")
        else:
            loaded = 0
            print("  [DRY RUN] Would load into adp_wage_rates")
        results["adp_wage_rates"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "square_item_lines" in target_tables:
        print("Reading Square item_lines from Sheet...")
        sheet_rows = read_raw_square_item_lines(square_raw_sid, account=account)
        bq_rows = [map_square_item_line(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r["date_local"] is not None]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run:
            loaded = load_rows("square_item_lines", bq_rows,
                               merge_keys=["transaction_id", "item_name", "item_sold_at_local", "line_seq"])
            print(f"  Loaded {loaded} rows into square_item_lines")
        else:
            loaded = 0
            print("  [DRY RUN] Would load into square_item_lines")
        results["square_item_lines"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "square_item_daily" in target_tables:
        print("Reading Square item_daily_rollup from Sheet...")
        sheet_rows = read_raw_square_item_daily(square_raw_sid, account=account)
        bq_rows = [map_square_item_daily(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r["date_local"] is not None]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run:
            loaded = load_rows("square_item_daily", bq_rows, merge_keys=["date_local"])
            print(f"  Loaded {loaded} rows into square_item_daily")
        else:
            loaded = 0
            print("  [DRY RUN] Would load into square_item_daily")
        results["square_item_daily"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "square_kds_daily" in target_tables:
        print("Reading Square kds_daily from Sheet...")
        sheet_rows = read_raw_square_kds_daily(square_raw_sid, account=account)
        bq_rows = [map_square_kds_daily(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r["date_local"] is not None]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run:
            loaded = load_rows("square_kds_daily", bq_rows, merge_keys=["date_local"])
            print(f"  Loaded {loaded} rows into square_kds_daily")
        else:
            loaded = 0
            print("  [DRY RUN] Would load into square_kds_daily")
        results["square_kds_daily"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "square_kds_tickets" in target_tables:
        print("Reading Square kds_tickets from Sheet...")
        try:
            sheet_rows = read_raw_kds_tickets(square_raw_sid, account=account)
        except (KeyError, ValueError) as exc:
            print(f"  WARN: kds_tickets tab not yet present in Sheet — skipping ({exc})")
            sheet_rows = []
        bq_rows = [map_kds_ticket(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r["date_local"] is not None]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run and bq_rows:
            loaded = load_rows("square_kds_tickets", bq_rows,
                               merge_keys=["date_local", "time_created", "ticket_name"])
            print(f"  Loaded {loaded} rows into square_kds_tickets")
        else:
            loaded = 0
            if dry_run:
                print("  [DRY RUN] Would load into square_kds_tickets")
        results["square_kds_tickets"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "adp_earnings" in target_tables:
        print("Reading ADP earnings from Sheet...")
        try:
            sheet_rows = read_raw_adp_earnings(adp_raw_sid, account=account)
        except (KeyError, ValueError) as exc:
            print(f"  WARN: earnings tab not yet present in ADP Raw Sheet — skipping ({exc})")
            sheet_rows = []
        bq_rows = [map_adp_earnings_row(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r["period_start"] is not None]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run and bq_rows:
            loaded = load_rows("adp_earnings", bq_rows,
                               merge_keys=["period_start", "period_end", "employee", "description", "check_date"])
            print(f"  Loaded {loaded} rows into adp_earnings")
        else:
            loaded = 0
            if dry_run:
                print("  [DRY RUN] Would load into adp_earnings")
        results["adp_earnings"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    if "google_reviews" in target_tables:
        print("Reading Google reviews from Sheet...")
        sheet_rows = read_raw_google_reviews(review_raw_sid, account=account)
        bq_rows = [map_google_review(r) for r in sheet_rows]
        bq_rows = [r for r in bq_rows if r.get("review_id")]
        print(f"  {len(sheet_rows)} rows in sheet, {len(bq_rows)} valid rows for BQ")
        if not dry_run:
            loaded = load_rows("google_reviews", bq_rows, merge_keys=["review_id"],
                               column_bq_types={"ingested_at_utc": "TIMESTAMP"})
            print(f"  Loaded {loaded} rows into google_reviews")
        else:
            loaded = 0
            print("  [DRY RUN] Would load into google_reviews")
        results["google_reviews"] = {"rows_in_sheet": len(sheet_rows), "rows_loaded": loaded}

    print("\n=== Backfill Summary ===")
    for tbl, info in sorted(results.items()):
        print(f"  {tbl}: {info['rows_loaded']} rows loaded, {info['rows_in_sheet']} rows in sheet")

    return results


def main():
    parser = argparse.ArgumentParser(description="Backfill BigQuery from BHAGA raw Sheets")
    parser.add_argument("--store", required=True, help="Store profile name (e.g. palmetto)")
    parser.add_argument("--tables", help="Comma-separated list of tables to backfill (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Read sheets but don't write to BQ")
    args = parser.parse_args()

    target_tables = set(args.tables.split(",")) if args.tables else None
    backfill(args.store, tables=target_tables, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
