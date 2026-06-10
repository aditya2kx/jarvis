#!/usr/bin/env python3
"""Materialize the computed BHAGA model into BigQuery.

Reads raw data from BigQuery (via core.datastore_reader), rebuilds the seven
model tabs using the same build_* functions used by update_model_sheet.py, and
writes the results to model_* BQ tables via core.datastore.load_rows (MERGE).

Requires BHAGA_DATASTORE=bigquery (or --datastore bigquery) and Google auth
sufficient to read from BQ (ADC / service-account / BHAGA_IMPERSONATE_SA).

Usage:
    python3 -m agents.bhaga.scripts.materialize_model_bq --store palmetto
    python3 -m agents.bhaga.scripts.materialize_model_bq --store palmetto --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

# ── Ensure bigquery datastore unless overridden ────────────────────────────
if "BHAGA_DATASTORE" not in os.environ:
    os.environ["BHAGA_DATASTORE"] = "bigquery"

from core.datastore import load_rows
from core.datastore_reader import (
    read_item_daily_bq,
    read_kds_daily_bq,
    read_shifts_bq,
    read_transactions_bq,
    read_wage_rates_bq,
)
from agents.bhaga.scripts.update_model_sheet import (
    DEFAULT_SATURATION_THRESHOLD,
    actual_cc_tips_by_period,
    append_open_period,
    build_daily_rows,
    build_labor_daily_rows,
    build_labor_period_rows,
    build_labor_weekly_rows,
    build_period_results,
    build_period_summary_rows,
    build_tip_alloc_daily_rows,
    build_tip_alloc_period_rows,
    discover_periods,
    load_cc_tips_earnings_from_bq,
    _read_training_excluded_from_sheet,
    _read_training_shifts_from_sheet,
)
from skills.adp_run_automation.shift_backend import normalize_employee_name
from skills.store_profile import load_aliases, load_exclusions

_PROJECT_DIR = pathlib.Path(__file__).resolve().parents[3]
_STORE_PROFILES = _PROJECT_DIR / "agents" / "bhaga" / "knowledge-base" / "store-profiles"

_BOOL_COLS: dict[str, set[str]] = {
    "model_daily": set(),
    "model_labor_daily": {"over_saturation", "outlier_flag", "forecast_exclude"},
    "model_labor_weekly": {"is_partial", "over_saturation"},
    "model_labor_period": {"is_open", "over_saturation"},
    "model_tip_alloc_period": {"is_open"},
    "model_tip_alloc_daily": set(),
    "model_period_summary": {"is_open"},
    # Added by migration 004 (dashboard refactor)
    "model_review_bonus_period": {"is_open"},
}

_DATE_COLS: dict[str, set[str]] = {
    "model_daily": {"date"},
    "model_labor_daily": {"date"},
    "model_labor_weekly": {"week_start", "week_end"},
    "model_labor_period": {"pay_period_start", "pay_period_end"},
    "model_tip_alloc_period": {"period_start", "period_end"},
    "model_tip_alloc_daily": {"date", "period_start", "period_end"},
    "model_period_summary": {"period_start", "period_end"},
    # Added by migration 004 (dashboard refactor)
    "model_review_bonus_period": {"period_start", "period_end"},
}


_MERGE_KEYS: dict[str, list[str]] = {
    "model_daily": ["date"],
    "model_labor_daily": ["date"],
    "model_labor_weekly": ["iso_week"],
    "model_labor_period": ["pay_period_start"],
    "model_tip_alloc_period": ["period_start", "employee"],
    "model_tip_alloc_daily": ["date", "employee"],
    "model_period_summary": ["period_start"],
    # Added by migration 004 (dashboard refactor)
    "model_review_bonus_period": ["period_start", "employee"],
}


def _clean_str(v: object) -> str:
    """Strip Google Sheets text-force prefix (') and whitespace from strings."""
    s = str(v).strip()
    return s.lstrip("'")


def _coerce(table: str, row: dict, materialized_at: datetime.datetime) -> dict:
    """Coerce Sheet-style values to BQ-compatible Python types.

    Sheet quirks handled:
    - Dates prefixed with ' (text-force marker): "'2026-02-16" -> date(2026,2,16)
    - Percentages as strings: "12.34%" -> 0.1234
    - Empty string -> None
    - Bool strings: "TRUE"/"FALSE"
    """
    bool_cols = _BOOL_COLS.get(table, set())
    date_cols = _DATE_COLS.get(table, set())
    out: dict = {}
    for k, v in row.items():
        if v == "" or v is None:
            out[k] = None
        elif k in bool_cols:
            if isinstance(v, bool):
                out[k] = v
            else:
                out[k] = str(v).strip().lower() in ("true", "1", "yes")
        elif k in date_cols:
            if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
                out[k] = v
            else:
                try:
                    out[k] = datetime.date.fromisoformat(_clean_str(v))
                except (ValueError, TypeError):
                    out[k] = None
        elif isinstance(v, str):
            s = _clean_str(v)
            if s.endswith("%"):
                try:
                    out[k] = float(s[:-1]) / 100.0
                except ValueError:
                    out[k] = None
            elif s == "" or s.lower() in ("n/a", "none", "-") or s in ("—", "–", "N/A"):
                    out[k] = None
            else:
                # Try numeric coercion; fall back to string
                try:
                    as_int = int(s)
                    out[k] = as_int
                except ValueError:
                    try:
                        out[k] = float(s)
                    except ValueError:
                        out[k] = s
        else:
            out[k] = v
    out["materialized_at_utc"] = materialized_at
    return out


def _header_rows_to_dicts(rows: list) -> list[dict]:
    """Convert build_* output (first row = headers) to list[dict]."""
    if not rows or len(rows) < 2:
        return []
    headers = rows[0]
    return [dict(zip(headers, r)) for r in rows[1:]]


_SCHEMA_CACHE: dict[str, dict[str, str]] = {}

_FIELD_TYPE_MAP = {
    "INTEGER": "INT64",
    "INT64": "INT64",
    "FLOAT": "FLOAT64",
    "FLOAT64": "FLOAT64",
    "NUMERIC": "FLOAT64",
    "BOOL": "BOOL",
    "BOOLEAN": "BOOL",
    "STRING": "STRING",
    "DATE": "DATE",
    "DATETIME": "DATETIME",
    "TIMESTAMP": "TIMESTAMP",
}


def _col_type_hints(table: str) -> dict[str, str]:
    """Return a {col: bq_type} dict from the BQ table schema.

    Results are cached per table name for the process lifetime.
    """
    if table in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[table]

    from core.datastore import get_client, _PROJECT_ID, _DATASET

    hints: dict[str, str] = {}
    client = get_client()
    if client is not None:
        try:
            bq_table = client.get_table(f"{_PROJECT_ID}.{_DATASET}.{table}")
            for field in bq_table.schema:
                hints[field.name] = _FIELD_TYPE_MAP.get(field.field_type, "STRING")
        except Exception:
            pass

    # Overlay known types from the schema dicts (belt-and-suspenders)
    for col in _DATE_COLS.get(table, set()):
        hints[col] = "DATE"
    for col in _BOOL_COLS.get(table, set()):
        hints[col] = "BOOL"
    hints["materialized_at_utc"] = "TIMESTAMP"

    _SCHEMA_CACHE[table] = hints
    return hints


def _load(table: str, dicts: list[dict], materialized_at: datetime.datetime, dry_run: bool) -> int:
    coerced = [_coerce(table, d, materialized_at) for d in dicts]
    if dry_run:
        print(f"  [DRY-RUN] would load {len(coerced)} rows into {table}")
        if coerced:
            print(f"    sample keys: {list(coerced[0].keys())[:8]}...")
        return 0
    loaded = load_rows(
        table, coerced, merge_keys=_MERGE_KEYS[table], column_bq_types=_col_type_hints(table)
    )
    print(f"  {table}: {loaded} rows merged")
    return loaded


def load_model_rows(
    table: str,
    header_rows: list[list],
    *,
    dry_run: bool = False,
    materialized_at: datetime.datetime | None = None,
    replace: bool = False,
) -> int:
    """Convert build_*-style header+rows output and upsert into a BQ model table.

    This is the single BQ-write path shared by materialize(), render (M2),
    and process_reviews (M3) so coercion logic is never duplicated.

    ``header_rows`` is the raw build_* output (first element = header list,
    rest = data rows). Returns the number of rows merged (0 on dry-run or empty).

    ``replace=True`` truncates the table before loading, mirroring the Sheet's
    clear-and-write semantics for tabs that are FULLY rebuilt each run (e.g.
    review_bonus_period). Without it the MERGE-upsert leaves ghost rows whenever
    a (period_start, employee) key drops out of the rebuild — which is how a
    leaked sandbox 'Alice' row stranded itself in prod model_review_bonus_period.
    """
    if not header_rows or len(header_rows) < 2:
        return 0
    if materialized_at is None:
        materialized_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if replace and not dry_run:
        from core.datastore import (  # noqa: PLC0415
            read_query, _PROJECT_ID, _DATASET, _assert_sandbox_write_isolation,
        )
        _assert_sandbox_write_isolation()
        read_query(f"DELETE FROM `{_PROJECT_ID}.{_DATASET}.{table}` WHERE TRUE")
    dicts = _header_rows_to_dicts(header_rows)
    return _load(table, dicts, materialized_at, dry_run)


def _assert_conservation(period_results: list[dict]) -> None:
    """Verify tip-pool conservation for every period: allocated == pool.

    The allocator distributes the full tip pool across employees — the sum of
    per-employee allocations must equal the total pool within $0.01 rounding
    tolerance (1 cent). Raises RuntimeError with the offending period + delta
    on violation so the pipeline fails loudly rather than silently writing
    incorrect BQ rows.
    """
    for p in period_results:
        if p.get("is_open"):
            # Open periods are in-progress; conservation holds only for closed ones.
            continue
        pool_cents = sum(a["share_cents"] for a in p["per_day_allocations"])
        our_total_cents = sum(p["per_period_ours"].values())
        delta_cents = abs(pool_cents - our_total_cents)
        if delta_cents > 1:
            raise RuntimeError(
                f"materialize_model_bq: tip-pool conservation violated for period "
                f"{p['start']} – {p['end']}: pool=${pool_cents/100:.2f}, "
                f"allocated=${our_total_cents/100:.2f}, delta=${delta_cents/100:.2f} "
                f"(max allowed $0.01). Investigate build_period_results / allocate()."
            )


def materialize(store: str, *, dry_run: bool = False) -> None:
    """Build all model tabs from BQ raw data and write to model_* tables."""
    import json

    profile_path = _STORE_PROFILES / f"{store}.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"Store profile not found: {profile_path}")
    profile = json.loads(profile_path.read_text())

    print(f"# materialize_model_bq [{store}] dry_run={dry_run}")

    # ── Load raw data from BQ ────────────────────────────────────────────────
    print("# Loading raw data from BigQuery...")
    aliases = load_aliases(store)
    excluded = set(load_exclusions(store)["permanent"])
    shifts = read_shifts_bq()
    txns = read_transactions_bq()
    wage_rates = read_wage_rates_bq()
    # Item + KDS daily rollups feed the per-item operations metrics on
    # labor_daily/weekly/period (items_sold, avg_item_price, KDS percentiles).
    # Without these, those columns materialize as NULL (the historical gap that
    # left model_labor_daily.items_sold empty for every row).
    items_by_date = {r["date_local"]: r for r in read_item_daily_bq()}
    kds_by_date = {r["date_local"]: r for r in read_kds_daily_bq()}
    print(f"  shifts={len(shifts)} txns={len(txns)} wage_rates={len(wage_rates)} "
          f"item_days={len(items_by_date)} kds_days={len(kds_by_date)}")

    # Defensive breadcrumb: the model is derived from Square transactions, so an
    # empty `txns` means there is nothing to materialize. Without this guard the
    # next line (`max(... for t in txns ...)`) raises a cryptic
    # "max() iterable argument is empty" that hides the real cause. A genuinely
    # empty BQ raw table is itself a signal worth surfacing loudly (the BQ mirror
    # never got populated — historically the orchestrator SA lacked
    # bigquery.dataEditor; see RUNBOOK §14). Access errors are re-raised upstream
    # in core.datastore.read_query, so reaching here with empty txns means the
    # query succeeded but returned no rows.
    if not txns:
        raise RuntimeError(
            "materialize_model_bq: BigQuery raw `square_transactions` is empty — "
            "nothing to materialize. Run backfill_bigquery first (RUNBOOK §14) and "
            "confirm the orchestrator SA has roles/bigquery.jobUser + "
            "roles/bigquery.dataEditor."
        )

    # Normalize employee names using store aliases
    for rec in shifts + wage_rates:
        for k in ("employee_name", "employee_id", "canonical_name"):
            if k in rec:
                rec[k] = normalize_employee_name(rec[k], aliases)

    # Deduplicate on primary keys (idempotent: keep last scraped)
    seen: set = set()
    deduped_shifts: list[dict] = []
    for r in shifts:
        key = (r.get("date"), r.get("employee_id"))
        if key not in seen:
            seen.add(key)
            deduped_shifts.append(r)
    shifts = deduped_shifts

    seen = set()
    deduped_rates: list[dict] = []
    for r in wage_rates:
        key = r.get("employee_id", "")
        if key not in seen:
            seen.add(key)
            deduped_rates.append(r)
    wage_rates = deduped_rates

    # ── Discover periods + load earnings actuals ─────────────────────────────
    last_data_date = max(t["date_local"] for t in txns if t.get("date_local"))
    sat_thresh = float(
        profile.get("labor_config", {}).get(
            "saturation_orders_per_labor_hour", DEFAULT_SATURATION_THRESHOLD
        )
    )
    # Flat staffing-solver target prep time per item; doubles as the goal for
    # kds_pct_items_over_goal. Mirrors update_model_sheet's profile fallback so
    # the BQ model matches the Sheet when the operator hasn't overridden it.
    kds_goal_sec = float(
        profile.get("labor_config", {}).get(
            "forecast_target_completion_time_per_item_sec", 420.0
        )
    )
    periods = discover_periods(
        anchor_end_date=profile["adp_run"]["pay_periods_anchor_end_date"],
        pay_frequency=profile["adp_run"].get("pay_frequency", ""),
        data_start=profile["calibration"]["first_data_window"]["start"],
        last_data_date=last_data_date,
    )
    periods = append_open_period(periods, last_data_date=last_data_date)

    earnings = load_cc_tips_earnings_from_bq(
        store=store,
        data_window_start=periods[0]["start"],
        last_data_date=last_data_date,
    )
    actuals = actual_cc_tips_by_period(earnings)
    print(f"  earnings={len(earnings)} actuals_periods={len(actuals)} last_data_date={last_data_date}")

    # ── Load training exclusions from the model Sheet ────────────────────────
    model_sid = profile["google_sheets"]["bhaga_model"]["spreadsheet_id"]
    training_through = _read_training_excluded_from_sheet(
        spreadsheet_id=model_sid, store=store
    )
    training_shifts = _read_training_shifts_from_sheet(
        spreadsheet_id=model_sid, store=store
    )

    # ── Build all model tabs (same logic as update_model_sheet) ──────────────
    print("# Building model...")
    daily_rows, daily_summary = build_daily_rows(
        txns=txns,
        shifts=shifts,
        excluded=excluded,
        training_through=training_through,
        training_shifts=training_shifts,
    )
    labor_daily_rows = build_labor_daily_rows(
        txns=txns,
        shifts=shifts,
        wage_rates=wage_rates,
        excluded_from_tip_pool=excluded,
        saturation_threshold=sat_thresh,
        items_by_date=items_by_date,
        kds_by_date=kds_by_date,
        kds_goal_sec=kds_goal_sec,
    )
    labor_period_rows = build_labor_period_rows(
        periods=periods,
        labor_daily_rows=labor_daily_rows,
        saturation_threshold=sat_thresh,
        kds_by_date=kds_by_date,
        kds_goal_sec=kds_goal_sec,
    )
    labor_weekly_rows = build_labor_weekly_rows(
        labor_daily_rows=labor_daily_rows,
        saturation_threshold=sat_thresh,
        kds_by_date=kds_by_date,
        kds_goal_sec=kds_goal_sec,
    )
    period_results = build_period_results(
        periods=periods,
        shifts=shifts,
        txns=txns,
        actuals=actuals,
        excluded=excluded,
        square_data_start=min(t["date_local"] for t in txns),
        training_through=training_through,
        training_shifts=training_shifts,
    )
    period_rows = build_tip_alloc_period_rows(period_results)
    day_alloc_rows = build_tip_alloc_daily_rows(period_results, daily_summary)
    summary_rows = build_period_summary_rows(period_results)

    # ── Post-build conservation check (fail loudly on any tip-pool drift) ────
    _assert_conservation(period_results)

    materialized_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # ── Write to BQ model tables via the shared loader ───────────────────────
    print("# Writing to BigQuery...")
    load_model_rows("model_daily", daily_rows, dry_run=dry_run, materialized_at=materialized_at)
    load_model_rows("model_labor_daily", labor_daily_rows, dry_run=dry_run, materialized_at=materialized_at)
    load_model_rows("model_labor_weekly", labor_weekly_rows, dry_run=dry_run, materialized_at=materialized_at)
    load_model_rows("model_labor_period", labor_period_rows, dry_run=dry_run, materialized_at=materialized_at)
    load_model_rows("model_tip_alloc_period", period_rows, dry_run=dry_run, materialized_at=materialized_at)
    load_model_rows("model_tip_alloc_daily", day_alloc_rows, dry_run=dry_run, materialized_at=materialized_at)
    load_model_rows("model_period_summary", summary_rows, dry_run=dry_run, materialized_at=materialized_at)

    # ── Load forecast (future window only; non-fatal) ─────────────────────────
    # Writes today+1..today+N rows to model_forecast_daily (merge_keys=["date"]).
    # Past dates are never in the row set → they freeze at their last 1-day-ahead
    # value, giving an implicit forecast-vs-actual accuracy series.
    # Skip: BHAGA_SKIP_FORECAST=1 env var.
    if not os.environ.get("BHAGA_SKIP_FORECAST") and not dry_run:
        try:
            from agents.bhaga.scripts.forecast_bq import (
                build_backfill_rows,
                build_forecast_rows,
            )
            from agents.bhaga.scripts.backfill_bigquery import map_forecast_daily
            horizon = int(profile.get("forecast_horizon_days", 30))
            f_rows = build_forecast_rows(
                labor_daily_rows=labor_daily_rows,
                wage_rates=wage_rates,
                horizon_days=horizon,
            )
            # Leakage-free historical forecasts so vw_forecast_accuracy has
            # forecast-vs-actual history immediately (and stays self-healing).
            b_rows = build_backfill_rows(labor_daily_rows=labor_daily_rows, weeks=8)
            all_rows = f_rows + b_rows
            bq_f_rows = [map_forecast_daily(r) for r in all_rows]
            load_rows("model_forecast_daily", bq_f_rows, merge_keys=["date"])
            print(f"# [load_forecast_bq] {len(f_rows)} future + {len(b_rows)} backfill "
                  f"rows → model_forecast_daily (today+1..today+{horizon}, "
                  f"plus last 8 weeks for accuracy).")
        except Exception as _exc:  # noqa: BLE001
            print(f"# [load_forecast_bq] WARNING: non-fatal failure: {_exc}")

    print("# Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize BHAGA model into BigQuery.")
    parser.add_argument("--store", required=True, help="Store name (e.g. palmetto)")
    parser.add_argument("--dry-run", action="store_true", help="Print row counts without writing")
    args = parser.parse_args()
    materialize(args.store, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
