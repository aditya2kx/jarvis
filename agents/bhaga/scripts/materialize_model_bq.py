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

# Per-employee model tables: any table whose merge key contains "employee".
# These must be scope-cleared before reload so a dropped employee leaves no ghost.
# Maps table -> the non-employee partition column to delete on (date or period_start).
_SCOPE_CLEAR_COL: dict[str, str] = {
    t: next(k for k in keys if k != "employee")
    for t, keys in _MERGE_KEYS.items()
    if "employee" in keys
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
    replace_scope: bool = False,
) -> int:
    """Convert build_*-style header+rows output and upsert into a BQ model table.

    This is the single BQ-write path shared by materialize(), render (M2),
    and process_reviews (M3) so coercion logic is never duplicated.

    ``header_rows`` is the raw build_* output (first element = header list,
    rest = data rows). Returns the number of rows merged (0 on dry-run or empty).

    ``replace=True`` truncates the table before loading — correct for tabs that
    are FULLY rebuilt each run. Avoid for per-employee tables where only a subset
    of partitions is rebuilt per run (would drop out-of-window rows).

    ``replace_scope=True`` (preferred for per-employee model tables) deletes only
    the partition values present in the incoming batch before the MERGE. This
    evicts ghost rows for employees who dropped out (e.g. excluded mid-period)
    without touching unrelated partitions. Use for all tables in _SCOPE_CLEAR_COL.
    The delete is idempotent: re-running with the same batch converges correctly.
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
    if replace_scope and not dry_run:
        from core.datastore import (  # noqa: PLC0415
            read_query, _PROJECT_ID, _DATASET, _assert_sandbox_write_isolation,
        )
        _assert_sandbox_write_isolation()
        col = _SCOPE_CLEAR_COL[table]
        vals = sorted({str(d[col]) for d in dicts if d.get(col) is not None})
        if vals:
            in_list = ", ".join(f"'{v}'" for v in vals)
            read_query(
                f"DELETE FROM `{_PROJECT_ID}.{_DATASET}.{table}` WHERE {col} IN ({in_list})"
            )
    return _load(table, dicts, materialized_at, dry_run)


def _evict_whole_day_exempt_tip_alloc(
    store: str,
    training_shifts: dict[tuple[str, str], dict],
    *,
    dry_run: bool,
) -> int:
    """DELETE tip_alloc_daily rows for whole-day tip exemptions (0 eligible hours).

    Whole-day marks (null exempt_start/end) correctly drop out of
    ``build_period_results``; without this eviction, raced concurrent
    materializes can leave ghost rows that break tip-pool conservation
    (alloc > pool by exactly those shares).
    """
    whole_day = [
        (name, date_iso)
        for (name, date_iso), meta in training_shifts.items()
        if not (meta.get("exempt_start") and meta.get("exempt_end"))
    ]
    if not whole_day:
        return 0
    if dry_run:
        print(f"  [DRY-RUN] would evict {len(whole_day)} whole-day tip_alloc rows")
        return len(whole_day)
    from core.datastore import read_query, _PROJECT_ID, _DATASET, _assert_sandbox_write_isolation

    _assert_sandbox_write_isolation()
    # Pairwise delete via UNNEST structs — small N (operator tip exemptions).
    pairs_sql = ", ".join(
        f"STRUCT('{name.replace(chr(39), chr(39)+chr(39))}' AS employee, DATE('{date_iso}') AS d)"
        for name, date_iso in whole_day
    )
    sql = (
        f"DELETE FROM `{_PROJECT_ID}.{_DATASET}.model_tip_alloc_daily` T "
        f"WHERE EXISTS ("
        f"  SELECT 1 FROM UNNEST([{pairs_sql}]) P "
        f"  WHERE P.employee = T.employee AND P.d = T.date"
        f")"
    )
    read_query(sql)
    print(f"  model_tip_alloc_daily: evicted {len(whole_day)} whole-day exempt ghost row(s)")
    return len(whole_day)


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

    # ── Normalize training input names through the alias map ─────────────────
    # training_shifts is {(raw_name, date_iso): meta}; training_through is {raw_name: date}.
    # Normalize so a typo'd name still matches the canonical roster entry.
    # Unresolved names are dropped with a warning — they would produce a silent no-op
    # in the allocator, so skipping them here is the safer failure mode.
    normalized_shifts: dict[tuple[str, str], dict] = {}
    for (raw_name, date_iso), meta in training_shifts.items():
        canon = aliases.get(raw_name.strip())
        if canon:
            normalized_shifts[(canon, date_iso)] = meta
        else:
            print(
                f"  [materialize] WARN: training_shifts name {raw_name!r} not in aliases — skipped"
            )
    training_shifts = normalized_shifts

    normalized_through: dict = {}
    for raw_name, excl_date in training_through.items():
        canon = aliases.get(raw_name.strip())
        if canon:
            normalized_through[canon] = excl_date
        else:
            print(
                f"  [materialize] WARN: training_excluded name {raw_name!r} not in aliases — skipped"
            )
    training_through = normalized_through

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
    load_model_rows("model_tip_alloc_period", period_rows, dry_run=dry_run, materialized_at=materialized_at, replace_scope=True)
    load_model_rows("model_tip_alloc_daily", day_alloc_rows, dry_run=dry_run, materialized_at=materialized_at, replace_scope=True)
    # Whole-day tip exemptions drop to 0 eligible hours and disappear from
    # day_alloc_rows. replace_scope clears by date then MERGEs survivors — but
    # a concurrent raced materialize can re-insert pre-exemption ghost rows for
    # those employees. Evict explicitly from training_shifts whole-day marks.
    _evict_whole_day_exempt_tip_alloc(store, training_shifts, dry_run=dry_run)
    load_model_rows("model_period_summary", summary_rows, dry_run=dry_run, materialized_at=materialized_at)

    # ── Load forecast (future window + gap-fill backfill; non-fatal) ─────────
    # Future rows (today+1..today+N): MERGE on date — always reflects the current
    # model + latest actuals; freezes naturally once the day passes into the past.
    # Backfill rows (past dates): gap-fill-only — we read existing past dates first
    # and drop any b_rows that already exist, so history is stable across model
    # updates. Only genuine gaps (e.g. first run after migration) get written.
    # Skip: BHAGA_SKIP_FORECAST=1 env var.
    if not os.environ.get("BHAGA_SKIP_FORECAST") and not dry_run:
        try:
            from agents.bhaga.scripts.forecast_bq import (
                build_backfill_rows,
                build_forecast_rows,
            )
            from agents.bhaga.scripts.backfill_bigquery import map_forecast_daily
            from core.datastore import get_client as _get_bq_client
            horizon = int(profile.get("forecast_horizon_days", 30))
            f_rows = build_forecast_rows(
                labor_daily_rows=labor_daily_rows,
                wage_rates=wage_rates,
                horizon_days=horizon,
            )
            # Leakage-free historical forecasts for vw_forecast_accuracy history.
            b_rows = build_backfill_rows(labor_daily_rows=labor_daily_rows, weeks=8)
            # Gap-fill-only: drop b_rows whose date already exists in BQ.
            if b_rows:
                _bq = _get_bq_client()
                _existing = {
                    str(r["date"])
                    for r in _bq.query(
                        "SELECT date FROM `jarvis-bhaga-prod.bhaga.model_forecast_daily`"
                        " WHERE date < CURRENT_DATE('America/Chicago')"
                    ).result()
                }
                b_rows_new = [r for r in b_rows if r["date"] not in _existing]
                skipped = len(b_rows) - len(b_rows_new)
                if skipped:
                    print(f"# [load_forecast_bq] gap-fill: skipping {skipped} "
                          f"backfill rows already in BQ (history preserved).")
                b_rows = b_rows_new
            all_rows = f_rows + b_rows
            bq_f_rows = [map_forecast_daily(r) for r in all_rows]
            load_rows("model_forecast_daily", bq_f_rows, merge_keys=["date"])
            print(f"# [load_forecast_bq] {len(f_rows)} future + {len(b_rows)} backfill "
                  f"rows → model_forecast_daily (today+1..today+{horizon}, "
                  f"plus gap-fill backfill last 8 weeks).")
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
