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
    update_aliases_bq,
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


# --skip flag names, NOT receipt source names: the timecard is `adp_shifts`
# here but `adp_timecard` in source_load_receipts. The two namespaces are
# deliberately not unioned or compared element-wise anywhere — adp_inputs_absent
# only asks whether loaded_sources is empty. Compare them and the timecard will
# look permanently absent.
_ADP_SOURCES = ("adp_shifts", "adp_schedule", "adp_liability", "adp_rates")


def adp_sources_requested(skip: list[str]) -> set[str]:
    """ADP sources this invocation was asked to load (--skip flag names)."""
    return {s for s in _ADP_SOURCES if s not in skip}


def record_load_receipt(
    source: str,
    *,
    store: str,
    refresh_date: datetime.date | None,
    rows: int,
    loaded_sources: set[str],
    dry_run: bool = False,
) -> None:
    """Durable BQ proof that `source`'s export was parsed and upserted.

    Written even when rows == 0: a store-closed day parses a timecard with no
    shifts, and the receipt is what keeps the scrape gate from firing again the
    next night (and re-prompting for ADP OTP). Never raises — a receipt-write
    failure must not discard data that already landed, and the gate re-scrapes
    on a missing receipt, which is idempotent.

    `loaded_sources` is always updated even when no receipt is written, because
    the empty-load guard needs to know the export was physically present.
    """
    loaded_sources.add(source)
    if dry_run or refresh_date is None:
        return
    try:
        # Deliberately the unwrapped datastore call: the module-level load_rows
        # injects replace=True in --replace mode, which would TRUNCATE this
        # table and destroy every other date's receipt.
        _ds_load_rows(
            "source_load_receipts",
            [{
                "store": store,
                "refresh_date": refresh_date.isoformat(),
                "source": source,
                "rows_upserted": rows,
                "loaded_at_utc": datetime.datetime.now(datetime.UTC).isoformat(),
                "run_id": os.environ.get("BHAGA_RUN_ID"),
            }],
            merge_keys=["store", "refresh_date", "source"],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: load receipt for {source} failed (non-fatal): "
              f"{type(exc).__name__}: {exc}")


