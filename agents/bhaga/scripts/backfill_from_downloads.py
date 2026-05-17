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

from core.config_loader import project_dir
from skills.adp_run_automation import compensation_backend, shift_backend
from skills.adp_run_automation.employee_aliases import (
    detect_new_employees,
    update_profile_with_new_aliases,
)
from skills.square_tips import transactions_backend
from skills.tip_ledger_writer import (
    write_raw_adp_punches,
    write_raw_adp_rates,
    write_raw_adp_shifts,
    write_raw_square_daily_rollup,
    write_raw_square_transactions,
)

# Notify is optional — backfill may run in environments without Slack creds.
try:
    from agents.bhaga.notify import new_employee_alert
except Exception:  # noqa: BLE001
    def new_employee_alert(*args, **kwargs):  # type: ignore[misc]
        return None


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
        choices=["square", "adp_shifts", "adp_punches", "adp_rates", "square_rollup"],
        help="Skip a specific write. Can pass multiple times.",
    )
    cli.add_argument("--dry-run", action="store_true",
        help="Parse and aggregate but do NOT write to Google Sheets.")
    args = cli.parse_args()

    profile = load_store_profile(args.store)
    aliases = profile["employees"]["aliases"]
    excluded = profile["employees"]["excluded_from_tip_pool_and_labor_pct"]
    adp_raw_sid = profile["google_sheets"]["bhaga_adp_raw"]["spreadsheet_id"]
    square_raw_sid = profile["google_sheets"]["bhaga_square_raw"]["spreadsheet_id"]
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
                profile_path = STORE_PROFILE_DIR / f"{args.store}.json"
                profile = update_profile_with_new_aliases(profile_path, new_pairs)
                aliases = profile["employees"]["aliases"]
                new_employee_alert(new_pairs, profile_path=str(profile_path.relative_to(PROJECT)))
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

            if "adp_punches" not in args.skip:
                if args.dry_run:
                    print(f"  DRY: would write {len(punches)} punch rows")
                else:
                    s = write_raw_adp_punches(adp_raw_sid, punches, account=google_account)
                    summaries.append(s)
                    print(f"  punches: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")

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
            if args.dry_run:
                print(f"  DRY: would write {len(rates)} wage rows")
            else:
                s = write_raw_adp_rates(adp_raw_sid, rates, account=google_account)
                summaries.append(s)
                print(f"  wage_rates: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")

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

            if "square_rollup" not in args.skip:
                rollup = aggregate_square_daily(txns)
                print(f"  computed daily rollup: {len(rollup)} days")
                if args.dry_run:
                    print(f"  DRY: would write {len(rollup)} rollup rows")
                else:
                    s = write_raw_square_daily_rollup(square_raw_sid, rollup, account=google_account)
                    summaries.append(s)
                    print(f"  daily_rollup: +{s['inserted']} new, {s['updated']} updated, {s['total_after']} total")

    print()
    print("=" * 60)
    print("SUMMARY")
    print(json.dumps(summaries, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
