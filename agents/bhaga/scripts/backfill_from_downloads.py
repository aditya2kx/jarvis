#!/usr/bin/env python3
"""BHAGA end-to-end backfill from already-downloaded scrape files.

Reads the most recent files in extracted/downloads/ (Square Transactions CSV,
ADP Timecard XLSX, ADP Earnings XLSX), parses them via the source skills, and
upserts the results into the three BHAGA workbooks via tip_ledger_writer.

This is the offline equivalent of the future M3 orchestrator daily_refresh.py
which will also drive the scrapes. Used in M1 for the initial backfill and as
a re-run mechanism if the operator manually re-downloads a report.

Usage:
    python3 -m agents.bhaga.scripts.backfill_from_downloads --store palmetto
    python3 -m agents.bhaga.scripts.backfill_from_downloads --store palmetto --start 2026-03-22 --end 2026-05-15
    python3 -m agents.bhaga.scripts.backfill_from_downloads --store palmetto --skip square    # only ADP
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core.config_loader import project_dir, resolve_sheet_id
from skills.adp_run_automation import compensation_backend, shift_backend
from skills.adp_run_automation.employee_aliases import (
    detect_new_employees,
    update_sheet_with_new_aliases,
)
from skills.square_tips import transactions_backend
from skills.tip_ledger_writer import (
    read_raw_adp_rates,
    write_raw_adp_earnings,
    write_raw_adp_punches,
    write_raw_adp_rates,
    write_raw_adp_shifts,
    write_raw_square_daily_rollup,
    write_raw_square_transactions,
)
from skills.tip_ledger_writer.writer import write_raw_square_item_daily_rollup, write_raw_kds_daily

# Notify is optional — backfill may run in environments without Slack creds.
try:
    from agents.bhaga.notify import new_employee_alert
except Exception:  # noqa: BLE001
    def new_employee_alert(*args, **kwargs):  # type: ignore[misc]
        return None


def _bq_enabled() -> bool:
    return os.environ.get("BHAGA_DATASTORE", "").lower() == "bigquery"


def _write_to_bq(table_name: str, rows: list[dict], merge_keys: list[str]) -> int:
    """Dual-write to BigQuery when enabled. Returns rows affected or 0."""
    if not _bq_enabled() or not rows:
        return 0
    from core.datastore import load_rows
    try:
        n = load_rows(table_name, rows, merge_keys=merge_keys)
        return n
    except Exception as exc:
        print(f"  WARN: BigQuery write to {table_name} failed: {exc}")
        return 0


PROJECT = pathlib.Path(project_dir())
DOWNLOADS = PROJECT / "extracted" / "downloads"
STORE_PROFILE_DIR = PROJECT / "agents" / "bhaga" / "knowledge-base" / "store-profiles"


def _newest(pattern: str) -> pathlib.Path | None:
    paths = [pathlib.Path(p) for p in glob.glob(str(DOWNLOADS / pattern))]
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def load_store_profile(store: str) -> dict:
    path = STORE_PROFILE_DIR / f"{store}.json"
    if not path.exists():
        raise FileNotFoundError(f"Store profile not found: {path}")
    return json.loads(path.read_text())


def aggregate_square_daily(records: list[dict]) -> list[dict]:
    """Per-shop-local-day rollup matching the daily_rollup tab schema."""
    by_day: dict[str, dict] = {}
    for r in records:
        d = r["date_local"]
        bucket = by_day.setdefault(d, {
            "date_local": d,
            "txn_count": 0,
            "gross_sales_cents": 0,
            "tip_cents": 0,
            "net_sales_cents": 0,
            "refund_cents": 0,
        })
        bucket["txn_count"] += 1
        bucket["gross_sales_cents"] += r.get("gross_sales_cents", 0)
        bucket["tip_cents"] += r.get("tip_cents", 0)
        # net_sales = gross + discount (discount is negative); refund handled
        # separately so net_sales here is "what we billed" excluding refunds.
        if r.get("event_type") == "Refund":
            bucket["refund_cents"] += r.get("total_collected_cents", 0)
        else:
            bucket["net_sales_cents"] += r.get("total_collected_cents", 0)
    return sorted(by_day.values(), key=lambda b: b["date_local"])


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", required=True)
    cli.add_argument("--start", default=None, help="YYYY-MM-DD; trims input to this window. Default: no trim.")
    cli.add_argument("--end", default=None)
    cli.add_argument(
        "--skip", default=[], action="append",
        choices=["square", "adp_shifts", "adp_punches", "adp_earnings", "adp_rates", "square_rollup"],
        help="Skip a specific write. Can pass multiple times.",
    )
    cli.add_argument("--dry-run", action="store_true",
        help="Parse and aggregate but do NOT write to Google Sheets.")
    args = cli.parse_args()

    profile = load_store_profile(args.store)
    # Aliases + exclusions now live in bhaga_model > employees + > config.
    # The local JSON is just a bootstrap pointer for sheet IDs.
    from skills.store_profile import load_aliases, load_exclusions
    aliases = load_aliases(args.store)
    excluded = load_exclusions(args.store)["permanent"]
    adp_raw_sid = resolve_sheet_id("bhaga_adp_raw", profile)
    square_raw_sid = resolve_sheet_id("bhaga_square_raw", profile)
    shop_tz = profile["timezone"]["shop_tz"]
    google_account = profile["google_account_key"]

    start = datetime.date.fromisoformat(args.start) if args.start else None
    end = datetime.date.fromisoformat(args.end) if args.end else None

    def _in_window(date_iso: str) -> bool:
        if not (start or end):
            return True
        d = datetime.date.fromisoformat(date_iso)
        if start and d < start:
            return False
        if end and d > end:
            return False
        return True

    summaries: list[dict] = []

    # ── ADP shifts + punches ──────────────────────────────────────
    if "adp_shifts" not in args.skip or "adp_punches" not in args.skip:
        timecard_xlsx = _newest("Timecard*.xlsx")
        if not timecard_xlsx:
            print("WARN: no Timecard*.xlsx found — skipping ADP shifts/punches")
        else:
            print(f"# parsing ADP timecard: {timecard_xlsx.name}")
            punches = shift_backend.parse_xlsx(timecard_xlsx, employee_aliases=aliases)

            # AUTO-DETECT new employees: parse_xlsx with an incomplete alias map
            # falls through to raw_name as employee_id, which would fork the
            # ledger identity (one person becomes two rows). Catch those here,
            # auto-add canonical "Last, First" aliases to the profile JSON,
            # Slack-notify the operator, then RE-PARSE with the updated map so
            # the writes land canonical employee_ids the first time.
            new_pairs = detect_new_employees(punches, aliases)
            if new_pairs:
                print(f"  detected {len(new_pairs)} new employee(s): "
                      + ", ".join(f"{r!r}→{c!r}" for r, c in new_pairs))
                # Write the new aliases to bhaga_model > employees (canonical SOT).
                # No more local JSON mutation — the sheet survives laptop loss.
                added = update_sheet_with_new_aliases(args.store, new_pairs)
                print(f"  wrote {added} new alias entries to bhaga_model > employees")
                from skills.store_profile import load_aliases as _reload_aliases
                aliases = _reload_aliases(args.store)
                new_employee_alert(
                    new_pairs,
                    profile_path="bhaga_model > employees (sheet)",
                )
                # Re-parse with the now-complete alias map.
                punches = shift_backend.parse_xlsx(timecard_xlsx, employee_aliases=aliases)
                print(f"  re-parsed with updated aliases: {len(punches)} punches")

            punches = [p for p in punches if _in_window(p["date"])]
            shifts = shift_backend.aggregate_by_day(punches)
            print(f"  parsed: {len(punches)} punches, {len(shifts)} shift-days")

            if "adp_shifts" not in args.skip:
                if args.dry_run:
                    print(f"  DRY: would write {len(shifts)} shift rows")
                else:
                    s = write_raw_adp_shifts(adp_raw_sid, shifts, account=google_account)
                    summaries.append(s)
                    print(f"  shifts: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")
                    # Dual-write to BigQuery
                    bq_rows = [{
                        "date": r["date"],
                        "employee_id": r["employee_id"],
                        "canonical_name": r.get("employee_name", r["employee_id"]),
                        "raw_employee_name": r.get("raw_employee_name", ""),
                        "in_time": r.get("in_time", ""),
                        "out_time": r.get("out_time", ""),
                        "regular_hours": float(r.get("regular_hours") or 0),
                        "ot_hours": float(r.get("ot_hours") or 0),
                        "doubletime_hours": float(r.get("doubletime_hours") or 0),
                        "total_hours": float(r.get("total_hours") or 0),
                        "shift_count": int(r.get("punch_count") or 1),
                        "scraped_at_utc": r.get("scraped_at_utc", ""),
                    } for r in shifts]
                    n_bq = _write_to_bq("adp_shifts", bq_rows, merge_keys=["date", "employee_id"])
                    if n_bq:
                        print(f"  shifts (BQ): {n_bq} rows upserted")

            if "adp_punches" not in args.skip:
                if args.dry_run:
                    print(f"  DRY: would write {len(punches)} punch rows")
                else:
                    s = write_raw_adp_punches(adp_raw_sid, punches, account=google_account)
                    summaries.append(s)
                    print(f"  punches: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")
                    # Dual-write to BigQuery
                    bq_rows = [{
                        "date": r["date"],
                        "employee_id": r["employee_id"],
                        "canonical_name": r.get("employee_name", r["employee_id"]),
                        "raw_employee_name": r.get("raw_employee_name", ""),
                        "punch_index": int(r.get("punch_idx_in_day") or 0),
                        "in_time": r.get("in_time", ""),
                        "out_time": r.get("out_time", ""),
                        "regular_hours": float(r.get("regular_hours") or 0),
                        "ot_hours": float(r.get("ot_hours") or 0),
                        "doubletime_hours": float(r.get("doubletime_hours") or 0),
                        "total_hours": float(r.get("total_hours") or 0),
                        "scraped_at_utc": r.get("scraped_at_utc", ""),
                    } for r in punches]
                    n_bq = _write_to_bq("adp_punches", bq_rows, merge_keys=["date", "employee_id", "punch_index"])
                    if n_bq:
                        print(f"  punches (BQ): {n_bq} rows upserted")

    # ── ADP raw earnings (per-line) ───────────────────────────────
    if "adp_earnings" not in args.skip:
        earnings_xlsx_for_raw = _newest("Earnings*.xlsx")
        if not earnings_xlsx_for_raw:
            print("WARN: no Earnings*.xlsx found — skipping ADP raw earnings")
        else:
            print(f"# parsing ADP earnings (raw lines): {earnings_xlsx_for_raw.name}")
            raw_earnings = compensation_backend.parse_xlsx(earnings_xlsx_for_raw, employee_aliases=aliases)
            raw_earnings = [e for e in raw_earnings if _in_window(e["check_date"])]
            print(f"  parsed {len(raw_earnings)} earning lines")
            if args.dry_run:
                print(f"  DRY: would write {len(raw_earnings)} earning rows")
            else:
                s = write_raw_adp_earnings(adp_raw_sid, raw_earnings, account=google_account)
                summaries.append(s)
                print(f"  earnings: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")

    # ── ADP wage rates ────────────────────────────────────────────
    if "adp_rates" not in args.skip:
        earnings_xlsx = _newest("Earnings*.xlsx")
        if not earnings_xlsx:
            print("WARN: no Earnings*.xlsx found — skipping ADP wage rates")
        else:
            print(f"# parsing ADP earnings: {earnings_xlsx.name}")
            earnings = compensation_backend.parse_xlsx(earnings_xlsx, employee_aliases=aliases)
            rates = compensation_backend.infer_wage_rates(earnings, excluded_employees=excluded)
            print(f"  inferred rates for {len(rates)} employees")

            # Ensure roster employees missing from ADP earnings still get a
            # wage_rates row. Covers former employees (Latham, Steele) whose
            # historical shifts are in the data window but who no longer
            # appear in ADP payroll downloads. Only adds stubs for employees
            # absent from BOTH the current rates AND the existing raw sheet
            # (so we never overwrite a previously-written real rate).
            from skills.store_profile import load_employee_roster
            roster = load_employee_roster(args.store)
            rate_names = {r["employee_name"] for r in rates}
            existing_rates = read_raw_adp_rates(adp_raw_sid, account=google_account)
            existing_ids = {r["employee_id"] for r in existing_rates}
            excluded_set = set(excluded)
            roster_stubs = 0
            for rec in roster:
                canonical = rec["canonical_name"]
                if canonical not in rate_names and canonical not in existing_ids:
                    rates.append({
                        "employee_id": canonical,
                        "employee_name": canonical,
                        "wage_rate_dollars": None,
                        "ot_rate_dollars": None,
                        "is_salaried": False,
                        "multi_rate": False,
                        "rate_history": [],
                        "ot_rate_history": [],
                        "excluded_from_labor_pct": canonical in excluded_set,
                        "raw_employee_names": [],
                    })
                    roster_stubs += 1
            if roster_stubs:
                print(f"  added {roster_stubs} roster stub(s) for employees without ADP earnings")

            # Detect stale wage_rates rows left behind by alias corrections.
            # When an alias change renames an employee_id (e.g., "Johnson,
            # Dolce J" → "Johnson, Dolce"), the old row persists because the
            # upsert keys on employee_id. Find old rows whose
            # raw_employee_names overlap with incoming rows under a *different*
            # employee_id, and mark them for removal.
            incoming_raw_to_id: dict[str, str] = {}
            for r in rates:
                for rn in r.get("raw_employee_names", []):
                    incoming_raw_to_id[rn] = r["employee_id"]

            stale_keys: set[tuple] = set()
            for er in existing_rates:
                eid = er["employee_id"]
                raw_names = er.get("raw_employee_names_json", [])
                if not isinstance(raw_names, list):
                    continue
                for rn in raw_names:
                    incoming_id = incoming_raw_to_id.get(rn)
                    if incoming_id and incoming_id != eid:
                        stale_keys.add((eid,))
                        print(f"  stale wage_rate row: {eid!r} "
                              f"(raw name {rn!r} now belongs to {incoming_id!r})")
                        break

            if stale_keys:
                print(f"  will supersede {len(stale_keys)} stale wage_rate row(s)")

            if args.dry_run:
                print(f"  DRY: would write {len(rates)} wage rows")
            else:
                s = write_raw_adp_rates(
                    adp_raw_sid, rates, account=google_account,
                    superseded_keys=stale_keys or None,
                )
                summaries.append(s)
                print(f"  wage_rates: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")
                # Dual-write to BigQuery
                bq_rows = [{
                    "employee_id": r["employee_id"],
                    "canonical_name": r.get("employee_name", r["employee_id"]),
                    "wage_rate_dollars": float(r["wage_rate_dollars"]) if r.get("wage_rate_dollars") else None,
                    "ot_rate_dollars": float(r["ot_rate_dollars"]) if r.get("ot_rate_dollars") else None,
                    "is_salaried": bool(r.get("is_salaried")),
                    "excluded_from_labor_pct": bool(r.get("excluded_from_labor_pct")),
                    "excluded_from_tip_pool": bool(r.get("excluded_from_labor_pct")),
                    "raw_employee_names_json": json.dumps(r.get("raw_employee_names", [])),
                    "earnings_json": json.dumps(r.get("rate_history", [])),
                    "scraped_at_utc": r.get("scraped_at_utc", ""),
                } for r in rates]
                n_bq = _write_to_bq("adp_wage_rates", bq_rows, merge_keys=["employee_id"])
                if n_bq:
                    print(f"  wage_rates (BQ): {n_bq} rows upserted")

    # ── Square transactions + daily rollup ────────────────────────
    if "square" not in args.skip:
        tx_csv = _newest("transactions-*.csv")
        if not tx_csv:
            print("WARN: no transactions-*.csv found — skipping Square")
        else:
            print(f"# parsing Square transactions: {tx_csv.name}")
            txns = transactions_backend.parse_csv(tx_csv, shop_tz=shop_tz)
            txns = [t for t in txns if _in_window(t["date_local"])]
            print(f"  parsed {len(txns)} txns")

            if args.dry_run:
                print(f"  DRY: would write {len(txns)} txn rows")
            else:
                s = write_raw_square_transactions(square_raw_sid, txns, account=google_account)
                summaries.append(s)
                print(f"  transactions: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")
                # Dual-write to BigQuery
                bq_rows = [{
                    "transaction_id": t["transaction_id"],
                    "date_local": t["date_local"],
                    "event_type": t.get("event_type", ""),
                    "gross_sales_cents": int(t.get("gross_sales_cents") or 0),
                    "discount_cents": int(t.get("discount_cents") or 0),
                    "net_sales_cents": int(t.get("net_sales_cents") or 0),
                    "tip_cents": int(t.get("tip_cents") or 0),
                    "total_collected_cents": int(t.get("total_collected_cents") or 0),
                    "net_total_cents": int(t.get("net_total_cents") or 0),
                    "source": t.get("source", ""),
                    "staff_name": t.get("staff_name", ""),
                    "location": t.get("location", ""),
                    "created_at_src_iso": t.get("created_at_src_iso", ""),
                    "created_at_local_iso": t.get("created_at_local_iso", ""),
                    "scraped_at_utc": t.get("scraped_at_utc", ""),
                } for t in txns]
                n_bq = _write_to_bq("square_transactions", bq_rows, merge_keys=["transaction_id"])
                if n_bq:
                    print(f"  transactions (BQ): {n_bq} rows upserted")

            if "square_rollup" not in args.skip:
                rollup = aggregate_square_daily(txns)
                print(f"  computed daily rollup: {len(rollup)} days")
                if args.dry_run:
                    print(f"  DRY: would write {len(rollup)} rollup rows")
                else:
                    s = write_raw_square_daily_rollup(square_raw_sid, rollup, account=google_account)
                    summaries.append(s)
                    print(f"  daily_rollup: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")
                    # Dual-write to BigQuery
                    bq_rows = [{
                        "date_local": r["date_local"],
                        "txn_count": int(r.get("txn_count") or 0),
                        "gross_sales_cents": int(r.get("gross_sales_cents") or 0),
                        "tip_cents": int(r.get("tip_cents") or 0),
                        "net_sales_cents": int(r.get("net_sales_cents") or 0),
                        "refund_cents": int(r.get("refund_cents") or 0),
                        "order_count": int(r.get("txn_count") or 0),
                        "scraped_at_utc": r.get("scraped_at_utc", ""),
                    } for r in rollup]
                    n_bq = _write_to_bq("square_daily_rollup", bq_rows, merge_keys=["date_local"])
                    if n_bq:
                        print(f"  daily_rollup (BQ): {n_bq} rows upserted")

    # ── Square item sales + item daily rollup ────────────────────
    if "square" not in args.skip:
        item_csv = _newest("items-*.csv")
        if not item_csv:
            print("WARN: no items-*.csv found — skipping item daily rollup")
        else:
            print(f"# parsing Square item sales: {item_csv.name}")
            item_records = transactions_backend.parse_item_sales_csv(item_csv, shop_tz=shop_tz)
            item_records = [r for r in item_records if _in_window(r["date_local"])]
            print(f"  parsed {len(item_records)} item records")

            item_daily = transactions_backend.aggregate_daily_item_stats(item_records)
            print(f"  computed item daily rollup: {len(item_daily)} days")
            if args.dry_run:
                print(f"  DRY: would write {len(item_daily)} item rollup rows")
            else:
                s = write_raw_square_item_daily_rollup(square_raw_sid, item_daily, account=google_account)
                summaries.append(s)
                print(f"  item_daily_rollup: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")

    # ── Square KDS performance report ─────────────────────────────
    if "square" not in args.skip:
        kds_csv = _newest("kds-*.csv")
        if not kds_csv:
            print("WARN: no kds-*.csv found — skipping KDS daily rollup")
        else:
            print(f"# parsing Square KDS report: {kds_csv.name}")
            kds_tickets = transactions_backend.parse_kds_csv(kds_csv, shop_tz=shop_tz)
            kds_tickets = [t for t in kds_tickets if _in_window(t["date_local"])]
            print(f"  parsed {len(kds_tickets)} KDS tickets")

            kds_daily = transactions_backend.aggregate_daily_kds_stats(kds_tickets)
            kds_rollups = [
                {"date_local": d, **stats} for d, stats in sorted(kds_daily.items())
            ]
            print(f"  computed KDS daily rollup: {len(kds_rollups)} days")
            if args.dry_run:
                print(f"  DRY: would write {len(kds_rollups)} KDS daily rows")
            else:
                s = write_raw_kds_daily(square_raw_sid, kds_rollups, account=google_account)
                summaries.append(s)
                print(f"  kds_daily: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")

    print()
    print("=" * 60)
    print("SUMMARY")
    print(json.dumps(summaries, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
