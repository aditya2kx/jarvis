#!/usr/bin/env python3
"""BHAGA Model sheet — populate all 5 tabs for verification.

Writes the BHAGA Model workbook with everything the person who did the manual
tip calc needs to compare:

    config              — what assumptions drove the numbers
    daily               — per-day store inputs (sales, pool, hours)
    tip_alloc_daily     — per-employee per-day breakdown (full drilldown evidence)
    tip_alloc_period    — THE VERIFICATION VIEW: ours vs ADP-paid + reason
    period_summary      — period-level totals (sanity check)

The tip_alloc_period tab adds the requested columns:
    period_start | period_end | employee | hours | our_calc | adp_paid |
        diff | diff_pct | likely_reason | per_hour_ours | per_hour_adp

Heuristics for `likely_reason`:
    - |diff| < $1                                       -> "OK (rounding)"
    - ADP = $0 AND we > $0                              -> "ADP missed payout — back-pay candidate"
    - ADP > us by >$5 AND >15%                          -> "ADP paid more — possible extra day/double entry"
    - ADP < us by >$5 AND >15%                          -> "ADP paid less — possible missed shift"
    - ADP > us by >$5 AND <=15%                         -> "Manual calc drift (high)"
    - other small diffs                                 -> "Manual calc drift (small)"
    - open period (no ADP payout yet)                   -> "Open period — not yet paid"

Idempotent: clears and re-writes every tab on every run.

Usage:
    python3 -m agents.bhaga.scripts.update_model_sheet --store palmetto
    python3 -m agents.bhaga.scripts.update_model_sheet --store palmetto --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core.config_loader import project_dir, refresh_access_token
from skills.adp_run_automation import compensation_backend
from skills.square_tips import transactions_backend
from skills.tip_ledger_writer import (
    read_raw_adp_shifts,
    read_raw_square_transactions,
)
from skills.tip_pool_allocation.adapter import allocate


PROJECT = pathlib.Path(project_dir())
DOWNLOADS = PROJECT / "extracted" / "downloads"
STORE_PROFILE_DIR = PROJECT / "agents" / "bhaga" / "knowledge-base" / "store-profiles"
SHEETS_API = "https://sheets.googleapis.com/v4"


# ---------- Sheets HTTP helpers ----------


def _api(url: str, token: str, *, method: str = "GET", data: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}\n{err}") from None


def get_spreadsheet_meta(spreadsheet_id: str, token: str) -> dict:
    return _api(f"{SHEETS_API}/spreadsheets/{spreadsheet_id}", token)


def add_sheet_if_missing(
    spreadsheet_id: str,
    token: str,
    *,
    tab_name: str,
    column_count: int,
    frozen_rows: int = 1,
) -> int:
    """Add the sheet if not present. Return its sheetId."""
    meta = get_spreadsheet_meta(spreadsheet_id, token)
    for s in meta["sheets"]:
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    body = {
        "requests": [{
            "addSheet": {
                "properties": {
                    "title": tab_name,
                    "gridProperties": {
                        "frozenRowCount": frozen_rows,
                        "columnCount": column_count,
                    },
                }
            }
        }]
    }
    resp = _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}:batchUpdate",
        token, method="POST", data=body,
    )
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def clear_and_write_tab(
    spreadsheet_id: str,
    token: str,
    *,
    tab_name: str,
    values: list[list],
) -> None:
    """Clear the tab and write the supplied 2D matrix starting at A1."""
    _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/"
        f"{urllib.parse.quote(tab_name)}!A1:ZZ10000:clear",
        token, method="POST", data={},
    )
    if not values:
        return
    _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/"
        f"{urllib.parse.quote(tab_name)}!A1?valueInputOption=USER_ENTERED",
        token, method="PUT",
        data={"values": values},
    )


def format_currency_columns(
    spreadsheet_id: str,
    token: str,
    *,
    sheet_id: int,
    column_indices: list[int],
    start_row: int = 1,
) -> None:
    """Apply USD currency format to specified columns (0-indexed) from start_row down."""
    if not column_indices:
        return
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "startColumnIndex": col,
                    "endColumnIndex": col + 1,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "\"$\"#,##0.00"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        }
        for col in column_indices
    ]
    _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}:batchUpdate",
        token, method="POST", data={"requests": requests},
    )


def bold_header_row(spreadsheet_id: str, token: str, *, sheet_id: int) -> None:
    _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}:batchUpdate",
        token, method="POST",
        data={"requests": [{
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        }]},
    )


def auto_resize_columns(spreadsheet_id: str, token: str, *, sheet_id: int, num_cols: int) -> None:
    _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}:batchUpdate",
        token, method="POST",
        data={"requests": [{
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id, "dimension": "COLUMNS",
                    "startIndex": 0, "endIndex": num_cols,
                }
            }
        }]},
    )


# ---------- Data loading & period discovery ----------


def _newest(pattern: str) -> pathlib.Path | None:
    paths = [pathlib.Path(p) for p in glob.glob(str(DOWNLOADS / pattern))]
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None


# ---------- Training exclusions (sheet-driven, sheet-survived) ----------

TRAINING_EXCLUDED_PREFIX = "training_excluded:"


def _read_config_value(
    *, spreadsheet_id: str, store: str, key: str
) -> Optional[str]:
    """Read a single value from the model config tab (returns None if absent).

    Used to preserve user-edited bonus tunables across refreshes —
    build_config_rows() echoes back whatever the operator set in-sheet.
    """
    token = refresh_access_token(store)
    rng = urllib.parse.quote("config!A1:B200", safe="!:")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception:  # noqa: BLE001
        return None
    for row in data.get("values", []):
        if row and row[0] == key and len(row) > 1 and str(row[1]).strip():
            return str(row[1]).strip()
    return None


def _read_training_excluded_from_sheet(
    *, spreadsheet_id: str, store: str
) -> dict[str, datetime.date]:
    """Read any `training_excluded:<canonical_name>` rows from the config tab.

    Each value is the LAST date (inclusive) of the employee's training period.
    Shifts on or before that date receive no tip-pool share AND don't count
    toward the per-hour rate denominator (redistribute model). Shifts after
    that date are treated as normal tipped shifts.

    Returns {canonical_name: last_training_date}. Empty dict if sheet/tab/key
    missing or the sheet is unreachable (so we degrade gracefully).
    """
    token = refresh_access_token(store)
    rng = urllib.parse.quote("config!A1:C200", safe="!:")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    out: dict[str, datetime.date] = {}
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  [training-read] could not read config tab: {exc}")
        return out
    for row in data.get("values", []):
        if not row or not row[0].startswith(TRAINING_EXCLUDED_PREFIX):
            continue
        name = row[0][len(TRAINING_EXCLUDED_PREFIX):].strip()
        if len(row) < 2 or not row[1].strip():
            continue
        try:
            out[name] = datetime.date.fromisoformat(row[1].strip())
        except ValueError:
            print(f"  [training-read] unparseable date for {name!r}: {row[1]!r}")
    return out


def _is_excluded(
    employee_name: str,
    date_iso: str,
    *,
    permanent: set[str],
    training_through: dict[str, datetime.date],
) -> bool:
    """Single-source exclusion check used by every tab builder.

    - Permanent: always excluded (manager, etc.)
    - Training: excluded on or before their `last_training_date`.
    """
    if employee_name in permanent:
        return True
    last = training_through.get(employee_name)
    if last is not None and date_iso <= last.isoformat():
        return True
    return False


def discover_periods(earnings: list[dict]) -> list[dict]:
    """Group earnings rows into canonical pay periods (merge off-by-1-day variants)."""
    buckets: dict[tuple, dict] = {}
    for r in earnings:
        ps, pe = r.get("period_start"), r.get("period_end")
        if not ps or not pe:
            continue
        b = buckets.setdefault((ps, pe), {"start": ps, "end": pe, "check_dates": set()})
        b["check_dates"].add(r["check_date"])

    raw = []
    for (ps, pe), b in sorted(buckets.items()):
        raw.append({"start": ps, "end": pe, "check_dates": sorted(b["check_dates"])})

    canonical: list[dict] = []
    for p in raw:
        ps_d = datetime.date.fromisoformat(p["start"])
        pe_d = datetime.date.fromisoformat(p["end"])
        merged = False
        for c in canonical:
            cs_d = datetime.date.fromisoformat(c["start"])
            ce_d = datetime.date.fromisoformat(c["end"])
            if ps_d == cs_d and abs((pe_d - ce_d).days) <= 2:
                c["variants"].append({"start": p["start"], "end": p["end"]})
                c["check_dates"] = sorted(set(c["check_dates"]) | set(p["check_dates"]))
                if pe_d > ce_d:
                    c["end"] = p["end"]
                merged = True
                break
        if not merged:
            canonical.append({
                "start": p["start"], "end": p["end"],
                "check_dates": p["check_dates"],
                "variants": [{"start": p["start"], "end": p["end"]}],
                "is_open": False,
            })
    return canonical


def append_open_period(
    canonical: list[dict],
    *,
    last_data_date: str,
    pay_frequency_days: int = 14,
) -> list[dict]:
    """Append a synthetic open period covering the days after the most recent
    completed pay-period end, up through last_data_date."""
    if not canonical:
        return canonical
    last_end = max(canonical, key=lambda p: p["end"])["end"]
    last_end_d = datetime.date.fromisoformat(last_end)
    open_start_d = last_end_d + datetime.timedelta(days=1)
    last_data_d = datetime.date.fromisoformat(last_data_date)
    if open_start_d > last_data_d:
        return canonical
    canonical.append({
        "start": open_start_d.isoformat(),
        "end": last_data_d.isoformat(),
        "check_dates": [],
        "variants": [{"start": open_start_d.isoformat(), "end": last_data_d.isoformat()}],
        "is_open": True,
    })
    return canonical


def actual_cc_tips_by_period(earnings: list[dict]) -> dict[tuple, dict[str, int]]:
    out: dict[tuple, dict[str, int]] = {}
    for r in earnings:
        if r.get("description") != "Credit Card Tips Owed":
            continue
        key = (r["period_start"], r["period_end"])
        bucket = out.setdefault(key, {})
        cents = int(round(r["amount"] * 100))
        bucket[r["employee_name"]] = bucket.get(r["employee_name"], 0) + cents
    return out


# ---------- Tab builders ----------


REVIEW_TUNABLE_KEYS = (
    "review_bonus_started_date",
    "review_base_bonus_dollars",
    "review_named_bonus_dollars",
)


def build_config_rows(
    profile: dict,
    last_data_date: str,
    *,
    training_through: dict[str, datetime.date] | None = None,
    review_tunables: dict[str, str] | None = None,
) -> list[list]:
    training_through = training_through or {}
    review_tunables = review_tunables or {}
    rows: list[list] = [["Key", "Value", "Notes"]]
    rows += [
        ["store", profile["display_name"], ""],
        ["store_id", profile["store_id"], ""],
        ["legal_entity", profile.get("legal_entity", ""), ""],
        ["shop_timezone", profile["timezone"]["shop_tz"], "Times in `daily` and `tip_alloc_daily` are shop-local."],
        ["square_account_display_tz", profile["timezone"]["square_account_display_tz"],
         "Square's source TZ. We convert to shop_tz in transactions_backend."],
        ["excluded_from_tip_pool",
         ", ".join(profile["employees"]["excluded_from_tip_pool_and_labor_pct"]),
         "These employees are excluded from the tip pool AND from labor% calcs."],
        ["pay_frequency", profile["adp_run"].get("pay_frequency", ""), ""],
        ["data_window_start", profile["calibration"]["first_data_window"]["start"],
         "Square data starts here. Pay periods before this are partial."],
        ["data_window_end", last_data_date,
         "Latest day for which we have Square + ADP data."],
        ["last_refreshed_utc",
         datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
         "When this Model workbook was last refreshed."],
        ["adp_wage_rate_report", profile["adp_run"].get("wage_rate_report_name", ""), ""],
        ["bhaga_adp_raw_url", profile["google_sheets"]["bhaga_adp_raw"]["url"], "Raw ADP data."],
        ["bhaga_square_raw_url", profile["google_sheets"]["bhaga_square_raw"]["url"], "Raw Square data."],
        ["bhaga_review_raw_url",
         profile["google_sheets"].get("bhaga_review_raw", {}).get("url", ""),
         "Raw Google reviews data (process_reviews.py output)."],
        # ── Review bonus tuning ──
        # process_reviews reads these from this tab. Edit in-sheet to retune
        # without touching code. Defaults seed on first run; subsequent
        # refreshes preserve whatever the operator has set.
        ["review_bonus_started_date",
         review_tunables.get("review_bonus_started_date", "2026-05-11"),
         "Reviews on or after this date are eligible for shoutout/base bonuses."],
        ["review_base_bonus_dollars",
         review_tunables.get("review_base_bonus_dollars", "10"),
         "Per-person bonus on a no-shoutout 5★ review (every non-excluded shift member)."],
        ["review_named_bonus_dollars",
         review_tunables.get("review_named_bonus_dollars", "20"),
         "Per-person bonus on a shoutout review (only the named people; overrides exclusions)."],
    ]
    # Echo training exclusions verbatim so they survive the config-tab rewrite.
    # USERS: edit these rows directly in Google Sheets. Set the date to the
    # LAST training shift (inclusive). After training ends, either delete the
    # row or set the date to a day before the employee's first non-training
    # shift. Format: training_excluded:<Last, First>  <YYYY-MM-DD>
    for name in sorted(training_through.keys()):
        rows.append([
            f"{TRAINING_EXCLUDED_PREFIX}{name}",
            training_through[name].isoformat(),
            (
                "TEMP training exclusion. Shifts on/before this date contribute hours "
                "but receive NO tip share; their share is redistributed to other tipped "
                "employees. Edit the date or delete this row when training ends."
            ),
        ])
    return rows


def build_daily_rows(
    *,
    txns: list[dict],
    shifts: list[dict],
    excluded: set[str],
    training_through: dict[str, datetime.date] | None = None,
) -> tuple[list[list], dict[str, dict]]:
    training_through = training_through or {}
    sales = transactions_backend.aggregate_daily_sales(txns)
    daily_hours_excl: dict[str, float] = {}
    daily_hours_all: dict[str, float] = {}
    for s in shifts:
        d = s["date"]
        daily_hours_all[d] = daily_hours_all.get(d, 0.0) + s.get("total_hours", 0.0)
        if _is_excluded(s["employee_name"], d,
                        permanent=excluded, training_through=training_through):
            continue
        daily_hours_excl[d] = daily_hours_excl.get(d, 0.0) + s.get("total_hours", 0.0)

    all_dates = sorted(set(sales.keys()) | set(daily_hours_all.keys()))
    header = [
        "date", "dow",
        "gross_sales", "tip_pool", "tips_pct_of_sales",
        "team_hours_eligible", "team_hours_total",
        "pool_per_hour", "txn_count",
    ]
    rows: list[list] = [header]
    summary: dict[str, dict] = {}
    for d in all_dates:
        s = sales.get(d, {"gross_sales_cents": 0, "tip_cents": 0, "transaction_count": 0})
        h_excl = daily_hours_excl.get(d, 0.0)
        h_all = daily_hours_all.get(d, 0.0)
        pool_dollars = s["tip_cents"] / 100
        sales_dollars = s["gross_sales_cents"] / 100
        per_hour = pool_dollars / h_excl if h_excl > 0 else 0.0
        tips_pct = (pool_dollars / sales_dollars) if sales_dollars > 0 else 0.0
        dow = datetime.date.fromisoformat(d).strftime("%a")
        rows.append([
            d, dow,
            round(sales_dollars, 2),
            round(pool_dollars, 2),
            f"{tips_pct:.2%}",
            round(h_excl, 2),
            round(h_all, 2),
            round(per_hour, 2),
            s["transaction_count"],
        ])
        summary[d] = {
            "pool_cents": s["tip_cents"],
            "sales_cents": s["gross_sales_cents"],
            "team_hours": h_excl,
            "txn_count": s["transaction_count"],
        }
    return rows, summary


def build_period_results(
    *,
    periods: list[dict],
    shifts: list[dict],
    txns: list[dict],
    actuals: dict[tuple, dict[str, int]],
    excluded: set[str],
    square_data_start: str,
    training_through: dict[str, datetime.date] | None = None,
) -> list[dict]:
    training_through = training_through or {}
    """Run the allocator for each period; return list of period result dicts."""
    out: list[dict] = []
    for p in periods:
        start, end = p["start"], p["end"]
        if end < square_data_start:
            coverage = "pre_square_only"
        elif start < square_data_start:
            coverage = "partial_pre_square"
        else:
            coverage = "full"

        daily_hours: dict[tuple[str, str], float] = {}
        for s in shifts:
            if not (start <= s["date"] <= end):
                continue
            if _is_excluded(s["employee_name"], s["date"],
                            permanent=excluded, training_through=training_through):
                continue
            k = (s["employee_name"], s["date"])
            daily_hours[k] = daily_hours.get(k, 0.0) + s.get("total_hours", 0.0)

        daily_tips_cents: dict[str, int] = {}
        for t in txns:
            if not (start <= t["date_local"] <= end):
                continue
            daily_tips_cents[t["date_local"]] = (
                daily_tips_cents.get(t["date_local"], 0) + t.get("tip_cents", 0)
            )
        for d in list(daily_tips_cents.keys()):
            if daily_tips_cents[d] < 0:
                daily_tips_cents[d] = 0

        result = allocate(daily_tips_cents, daily_hours)

        actual_by_emp: dict[str, int] = {}
        if not p.get("is_open"):
            for v in p["variants"]:
                for emp, c in actuals.get((v["start"], v["end"]), {}).items():
                    actual_by_emp[emp] = actual_by_emp.get(emp, 0) + c

        out.append({
            "start": start, "end": end,
            "check_dates": p["check_dates"],
            "is_open": p.get("is_open", False),
            "coverage": coverage,
            "per_period_ours": {p.employee: p.total_tip_cents for p in result.per_period},
            "per_period_hours": {p.employee: p.total_hours for p in result.per_period},
            "per_day_allocations": [
                {"date": a.date, "employee": a.employee,
                 "hours": a.hours, "share_cents": a.share_cents}
                for a in result.per_day
            ],
            "per_period_adp": actual_by_emp,
        })
    return out


def likely_reason(
    *,
    ours_c: int,
    adp_c: int,
    is_open: bool,
    coverage: str,
) -> str:
    """coverage ∈ {'full', 'partial_pre_square', 'pre_square_only'}."""
    if is_open:
        return "Open period — not yet paid"
    if coverage == "pre_square_only":
        return "Pre-Square-data window — cannot compare (no tip pool data)"
    if coverage == "partial_pre_square":
        return "Partial Square coverage — calc covers only part of the period"
    diff_c = ours_c - adp_c
    if abs(diff_c) < 100:
        return "OK (rounding)"
    if adp_c == 0 and ours_c > 0:
        return "ADP missed payout — back-pay candidate"
    if ours_c == 0 and adp_c > 0:
        return "We computed $0 — check exclusion or missing shifts"
    pct = abs(diff_c) / max(adp_c, 1)
    if diff_c < 0 and abs(diff_c) > 500 and pct > 0.15:
        return "ADP paid more — possible extra day/double entry"
    if diff_c > 0 and diff_c > 500 and pct > 0.15:
        return "ADP paid less — possible missed shift"
    if abs(diff_c) > 500:
        return "Manual calc drift (high)"
    return "Manual calc drift (small)"


def build_tip_alloc_period_rows(period_results: list[dict]) -> list[list]:
    header = [
        "period_start", "period_end", "coverage", "is_open",
        "employee", "hours_worked",
        "our_calc", "adp_paid", "diff", "diff_pct",
        "our_per_hour", "adp_per_hour", "likely_reason",
    ]
    rows: list[list] = [header]
    for p in period_results:
        emps = sorted(set(p["per_period_ours"]) | set(p["per_period_adp"]))
        for emp in emps:
            ours_c = p["per_period_ours"].get(emp, 0)
            adp_c = p["per_period_adp"].get(emp, 0)
            hrs = p["per_period_hours"].get(emp, 0.0)
            diff_c = ours_c - adp_c
            pct = (diff_c / adp_c) if adp_c else None
            rows.append([
                p["start"], p["end"], p["coverage"], "yes" if p["is_open"] else "no",
                emp, round(hrs, 2),
                round(ours_c / 100, 2),
                round(adp_c / 100, 2),
                round(diff_c / 100, 2),
                f"{pct:+.1%}" if pct is not None else ("n/a" if p["is_open"] else "—"),
                round((ours_c / 100 / hrs), 2) if hrs > 0 else 0,
                round((adp_c / 100 / hrs), 2) if hrs > 0 else 0,
                likely_reason(
                    ours_c=ours_c, adp_c=adp_c,
                    is_open=p["is_open"], coverage=p["coverage"],
                ),
            ])
    return rows


def build_tip_alloc_daily_rows(
    period_results: list[dict],
    daily_summary: dict[str, dict],
) -> list[list]:
    header = [
        "date", "dow", "period_start", "period_end",
        "employee", "hours_worked", "day_pool",
        "team_hours_eligible", "pct_of_day_hours", "our_share",
    ]
    rows: list[list] = [header]
    for p in period_results:
        for a in p["per_day_allocations"]:
            day = daily_summary.get(a["date"], {})
            team_hrs = day.get("team_hours", 0.0)
            pool_c = day.get("pool_cents", 0)
            pct = (a["hours"] / team_hrs) if team_hrs > 0 else 0.0
            dow = datetime.date.fromisoformat(a["date"]).strftime("%a")
            rows.append([
                a["date"], dow, p["start"], p["end"],
                a["employee"], round(a["hours"], 2),
                round(pool_c / 100, 2),
                round(team_hrs, 2),
                f"{pct:.1%}",
                round(a["share_cents"] / 100, 2),
            ])
    return rows


def build_period_summary_rows(period_results: list[dict]) -> list[list]:
    header = [
        "period_start", "period_end", "coverage", "is_open", "check_dates",
        "employees_count", "team_hours", "tip_pool",
        "our_total_allocated", "adp_total_paid", "total_diff",
        "employees_with_diff_over_1usd",
    ]
    rows: list[list] = [header]
    for p in period_results:
        team_hrs = sum(p["per_period_hours"].values())
        pool_c = sum(a["share_cents"] for a in p["per_day_allocations"])
        our_total_c = sum(p["per_period_ours"].values())
        adp_total_c = sum(p["per_period_adp"].values())
        diff_c = our_total_c - adp_total_c
        flagged = sum(
            1 for emp in (set(p["per_period_ours"]) | set(p["per_period_adp"]))
            if abs(p["per_period_ours"].get(emp, 0) - p["per_period_adp"].get(emp, 0)) >= 100
        )
        rows.append([
            p["start"], p["end"], p["coverage"], "yes" if p["is_open"] else "no",
            ", ".join(p["check_dates"]) or ("(not yet paid)" if p["is_open"] else ""),
            len(set(p["per_period_ours"]) | set(p["per_period_adp"])),
            round(team_hrs, 2),
            round(pool_c / 100, 2),
            round(our_total_c / 100, 2),
            round(adp_total_c / 100, 2),
            round(diff_c / 100, 2),
            flagged,
        ])
    return rows


# ---------- Driver ----------


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", required=True)
    cli.add_argument("--dry-run", action="store_true",
                     help="Print row counts per tab and the first few rows but do not write to Sheets.")
    args = cli.parse_args()

    profile = json.loads((STORE_PROFILE_DIR / f"{args.store}.json").read_text())
    aliases = profile["employees"]["aliases"]
    excluded = set(profile["employees"]["excluded_from_tip_pool_and_labor_pct"])
    shop_tz = profile["timezone"]["shop_tz"]
    model_sid = profile["google_sheets"]["bhaga_model"]["spreadsheet_id"]
    model_url = profile["google_sheets"]["bhaga_model"]["url"]

    # Sheet-driven training exclusions (see config tab `training_excluded:*`).
    # READ FIRST so they survive the upcoming config-tab rewrite.
    training_through = _read_training_excluded_from_sheet(
        spreadsheet_id=model_sid, store=args.store
    )
    if training_through:
        print("# training exclusions in effect (canonical_name → last training date):")
        for n, d in sorted(training_through.items()):
            print(f"    {n}: {d.isoformat()}")

    # ARCHITECTURE: model sheet reads canonical data from RAW SHEETS only.
    # The orchestrator (daily_refresh.py) is responsible for keeping the raw
    # sheets in sync with the latest local scrapes via tip_ledger_writer.
    #   * shifts/punches  ← BHAGA ADP Raw   > shifts
    #   * transactions    ← BHAGA Square Raw > transactions
    #   * earnings (per-period gross+CC tips) is the one remaining local-XLSX
    #     read — there is no raw-sheet schema for it yet (TODO: add a
    #     bhaga_adp_raw > earnings tab so this script becomes 100% sheet-driven).
    adp_raw_sid = profile["google_sheets"]["bhaga_adp_raw"]["spreadsheet_id"]
    square_raw_sid = profile["google_sheets"]["bhaga_square_raw"]["spreadsheet_id"]
    earnings_xlsx = _newest("Earnings*.xlsx")
    if not earnings_xlsx:
        print(f"MISSING input: no Earnings*.xlsx in {DOWNLOADS}; orchestrator must run ADP earnings scrape first.")
        return 1

    print(f"# loading shifts from raw sheet {adp_raw_sid} (BHAGA ADP Raw > shifts)")
    shifts = read_raw_adp_shifts(adp_raw_sid, account=args.store)
    print(f"#   → {len(shifts)} shift rows")

    print(f"# loading transactions from raw sheet {square_raw_sid} (BHAGA Square Raw > transactions)")
    txns = read_raw_square_transactions(square_raw_sid, account=args.store)
    print(f"#   → {len(txns)} transaction rows")

    print(f"# loading earnings from local XLSX (no raw-sheet equiv yet): {earnings_xlsx.name}")
    earnings = compensation_backend.parse_xlsx(earnings_xlsx, employee_aliases=aliases)
    print(f"#   → {len(earnings)} earnings rows")

    if not (shifts and earnings and txns):
        print(
            "Empty input: shifts={} earnings={} txns={}. "
            "If raw sheets are empty, the orchestrator's write_raw_sheets step "
            "needs to run first (or run agents/bhaga/scripts/backfill_from_downloads.py manually).".format(
                len(shifts), len(earnings), len(txns)
            )
        )
        return 1

    # data_window_end = the most recent date for which BOTH raw inputs look
    # "complete enough" to publish in the model. Definitions:
    #   * Square completeness: a date with >= 1 transaction is treated as
    #     covered. (No retrospective Square corrections happen — once we have
    #     any txn for a day, we have them all from that scrape.)
    #   * ADP completeness: a date with >= MIN_BARISTAS_PER_DAY non-manager
    #     shifts is treated as covered. (A lone manager punch — e.g. Lindsay
    #     coming in to open — is NOT a complete day because no baristas have
    #     clocked in/out yet, which means we'd publish $0 tips against a real
    #     working day. This is exactly the May-16-AM bug.)
    # Take the most recent date that satisfies BOTH (intersection).
    MIN_BARISTAS_PER_DAY = 2  # Palmetto has 2-3 baristas per shift typically
    square_dates_covered = {t["date_local"] for t in txns}
    barista_shift_counts: dict[str, int] = {}
    for s in shifts:
        if s["employee_id"] in excluded:
            continue
        barista_shift_counts[s["date"]] = barista_shift_counts.get(s["date"], 0) + 1
    adp_dates_covered = {
        d for d, n in barista_shift_counts.items() if n >= MIN_BARISTAS_PER_DAY
    }
    both_covered = square_dates_covered & adp_dates_covered
    if not both_covered:
        print(
            "ERROR: no date is covered by BOTH Square AND >=2 barista shifts. "
            f"square_dates={len(square_dates_covered)}, adp_dates>={MIN_BARISTAS_PER_DAY}={len(adp_dates_covered)}. "
            "Raw sheets likely stale — run the orchestrator's write_raw_sheets step."
        )
        return 1
    last_data_date = max(both_covered)

    # Surface ADP-incomplete dates that we deliberately excluded so the
    # operator can see why data_window_end did not advance to "today".
    adp_incomplete_recent = sorted(
        d for d in square_dates_covered
        if d > last_data_date
        and barista_shift_counts.get(d, 0) < MIN_BARISTAS_PER_DAY
    )
    if adp_incomplete_recent:
        print(
            f"# data_window_end held at {last_data_date}; later days have Square "
            f"but <{MIN_BARISTAS_PER_DAY} barista shifts: " + ", ".join(
                f"{d}({barista_shift_counts.get(d, 0)})" for d in adp_incomplete_recent
            )
        )
    print(f"# last_data_date = {last_data_date}")

    periods = discover_periods(earnings)
    periods = append_open_period(periods, last_data_date=last_data_date)
    actuals = actual_cc_tips_by_period(earnings)
    square_data_start = min(t["date_local"] for t in txns)
    period_results = build_period_results(
        periods=periods, shifts=shifts, txns=txns,
        actuals=actuals, excluded=excluded,
        square_data_start=square_data_start,
        training_through=training_through,
    )
    print(f"# periods: {len(periods)} (open: {sum(1 for p in periods if p.get('is_open'))})")

    # Preserve any operator-edited review tunables across refreshes.
    review_tunables = {
        k: _read_config_value(spreadsheet_id=model_sid, store=args.store, key=k)
        for k in REVIEW_TUNABLE_KEYS
    }
    review_tunables = {k: v for k, v in review_tunables.items() if v is not None}
    config_rows = build_config_rows(
        profile, last_data_date,
        training_through=training_through,
        review_tunables=review_tunables,
    )
    daily_rows, daily_summary = build_daily_rows(
        txns=txns, shifts=shifts, excluded=excluded, training_through=training_through,
    )
    period_rows = build_tip_alloc_period_rows(period_results)
    day_alloc_rows = build_tip_alloc_daily_rows(period_results, daily_summary)
    summary_rows = build_period_summary_rows(period_results)

    tab_payloads = [
        {"tab": "config",            "rows": config_rows,    "currency_cols": []},
        {"tab": "daily",             "rows": daily_rows,     "currency_cols": [2, 3, 7]},  # sales, pool, $/hr
        {"tab": "tip_alloc_period",  "rows": period_rows,    "currency_cols": [6, 7, 8, 10, 11]},  # our, adp, diff, /hr both
        {"tab": "tip_alloc_daily",   "rows": day_alloc_rows, "currency_cols": [6, 9]},  # day_pool, our_share
        {"tab": "period_summary",    "rows": summary_rows,   "currency_cols": [7, 8, 9, 10]},  # pool, ours, adp, diff
    ]

    print()
    print("# Tab summary:")
    for p in tab_payloads:
        n = len(p["rows"]) - 1
        print(f"    {p['tab']:<22} {n:>5} rows")

    if args.dry_run:
        print("\n# Dry-run: first 3 data rows of tip_alloc_period:")
        for r in period_rows[:4]:
            print("   ", r)
        return 0

    token = refresh_access_token(account=args.store)
    print(f"\n# Got token (len={len(token)}); writing to Model sheet {model_sid}...")

    for p in tab_payloads:
        col_count = max(20, len(p["rows"][0]) + 2)
        sheet_id = add_sheet_if_missing(
            model_sid, token, tab_name=p["tab"], column_count=col_count,
        )
        clear_and_write_tab(model_sid, token, tab_name=p["tab"], values=p["rows"])
        bold_header_row(model_sid, token, sheet_id=sheet_id)
        if p["currency_cols"]:
            format_currency_columns(
                model_sid, token, sheet_id=sheet_id,
                column_indices=p["currency_cols"], start_row=1,
            )
        auto_resize_columns(model_sid, token, sheet_id=sheet_id, num_cols=len(p["rows"][0]))
        print(f"    wrote {p['tab']:<22} ({len(p['rows'])-1} rows)")

    print(f"\nDone. {model_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
