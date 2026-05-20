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
    read_raw_adp_rates,
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


def reset_user_entered_format(
    spreadsheet_id: str,
    token: str,
    *,
    sheet_id: int,
    num_cols: int,
    start_row: int = 1,
) -> None:
    """Wipe userEnteredFormat across the data range before re-styling.

    Google Sheets retains per-cell formatting across values:clear writes —
    when a column gains/loses meaning (e.g. adding `orders` between
    net_sales_plus_tips and hourly_hours), residual styling from the
    previous layout leaks through and renders a count as currency or a
    0..2 ratio as "22.00%". Call this BEFORE bold_header_row and
    format_currency_columns so their targeted styling wins on top.
    """
    if num_cols <= 0:
        return
    _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}:batchUpdate",
        token, method="POST",
        data={"requests": [{
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        }]},
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

# Labor-saturation tunables. Same round-trip-preserve pattern as the review
# tunables — operator edits in-sheet, subsequent refreshes echo back the value.
LABOR_TUNABLE_KEYS = (
    "saturation_orders_per_labor_hour",
)

# Seed value for the saturation threshold (orders / labor-hour). Tune from gut
# by watching busy vs slow days; default 10 ≈ one completed order every six
# labor-minutes across the whole team.
DEFAULT_SATURATION_THRESHOLD = 10.0


def build_config_rows(
    profile: dict,
    last_data_date: str,
    *,
    training_through: dict[str, datetime.date] | None = None,
    review_tunables: dict[str, str] | None = None,
    labor_tunables: dict[str, str] | None = None,
) -> list[list]:
    training_through = training_through or {}
    review_tunables = review_tunables or {}
    labor_tunables = labor_tunables or {}
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
        # Labor saturation tuning. labor_daily / labor_period / labor_weekly
        # emit an `over_saturation` flag — "OVER" iff orders_per_labor_hour
        # >=1.0 = at/over capacity; <1.0 = headroom. Tune in-sheet.
        ["saturation_orders_per_labor_hour",
         labor_tunables.get(
             "saturation_orders_per_labor_hour",
             str(DEFAULT_SATURATION_THRESHOLD),
         ),
         "Orders/labor-hour above which the day is considered saturated. "
         "Default 10 = ~1 completed order every 6 labor-minutes across the "
         "whole team. Raise if staff have idle time at this rate; lower if "
         "lines/wait grow at this rate."],
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


def _spread_shift_minutes_by_hour(in_time: str, out_time: str) -> dict[int, float]:
    """Spread a single shift's minutes across clock-hour buckets.

    Used for the peak-hour saturation view: a 06:27 → 13:48 shift contributes
    0.55 hr to hour-6, 1.0 hr to hours 7-12, and 0.80 hr to hour-13.

    Returns {clock_hour: labor_hours_in_that_hour}. Returns {} for malformed
    inputs (empty strings, unparseable HH:MM) so a single bad row doesn't
    nuke the day. BHAGA closes well before midnight, so overnight wraps are
    treated as "ignore" rather than +24h.
    """
    try:
        h1, m1 = map(int, in_time.split(":")[:2])
        h2, m2 = map(int, out_time.split(":")[:2])
    except (ValueError, AttributeError):
        return {}
    start = h1 * 60 + m1
    end = h2 * 60 + m2
    if end <= start:
        return {}
    by_hour: dict[int, float] = {}
    cur = start
    while cur < end:
        hour = cur // 60
        next_boundary = (hour + 1) * 60
        slice_end = min(end, next_boundary)
        by_hour[hour] = by_hour.get(hour, 0.0) + (slice_end - cur) / 60.0
        cur = slice_end
    return by_hour


def _peak_hour_orders_per_labor_hour(
    *,
    hourly_labor_by_clock_hour: dict[int, float],
    orders_by_clock_hour: dict[int, int],
):
    """For one date: worst hour's orders/hourly-labor-hour saturation.

    "Worst" = max ratio over clock hours where hourly labor > 0. Hours with
    zero hourly labor are skipped (we're not open or no hourly staff was on;
    div-by-zero would be misleading). Hours with zero orders contribute 0
    to the max (still considered, just not peaks).

    Returns "" when no hour qualifies (no hourly labor scheduled all day),
    matching the blank-cell convention used throughout this module.
    """
    best = 0.0
    seen_any = False
    for hour, labor_h in hourly_labor_by_clock_hour.items():
        if labor_h <= 0:
            continue
        orders_h = orders_by_clock_hour.get(hour, 0)
        ratio = orders_h / labor_h
        if ratio > best:
            best = ratio
        seen_any = True
    if not seen_any:
        return ""
    return round(best, 1)


