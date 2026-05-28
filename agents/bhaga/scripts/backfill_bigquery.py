#!/usr/bin/env python3
"""Backfill BigQuery tables from production Google Sheets.

Reads all data from the BHAGA raw Google Sheets (ADP Raw + Square Raw),
maps Sheet column names to the BigQuery schema, converts types, and
bulk-inserts into the bhaga dataset.

Usage:
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
    read_raw_adp_punches,
    read_raw_adp_rates,
    read_raw_adp_shifts,
    read_raw_square_daily_rollup,
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
    """Parse a scraped_at_utc string into a datetime."""
    if val is None or str(val).strip() == "":
        return None
    s = str(val).strip()
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
        "excluded_from_labor_pct": _parse_bool(rec.get("excluded_from_labor_pct")),
        "excluded_from_tip_pool": excluded_tip,
        "raw_employee_names_json": raw_names,
        "earnings_json": earnings,
        "scraped_at_utc": _parse_timestamp(rec.get("scraped_at_utc")),
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

    results: dict[str, dict] = {}
    all_tables = {"square_transactions", "square_daily_rollup", "adp_shifts", "adp_punches", "adp_wage_rates"}
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