def adp_inputs_absent(
    skip: list[str], loaded_sources: set[str], *, require_adp: bool = False,
) -> bool:
    """True when ADP exports were expected on disk but not one was found.

    `require_adp` is passed only when the ADP pipeline SUCCEEDED in the same
    execution, which is the only state that guarantees exports are on local
    disk. Without it an empty ADP load is legitimate and must still exit 0:

      * the ADP pipeline failed — an unanswered OTP is a documented graceful
        skip (`bhaga.mdc`: "On no reply: ADP step is gracefully skipped, run
        exits 0, next nightly retries");
      * the ADP pipeline was skipped — the scrape gate only skips once a BQ
        load receipt proves the data already landed, so there is nothing to do.

    Deliberately "none of them" rather than "any missing", so a single
    legitimately-absent export — no schedule published yet, rates not due this
    cadence — does not fail the nightly.
    """
    if not require_adp:
        return False
    return bool(adp_sources_requested(skip)) and not loaded_sources


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", required=True)
    cli.add_argument("--start", default=None, help="YYYY-MM-DD; trims input to this window. Default: no trim.")
    cli.add_argument("--end", default=None)
    cli.add_argument(
        "--skip", default=[], action="append",
        choices=["square", "adp_shifts", "adp_punches", "adp_rates", "adp_schedule", "adp_liability", "square_rollup"],
        help="Skip a specific write. Can pass multiple times.",
    )
    cli.add_argument("--dry-run", action="store_true",
        help="Parse and aggregate but do NOT write to BigQuery.")
    cli.add_argument(
        "--refresh-date", default=None,
        help="YYYY-MM-DD business date this load belongs to. When given, each "
             "ADP source that parses and upserts writes a source_load_receipts "
             "row, which is what the scrape gate consults to decide whether ADP "
             "already landed for the date.")
    cli.add_argument(
        "--require-adp", action="store_true",
        help="Fail (exit 1) if no ADP export is found. Set by daily_refresh "
             "only when the ADP pipeline succeeded in the same execution, so "
             "the exports are known to be on disk; without it an empty ADP "
             "load is a legitimate graceful skip.")
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
    refresh_date = (
        datetime.date.fromisoformat(args.refresh_date) if args.refresh_date else None
    )
    # Which ADP sources actually had an export to parse. A requested source that
    # leaves no entry here means its file was absent from DOWNLOADS, which in a
    # Cloud Run container means the scrape ran in an earlier, now-dead execution.
    loaded_sources: set[str] = set()

    def _record_load_receipt(source: str, *, rows: int) -> None:
        record_load_receipt(
            source,
            store=args.store,
            refresh_date=refresh_date,
            rows=rows,
            loaded_sources=loaded_sources,
            dry_run=args.dry_run,
        )

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
                added = update_aliases_bq(args.store, new_pairs)
                print(f"  wrote {added} new alias entries to employee_aliases BQ table")
                from skills.store_profile import load_aliases as _reload_aliases
                aliases = _reload_aliases(args.store)
                new_employee_alert(
                    new_pairs,
                    profile_path="employee_aliases BQ table",
                )
                punches = shift_backend.parse_xlsx(timecard_xlsx, employee_aliases=aliases)
                print(f"  re-parsed with updated aliases: {len(punches)} punches")

            punches = [p for p in punches if _in_window(p["date"])]
            shifts = shift_backend.aggregate_by_day(punches)
            print(f"  parsed: {len(punches)} punches, {len(shifts)} shift-days")
            # Receipt keyed on the export being present and parsed, not on any
            # one sub-table being written — --skip adp_shifts must not suppress
            # the proof that the timecard itself landed.
            _record_load_receipt("adp_timecard", rows=len(shifts))

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
            _record_load_receipt("adp_schedule", rows=len(records))
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

            # Per-employee forward shifts (Issue #166 follow-through)
            emp_records = schedule_backend.build_employee_schedule_records(
                payload.get("weeks", [])
            )
            for warn in schedule_backend.reconcile_employee_vs_footer(
                payload.get("weeks", [])
            ):
                print(f"  WARN: {warn}")
            emp_bq = [
                {
                    "date": r["date"],
                    "employee_id": r["employee_id"],
                    "employee_name": r["employee_name"],
                    "scheduled_hours": r["scheduled_hours"],
                    "shift_ranges_json": r.get("shift_ranges_json"),
                    "week_start": r["week_start"],
                    "hour_kind": r.get("hour_kind") or "shift",
                    "scraped_at_utc": scraped_at,
                    "materialized_at_utc": now_utc,
                }
                for r in emp_records
            ]
            print(f"  parsed: {len(emp_bq)} scheduled employee-days")
            if args.dry_run:
                print(f"  DRY: would load {len(emp_bq)} scheduled-shift rows into BQ")
            elif emp_bq:
                # MERGE alone leaves stale (date, employee) rows when someone is
                # removed from a day (Lindsay Aug 6 → Aug 7). Purge every date
                # covered by this scrape, then upsert the authoritative set.
                from core.datastore import fq, get_client  # noqa: PLC0415
                dates = sorted({r["date"] for r in emp_bq})
                client = get_client()
                if client is not None and dates:
                    date_list = ", ".join(f"DATE '{d}'" for d in dates)
                    del_job = client.query(
                        f"DELETE FROM {fq('adp_scheduled_shifts')} "
                        f"WHERE date IN ({date_list})"
                    )
                    del_job.result()
                    print(
                        f"  purged adp_scheduled_shifts for {len(dates)} dates "
                        f"({dates[0]}→{dates[-1]}) before upsert"
                    )
                n = load_rows(
                    "adp_scheduled_shifts", emp_bq,
                    merge_keys=["date", "employee_id"],
                    column_bq_types={
                        "date": "DATE", "week_start": "DATE",
                        "scraped_at_utc": "TIMESTAMP",
                        "materialized_at_utc": "TIMESTAMP",
                    },
                )
                print(f"  adp_scheduled_shifts (BQ): {n} rows upserted")
                summaries.append({"table": "adp_scheduled_shifts", "rows": n})

    # ── ADP Payroll Liability (employer burden calibration) ───────
    if "adp_liability" not in args.skip:
        liability_json = _newest("PayrollLiability-*.json")
        if not liability_json:
            print("WARN: no PayrollLiability-*.json — skipping employer burden load")
        else:
            from skills.adp_run_automation import payroll_liability_backend as plb
            print(f"# parsing ADP payroll liability: {liability_json.name}")
            payload = json.loads(liability_json.read_text())
            scraped_at = payload.get("scraped_at_utc")
            now_utc = datetime.datetime.utcnow().isoformat() + "Z"
            texts = payload.get("reports") or (
                [payload["text"]] if payload.get("text") else []
            )
            bq_rows = []
            for text in texts:
                try:
                    rec = plb.parse_payroll_liability_text(text)
                except ValueError as exc:
                    print(f"  WARN: skip liability parse: {exc}")
                    continue
                rec["scraped_at_utc"] = scraped_at
                rec["materialized_at_utc"] = now_utc
                bq_rows.append(rec)
            _record_load_receipt("adp_liability", rows=len(bq_rows))
            if args.dry_run:
                print(f"  DRY: would load {len(bq_rows)} liability rows into BQ")
            elif bq_rows:
                n = load_rows(
                    "adp_payroll_liability", bq_rows,
                    merge_keys=["check_date", "payroll_label"],
                    column_bq_types={
                        "check_date": "DATE",
                        "scraped_at_utc": "TIMESTAMP",
                        "materialized_at_utc": "TIMESTAMP",
                    },
                )
                print(f"  adp_payroll_liability (BQ): {n} rows upserted")
                summaries.append({"table": "adp_payroll_liability", "rows": n})

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
            _record_load_receipt("adp_rates", rows=len(rates))

            # Roster stubs: ensure employees absent from current ADP download
            # still have a wage_rates row (covers former employees whose
            # historical shifts are in the data window). Soft-fail when Sheets
            # roster/config is unavailable (CI / laptop without config.yaml).
            try:
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
                            "rate_source": "roster_stub",
                        })
                        roster_stubs += 1
                if roster_stubs:
                    print(f"  added {roster_stubs} roster stub(s)")
            except Exception as exc:  # noqa: BLE001
                print(f"WARN: roster stubs skipped: {type(exc).__name__}: {exc}")

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

    # ── ADP Payroll-info gap-fill rates (Issue #213) ───────────────
    if "adp_rates" not in args.skip:
        pay_info_json = _newest("PayInfoRates*.json")
        if pay_info_json:
            print(f"# loading ADP pay_info rates: {pay_info_json.name}")
            try:
                from skills.adp_run_automation.pay_info_backend import (  # noqa: PLC0415
                    write_pay_info_rates_bq,
                )
                payload = json.loads(pay_info_json.read_text())
                rates = payload.get("rates") or []
                if args.dry_run:
                    print(f"  DRY: would MERGE {len(rates)} pay_info rate rows")
                else:
                    n = write_pay_info_rates_bq(rates, dry_run=False)
                    summaries.append({"table": "adp_wage_rates_pay_info", "rows": n})
                    remaining: list[str] = []
                    try:
                        from skills.adp_run_automation.pay_info_backend import (  # noqa: PLC0415
                            assert_no_missing_puncher_rates,
                        )
                        remaining = assert_no_missing_puncher_rates(days=60)
                    except Exception as exc:  # noqa: BLE001
                        print(f"WARN: wage gap assert failed: {type(exc).__name__}: {exc}")
                    scrape_errors = payload.get("errors") or {}
                    if scrape_errors or remaining:
                        try:
                            from skills.adp_run_automation.pay_info_backend import (  # noqa: PLC0415
                                report_pay_info_issues,
                            )
                            report_pay_info_issues(
                                date=datetime.date.today().isoformat(),
                                scrape_errors=scrape_errors,
                                remaining_gaps=remaining,
                                attempted=len(payload.get("attempted") or []),
                                scraped_ok=len(rates),
                            )
                        except Exception as exc:  # noqa: BLE001
                            print(f"WARN: pay_info Slack warn failed: {type(exc).__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001
                print(f"WARN: pay_info rate load failed: {type(exc).__name__}: {exc}")

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

    requested = adp_sources_requested(args.skip)
    if adp_inputs_absent(args.skip, loaded_sources, require_adp=args.require_adp):
        print(
            "BREADCRUMB adp_inputs_absent — requested "
            f"{sorted(requested)} but no ADP export was present in "
            f"{DOWNLOADS}; refusing to report success on an empty load"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