def build_labor_daily_rows(
    *,
    txns: list[dict],
    shifts: list[dict],
    wage_rates: list[dict],
    excluded_from_tip_pool: set[str],
    saturation_threshold: float = DEFAULT_SATURATION_THRESHOLD,
) -> list[list]:
    """One row per day with labor cost / labor% computed from ADP wage rates.

    Buckets the workforce into "hourly" (tipped staff) and "fulltime"
    (salaried / manager / anyone excluded from the tip pool). The union
    of three flags decides the bucket:

      1. Listed in `config.excluded_from_tip_pool` (same employees that are
         excluded from the tip pool — Lindsay today, the policy explicitly
         couples these two filters). → fulltime bucket.
      2. `wage_rates.is_salaried == True` (auto-exclude salaried staff;
         currently no one). → fulltime bucket.
      3. `wage_rates.excluded_from_labor_pct == True` (explicit override
         baked into the ADP-rate scrape from the store profile — Lindsay). →
         fulltime bucket.

    All three resolve to {Lindsay} today; the union keeps it future-proof
    when a second manager / salaried hire shows up.

    Labor cost per shift = regular_hours * wage_rate
                         + ot_hours * (ot_rate or wage_rate*1.5)
                         + doubletime_hours * wage_rate * 2.

    Sales columns mirror Square's terminology exactly so they cross-
    reference the Square dashboard reports without translation:
      • `gross_sales`         — Square's "Gross Sales" (pre-discount item revenue)
      • `discounts`           — Square's "Discounts" (negative numbers)
      • `net_sales`           — Square's "Net Sales" = gross_sales + discounts
                                (post-discount item revenue, ex-tax, ex-tips,
                                ex-service-charges). THIS is the industry-
                                standard restaurant labor% denominator since
                                tips are a customer-to-staff pass-through.
      • `tip_pool`            — Square's "Tip" (separate from sales)
      • `net_sales_plus_tips` — net_sales + tip_pool (total customer revenue
                                ex-tax, ex-SC). Used for the "what share of
                                every dollar walking in goes to labor" view.

    Each labor bucket gets TWO percentage columns so both views sit side-
    by-side without flipping tabs.

    Columns:
      date | dow
      | gross_sales | discounts | net_sales | tip_pool | net_sales_plus_tips
      | orders                                      ← completed Square txns
      | hourly_hours | hourly_labor_cost
      | fulltime_hours | fulltime_labor_cost
      | total_labor_cost
      | hourly_pct_of_net_sales | hourly_pct_of_net_sales_plus_tips
      | fulltime_pct_of_net_sales | fulltime_pct_of_net_sales_plus_tips
      | total_labor_pct_of_net_sales | total_labor_pct_of_net_sales_plus_tips
      | tips_pct_of_net_sales | all_in_cost_pct_of_net_sales_plus_tips
      | hourly_labor_per_order | fulltime_labor_per_order | total_labor_per_order
      | orders_per_labor_hour                       ← orders / hourly_hours
      | peak_hour_orders_per_labor_hour             ← worst-hour saturation
      | over_saturation                             ← "OVER" iff >threshold

    `orders_per_labor_hour` uses HOURLY labor only as the denominator —
    full-timers like managers don't add bar throughput, so they're excluded
    from the saturation view. The numerator is completed Square
    transactions (event_type=="Payment"; refunds excluded).

    `peak_hour_orders_per_labor_hour` spreads each hourly shift across the
    clock hours it covered (06:27→13:48 → 0.55h to hour-6, 1.0h to hours
    7-12, 0.80h to hour-13) and reports the worst hour's ratio. This is
    the actionable column for "add a shift during the 11am-1pm rush"
    decisions — a day with a flat 6 average can hide a 14 peak.

    `over_saturation` flips to "OVER" when orders_per_labor_hour exceeds
    the threshold from config (`saturation_orders_per_labor_hour`). Blank
    ("") when there's no hourly labor or no orders that day, rather than
    div-by-zero or stale "ok".
    """
    sales = transactions_backend.aggregate_daily_sales(txns)
    rates_by_emp = {r["employee_name"]: r for r in wage_rates}

    def _is_excluded(emp_name: str) -> bool:
        if emp_name in excluded_from_tip_pool:
            return True
        r = rates_by_emp.get(emp_name, {})
        return bool(r.get("is_salaried")) or bool(r.get("excluded_from_labor_pct"))

    # Fallback rate for employees missing from wage_rates (typically new hires
    # whose first paycheck hasn't posted, like Juan Flores and Lisette Padron).
    # Use the MEDIAN wage_rate across the existing hourly bucket — the
    # "what other hourly employees commonly get" approach. Median is more
    # robust than mean against a single high outlier (e.g. shift lead). OT
    # falls back to 1.5x the fallback rate (FLSA default).
    hourly_rates = [
        float(r["wage_rate_dollars"])
        for r in wage_rates
        if r.get("wage_rate_dollars")
        and not r.get("is_salaried")
        and not r.get("excluded_from_labor_pct")
    ]
    if hourly_rates:
        srt = sorted(hourly_rates)
        n = len(srt)
        fallback_rate = (srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2)
    else:
        fallback_rate = 0.0

    daily: dict[str, dict[str, float]] = {}
    # Per-date map of {clock_hour: hourly-bucket labor-hours in that hour}.
    # Only hourly-bucket shifts contribute — peak-hour saturation uses the
    # same denominator as the daily orders_per_labor_hour column for
    # consistency. Built from shift in_time/out_time (HH:MM from ADP).
    hourly_labor_by_date_hour: dict[str, dict[int, float]] = {}
    fallback_used: set[str] = set()
    fallback_dropped: set[str] = set()
    for s in shifts:
        d = s["date"]
        emp = s["employee_name"]
        rate_row = rates_by_emp.get(emp)
        if rate_row:
            rate = float(rate_row.get("wage_rate_dollars") or 0)
            ot_rate = float(rate_row.get("ot_rate_dollars") or 0) or (rate * 1.5)
            bucket_excluded = _is_excluded(emp)
        elif fallback_rate > 0:
            # Apply fallback to UNTIL-NOW-UNSEEN employees. Treat as hourly
            # (the fallback rate IS the hourly median — anyone in the
            # fulltime bucket would already have a wage_rates row).
            rate = fallback_rate
            ot_rate = rate * 1.5
            bucket_excluded = emp in excluded_from_tip_pool  # only honor explicit config flag
            fallback_used.add(emp)
        else:
            fallback_dropped.add(emp)
            continue

        reg_h = float(s.get("regular_hours") or 0)
        ot_h = float(s.get("ot_hours") or 0)
        dt_h = float(s.get("doubletime_hours") or 0)
        cost = reg_h * rate + ot_h * ot_rate + dt_h * rate * 2
        h = reg_h + ot_h + dt_h
        b = daily.setdefault(d, {"el_h": 0.0, "el_c": 0.0, "ex_h": 0.0, "ex_c": 0.0})
        if bucket_excluded:
            b["ex_h"] += h
            b["ex_c"] += cost
        else:
            b["el_h"] += h
            b["el_c"] += cost
            # Spread this hourly-bucket shift across clock hours for the
            # peak-hour view. ADP only tracks one in/out pair per shift row;
            # split shifts would underrepresent labor hours mid-day but in
            # practice BHAGA shifts are contiguous, and the punches tab
            # (where split shifts ARE separated) would be overkill here.
            for hour, mins in _spread_shift_minutes_by_hour(
                s.get("in_time", ""), s.get("out_time", "")
            ).items():
                day_map = hourly_labor_by_date_hour.setdefault(d, {})
                day_map[hour] = day_map.get(hour, 0.0) + mins
    if fallback_used:
        print(f"# NOTE: labor_daily used fallback wage rate ${fallback_rate:.2f}/hr "
              f"(median of {len(hourly_rates)} hourly rates) for: {sorted(fallback_used)}")
    if fallback_dropped:
        print(f"# WARN: labor_daily skipped shifts with no wage_rate AND no fallback available: "
              f"{sorted(fallback_dropped)}")

    # Aggregate completed orders by (date, clock_hour) for peak-hour view.
    # event_type=="Refund" is excluded so the numerator matches the daily
    # `orders` column (Payments only).
    orders_by_date_hour: dict[str, dict[int, int]] = {}
    for t in txns:
        if t.get("event_type") == "Refund":
            continue
        d_iso = t.get("date_local") or ""
        if not d_iso:
            continue
        hour = t.get("hour_local")
        if hour is None or hour == "":
            continue
        day_map = orders_by_date_hour.setdefault(d_iso, {})
        day_map[int(hour)] = day_map.get(int(hour), 0) + 1

    all_dates = sorted(set(sales.keys()) | set(daily.keys()))
    header = [
        "date", "dow",
        "gross_sales", "discounts", "net_sales", "tip_pool", "net_sales_plus_tips",
        "orders",
        "hourly_hours", "hourly_labor_cost",
        "fulltime_hours", "fulltime_labor_cost",
        "total_labor_cost",
        "hourly_pct_of_net_sales", "hourly_pct_of_net_sales_plus_tips",
        "fulltime_pct_of_net_sales", "fulltime_pct_of_net_sales_plus_tips",
        "total_labor_pct_of_net_sales", "total_labor_pct_of_net_sales_plus_tips",
        "tips_pct_of_net_sales", "all_in_cost_pct_of_net_sales_plus_tips",
        "hourly_labor_per_order", "fulltime_labor_per_order", "total_labor_per_order",
        "orders_per_labor_hour", "peak_hour_orders_per_labor_hour",
        "over_saturation",
    ]
    rows: list[list] = [header]
    for d in all_dates:
        s_d = sales.get(d, {
            "gross_sales_cents": 0, "discount_cents": 0,
            "net_sales_cents": 0, "tip_cents": 0, "order_count": 0,
        })
        b = daily.get(d, {"el_h": 0.0, "el_c": 0.0, "ex_h": 0.0, "ex_c": 0.0})
        gross = s_d["gross_sales_cents"] / 100
        disc = s_d["discount_cents"] / 100
        net = s_d["net_sales_cents"] / 100
        pool = s_d["tip_cents"] / 100
        net_plus_tips = net + pool
        orders = int(s_d.get("order_count", 0) or 0)
        hourly_cost = b["el_c"]
        fulltime_cost = b["ex_c"]
        total_cost = hourly_cost + fulltime_cost
        hourly_h = b["el_h"]
        total_h = hourly_h + b["ex_h"]

        def _pct(num: float, denom: float) -> float:
            return (num / denom) if denom > 0 else 0.0

        def _per_order(cost: float):
            return round(cost / orders, 2) if orders > 0 else ""

        # orders_per_labor_hour denominator = HOURLY-bucket labor only.
        # Full-timers don't add bar throughput, so excluding them from the
        # denominator is the whole point of the saturation view.
        orders_per_hr = round(orders / hourly_h, 1) if hourly_h > 0 else ""
        peak_hour = _peak_hour_orders_per_labor_hour(
            hourly_labor_by_clock_hour=hourly_labor_by_date_hour.get(d, {}),
            orders_by_clock_hour=orders_by_date_hour.get(d, {}),
        )
        if isinstance(orders_per_hr, (int, float)) and saturation_threshold > 0:
            over = "OVER" if orders_per_hr > saturation_threshold else "ok"
        else:
            over = ""

        rows.append([
            d, datetime.date.fromisoformat(d).strftime("%a"),
            round(gross, 2),
            round(disc, 2),
            round(net, 2),
            round(pool, 2),
            round(net_plus_tips, 2),
            orders,
            round(hourly_h, 2),
            round(hourly_cost, 2),
            round(b["ex_h"], 2),
            round(fulltime_cost, 2),
            round(total_cost, 2),
            f"{_pct(hourly_cost, net):.2%}",
            f"{_pct(hourly_cost, net_plus_tips):.2%}",
            f"{_pct(fulltime_cost, net):.2%}",
            f"{_pct(fulltime_cost, net_plus_tips):.2%}",
            f"{_pct(total_cost, net):.2%}",
            f"{_pct(total_cost, net_plus_tips):.2%}",
            f"{_pct(pool, net):.2%}",
            f"{_pct(total_cost + pool, net_plus_tips):.2%}",
            _per_order(hourly_cost),
            _per_order(fulltime_cost),
            _per_order(total_cost),
            orders_per_hr,
            peak_hour,
            over,
        ])
    return rows


