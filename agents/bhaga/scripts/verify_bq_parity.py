#!/usr/bin/env python3
"""Layer 2 verification: compare model outputs from Sheets vs BigQuery data sources.

Reads the staging model sheet (already populated from Sheets data), then rebuilds
the model using BigQuery data and compares cell-by-cell. Reports mismatches.

The goal: prove that reading from BigQuery produces identical model outputs to
reading from Sheets, which means the cutover is safe.

Usage:
    # Requires BHAGA_DATASTORE=bigquery to be set for BQ client
    BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.verify_bq_parity --store palmetto
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.daily_refresh import is_refresh_date_complete
from agents.bhaga.scripts.update_model_sheet import (
    build_daily_rows,
    build_labor_daily_rows,
    build_labor_period_rows,
    build_labor_weekly_rows,
    build_period_results,
    build_period_summary_rows,
    build_tip_alloc_daily_rows,
    build_tip_alloc_period_rows,
    append_open_period,
    discover_periods,
    actual_cc_tips_by_period,
    load_cc_tips_earnings_from_bq,
    _read_training_excluded_from_sheet,
    _read_training_shifts_from_sheet,
    DEFAULT_SATURATION_THRESHOLD,
)
from core.config_loader import project_dir, refresh_access_token, resolve_sheet_id
from core.datastore_reader import read_shifts_bq, read_transactions_bq, read_wage_rates_bq
from skills.adp_run_automation.shift_backend import normalize_employee_name
from skills.bhaga_config.dates import coerce_iso_date

PROJECT = pathlib.Path(project_dir())
STORE_PROFILE_DIR = PROJECT / "agents" / "bhaga" / "knowledge-base" / "store-profiles"
SHEETS_API = "https://sheets.googleapis.com/v4"


def _read_sheet_tab(spreadsheet_id: str, tab_name: str, token: str) -> list[list]:
    """Read all values from a sheet tab."""
    rng = urllib.parse.quote(f"{tab_name}!A1:ZZ10000", safe="!:")
    url = f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{rng}"
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data.get("values", [])
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return []
        raise


def _normalize_cell(val) -> str:
    """Normalize a cell value for comparison (handles date prefixes, float precision)."""
    if val is None:
        return ""
    s = str(val).strip()
    # Strip apostrophe prefix used for text-literal dates
    if s.startswith("'"):
        s = s[1:]
    # Normalize float precision
    try:
        f = float(s)
        if f == int(f) and "." not in s and "%" not in s:
            return str(int(f))
        if "%" in s:
            return s
        return f"{f:.6f}"
    except (ValueError, TypeError):
        pass
    return s


def _cells_match(sheet_val, computed_val) -> bool:
    """Compare a sheet cell value against a computed value, handling format differences.

    Known differences:
    - Sheets stores "7.16%" as 0.0716 (decimal) when the cell has percent format
    - Sheets coerces dates to serial numbers (e.g., 46164 = 2026-05-22)
    - Percentage strings: "+46.8%" vs "0.47"
    """
    s = str(sheet_val).strip() if sheet_val is not None else ""
    c = str(computed_val).strip() if computed_val is not None else ""

    if s.startswith("'"):
        s = s[1:]
    if c.startswith("'"):
        c = c[1:]

    if s == c:
        return True

    # Case 1: Computed is a percentage string ("7.16%", "+46.8%", "-10.5%")
    # and sheet is the decimal equivalent (0.0716, 0.468, -0.105)
    if "%" in c and "%" not in s:
        try:
            pct_str = c.replace("%", "").replace("+", "")
            pct_val = float(pct_str) / 100.0
            sheet_float = float(s)
            if abs(pct_val - sheet_float) < 0.005:
                return True
        except (ValueError, TypeError):
            pass

    # Case 2: Both are numbers — compare with tolerance
    try:
        sf = float(s.replace("%", "").replace("$", "").replace(",", "").replace("+", ""))
        cf = float(c.replace("%", "").replace("$", "").replace(",", "").replace("+", ""))
        # If both had %, compare directly
        if "%" in s and "%" in c:
            return abs(sf - cf) < 0.15
        # If neither had %, compare with small tolerance
        if "%" not in s and "%" not in c:
            return abs(sf - cf) < 0.015
    except (ValueError, TypeError):
        pass

    # Case 3: Date serial vs ISO date
    # Excel date serial: days since 1899-12-30
    try:
        serial = int(float(s))
        if 40000 < serial < 60000:
            import datetime
            base = datetime.date(1899, 12, 30)
            date_from_serial = (base + datetime.timedelta(days=serial)).isoformat()
            c_date = coerce_iso_date(c) or c
            if date_from_serial == c_date:
                return True
    except (ValueError, TypeError):
        pass

    return False


def _compare_tabs(
    tab_name: str,
    sheet_rows: list[list],
    computed_rows: list[list],
    *,
    skip_columns: set[str] | None = None,
) -> dict:
    """Compare sheet tab values against computed model output.

    Returns dict with match stats and first N mismatches.
    """
    skip_columns = skip_columns or set()
    mismatches: list[dict] = []
    total_cells = 0
    matched_cells = 0

    if not sheet_rows:
        return {"tab": tab_name, "status": "EMPTY_SHEET", "mismatches": []}
    if not computed_rows:
        return {"tab": tab_name, "status": "EMPTY_COMPUTED", "mismatches": []}

    # Align headers
    sheet_header = sheet_rows[0] if sheet_rows else []
    computed_header = computed_rows[0] if computed_rows else []

    # Compare data rows (skip header)
    sheet_data = sheet_rows[1:]
    computed_data = computed_rows[1:]

    row_count = min(len(sheet_data), len(computed_data))
    if len(sheet_data) != len(computed_data):
        mismatches.append({
            "type": "row_count",
            "sheet_rows": len(sheet_data),
            "computed_rows": len(computed_data),
        })

    for row_idx in range(row_count):
        s_row = sheet_data[row_idx]
        c_row = computed_data[row_idx]
        col_count = min(len(s_row), len(c_row), len(computed_header))

        for col_idx in range(col_count):
            col_name = computed_header[col_idx] if col_idx < len(computed_header) else f"col_{col_idx}"
            if col_name in skip_columns:
                continue

            total_cells += 1
            s_val = s_row[col_idx] if col_idx < len(s_row) else ""
            c_val = c_row[col_idx]

            if _cells_match(s_val, c_val):
                matched_cells += 1
            else:
                if len(mismatches) < 20:
                    mismatches.append({
                        "type": "cell",
                        "row": row_idx + 2,
                        "col": col_name,
                        "sheet": str(s_val).strip() if s_val else "",
                        "computed": str(c_val).strip() if c_val else "",
                    })

    pct = (matched_cells / total_cells * 100) if total_cells > 0 else 0
    status = "PASS" if not mismatches else "MISMATCH"
    return {
        "tab": tab_name,
        "status": status,
        "total_cells": total_cells,
        "matched_cells": matched_cells,
        "match_pct": f"{pct:.2f}%",
        "mismatches": mismatches,
    }


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--verbose", "-v", action="store_true")
    args = cli.parse_args()

    if os.environ.get("BHAGA_DATASTORE", "").lower() != "bigquery":
        print("ERROR: Set BHAGA_DATASTORE=bigquery to enable BQ reads.")
        print("  export BHAGA_DATASTORE=bigquery")
        return 1

    profile = json.loads(
        (STORE_PROFILE_DIR / f"{args.store}.json").read_text()
    )
    from skills.store_profile import load_aliases, load_exclusions
    aliases = load_aliases(args.store)
    excluded = set(load_exclusions(args.store)["permanent"])

    model_sid = resolve_sheet_id("bhaga_model", profile)
    token = refresh_access_token(account=args.store)

    # ── Step 1: Read existing staging model tabs ──
    print("=" * 60)
    print("LAYER 2 VERIFICATION: BigQuery vs Sheets model parity")
    print("=" * 60)
    print(f"\n# Reading staging model sheet {model_sid}...")

    tabs_to_verify = [
        "daily", "labor_daily", "labor_weekly", "labor_period",
        "tip_alloc_period", "tip_alloc_daily", "period_summary",
    ]
    sheet_data: dict[str, list[list]] = {}
    for tab in tabs_to_verify:
        rows = _read_sheet_tab(model_sid, tab, token)
        sheet_data[tab] = rows
        print(f"    {tab:<22} {len(rows) - 1 if rows else 0:>5} rows from sheet")

    # ── Step 2: Build model from BigQuery data ──
    print(f"\n# Loading raw data from BigQuery...")
    shifts = read_shifts_bq()
    print(f"    shifts:         {len(shifts)} rows")
    wage_rates = read_wage_rates_bq()
    print(f"    wage_rates:     {len(wage_rates)} rows")
    txns = read_transactions_bq()
    print(f"    transactions:   {len(txns)} rows")

    # Apply alias resolution (same as update_model_sheet.py)
    for rec in shifts:
        for key in ("employee_name", "employee_id"):
            if key in rec:
                rec[key] = normalize_employee_name(rec[key], aliases)
    for rec in wage_rates:
        for key in ("employee_name", "employee_id"):
            if key in rec:
                rec[key] = normalize_employee_name(rec[key], aliases)

    # Dedup shifts
    _seen_shifts: set[tuple] = set()
    _deduped_shifts: list[dict] = []
    for rec in shifts:
        key = (rec.get("date"), rec.get("employee_id"))
        if key in _seen_shifts:
            continue
        _seen_shifts.add(key)
        _deduped_shifts.append(rec)
    shifts = _deduped_shifts

    # Dedup rates
    _seen_rates: set[str] = set()
    _deduped_rates: list[dict] = []
    for rec in wage_rates:
        key = rec.get("employee_id", "")
        if key in _seen_rates:
            continue
        _seen_rates.add(key)
        _deduped_rates.append(rec)
    wage_rates = _deduped_rates

    # Training exclusions (bulk through-date + per-shift overlay) — read both so
    # the BigQuery parity recompute applies the SAME exclusions as the prod build.
    training_through = _read_training_excluded_from_sheet(
        spreadsheet_id=model_sid, store=args.store,
    )
    training_shifts = _read_training_shifts_from_sheet(
        spreadsheet_id=model_sid, store=args.store,
    )

    # Compute last_data_date
    square_dates_covered = {t["date_local"] for t in txns}
    both_covered_complete = {
        d for d in square_dates_covered
        if is_refresh_date_complete(datetime.date.fromisoformat(d))
    }
    if not both_covered_complete:
        print("ERROR: no complete dates in BigQuery transactions")
        return 1
    last_data_date = max(both_covered_complete)
    print(f"    last_data_date: {last_data_date}")

    # Load earnings from BQ (single source of truth; needs last_data_date for log)
    earnings = load_cc_tips_earnings_from_bq(
        store=args.store,
        data_window_start=profile["calibration"]["first_data_window"]["start"],
        last_data_date=last_data_date,
    )
    print(f"    earnings:       {len(earnings)} rows (from BQ)")

    # Discover periods algorithmically from the store profile (biweekly
    # anchor), not from earnings rows — see update_model_sheet.discover_periods.
    periods = discover_periods(
        anchor_end_date=profile["adp_run"]["pay_periods_anchor_end_date"],
        pay_frequency=profile["adp_run"].get("pay_frequency", ""),
        data_start=profile["calibration"]["first_data_window"]["start"],
        last_data_date=last_data_date,
    )
    periods = append_open_period(periods, last_data_date=last_data_date)
    actuals = actual_cc_tips_by_period(earnings)
    square_data_start = min(t["date_local"] for t in txns)

    # Saturation threshold
    saturation_threshold = float(
        profile.get("labor_config", {}).get(
            "saturation_orders_per_labor_hour", DEFAULT_SATURATION_THRESHOLD
        )
    )

    # ── Step 3: Build all model tabs from BigQuery data ──
    print(f"\n# Building model from BigQuery data...")

    daily_rows, daily_summary = build_daily_rows(
        txns=txns, shifts=shifts, excluded=excluded, training_through=training_through,
        training_shifts=training_shifts,
    )
    labor_daily_rows = build_labor_daily_rows(
        txns=txns, shifts=shifts, wage_rates=wage_rates,
        excluded_from_tip_pool=excluded,
        saturation_threshold=saturation_threshold,
    )
    labor_period_rows = build_labor_period_rows(
        periods=periods, labor_daily_rows=labor_daily_rows,
        saturation_threshold=saturation_threshold,
    )
    labor_weekly_rows = build_labor_weekly_rows(
        labor_daily_rows=labor_daily_rows,
        saturation_threshold=saturation_threshold,
    )

    period_results = build_period_results(
        periods=periods, shifts=shifts, txns=txns,
        actuals=actuals, excluded=excluded,
        square_data_start=square_data_start,
        training_through=training_through,
        training_shifts=training_shifts,
    )
    period_rows = build_tip_alloc_period_rows(period_results)
    day_alloc_rows = build_tip_alloc_daily_rows(period_results, daily_summary)
    summary_rows = build_period_summary_rows(period_results)

    computed_data = {
        "daily": daily_rows,
        "labor_daily": labor_daily_rows,
        "labor_weekly": labor_weekly_rows,
        "labor_period": labor_period_rows,
        "tip_alloc_period": period_rows,
        "tip_alloc_daily": day_alloc_rows,
        "period_summary": summary_rows,
    }

    for tab, rows in computed_data.items():
        print(f"    {tab:<22} {len(rows) - 1:>5} rows computed from BQ")

    # ── Step 4: Compare ──
    print(f"\n# Comparing model outputs...")
    print("-" * 60)

    # Skip timestamp/refresh columns that will always differ
    skip_cols = {"last_refreshed_ct", "scraped_at_utc"}
    results: list[dict] = []

    for tab in tabs_to_verify:
        result = _compare_tabs(
            tab,
            sheet_data.get(tab, []),
            computed_data.get(tab, []),
            skip_columns=skip_cols,
        )
        results.append(result)
        status_icon = "PASS" if result["status"] == "PASS" else "FAIL"
        print(f"    [{status_icon}] {tab:<22} "
              f"{result.get('matched_cells', 0)}/{result.get('total_cells', 0)} cells "
              f"({result.get('match_pct', 'n/a')})")

        if args.verbose and result["mismatches"]:
            for m in result["mismatches"][:5]:
                if m["type"] == "row_count":
                    print(f"           row count: sheet={m['sheet_rows']}, computed={m['computed_rows']}")
                else:
                    print(f"           row {m['row']}, col '{m['col']}': "
                          f"sheet={m['sheet']!r} vs computed={m['computed']!r}")

    # ── Summary ──
    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r["status"] == "PASS")
    total = len(results)
    print(f"RESULT: {passed}/{total} tabs match perfectly")

    if passed < total:
        print("\nMismatch details:")
        for r in results:
            if r["status"] != "PASS":
                print(f"\n  {r['tab']}:")
                for m in r["mismatches"][:10]:
                    if m["type"] == "row_count":
                        print(f"    - Row count: sheet={m['sheet_rows']}, BQ={m['computed_rows']}")
                    else:
                        print(f"    - Row {m['row']}, '{m['col']}': sheet={m['sheet']!r} vs BQ={m['computed']!r}")
        return 1

    print("\nLayer 2 PASSED — BigQuery data produces identical model outputs.")
    print("Safe to cut over to --data-source=bigquery.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
