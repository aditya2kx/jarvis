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
from core.datastore_reader import read_shifts_bq, read_transactions_bq, read_wage_rates_bq
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
    load_cc_tips_earnings_from_gcs,
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
}

_DATE_COLS: dict[str, set[str]] = {
    "model_daily": {"date"},
    "model_labor_daily": {"date"},
    "model_labor_weekly": {"week_start", "week_end"},
    "model_labor_period": {"pay_period_start", "pay_period_end"},
    "model_tip_alloc_period": {"period_start", "period_end"},
    "model_tip_alloc_daily": {"date", "period_start", "period_end"},
    "model_period_summary": {"period_start", "period_end"},
}


_MERGE_KEYS: dict[str, list[str]] = {
    "model_daily": ["date"],
    "model_labor_daily": ["date"],
    "model_labor_weekly": ["iso_week"],
    "model_labor_period": ["pay_period_start"],
    "model_tip_alloc_period": ["period_start", "employee"],
    "model_tip_alloc_daily": ["date", "employee"],
    "model_period_summary": ["period_start"],
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
    print(f"  shifts={len(shifts)} txns={len(txns)} wage_rates={len(wage_rates)}")

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
    periods = discover_periods(
        anchor_end_date=profile["adp_run"]["pay_periods_anchor_end_date"],
        pay_frequency=profile["adp_run"].get("pay_frequency", ""),
        data_start=profile["calibration"]["first_data_window"]["start"],
        last_data_date=last_data_date,
    )
    periods = append_open_period(periods, last_data_date=last_data_date)

    earnings = load_cc_tips_earnings_from_gcs(
        store=store,
        aliases=aliases,
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
    )
    labor_period_rows = build_labor_period_rows(
        periods=periods,
        labor_daily_rows=labor_daily_rows,
        saturation_threshold=sat_thresh,
    )
    labor_weekly_rows = build_labor_weekly_rows(
        labor_daily_rows=labor_daily_rows,
        saturation_threshold=sat_thresh,
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

    materialized_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # ── Write to BQ model tables ─────────────────────────────────────────────
    print("# Writing to BigQuery...")
    _load("model_daily", _header_rows_to_dicts(daily_rows), materialized_at, dry_run)
    _load("model_labor_daily", _header_rows_to_dicts(labor_daily_rows), materialized_at, dry_run)
    _load("model_labor_weekly", _header_rows_to_dicts(labor_weekly_rows), materialized_at, dry_run)
    _load("model_labor_period", _header_rows_to_dicts(labor_period_rows), materialized_at, dry_run)
    _load("model_tip_alloc_period", _header_rows_to_dicts(period_rows), materialized_at, dry_run)
    _load("model_tip_alloc_daily", _header_rows_to_dicts(day_alloc_rows), materialized_at, dry_run)
    _load("model_period_summary", _header_rows_to_dicts(summary_rows), materialized_at, dry_run)
    print("# Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize BHAGA model into BigQuery.")
    parser.add_argument("--store", required=True, help="Store name (e.g. palmetto)")
    parser.add_argument("--dry-run", action="store_true", help="Print row counts without writing")
    args = parser.parse_args()
    materialize(args.store, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