def build_labor_period_rows(
    *,
    periods: list[dict],
    labor_daily_rows: list[list],
    saturation_threshold: float = DEFAULT_SATURATION_THRESHOLD,
) -> list[list]:
    """Aggregate labor_daily rows by pay period.

    Sums the raw $/hour columns across the days in each period, then
    recomputes percentages from those sums (NOT an average of per-day
    percentages — that would mis-weight low-sales days against high-sales
    ones and produce a meaningless number).

    `periods` come from discover_periods() + append_open_period() so the
    open in-progress period is included as the last row, flagged via
    `is_open=Y`. Helps the operator see "where we're trending" before
    the period closes.

    Columns (22 total):
      pay_period_start | pay_period_end | is_open | days_covered
      gross_sales | discounts | net_sales | tip_pool | net_sales_plus_tips
      hourly_hours | hourly_labor_cost
      fulltime_hours | fulltime_labor_cost
      total_labor_cost
      hourly_pct_of_net_sales | hourly_pct_of_net_sales_plus_tips
      fulltime_pct_of_net_sales | fulltime_pct_of_net_sales_plus_tips
      total_labor_pct_of_net_sales | total_labor_pct_of_net_sales_plus_tips
      tips_pct_of_net_sales | all_in_cost_pct_of_net_sales_plus_tips
    """
    header = [
        "pay_period_start", "pay_period_end", "is_open", "days_covered",
        "gross_sales", "discounts", "net_sales", "tip_pool", "net_sales_plus_tips",
        "orders",
        "hourly_hours", "hourly_labor_cost",
        "fulltime_hours", "fulltime_labor_cost",
        "total_labor_cost",
        "hourly_pct_of_net_sales", "hourly_pct_of_net_sales_plus_tips",
        "fulltime_pct_of_net_sales", "fulltime_pct_of_net_sales_plus_tips",
        "total_labor_pct_of_net_sales", "total_labor_pct_of_net_sales_plus_tips",
        "tips_pct_of_net_sales", "all_in_cost_pct_of_net_sales_plus_tips",
        "hourly_labor_per_order", "fulltime_labor_per_order", "total_labor_per_order",
        "orders_per_labor_hour", "peak_hour_orders_per_labor_hour",
        "over_saturation",
    ]
    if len(labor_daily_rows) <= 1:
        return [header]

    # Index labor_daily by date for fast period-bucketing. Field positions
    # match the header in build_labor_daily_rows: date=0, dow=1, gross=2,
    # disc=3, net=4, pool=5, net_plus_tips=6, orders=7, hourly_hours=8,
    # hourly_cost=9, fulltime_hours=10, fulltime_cost=11.
    daily_by_date: dict[str, dict] = {}
    for row in labor_daily_rows[1:]:
        # column positions match build_labor_daily_rows header:
        # peak_hour_orders_per_labor_hour=25 (was numeric saturation pre-rename).
        peak_cell = row[25]
        daily_by_date[row[0]] = {
            "gross": float(row[2]),
            "disc": float(row[3]),
            "net": float(row[4]),
            "pool": float(row[5]),
            "orders": int(row[7] or 0),
            "hourly_h": float(row[8]),
            "hourly_c": float(row[9]),
            "fulltime_h": float(row[10]),
            "fulltime_c": float(row[11]),
            # peak is "" on no-orders/no-labor days; treat as None so the
            # max-of-peaks aggregation skips them.
            "peak": (float(peak_cell) if peak_cell not in ("", None) else None),
        }

    rows: list[list] = [header]
    for p in periods:
        start = p["start"]
        end = p["end"]
        is_open = "Y" if p.get("is_open") else "N"
        gross = disc = net = pool = h_hours = h_cost = ft_hours = ft_cost = 0.0
        orders = 0
        days = 0
        peak_max = None
        cursor = datetime.date.fromisoformat(start)
        end_d = datetime.date.fromisoformat(end)
        while cursor <= end_d:
            iso = cursor.isoformat()
            if iso in daily_by_date:
                bucket = daily_by_date[iso]
                gross += bucket["gross"]
                disc += bucket["disc"]
                net += bucket["net"]
                pool += bucket["pool"]
                orders += bucket["orders"]
                h_hours += bucket["hourly_h"]
                h_cost += bucket["hourly_c"]
                ft_hours += bucket["fulltime_h"]
                ft_cost += bucket["fulltime_c"]
                if bucket["peak"] is not None:
                    peak_max = bucket["peak"] if peak_max is None else max(peak_max, bucket["peak"])
                days += 1
            cursor += datetime.timedelta(days=1)
        net_plus_tips = net + pool
        total_cost = h_cost + ft_cost

        def _pct(num: float, denom: float) -> float:
            return (num / denom) if denom > 0 else 0.0

        def _per_order(cost: float):
            return round(cost / orders, 2) if orders > 0 else ""

        orders_per_hr = round(orders / h_hours, 1) if h_hours > 0 else ""
        peak_out = round(peak_max, 1) if peak_max is not None else ""
        if isinstance(orders_per_hr, (int, float)) and saturation_threshold > 0:
            over = "OVER" if orders_per_hr > saturation_threshold else "ok"
        else:
            over = ""

        rows.append([
            start, end, is_open, days,
            round(gross, 2),
            round(disc, 2),
            round(net, 2),
            round(pool, 2),
            round(net_plus_tips, 2),
            orders,
            round(h_hours, 2),
            round(h_cost, 2),
            round(ft_hours, 2),
            round(ft_cost, 2),
            round(total_cost, 2),
            f"{_pct(h_cost, net):.2%}",
            f"{_pct(h_cost, net_plus_tips):.2%}",
            f"{_pct(ft_cost, net):.2%}",
            f"{_pct(ft_cost, net_plus_tips):.2%}",
            f"{_pct(total_cost, net):.2%}",
            f"{_pct(total_cost, net_plus_tips):.2%}",
            f"{_pct(pool, net):.2%}",
            f"{_pct(total_cost + pool, net_plus_tips):.2%}",
            _per_order(h_cost),
            _per_order(ft_cost),
            _per_order(total_cost),
            orders_per_hr,
            peak_out,
            over,
        ])
    return rows


