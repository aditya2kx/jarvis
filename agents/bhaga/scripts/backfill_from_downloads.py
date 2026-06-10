#!/usr/bin/env python3
"""BHAGA end-to-end scrape-file loader: parse downloads → write BigQuery (primary).

Reads the most-recent files in extracted/downloads/ (Square Transactions CSV,
Square Items CSV, Square KDS CSV, ADP Timecard XLSX, ADP Earnings XLSX),
parses them via the source skills, maps the parser dicts through the canonical
``map_*`` functions from ``backfill_bigquery``, and upserts the results into
BigQuery as the single system of record.

Raw Google Sheets are NOT written by this script. They are rendered as
projections afterward by ``render_raw_sheet_from_bq.py``.

Requires BHAGA_DATASTORE=bigquery (enforced at startup).

Usage:
    BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.backfill_from_downloads --store palmetto
    BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.backfill_from_downloads --store palmetto \\
            --start 2026-03-22 --end 2026-05-15
    BHAGA_DATASTORE=bigquery \\
        python3 -m agents.bhaga.scripts.backfill_from_downloads --store palmetto \\
            --skip square --dry-run
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
from core.datastore import load_rows as _ds_load_rows

# Fresh-scrape replace mode (set from --replace / BHAGA_RAW_REPLACE in main()).
# When True, every load_rows() below TRUNCATEs its target table before loading,
# so a full-history scrape fully owns each table. Data always lands directly in
# BigQuery — this script never reads from or writes data files to GCS.
_REPLACE_TABLES = False


def load_rows(*args, **kwargs):
    """Module wrapper around core.datastore.load_rows that injects the
    fresh-scrape ``replace=True`` when this run is in replace mode."""
    if _REPLACE_TABLES:
        kwargs.setdefault("replace", True)
    return _ds_load_rows(*args, **kwargs)
from skills.adp_run_automation import compensation_backend, schedule_backend, shift_backend
from skills.adp_run_automation.employee_aliases import (
    detect_new_employees,
    update_sheet_with_new_aliases,
)
from skills.square_tips import transactions_backend
from skills.tip_ledger_writer import read_raw_adp_rates

from agents.bhaga.scripts.backfill_bigquery import (
    map_adp_earnings_row,
    map_adp_punch,
    map_adp_shift,
    map_adp_wage_rate,
    map_kds_ticket,
    map_square_daily_rollup,
    map_square_item_daily,
    map_square_item_line,
    map_square_kds_daily,
    map_square_transaction,
    load_store_profile,
)

# Notify is optional — backfill may run in environments without Slack creds.
try:
    from agents.bhaga.notify import new_employee_alert
except Exception:  # noqa: BLE001
    def new_employee_alert(*args, **kwargs):  # type: ignore[misc]
        return None


PROJECT = pathlib.Path(project_dir())
DOWNLOADS = PROJECT / "extracted" / "downloads"

# BQ type hints for TIMESTAMP columns that can be None. Without this, load_rows
# infers type STRING for None values, causing a BQ type conflict.
_TS_TYPES = {"scraped_at_utc": "TIMESTAMP"}


def _newest(pattern: str) -> pathlib.Path | None:
    paths = [pathlib.Path(p) for p in glob.glob(str(DOWNLOADS / pattern))]
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


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
        choices=["square", "adp_shifts", "adp_punches", "adp_rates", "adp_schedule", "square_rollup"],
        help="Skip a specific write. Can pass multiple times.",
    )
    cli.add_argument("--dry-run", action="store_true",
        help="Parse and aggregate but do NOT write to BigQuery.")
    cli.add_argument(
        "--replace", action="store_true",
        default=os.environ.get("BHAGA_RAW_REPLACE", "").strip() in ("1", "true", "yes"),
        help="Fresh-scrape mode: TRUNCATE each target BQ table before loading, "
             "so the scrape fully owns the table contents (and duplicate natural "
             "keys within a batch don't trip the MERGE one-source-row rule). Use "
             "ONLY for a full-history backfill — a windowed --replace drops "
             "out-of-window rows. Defaults to on when BHAGA_RAW_REPLACE=1 (set by "
             "the fresh-scrape sandbox path).")
    args = cli.parse_args()

    # Fresh-scrape replace applies to every load_rows() call in this run (the
    # module wrapper reads this flag and injects replace=True).
    if args.replace:
        global _REPLACE_TABLES
        _REPLACE_TABLES = True
        print("# --replace: TRUNCATE-then-load (fresh full-history scrape owns each table)")

    # BQ is now the system of record — this script must not run without it.
    if os.environ.get("BHAGA_DATASTORE", "").lower() != "bigquery":
        print(
            "ERROR: BHAGA_DATASTORE=bigquery is required. "
            "backfill_from_downloads writes BigQuery as the primary sink; "
            "Sheets are rendered afterward by render_raw_sheet_from_bq.py.",
            file=sys.stderr,
        )
        return 1

    profile = load_store_profile(args.store)
    from skills.store_profile import load_aliases, load_exclusions
    aliases = load_aliases(args.store)
    excluded = load_exclusions(args.store)["permanent"]
    adp_raw_sid = resolve_sheet_id("bhaga_adp_raw", profile)
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

            new_pairs = detect_new_employees(punches, aliases)
            if new_pairs:
                print(f"  detected {len(new_pairs)} new employee(s): "
                      + ", ".join(f"{r!r}→{c!r}" for r, c in new_pairs))
                added = update_sheet_with_new_aliases(args.store, new_pairs)
                print(f"  wrote {added} new alias entries to bhaga_model > employees")
                from skills.store_profile import load_aliases as _reload_aliases
                aliases = _reload_aliases(args.store)
                new_employee_alert(
                    new_pairs,
                    profile_path="bhaga_model > employees (sheet)",
                )
                punches = shift_backend.parse_xlsx(timecard_xlsx, employee_aliases=aliases)
                print(f"  re-parsed with updated aliases: {len(punches)} punches")

            punches = [p for p in punches if _in_window(p["date"])]
            shifts = shift_backend.aggregate_by_day(punches)
            print(f"  parsed: {len(punches)} punches, {len(shifts)} shift-days")

            if "adp_shifts" not in args.skip:
                bq_rows = [map_adp_shift(r) for r in shifts]
                bq_rows = [r for r in bq_rows if r["date"] is not None]
                if args.dry_run:
                    print(f"  DRY: would load {len(bq_rows)} shift rows into BQ")
                else:
                    n = load_rows("adp_shifts", bq_rows, merge_keys=["date", "employee_id"],
                                 column_bq_types=_TS_TYPES)
                    print(f"  adp_shifts (BQ): {n} rows upserted")
                    summaries.append({"table": "adp_shifts", "rows": n})

            if "adp_punches" not in args.skip:
                bq_rows = [map_adp_punch(r) for r in punches]
                bq_rows = [r for r in bq_rows if r["date"] is not None]
                if args.dry_run:
                    print(f"  DRY: would load {len(bq_rows)} punch rows into BQ")
                else:
                    n = load_rows("adp_punches", bq_rows,
                                  merge_keys=["date", "employee_id", "punch_index"],
                                  column_bq_types=_TS_TYPES)
                    print(f"  adp_punches (BQ): {n} rows upserted")
                    summaries.append({"table": "adp_punches", "rows": n})

    # ── ADP scheduled hours (Team Schedule, forward-looking) ─────
    if "adp_schedule" not in args.skip:
        schedule_json = _newest("Schedule-*.json")
        if not schedule_json:
            print("WARN: no Schedule-*.json found — skipping ADP scheduled hours")
        else:
            print(f"# parsing ADP schedule: {schedule_json.name}")
            payload = json.loads(schedule_json.read_text())
            scraped_at = payload.get("scraped_at_utc")
            records = schedule_backend.build_schedule_records(payload.get("weeks", []))
            now_utc = datetime.datetime.utcnow().isoformat() + "Z"
            bq_rows = [
                {
                    "date": r["date"],
                    "scheduled_hours": r["scheduled_hours"],
                    "employee_count": r["employee_count"],
                    "week_start": r["week_start"],
                    "scraped_at_utc": scraped_at,
                    "materialized_at_utc": now_utc,
                }
                for r in records
            ]
            print(f"  parsed: {len(bq_rows)} scheduled days")
            if args.dry_run:
                print(f"  DRY: would load {len(bq_rows)} scheduled-day rows into BQ")
            elif bq_rows:
                n = load_rows(
                    "adp_scheduled_daily", bq_rows, merge_keys=["date"],
                    column_bq_types={"date": "DATE", "week_start": "DATE",
                                     "scraped_at_utc": "TIMESTAMP",
                                     "materialized_at_utc": "TIMESTAMP"},
                )
                print(f"  adp_scheduled_daily (BQ): {n} rows upserted")
                summaries.append({"table": "adp_scheduled_daily", "rows": n})

    # ── ADP wage rates + per-line earnings ───────────────────────
    if "adp_rates" not in args.skip:
        earnings_xlsx = _newest("Earnings*.xlsx")
        if not earnings_xlsx:
            print("WARN: no Earnings*.xlsx found — skipping ADP wage rates")
        else:
            print(f"# parsing ADP earnings: {earnings_xlsx.name}")
            earnings = compensation_backend.parse_xlsx(earnings_xlsx, employee_aliases=aliases)
            rates = compensation_backend.infer_wage_rates(earnings, excluded_employees=excluded)
            print(f"  inferred rates for {len(rates)} employees")

            # Roster stubs: ensure employees absent from current ADP download
            # still have a wage_rates row (covers former employees whose
            # historical shifts are in the data window).
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
                print(f"  added {roster_stubs} roster stub(s)")

            bq_rows = [map_adp_wage_rate(r, profile) for r in rates]
            if args.dry_run:
                print(f"  DRY: would load {len(bq_rows)} wage_rate rows into BQ")
            else:
                n = load_rows("adp_wage_rates", bq_rows, merge_keys=["employee_id"],
                             column_bq_types=_TS_TYPES)
                print(f"  adp_wage_rates (BQ): {n} rows upserted")
                summaries.append({"table": "adp_wage_rates", "rows": n})

            # Per-line earnings (adp_earnings table)
            now_utc = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            earnings_rows = [
                {
                    "period_start": str(e.get("period_start", "")),
                    "period_end": str(e.get("period_end", "")),
                    "check_date": str(e.get("check_date", "")),
                    "employee_name": str(e.get("employee_name", "")),
                    "raw_employee_name": str(e.get("raw_employee_name", "")),
                    "description": str(e.get("description", "")),
                    "hours": e.get("hours", 0) or 0,
                    "hourly_rate": e.get("hourly_rate", 0) or 0,
                    "amount": e.get("amount", 0) or 0,
                    "scraped_at_utc": e.get("scraped_at_utc", now_utc),
                }
                for e in earnings
                if e.get("period_start")
            ]
            bq_earnings_rows = [map_adp_earnings_row(r) for r in earnings_rows]
            bq_earnings_rows = [r for r in bq_earnings_rows if r["period_start"] is not None]
            if args.dry_run:
                print(f"  DRY: would load {len(bq_earnings_rows)} adp_earnings rows into BQ")
            else:
                n = load_rows(
                    "adp_earnings", bq_earnings_rows,
                    merge_keys=["period_start", "period_end", "employee", "description", "check_date"],
                    column_bq_types=_TS_TYPES,
                )
                print(f"  adp_earnings (BQ): {n} rows upserted")
                summaries.append({"table": "adp_earnings", "rows": n})

    # ── Square transactions + daily rollup ────────────────────────
    if "square" not in args.skip:
        tx_csv = _newest("transactions-*.csv")
        if not tx_csv:
            print("WARN: no transactions-*.csv found — skipping Square transactions")
        else:
            print(f"# parsing Square transactions: {tx_csv.name}")
            txns = transactions_backend.parse_csv(tx_csv, shop_tz=shop_tz)
            txns = [t for t in txns if _in_window(t["date_local"])]
            print(f"  parsed {len(txns)} txns")

            bq_rows = [map_square_transaction(r) for r in txns]
            bq_rows = [r for r in bq_rows if r["date_local"] is not None]
            if args.dry_run:
                print(f"  DRY: would load {len(bq_rows)} transaction rows into BQ")
            else:
                n = load_rows("square_transactions", bq_rows, merge_keys=["transaction_id"],
                             column_bq_types=_TS_TYPES)
                print(f"  square_transactions (BQ): {n} rows upserted")
                summaries.append({"table": "square_transactions", "rows": n})

            if "square_rollup" not in args.skip:
                rollup = aggregate_square_daily(txns)
                print(f"  computed daily rollup: {len(rollup)} days")
                bq_rollup_rows = [map_square_daily_rollup(r) for r in rollup]
                bq_rollup_rows = [r for r in bq_rollup_rows if r["date_local"] is not None]
                if args.dry_run:
                    print(f"  DRY: would load {len(bq_rollup_rows)} daily_rollup rows into BQ")
                else:
                    n = load_rows("square_daily_rollup", bq_rollup_rows, merge_keys=["date_local"],
                                 column_bq_types=_TS_TYPES)
                    print(f"  square_daily_rollup (BQ): {n} rows upserted")
                    summaries.append({"table": "square_daily_rollup", "rows": n})

    # ── Square item sales + item daily rollup ────────────────────
    if "square" not in args.skip:
        item_csv = _newest("items-*.csv")
        if not item_csv:
            print("WARN: no items-*.csv found — skipping Square item sales")
        else:
            print(f"# parsing Square item sales: {item_csv.name}")
            item_records = transactions_backend.parse_item_sales_csv(item_csv, shop_tz=shop_tz)
            item_records = [r for r in item_records if _in_window(r["date_local"])]
            print(f"  parsed {len(item_records)} item records")

            bq_lines = [map_square_item_line(r) for r in item_records]
            bq_lines = [r for r in bq_lines if r["date_local"] is not None]
            if args.dry_run:
                print(f"  DRY: would load {len(bq_lines)} item_lines rows into BQ")
            else:
                n = load_rows(
                    "square_item_lines", bq_lines,
                    merge_keys=["transaction_id", "item_name", "item_sold_at_local", "line_seq"],
                    column_bq_types=_TS_TYPES,
                )
                print(f"  square_item_lines (BQ): {n} rows upserted")
                summaries.append({"table": "square_item_lines", "rows": n})

            item_daily = transactions_backend.aggregate_daily_item_stats(item_records)
            print(f"  computed item daily rollup: {len(item_daily)} days")
            bq_item_daily = [map_square_item_daily(r) for r in item_daily]
            bq_item_daily = [r for r in bq_item_daily if r["date_local"] is not None]
            if args.dry_run:
                print(f"  DRY: would load {len(bq_item_daily)} item_daily rows into BQ")
            else:
                n = load_rows("square_item_daily", bq_item_daily, merge_keys=["date_local"],
                             column_bq_types=_TS_TYPES)
                print(f"  square_item_daily (BQ): {n} rows upserted")
                summaries.append({"table": "square_item_daily", "rows": n})

    # ── Square KDS performance report ─────────────────────────────
    if "square" not in args.skip:
        kds_csv = _newest("kds-*.csv")
        if not kds_csv:
            print("WARN: no kds-*.csv found — skipping KDS report")
        else:
            print(f"# parsing Square KDS report: {kds_csv.name}")
            kds_tickets = transactions_backend.parse_kds_csv(kds_csv, shop_tz=shop_tz)
            kds_tickets = [t for t in kds_tickets if _in_window(t["date_local"])]
            print(f"  parsed {len(kds_tickets)} KDS tickets")

            kds_daily_agg = transactions_backend.aggregate_daily_kds_stats(kds_tickets)
            kds_rollups = [{"date_local": d, **stats} for d, stats in sorted(kds_daily_agg.items())]
            print(f"  computed KDS daily rollup: {len(kds_rollups)} days")

            bq_kds_daily = [map_square_kds_daily(r) for r in kds_rollups]
            bq_kds_daily = [r for r in bq_kds_daily if r["date_local"] is not None]
            if args.dry_run:
                print(f"  DRY: would load {len(bq_kds_daily)} kds_daily rows into BQ")
                print(f"  DRY: would load {len(kds_tickets)} kds_tickets rows into BQ")
            else:
                n = load_rows("square_kds_daily", bq_kds_daily, merge_keys=["date_local"],
                             column_bq_types=_TS_TYPES)
                print(f"  square_kds_daily (BQ): {n} rows upserted")
                summaries.append({"table": "square_kds_daily", "rows": n})

                bq_tickets = [map_kds_ticket(r) for r in kds_tickets]
                bq_tickets = [r for r in bq_tickets if r["date_local"] is not None]
                n = load_rows(
                    "square_kds_tickets", bq_tickets,
                    merge_keys=["date_local", "time_created", "ticket_name"],
                    column_bq_types=_TS_TYPES,
                )
                print(f"  square_kds_tickets (BQ): {n} rows upserted")
                summaries.append({"table": "square_kds_tickets", "rows": n})

    print()
    print("=" * 60)
    print("SUMMARY (BigQuery upserts)")
    print(json.dumps(summaries, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
