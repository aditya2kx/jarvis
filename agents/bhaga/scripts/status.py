#!/usr/bin/env python3
"""Read-only ops freshness checker for the BHAGA pipeline.

Answers "did yesterday's run land in Sheets, BigQuery, and Grafana?" without
spelunking coordinates or hand-writing queries.  Run this first for any
operational question about whether a nightly run completed — don't
hand-investigate.

Usage:
    # Check yesterday (default, America/Chicago):
    BHAGA_SECRETS_BACKEND=gcp \\
    BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \\
    python3 -m agents.bhaga.scripts.status --store palmetto

    # Check a specific date:
    python3 -m agents.bhaga.scripts.status --store palmetto --date 2026-06-03

    # Machine-readable JSON (for scripting / alerting):
    python3 -m agents.bhaga.scripts.status --store palmetto --json

    # Verify all registry date columns exist in live BQ (catches schema drift):
    python3 -m agents.bhaga.scripts.status --store palmetto --check-schema
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import zoneinfo
from dataclasses import dataclass, field
from typing import Literal

# Repo root on sys.path so imports work regardless of invocation style.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

# Enable BigQuery before the datastore module loads its gate check.
os.environ.setdefault("BHAGA_DATASTORE", "bigquery")

from core.config_loader import project_dir, refresh_access_token, resolve_sheet_id
from core.datastore import read_query
from skills.bhaga_config.dates import coerce_iso_date
from skills.tip_ledger_writer.writer import _read_tab

_PROJECT = "jarvis-bhaga-prod"
_DATASET = os.environ.get("BHAGA_BQ_DATASET", "bhaga")
_STORE_PROFILE_DIR = (
    pathlib.Path(project_dir())
    / "agents"
    / "bhaga"
    / "knowledge-base"
    / "store-profiles"
)

GRAFANA_DASHBOARD_URL = "https://steadyangelfish2985.grafana.net/d/bhaga-analytics-v1"

# ── Declarative target registry ───────────────────────────────────────────────
#
# THIS IS THE SINGLE SOURCE OF TRUTH for what the doctor checks.
#
# Anti-drift contract: tests in test_status.py parse:
#   - agents/bhaga/grafana/dashboard.json  → every vw_* must be in GRAFANA_VIEWS
#   - core/migrations/003_model_tables.sql → every model_* must be in BQ_TARGETS
#   - core/migrations/00*.sql column defs  → every date_column must exist
#
# When you add a new migration, model table, or Grafana panel on a new view:
#   1. Add the Target here.
#   2. Run: python3 -m pytest agents/bhaga/scripts/test_status.py
#
# (CI runs check_doc_freshness.py --strict which enforces editing this file
# alongside schema/dashboard changes.)
#
# Panel-SQL contract (learned the hard way — see RUNBOOK §14 incident
# 2026-06-07): dashboard.json column aliases MUST use BigQuery-valid identifiers
# — backticks, not double quotes (`AS "x"` is a string-literal syntax error) —
# and output field names may not contain `/` or `$` (spaces/hyphens are fine).
# Validate any panel SQL change with `python3 agents/bhaga/grafana/verify_panels.py`.
#
# 2026-06-15: weekly Labor % panels (ids 36, 37) got max=1/min=0 cap — display
# only, no new views or columns, no registry change needed.

SHEET_TABS: tuple[str, ...] = ("daily", "tip_alloc_daily")

CheckMode = Literal["exact", "iso_week", "period_coverage"]


@dataclass(frozen=True)
class Target:
    table: str
    date_column: str
    mode: CheckMode = "exact"


BQ_TARGETS: list[Target] = [
    # ── Model tables (materialized by materialize_model_bq.py) ──────────────
    Target("model_daily", "date"),
    Target("model_labor_daily", "date"),
    Target("model_tip_alloc_daily", "date"),
    Target("model_period_summary", "period_start", "period_coverage"),
    Target("model_labor_weekly", "iso_week", "iso_week"),
    Target("model_review_bonus_period", "period_start", "period_coverage"),  # migration 004
    # ── Raw tables (mirrored by backfill_bigquery.py) ────────────────────────
    Target("square_transactions", "date_local"),
    Target("adp_shifts", "date"),
    Target("adp_punches", "date"),
    Target("square_daily_rollup", "date_local"),
    # ── Raw-parity tables (migration 005) ────────────────────────────────────
    Target("square_item_lines", "date_local"),
    Target("square_item_daily", "date_local"),
    Target("square_kds_daily", "date_local"),
    Target("square_kds_tickets", "date_local"),
    Target("adp_earnings", "period_start", "period_coverage"),
    Target("google_reviews", "post_date_ct"),
    # store_config (migration 007) intentionally NOT a freshness target — it is a
    # config/tunables store (no date partition) edited via /bhaga-cloud config set.
    # ── Forecast table (migration 011) ───────────────────────────────────────
    # model_forecast_daily has future-only rows until the first nightly load runs;
    # status check will show EMPTY until then (expected pre-load).
    Target("model_forecast_daily", "date"),
    # ── ADP scheduled hours (migration 013) ──────────────────────────────────
    # adp_scheduled_daily is forward-looking (current + next week from the ADP
    # Team Schedule scrape); max_date is in the future, so the freshness check
    # reads as fresh. Empty until the first nightly schedule scrape runs.
    Target("adp_scheduled_daily", "date"),
]

GRAFANA_VIEWS: list[Target] = [
    # These are the exact views the Grafana dashboard renders.  All are checked
    # against BQ — no browser visit required; data-layer confirmation is the
    # contract.  See dashboard URL printed in output.
    Target("vw_daily_sales", "date_local"),
    Target("vw_labor_daily", "date"),
    Target("vw_tips_by_hour", "date_local"),
    Target("vw_sales_labor_daily", "date_local"),
    Target("vw_model_labor_daily", "date"),
    Target("vw_model_period_summary", "period_start", "period_coverage"),
    # migration 004 / dashboard refactor views
    Target("vw_model_labor_weekly", "iso_week", "iso_week"),
    Target("vw_model_payroll_period", "period_start", "period_coverage"),
    # per-item KDS time distribution (percentile chart); dashboard v27 added
    # a dashed p99 Goal series — same view, no new entry needed.
    Target("vw_order_quality_daily", "date"),
    # migration 025: per-source daily p95 KDS time (panel 51 per-source chart).
    Target("vw_kds_order_quality_by_source_daily", "date"),
    # migration 009: order-level KDS investigation (slow-orders table);
    # dashboard v27 wired $kds_date / $kds_min_per_item vars; v28 fixed the
    # $kds_date query-var shape + unquoted threshold — same view, no new entry.
    Target("vw_kds_order_investigation", "date_local"),
    Target("vw_staff_on_shift", "date"),
    # migration 011: Labor Forecast section (section 7) panels — dashboard v30.
    # migration 012 added prev-week %-change columns to vw_forecast_exclusions;
    # the view set is unchanged so these Targets still cover section 7.
    # vw_model_forecast is empty pre-load; vw_forecast_accuracy fills from the
    # leakage-free backfill (build_backfill_rows) on the first materialize.
    Target("vw_model_forecast", "date"),
    Target("vw_forecast_accuracy", "date"),
    Target("vw_forecast_exclusions", "date"),
    # migration 013: adp_scheduled_daily; vw_scheduled_vs_goal kept for ad-hoc queries
    # (panel 74 now reads vw_model_forecast directly, dashboard v40).
    Target("vw_scheduled_vs_goal", "date"),
    # migration 014: vw_model_forecast + vw_forecast_exclusions refreshed with
    # scheduled_hours / net_sales / aov columns (dashboard v33). Existing Targets
    # above cover freshness; migration 014 only adds columns — no new view entries.
    # migration 015: vw_model_forecast refreshed with dow column + zero-gated
    # prior-week fallback (dashboard v34). No new BQ_TARGETS entries needed; the
    # existing vw_model_forecast Target above still covers freshness.
    # migration 017: Pipeline Health two-table design (dashboard v38). Both
    # views are empty / NULL run_date until the first nightly after merge.
    # migration 019: vw_pipeline_runs exposes recovery_retrigger (dashboard v41
    # Pipeline Runs panel); same Target covers freshness — column add only.
    Target("vw_pipeline_runs", "run_date"),
    Target("vw_source_pulls", "run_date"),
    # migration 020: vw_training_shifts (panel 62, 6. Payroll — Training Shifts table).
    # PR2 (Sheets exit): Sheet projection panels removed; vw_training_shifts remains.
    Target("vw_training_shifts", "date"),
    # migration 026: vw_review_bonus_detail (panel 76, 6. Payroll — per-review payroll table).
    # Filters google_reviews to total_bonus > 0; per_employee_bonus = total_bonus / member_count.
    Target("vw_review_bonus_detail", "post_date_ct"),
    # migration 027: vw_inventory_base_latest_daily (panel 78, 8. Order Assistant timeseries).
    # Latest base inventory reading per (store, submitted_date, item). Sourced from
    # inventory_closing_daily; deduplicated via ROW_NUMBER on submitted_ts DESC.
    Target("vw_inventory_base_latest_daily", "submitted_date"),
    # migration 028: vw_inventory_order_assistant (panel 79, 8. Order Assistant analytics table).
    # Per-base: current qty, usage/avg/days-left over last 7 eligible reading days.
    Target("vw_inventory_order_assistant", "reported_date"),
]

# Tables/views referenced in dashboard.json that are NOT vw_* views and are
# therefore excluded from GRAFANA_VIEWS (Grafana queries them as model tables
# directly).  The sync test uses this allowlist to avoid false failures.
KNOWN_UNCHECKED_GRAFANA_REFS: frozenset[str] = frozenset({
    "model_tip_alloc_daily",   # panel 21 — Grafana reads this table directly
    "model_tip_alloc_period",  # panel 31 — Grafana reads this table directly
    "model_forecast_daily",    # panels 72/75 — Forecast vs Actual charts query the
                               # table directly (LEFT JOIN vw_model_labor_daily) so
                               # future forecast rows appear alongside historical actuals.
})

# ── Internal helpers ──────────────────────────────────────────────────────────


def _yesterday_chicago() -> datetime.date:
    tz = zoneinfo.ZoneInfo("America/Chicago")
    return (datetime.datetime.now(tz) - datetime.timedelta(days=1)).date()


def _iso_week(d: datetime.date) -> str:
    """Return ISO week string like '2026-W20' for a given date."""
    cal = d.isocalendar()
    return f"{cal.year}-W{cal.week:02d}"


@dataclass
class CheckResult:
    layer: str        # "sheets" | "bq" | "grafana"
    target: str
    present: bool
    rows: int | None = None
    max_date: str | None = None
    note: str = ""


def _run_bq_target(t: Target, date: datetime.date, layer: str = "bq") -> CheckResult:
    """Execute a single BQ freshness query for the given target and date."""
    fq = f"`{_PROJECT}.{_DATASET}.{t.table}`"

    if t.mode == "exact":
        sql = (
            f"SELECT COUNT(*) AS c, CAST(MAX({t.date_column}) AS STRING) AS m"
            f" FROM {fq}"
            f" WHERE {t.date_column} = '{date.isoformat()}'"
        )
        note = ""
    elif t.mode == "iso_week":
        week = _iso_week(date)
        sql = (
            f"SELECT COUNT(*) AS c, MAX({t.date_column}) AS m"
            f" FROM {fq}"
            f" WHERE {t.date_column} = '{week}'"
        )
        note = f"week={week}"
    elif t.mode == "period_coverage":
        sql = (
            f"SELECT COUNT(*) AS c, CAST(MAX(period_start) AS STRING) AS m"
            f" FROM {fq}"
            f" WHERE period_start <= '{date.isoformat()}'"
            f" AND period_end >= '{date.isoformat()}'"
        )
        note = "covers date"
    else:
        raise ValueError(f"Unknown CheckMode: {t.mode!r}")

    try:
        rows = read_query(sql)
    except Exception as exc:
        return CheckResult(layer, t.table, False, None, None, note=f"ERROR: {exc}")

    if not rows:
        return CheckResult(layer, t.table, False, 0, None, note=note)

    count = int(rows[0].get("c", 0) or 0)
    max_d = str(rows[0].get("m", "") or "").strip() or None
    return CheckResult(layer, t.table, count > 0, count, max_d, note=note)


def _check_sheet_tab(
    model_sid: str,
    tab: str,
    token: str,
    date: datetime.date,
) -> CheckResult:
    """Check a model sheet tab for presence of the given date."""
    try:
        rows = _read_tab(model_sid, tab, token)
    except Exception as exc:
        return CheckResult("sheets", tab, False, None, None, note=f"ERROR: {exc}")

    if not rows:
        return CheckResult("sheets", tab, False, 0, None, note="tab empty or missing")

    if tab == "config":
        # Report data_window_end from the config tab.
        dwe: str | None = None
        for row in rows:
            if row and len(row) > 1 and str(row[0]).strip() == "data_window_end":
                dwe = coerce_iso_date(row[1])
                break
        target_iso = date.isoformat()
        present = dwe == target_iso
        return CheckResult(
            "sheets", "config.data_window_end", present,
            rows=None, max_date=dwe, note="data_window_end",
        )

    # General tab: find the header's "date" column and count matching rows.
    header = [str(c).strip() for c in rows[0]] if rows else []
    try:
        date_col_idx = header.index("date")
    except ValueError:
        return CheckResult(
            "sheets", tab, False, None, None,
            note="'date' column not in header",
        )

    target_iso = date.isoformat()
    data_rows = rows[1:]
    matching = sum(
        1
        for r in data_rows
        if len(r) > date_col_idx and coerce_iso_date(r[date_col_idx]) == target_iso
    )
    # Max date across all non-header rows (for the table display).
    all_dates = [
        coerce_iso_date(r[date_col_idx])
        for r in data_rows
        if len(r) > date_col_idx
    ]
    max_d = max((d for d in all_dates if d), default=None)
    return CheckResult("sheets", tab, matching > 0, matching, max_d)


def _check_schema_live() -> int:
    """Query INFORMATION_SCHEMA to verify all registry date columns exist in BQ.

    Catches drift that bypassed migrations (e.g. a view edited directly in BQ).
    Returns 0 on success, 1 if any column is missing.
    """
    all_targets: list[Target] = BQ_TARGETS + GRAFANA_VIEWS
    missing: list[str] = []
    errors: list[str] = []

    for t in all_targets:
        sql = (
            f"SELECT COUNT(*) AS c"
            f" FROM `{_PROJECT}.{_DATASET}.INFORMATION_SCHEMA.COLUMNS`"
            f" WHERE table_name = '{t.table}'"
            f" AND column_name = '{t.date_column}'"
        )
        try:
            rows = read_query(sql)
            count = int(rows[0]["c"]) if rows else 0
            if count == 0:
                missing.append(f"{t.table}.{t.date_column}")
        except Exception as exc:
            errors.append(f"{t.table}.{t.date_column}: {exc}")

    if errors:
        print("Schema check encountered errors (could not query INFORMATION_SCHEMA):")
        for e in errors:
            print(f"  WARN: {e}")

    if missing:
        print(
            "Schema check FAILED — these registry date columns are absent from BQ:"
        )
        for m in missing:
            print(f"  - {m}")
        print(
            "\nUpdate the Target registry in agents/bhaga/scripts/status.py"
            " to match the current schema."
        )
        return 1

    total = len(all_targets)
    print(
        f"Schema check PASSED — all {total} registry date columns exist in BQ."
    )
    return 0


def _print_table(results: list[CheckResult], *, date: datetime.date) -> None:
    """Print a compact freshness table to stdout."""
    W = {"layer": 8, "target": 34, "present": 8, "rows": 7, "max_date": 12, "note": 22}
    sep = "  "

    def _fmt(*vals: str) -> str:
        parts = []
        for val, width in zip(vals, W.values()):
            parts.append(str(val)[: width].ljust(width))
        return sep.join(parts).rstrip()

    header_line = _fmt("LAYER", "TARGET", "PRESENT?", "ROWS", "MAX-DATE", "NOTE")
    rule = "-" * len(header_line)

    print()
    print(f"  BHAGA freshness check — {date.isoformat()}")
    print(rule)
    print(header_line)
    print(rule)

    prev_layer = None
    for r in results:
        if prev_layer and r.layer != prev_layer:
            print()
        flag = "YES" if r.present else "NO "
        rows_s = str(r.rows) if r.rows is not None else "—"
        max_s = r.max_date or "—"
        print(_fmt(r.layer, r.target, flag, rows_s, max_s, r.note))
        prev_layer = r.layer

    print(rule)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(
        description="Read-only BHAGA pipeline freshness checker.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes: 0 = all present, 1 = one or more layers missing the date,\n"
            "            2 = invocation / auth error."
        ),
    )
    cli.add_argument(
        "--store", default="palmetto",
        help="Store name (default: palmetto)",
    )
    cli.add_argument(
        "--date",
        help="Date to check YYYY-MM-DD (default: yesterday in America/Chicago)",
    )
    cli.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit JSON output suitable for scripting / alerting",
    )
    cli.add_argument(
        "--check-schema", action="store_true",
        help=(
            "Query INFORMATION_SCHEMA to verify all registry date columns "
            "exist in live BQ, then exit.  Useful after schema migrations."
        ),
    )
    args = cli.parse_args(argv)

    # Resolve check date.
    if args.date:
        try:
            check_date = datetime.date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: invalid --date {args.date!r} (expected YYYY-MM-DD)")
            return 2
    else:
        check_date = _yesterday_chicago()

    if args.check_schema:
        return _check_schema_live()

    # Load store profile.
    profile_path = _STORE_PROFILE_DIR / f"{args.store}.json"
    try:
        profile = json.loads(profile_path.read_text())
    except FileNotFoundError:
        print(f"ERROR: store profile not found: {profile_path}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse store profile {profile_path}: {exc}")
        return 2

    model_sid = resolve_sheet_id("bhaga_model", profile)

    try:
        token = refresh_access_token(account=args.store)
    except Exception as exc:
        print(f"ERROR: could not obtain Sheets token: {exc}")
        print(
            "Hint: set BHAGA_SECRETS_BACKEND=gcp and "
            "BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com"
        )
        return 2

    results: list[CheckResult] = []

    # ── Layer 0: BQ data_window_end (derived from square_transactions) ────────
    # Always derived from MAX(square_transactions.date_local); store_config is
    # never consulted for this key (see core.store_config._DERIVED_KEYS and the
    # 2026-06-15 stale-row incident).
    try:
        from core.store_config import resolve_data_window_end as _resolve_dwe
        dwe_raw = (_resolve_dwe(args.store) or "").strip()
        present = dwe_raw == check_date.isoformat()
        results.append(CheckResult(
            "bq", "data_window_end", present,
            rows=None, max_date=dwe_raw or None, note="MAX(square_transactions.date_local)",
        ))
    except Exception as _exc:
        results.append(CheckResult("bq", "data_window_end", False, None, None, note=f"ERROR: {_exc}"))

    # ── Layer 1: Google Sheets ────────────────────────────────────────────────
    for tab in SHEET_TABS:
        results.append(_check_sheet_tab(model_sid, tab, token, check_date))

    # ── Layer 2: BigQuery model + raw tables ──────────────────────────────────
    for t in BQ_TARGETS:
        results.append(_run_bq_target(t, check_date, layer="bq"))

    # ── Layer 3: Grafana BI contract views ────────────────────────────────────
    for t in GRAFANA_VIEWS:
        results.append(_run_bq_target(t, check_date, layer="grafana"))

    # Verdict.
    missing = [r for r in results if not r.present]

    if args.as_json:
        payload = {
            "date": check_date.isoformat(),
            "store": args.store,
            "grafana_dashboard": GRAFANA_DASHBOARD_URL,
            "verdict": "OK" if not missing else "MISSING",
            "results": [
                {
                    "layer": r.layer,
                    "target": r.target,
                    "present": r.present,
                    "rows": r.rows,
                    "max_date": r.max_date,
                    "note": r.note,
                }
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        _print_table(results, date=check_date)
        print()
        print(f"  Grafana: {GRAFANA_DASHBOARD_URL}")
        print()
        if not missing:
            print(
                f"  VERDICT: ALL PRESENT — {check_date.isoformat()} landed in"
                " Sheets, BigQuery, and Grafana."
            )
        else:
            print(
                f"  VERDICT: MISSING — {len(missing)} target(s) lack data"
                f" for {check_date.isoformat()}:"
            )
            for r in missing:
                print(f"    - [{r.layer}] {r.target}")

    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