def build_labor_weekly_rows(
    *,
    labor_daily_rows: list[list],
    saturation_threshold: float = DEFAULT_SATURATION_THRESHOLD,
) -> list[list]:
    """Aggregate labor_daily rows by ISO calendar week (Monday → Sunday).

    Why Monday-Sunday: Square's dashboard defaults to this convention, ISO
    8601 mandates it, and most restaurant industry "weekly labor%" reports
    use it (Sunday is part of the week ending that day, not the start of
    the next one). To flip to Sunday-Saturday, change `cursor.weekday()`
    to `(cursor.weekday() + 1) % 7` and adjust the +6 offset.

    Like build_labor_period_rows: aggregates raw $ totals and recomputes
    percentages from those sums. Never averages per-day percentages.

    Includes the current (potentially partial) week as the last row with
    `is_partial=Y` and `days_covered < 7` so the operator can see the
    week-to-date trend. Closed weeks have `is_partial=N`.

    Columns (23 total):
      iso_week | week_start | week_end | is_partial | days_covered
      gross_sales | discounts | net_sales | tip_pool | net_sales_plus_tips
      hourly_hours | hourly_labor_cost
      fulltime_hours | fulltime_labor_cost
      total_labor_cost
      hourly_pct_of_net_sales | hourly_pct_of_net_sales_plus_tips
      fulltime_pct_of_net_sales | fulltime_pct_of_net_sales_plus_tips
      total_labor_pct_of_net_sales | total_labor_pct_of_net_sales_plus_tips
      tips_pct_of_net_sales | all_in_cost_pct_of_net_sales_plus_tips
    """
    header = [
        "iso_week", "week_start", "week_end", "is_partial", "days_covered",
        "gross_sales", "discounts", "net_sales", "tip_pool", "net_sales_plus_tips",
        "orders",
        "hourly_hours", "hourly_labor_cost",
        "fulltime_hours", "fulltime_labor_cost",
        "total_labor_cost",
        "hourly_pct_of_net_sales", "hourly_pct_of_net_sales_plus_tips",
        "fulltime_pct_of_net_sales", "fulltime_pct_of_net_sales_plus_tips",
        "total_labor_pct_of_net_sales", "total_labor_pct_of_net_sales_plus_tips",
        "tips_pct_of_net_sales", "all_in_cost_pct_of_net_sales_plus_tips",
        "hourly_labor_per_order", "fulltime_labor_per_order", "total_labor_per_order",
        "orders_per_labor_hour", "peak_hour_orders_per_labor_hour",
        "over_saturation",
    ]
    if len(labor_daily_rows) <= 1:
        return [header]

    # Same field positions as labor_period (see build_labor_period_rows).
    daily_by_date: dict[str, dict] = {}
    for row in labor_daily_rows[1:]:
        peak_cell = row[25]
        daily_by_date[row[0]] = {
            "gross": float(row[2]),
            "disc": float(row[3]),
            "net": float(row[4]),
            "pool": float(row[5]),
            "orders": int(row[7] or 0),
            "hourly_h": float(row[8]),
            "hourly_c": float(row[9]),
            "fulltime_h": float(row[10]),
            "fulltime_c": float(row[11]),
            "peak": (float(peak_cell) if peak_cell not in ("", None) else None),
        }

    if not daily_by_date:
        return [header]

    all_dates = sorted(daily_by_date.keys())
    max_data_date = datetime.date.fromisoformat(all_dates[-1])

    # Group dates by their ISO week's Monday.
    weeks: dict[datetime.date, list[str]] = {}
    for iso in all_dates:
        d = datetime.date.fromisoformat(iso)
        monday = d - datetime.timedelta(days=d.weekday())  # weekday(): Mon=0 .. Sun=6
        weeks.setdefault(monday, []).append(iso)

    rows: list[list] = [header]
    for monday in sorted(weeks):
        sunday = monday + datetime.timedelta(days=6)
        iso_year, iso_week, _ = monday.isocalendar()
        iso_label = f"{iso_year}-W{iso_week:02d}"
        is_partial = "Y" if sunday > max_data_date else "N"

        gross = disc = net = pool = h_hours = h_cost = ft_hours = ft_cost = 0.0
        orders = 0
        days = 0
        peak_max = None
        for iso_date in weeks[monday]:
            bucket = daily_by_date[iso_date]
            gross += bucket["gross"]
            disc += bucket["disc"]
            net += bucket["net"]
            pool += bucket["pool"]
            orders += bucket["orders"]
            h_hours += bucket["hourly_h"]
            h_cost += bucket["hourly_c"]
            ft_hours += bucket["fulltime_h"]
            ft_cost += bucket["fulltime_c"]
            if bucket["peak"] is not None:
                peak_max = bucket["peak"] if peak_max is None else max(peak_max, bucket["peak"])
            days += 1
        net_plus_tips = net + pool
        total_cost = h_cost + ft_cost

        def _pct(num: float, denom: float) -> float:
            return (num / denom) if denom > 0 else 0.0

        def _per_order(cost: float):
            return round(cost / orders, 2) if orders > 0 else ""

        orders_per_hr = round(orders / h_hours, 1) if h_hours > 0 else ""
        peak_out = round(peak_max, 1) if peak_max is not None else ""
        if isinstance(orders_per_hr, (int, float)) and saturation_threshold > 0:
            over = "OVER" if orders_per_hr > saturation_threshold else "ok"
        else:
            over = ""

        rows.append([
            iso_label, monday.isoformat(), sunday.isoformat(), is_partial, days,
            round(gross, 2),
            round(disc, 2),
            round(net, 2),
            round(pool, 2),
            round(net_plus_tips, 2),
            orders,
            round(h_hours, 2),
            round(h_cost, 2),
            round(ft_hours, 2),
            round(ft_cost, 2),
            round(total_cost, 2),
            f"{_pct(h_cost, net):.2%}",
            f"{_pct(h_cost, net_plus_tips):.2%}",
            f"{_pct(ft_cost, net):.2%}",
            f"{_pct(ft_cost, net_plus_tips):.2%}",
            f"{_pct(total_cost, net):.2%}",
            f"{_pct(total_cost, net_plus_tips):.2%}",
            f"{_pct(pool, net):.2%}",
            f"{_pct(total_cost + pool, net_plus_tips):.2%}",
            _per_order(h_cost),
            _per_order(ft_cost),
            _per_order(total_cost),
            orders_per_hr,
            peak_out,
            over,
        ])
    return rows


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

    # Bootstrap pointer + sheet-derived aliases/exclusions.
    profile = json.loads((STORE_PROFILE_DIR / f"{args.store}.json").read_text())
    from skills.store_profile import load_aliases, load_exclusions
    aliases = load_aliases(args.store)
    excluded = set(load_exclusions(args.store)["permanent"])
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
    print(f"# loading wage_rates from raw sheet {adp_raw_sid} (BHAGA ADP Raw > wage_rates)")
    wage_rates = read_raw_adp_rates(adp_raw_sid, account=args.store)
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
    # Same round-trip for labor saturation tunables (currently just one).
    labor_tunables = {
        k: _read_config_value(spreadsheet_id=model_sid, store=args.store, key=k)
        for k in LABOR_TUNABLE_KEYS
    }
    labor_tunables = {k: v for k, v in labor_tunables.items() if v is not None}
    try:
        saturation_threshold = float(
            labor_tunables.get(
                "saturation_orders_per_labor_hour",
                DEFAULT_SATURATION_THRESHOLD,
            )
        )
    except (TypeError, ValueError):
        print(
            "# WARN: saturation_orders_per_labor_hour in config is "
            f"not a number ({labor_tunables.get('saturation_orders_per_labor_hour')!r}); "
            f"falling back to default {DEFAULT_SATURATION_THRESHOLD}."
        )
        saturation_threshold = float(DEFAULT_SATURATION_THRESHOLD)
    print(f"# saturation threshold = {saturation_threshold} orders/labor-hour")

    config_rows = build_config_rows(
        profile, last_data_date,
        training_through=training_through,
        review_tunables=review_tunables,
        labor_tunables=labor_tunables,
    )
    daily_rows, daily_summary = build_daily_rows(
        txns=txns, shifts=shifts, excluded=excluded, training_through=training_through,
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
    period_rows = build_tip_alloc_period_rows(period_results)
    day_alloc_rows = build_tip_alloc_daily_rows(period_results, daily_summary)
    summary_rows = build_period_summary_rows(period_results)

    tab_payloads = [
        {"tab": "config",            "rows": config_rows,      "currency_cols": []},
        {"tab": "daily",             "rows": daily_rows,       "currency_cols": [2, 3, 7]},
        # labor_daily currency cols (0-indexed against build_labor_daily_rows
        # header): 2=gross_sales, 3=discounts, 4=net_sales, 5=tip_pool,
        # 6=net_sales_plus_tips, 9=hourly_labor_cost, 11=fulltime_labor_cost,
        # 12=total_labor_cost, 21=hourly_labor_per_order,
        # 22=fulltime_labor_per_order, 23=total_labor_per_order. (orders=7 is
        # a count; saturation/per-hour cols are ratios, not currency.)
        {"tab": "labor_daily",       "rows": labor_daily_rows, "currency_cols": [2, 3, 4, 5, 6, 9, 11, 12, 21, 22, 23]},
        # labor_weekly inserts 5 lead cols before labor_daily layout → +3 shift.
        {"tab": "labor_weekly",      "rows": labor_weekly_rows, "currency_cols": [5, 6, 7, 8, 9, 12, 14, 15, 24, 25, 26]},
        # labor_period inserts 4 lead cols before labor_daily layout → +2 shift.
        {"tab": "labor_period",      "rows": labor_period_rows, "currency_cols": [4, 5, 6, 7, 8, 11, 13, 14, 23, 24, 25]},
        {"tab": "tip_alloc_period",  "rows": period_rows,      "currency_cols": [6, 7, 8, 10, 11]},
        {"tab": "tip_alloc_daily",   "rows": day_alloc_rows,   "currency_cols": [6, 9]},
        {"tab": "period_summary",    "rows": summary_rows,     "currency_cols": [7, 8, 9, 10]},
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
        # Wipe stale numberFormat / borders / colors from any prior layout
        # before re-applying the targeted bold-header + currency styling.
        # Without this, an old percent-formatted cell at a column index that
        # now holds a saturation ratio renders 0.22 as "22.00%".
        reset_user_entered_format(
            model_sid, token, sheet_id=sheet_id, num_cols=len(p["rows"][0]), start_row=0,
        )
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
