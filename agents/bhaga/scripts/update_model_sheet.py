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
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.daily_refresh import is_refresh_date_complete
from agents.bhaga.scripts.forecast import compute_outlier_stats
from core.config_loader import project_dir, refresh_access_token, resolve_sheet_id
from skills.adp_run_automation.shift_backend import normalize_employee_name
from skills.bhaga_config.dates import _iso_date_for_sheet_cell, coerce_iso_date
from skills.square_tips import transactions_backend
from skills.tip_ledger_writer import (
    read_raw_adp_rates,
    read_raw_adp_shifts,
    read_raw_square_transactions,
)
from skills.tip_ledger_writer.reader import read_raw_square_item_daily_rollup
from skills.tip_pool_allocation.adapter import allocate


# Config keys whose VALUE column is a date and therefore must be
# written through `_iso_date_for_sheet_cell` so Google Sheets keeps
# them as text literals instead of coercing them to date-serials.
# When you add another date-bearing key to build_config_rows, list
# it here too (the round-trip sentinel at the end of main() greps
# this tuple to verify the cell didn't drift after the write).
_DATE_CONFIG_KEYS = (
    "data_window_start",
    "data_window_end",
    "review_bonus_started_date",
)


PROJECT = pathlib.Path(project_dir())
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


def reset_number_format(
    spreadsheet_id: str,
    token: str,
    *,
    sheet_id: int,
    num_cols: int,
    start_row: int = 1,
) -> None:
    """Reset numberFormat to AUTOMATIC across the data range.

    Google Sheets retains per-cell number formatting across clear+write
    operations (the values:clear endpoint only clears values). When the
    column layout changes (e.g. adding a new column that shifts everything
    right), residual formatting from the previous layout leaks through —
    a numeric count gets rendered as currency, a 0..2 ratio gets rendered
    as "22.00%" because the old column at that position was a percentage.

    This call wipes numberFormat back to AUTOMATIC before format_currency_
    columns reapplies the targeted currency styling, so only columns we
    explicitly want as currency get the dollar sign.
    """
    if num_cols <= 0:
        return
    # Nuke the entire userEnteredFormat on the data range. We have to do this
    # broadly because a partial mask (fields=userEnteredFormat.numberFormat
    # with empty body) was empirically inconsistent — old PERCENT formatting
    # leaked through in some cells but not others on the same column. After
    # this call, format_currency_columns + bold_header_row re-apply the
    # targeted styling. Cover all rows including the header; bold_header_row
    # runs AFTER this so the header still ends up bold.
    _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}:batchUpdate",
        token, method="POST",
        data={"requests": [{
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    # cover from row 0 so the header's own old formatting
                    # (e.g. previous percent-suffix) doesn't survive either.
                    "startRowIndex": 0,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        }]},
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


def _percent_column_indices(header: list) -> list[int]:
    """Column indices (0-based) that hold percentage VALUES, by header name.

    The builders write percent cells as the string ``"66.67%"`` via
    USER_ENTERED, which Google Sheets parses into the underlying fraction
    (``0.6667``) and would auto-render as a percent — EXCEPT reset_number_format
    wipes that format back to AUTOMATIC, so the cell renders as a bare
    ``0.6667``. We re-apply a PERCENT numberFormat to these columns in the same
    post-write pass as the currency re-apply, so they render as ``66.67%``
    AND survive every rebuild.

    Detection is by name so it stays correct as columns shift between the
    labor_daily / labor_weekly / labor_period / forecast layouts (each adds a
    different number of lead columns): any header containing ``pct`` is a
    fraction column (hourly_pct_of_net_sales, tips_pct_of_sales, diff_pct,
    pct_of_day_hours, target_labor_pct, actual_labor_pct, *_error_pct,
    kds_pct_tickets_late, …), plus ``forecast_mape`` which is a MAPE fraction.
    Counts/ratios/seconds never contain ``pct`` so they're never matched.
    """
    out: list[int] = []
    for i, name in enumerate(header):
        n = str(name).strip().lower()
        if "pct" in n or n == "forecast_mape":
            out.append(i)
    return out


def _seconds_column_indices(header: list) -> list[int]:
    """Column indices (0-based) that hold per-item SECONDS values, by header name.

    The KDS percentile/median columns (``*_time_per_item_sec``) are plain
    seconds — NOT fractions. A prior sheet layout had percent columns at these
    same indices, and clearing userEnteredFormat alone has proven unreliable at
    wiping that stale PERCENT format (it survives on isolated rows, rendering
    e.g. 83501s as "8350100.00%"). So we POSITIVELY assert a NUMBER format on
    them in the post-write pass — same reliable mechanism as currency/percent —
    which deterministically overrides any residual percent format.
    """
    return [
        i for i, name in enumerate(header)
        if str(name).strip().lower().endswith("time_per_item_sec")
    ]


def format_number_columns(
    spreadsheet_id: str,
    token: str,
    *,
    sheet_id: int,
    column_indices: list[int],
    start_row: int = 1,
    pattern: str = "0.0",
) -> None:
    """Assert a plain NUMBER format on specified columns (0-indexed) from start_row down.

    Analogous to format_currency_columns / format_percent_columns. Used to force
    the KDS seconds columns to NUMBER so stale PERCENT formatting from a previous
    layout can't leak through (the userEnteredFormat wipe in reset_number_format
    is unreliable for this on isolated rows).
    """
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
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        }
        for col in column_indices
    ]
    _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}:batchUpdate",
        token, method="POST", data={"requests": requests},
    )


def format_percent_columns(
    spreadsheet_id: str,
    token: str,
    *,
    sheet_id: int,
    column_indices: list[int],
    start_row: int = 1,
    pattern: str = "0.00%",
) -> None:
    """Apply PERCENT format to specified columns (0-indexed) from start_row down.

    Analogous to format_currency_columns; runs AFTER reset_number_format so the
    targeted percent styling wins over the AUTOMATIC wipe. The stored values are
    fractions (e.g. 0.6667), so the ``0.00%`` pattern renders them as 66.67%.
    """
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
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": pattern}}},
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


def hide_columns(
    spreadsheet_id: str, token: str, *, sheet_id: int, column_indices: list[int],
) -> None:
    """Hide the given 0-based columns (hiddenByUser). Idempotent across runs.

    Used for the forecast tab's helper-constant columns: they exist only so the
    derived formulas have stable cell references, so we tuck them out of the
    operator's way. hiddenByUser is a dimension property (not a cell format), so
    it survives the values:clear + reset_number_format pass.
    """
    if not column_indices:
        return
    requests = [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col,
                    "endIndex": col + 1,
                },
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        }
        for col in column_indices
    ]
    _api(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}:batchUpdate",
        token, method="POST", data={"requests": requests},
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


def _fill_calendar_dates(dates: list[str]) -> list[str]:
    """Fill gaps in a sorted list of ISO date strings.

    Given ["2026-05-19", "2026-05-22"], returns the contiguous range
    ["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]. Used by
    build_daily_rows / build_labor_daily_rows so that days with zero
    activity (store closed, gap between shifts) still get a row with
    all-zero values — otherwise weekly/period aggregators silently drop
    those days and daily-tab row counts diverge between prod and staging.
    """
    if len(dates) < 2:
        return list(dates)
    start = datetime.date.fromisoformat(dates[0])
    end = datetime.date.fromisoformat(dates[-1])
    filled: list[str] = []
    cursor = start
    while cursor <= end:
        filled.append(cursor.isoformat())
        cursor += datetime.timedelta(days=1)
    return filled


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


def _parse_sheet_bool(v) -> bool:
    """Coerce a sheet cell into a boolean.

    Sheets returns native booleans as the strings "TRUE"/"FALSE" (and the
    Values API may echo Python bools). Anything truthy-looking maps to True;
    everything else (blank, "FALSE", "0", "no") maps to False.
    """
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("true", "1", "yes", "y", "t")


def _read_existing_labor_daily_forecast_exclude(
    *, spreadsheet_id: str, store: str
) -> dict[str, bool]:
    """Read the current labor_daily tab's date → forecast_exclude map.

    Lets the nightly rebuild PRESERVE operator-edited forecast_exclude values
    instead of clobbering them with the freshly-computed outlier default.
    Returns {} if the tab/column is absent or unreachable (graceful degrade).
    """
    out: dict[str, bool] = {}
    try:
        token = refresh_access_token(store)
        rng = urllib.parse.quote("labor_daily!A1:ZZ100000", safe="!:")
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{rng}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  [labor_daily-read] could not read existing forecast_exclude (first run?): {exc}")
        return out
    values = data.get("values", [])
    if not values:
        return out
    header = values[0]
    try:
        date_i = header.index("date")
    except ValueError:
        return out
    if "forecast_exclude" not in header:
        return out
    fe_i = header.index("forecast_exclude")
    for row in values[1:]:
        if len(row) <= max(date_i, fe_i):
            continue
        iso = coerce_iso_date(row[date_i])
        if iso is None:
            continue
        out[iso] = _parse_sheet_bool(row[fe_i])
    return out


def _read_existing_forecast_grid(
    *, spreadsheet_id: str, store: str
) -> list[list]:
    """Read the current labor_daily_forecast tab as a raw grid (header + rows).

    Single fetch that serves two jobs in the nightly rebuild:
      1. FREEZE-IN-PLACE — the Sheets Values API returns the EVALUATED value of
         a formula cell, so the derived columns here already carry their
         computed numbers. build_labor_daily_forecast_rows captures the rows
         that have rolled into the past AS VALUES (the forecast we actually
         made) instead of re-forecasting them with hindsight.
      2. PRESERVATION — _forecast_grid_col_numeric_map() extracts the per-date
         operator-edited input columns (target_time_per_item_sec,
         target_hourly_labor_pct) so they survive the rebuild.

    Returns [] if the tab is absent or unreachable (graceful degrade / first
    run), which makes the forecast build fall back to future-only behavior.
    """
    try:
        token = refresh_access_token(store)
        rng = urllib.parse.quote("labor_daily_forecast!A1:ZZ100000", safe="!:")
        # UNFORMATTED_VALUE so formula cells come back as their evaluated
        # NUMBERS (not "$9.00" / "23.45%" formatted strings) — otherwise freeze
        # would capture display strings and backfill's float() parse would fail
        # on the currency-formatted helper constants, silently zeroing the
        # frozen forecast it scores against.
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
            f"/values/{rng}?valueRenderOption=UNFORMATTED_VALUE"
        )
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  [forecast-read] could not read existing forecast tab (first run?): {exc}")
        return []
    return data.get("values", []) or []


def _forecast_grid_col_numeric_map(
    grid: list[list], col_name: str
) -> dict[str, float]:
    """Extract a date → numeric-value map for one column of a forecast grid.

    Pure helper over the raw grid from _read_existing_forecast_grid. Used to
    PRESERVE operator-edited per-row inputs (target_time_per_item_sec,
    target_hourly_labor_pct) across rebuilds — same idiom as the labor_daily
    forecast_exclude preservation. Skips blank cells and formula strings; keeps
    only parseable numbers. Returns {} if the tab/column is absent.
    """
    out: dict[str, float] = {}
    if not grid or len(grid) <= 1:
        return out
    header = grid[0]
    if "date" not in header or col_name not in header:
        return out
    date_i = header.index("date")
    col_i = header.index(col_name)
    for row in grid[1:]:
        if len(row) <= max(date_i, col_i):
            continue
        iso = coerce_iso_date(row[date_i])
        if iso is None:
            continue
        raw = str(row[col_i]).strip()
        if not raw or raw.startswith("="):
            continue
        try:
            out[iso] = float(raw)
        except (ValueError, TypeError):
            continue
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


def _period_length_days(pay_frequency: str) -> int:
    """Map a store-profile pay_frequency string to a period length in days.

    Only "Biweekly" (14-day) is supported today — it's the only frequency
    Palmetto (or any current store) uses. Any other value raises a clear
    error rather than silently guessing, so a future weekly/semi-monthly
    store fails loudly at the point the assumption breaks.
    """
    freq = (pay_frequency or "").strip().lower()
    if freq == "biweekly":
        return 14
    raise ValueError(
        f"discover_periods: unsupported pay_frequency {pay_frequency!r}. "
        f"Only 'Biweekly' (14-day) is implemented. Add the mapping in "
        f"_period_length_days() or fix the store profile's "
        f"adp_run.pay_frequency."
    )


def discover_periods(
    *,
    anchor_end_date: str,
    pay_frequency: str,
    data_start: str,
    last_data_date: str,
) -> list[dict]:
    """Derive canonical pay periods ALGORITHMICALLY from the store profile.

    Pay periods are fixed-length windows anchored on a known period END
    date (``adp_run.pay_periods_anchor_end_date`` in the store profile).
    A period ENDS on ``anchor_end_date`` and on every ±period_len days from
    there; each period spans ``[end - (period_len - 1), end]`` inclusive.

    We emit every COMPLETED period (``end <= last_data_date``) whose window
    overlaps the data window ``[data_start, last_data_date]`` — i.e. with
    ``end >= data_start``. The trailing in-progress/open period (from the
    last completed period's end+1 through ``last_data_date``) is appended
    separately by ``append_open_period``.

    Deriving periods from the profile — instead of from each shift's
    ``pay_period`` text field — makes the period tabs robust to raw rows
    that carry a blank ``pay_period`` (the field was dropped during
    aggregation in older backfills, and we no longer re-scrape ADP to
    repopulate it). Downstream period builders assign each shift/txn to a
    period purely by date membership in ``[start, end]``, so the windows
    just need to be the correct biweekly calendar — which is exactly what
    the anchor + frequency give us.

    Args:
        anchor_end_date: ISO date a pay period is known to END on.
        pay_frequency: store profile pay frequency (only "Biweekly" today).
        data_start: ISO date of the earliest day with any data.
        last_data_date: ISO date of the latest complete data day.

    Returns period dicts sorted ascending by start, each with the keys
    downstream builders expect: ``start``, ``end``, ``check_dates``,
    ``variants``, ``is_open``.
    """
    period_len = _period_length_days(pay_frequency)
    anchor_end = datetime.date.fromisoformat(anchor_end_date)
    data_start_d = datetime.date.fromisoformat(data_start)
    last_data_d = datetime.date.fromisoformat(last_data_date)

    # Largest period end that is <= last_data_date. The profile anchor may
    # sit in the future relative to the data window (anchored to a period
    # that hasn't closed yet) OR in the past; floor-division snaps it onto
    # the latest completed period boundary either way.
    k = (last_data_d - anchor_end).days // period_len
    end = anchor_end + datetime.timedelta(days=period_len * k)

    periods: list[dict] = []
    while end >= data_start_d:
        start = end - datetime.timedelta(days=period_len - 1)
        periods.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "check_dates": [],
            "variants": [{"start": start.isoformat(), "end": end.isoformat()}],
            "is_open": False,
        })
        end -= datetime.timedelta(days=period_len)

    periods.reverse()
    return periods


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


def actual_cc_tips_by_period(earnings: list[dict] | None) -> dict[tuple, dict[str, int]]:
    """Extract ADP 'Credit Card Tips Owed' actuals grouped by period.

    Returns empty dict when earnings data is not available, allowing
    downstream code to handle missing actuals gracefully.
    """
    if not earnings:
        return {}
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
# tunables — the operator can edit these in-sheet and subsequent refreshes
# will echo back whatever they set. Defaults seed on first run.
LABOR_TUNABLE_KEYS = (
    "saturation_orders_per_labor_hour",
    "forecast_target_labor_pct",
    "forecast_target_hourly_labor_pct",
    "forecast_fulltime_weekly_hours",
    "forecast_target_completion_time_per_item_sec",
    "forecast_outlier_window_weeks",
    "forecast_outlier_z_threshold",
)

# Orders / hourly-labor-hour above which we flag the day as "OVER" capacity.
# 10 is a reasonable starting point for a specialty coffee bar (~6 minutes per
# completed order per barista hour). The operator tunes this in-sheet by
# eyeballing busy vs slow days; this is just the seed.
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
    store_sat_default = str(
        profile.get("labor_config", {}).get(
            "saturation_orders_per_labor_hour", DEFAULT_SATURATION_THRESHOLD
        )
    )
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
        ["data_window_start",
         _iso_date_for_sheet_cell(profile["calibration"]["first_data_window"]["start"]),
         "Square data starts here. Pay periods before this are partial."],
        ["data_window_end", _iso_date_for_sheet_cell(last_data_date),
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
         _iso_date_for_sheet_cell(
             review_tunables.get("review_bonus_started_date", "2026-05-11")
         ),
         "Reviews on or after this date are eligible for shoutout/base bonuses."],
        ["review_base_bonus_dollars",
         review_tunables.get("review_base_bonus_dollars", "10"),
         "Per-person bonus on a no-shoutout 5★ review (every non-excluded shift member)."],
        ["review_named_bonus_dollars",
         review_tunables.get("review_named_bonus_dollars", "20"),
         "Per-person bonus on a shoutout review (only the named people; overrides exclusions)."],
        # ── Labor saturation tuning ──
        # labor_daily / labor_period / labor_weekly emit an `over_saturation`
        # flag — "OVER" when orders_per_labor_hour (hourly bucket only)
        # exceeds this threshold, "ok" otherwise. Forward-looking: this is
        # the "am I approaching the throughput wall? do I add a shift?"
        # signal, not historical accounting. Tune by watching busy vs slow
        # days; conditional-format "OVER" red in-sheet for at-a-glance use.
        ["saturation_orders_per_labor_hour",
         labor_tunables.get(
             "saturation_orders_per_labor_hour",
             store_sat_default,
         ),
         "Orders/hourly-labor-hour above which the day is flagged 'OVER'. "
         "Seed default comes from store profile labor_config "
         "(code fallback = 10 if profile omits it). "
         "Raise if your hourly staff have idle time at this rate; "
         "lower if lines/wait grow at this rate. Only hourly labor counts "
         "toward the denominator (full-timers like managers don't add bar "
         "throughput)."],
        # ── Forecast/staffing solver tuning ──
        ["forecast_target_labor_pct",
         labor_tunables.get("forecast_target_labor_pct", "0.25"),
         "Total labor cost / net sales target for the staffing solver ceiling. "
         "Default 25%."],
        ["forecast_target_hourly_labor_pct",
         labor_tunables.get("forecast_target_hourly_labor_pct", "0.20"),
         "Hourly (part-time-only) labor cost / net sales target — EXCLUDES "
         "Lindsay's full-time cost. Drives the forecast tab's "
         "hourly_staffing_flag (OVER_HOURLY_BUDGET when hourly_labor_pct "
         "exceeds this). Seeded per forecast row, editable per row. Default 20%."],
        ["forecast_fulltime_weekly_hours",
         labor_tunables.get("forecast_fulltime_weekly_hours", "40"),
         "Full-time (manager) weekly hour cap for forecast allocation."],
        ["forecast_target_completion_time_per_item_sec",
         labor_tunables.get(
             "forecast_target_completion_time_per_item_sec",
             str(profile.get("labor_config", {}).get(
                 "forecast_target_completion_time_per_item_sec", 420)),
         ),
         "Flat staffing-solver target prep time per item in seconds (7 min default). "
         "Drives forecast efficiency_hours (per forecast row) AND the operational "
         "kds_pct_items_over_goal threshold. NOT derived from observed KDS."],
        ["forecast_outlier_window_weeks",
         labor_tunables.get("forecast_outlier_window_weeks", "8"),
         "Trailing window (weeks) of trend-aware residuals the robust outlier "
         "detector computes its median/MAD dispersion over. Default 8."],
        ["forecast_outlier_z_threshold",
         labor_tunables.get("forecast_outlier_z_threshold", "2.5"),
         "Robust-z threshold for outlier_flag (both directions) and the "
         "DOWN-only forecast_exclude auto-default (anomalous lows: stock-out / "
         "early-close). Higher = more tolerant. Default 2.5."],
    ]
    # Echo training exclusions verbatim so they survive the config-tab rewrite.
    # USERS: edit these rows directly in Google Sheets. Set the date to the
    # LAST training shift (inclusive). After training ends, either delete the
    # row or set the date to a day before the employee's first non-training
    # shift. Format: training_excluded:<Last, First>  <YYYY-MM-DD>
    for name in sorted(training_through.keys()):
        rows.append([
            f"{TRAINING_EXCLUDED_PREFIX}{name}",
            _iso_date_for_sheet_cell(training_through[name]),
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
    now_ct: datetime.datetime | None = None,
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
    # Same in-progress filter as build_labor_daily_rows — see that function's
    # comment for the rationale. The `daily` tab is the per-day store-inputs
    # source-of-truth that tip_alloc_daily references for `team_hours` and
    # `pool_cents`; emitting a partial-day row here would silently propagate
    # into the per-employee tip allocation drill-down.
    all_dates = [
        d for d in all_dates
        if is_refresh_date_complete(datetime.date.fromisoformat(d), now_ct=now_ct)
    ]
    all_dates = _fill_calendar_dates(all_dates)
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
            _iso_date_for_sheet_cell(d), dow,
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

    Used for peak-hour saturation: a 06:27 → 13:48 shift contributes 0.55 hr
    to hour-6, 1.0 hr to hours 7-12, 0.80 hr to hour-13.

    Returns {clock_hour: labor_hours_in_that_hour}. Returns {} for malformed
    inputs (empty strings, unparseable HH:MM) so a single bad shift doesn't
    nuke the day. BHAGA closes well before midnight, so overnight wraps are
    treated as "ignore" rather than +24h — if a shift later happens to run
    past midnight we'd revisit this; logging would surface it.
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
        next_hour_boundary = (hour + 1) * 60
        slice_end = min(end, next_hour_boundary)
        by_hour[hour] = by_hour.get(hour, 0.0) + (slice_end - cur) / 60.0
        cur = slice_end
    return by_hour


def _peak_hour_orders_per_labor_hour(
    *,
    hourly_labor_by_clock_hour: dict[int, float],
    orders_by_clock_hour: dict[int, int],
) -> float | str:
    """For one date: worst hour's orders/hourly-labor-hour saturation.

    "Worst" = max ratio over clock hours where hourly labor > 0. Hours with
    zero hourly labor are skipped (we're not open or no hourly staff was
    on; computing infinity would be misleading). Hours with zero orders
    contribute 0 to the max (still considered, they just aren't peaks).

    Returns "" if no hour qualifies (no hourly labor scheduled all day,
    or no completed orders all day).
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
    now_ct: datetime.datetime | None = None,
    items_by_date: dict[str, dict] | None = None,
    kds_by_date: dict[str, dict] | None = None,
    existing_forecast_exclude: dict[str, bool] | None = None,
    outlier_window_weeks: float = 8,
    outlier_z_threshold: float = 2.5,
    kds_goal_sec: float = 420.0,
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
      | orders                                  ← completed Square txns only
      | hourly_hours | hourly_labor_cost
      | fulltime_hours | fulltime_labor_cost
      | total_labor_cost
      | hourly_pct_of_net_sales | hourly_pct_of_net_sales_plus_tips
      | fulltime_pct_of_net_sales | fulltime_pct_of_net_sales_plus_tips
      | total_labor_pct_of_net_sales | total_labor_pct_of_net_sales_plus_tips
      | tips_pct_of_net_sales | all_in_cost_pct_of_net_sales_plus_tips
      | hourly_labor_per_order                   ← $/order, hourly bucket
      | fulltime_labor_per_order                 ← $/order, fulltime bucket
      | total_labor_per_order                    ← $/order, all labor
      | orders_per_labor_hour                    ← orders / hourly_hours
      | peak_hour_orders_per_labor_hour          ← worst-hour saturation
      | over_saturation                          ← "OVER" if >threshold else "ok"

    `orders_per_labor_hour` denominator is HOURLY labor only — full-timers
    like managers don't add bar throughput, so they're excluded from the
    saturation view. The numerator is completed Square transactions
    (event_type=="Payment"; refunds excluded, matching net_sales).

    `peak_hour_orders_per_labor_hour` spreads each hourly shift across the
    clock hours it covered (06:27→13:48 → 0.55h to hour-6, 1.0h to hours
    7-12, 0.80h to hour-13) and reports the worst hour's ratio. This is
    the actionable column for "add a shift during the 11am-1pm rush"
    decisions: a day with a flat 6 orders/hr-hour average could still
    have a 14 orders/hr-hour peak hidden inside it.

    `over_saturation` flips to "OVER" when orders_per_labor_hour exceeds
    the threshold from config (`saturation_orders_per_labor_hour`); the
    operator conditional-formats this red in-sheet for at-a-glance use.
    Both columns are blank ("") when there's no hourly labor or no orders
    that day, rather than div-by-zero.
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
    # consistency. Built from shift in_time/out_time (HH:MM strings from
    # ADP). Used only for peak_hour_orders_per_labor_hour below.
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
            # peak-hour view. ADP only tracks one in/out pair per row, so
            # split shifts would underrepresent labor hours mid-day; in
            # practice BHAGA shifts are contiguous and the punches tab
            # (where split shifts ARE separated) is overkill here.
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
        d = t.get("date_local") or ""
        if not d:
            continue
        hour = t.get("hour_local")
        if hour is None or hour == "":
            continue
        day_map = orders_by_date_hour.setdefault(d, {})
        day_map[int(hour)] = day_map.get(int(hour), 0) + 1

    all_dates = sorted(set(sales.keys()) | set(daily.keys()))
    # Drop any in-progress date (today_ct before 21:00 CT shop-close buffer,
    # or any future date). Without this filter, a refresh that runs mid-day
    # would publish a half-day labor row whose orders / hourly_hours are a
    # partial snapshot — the very bug that motivated the marker-dir + gate
    # changes in daily_refresh.py. `now_ct` is wired through for tests so
    # they can assert behavior at boundary times without depending on
    # wall-clock.
    all_dates = [
        d for d in all_dates
        if is_refresh_date_complete(datetime.date.fromisoformat(d), now_ct=now_ct)
    ]
    all_dates = _fill_calendar_dates(all_dates)
    items_by_date = items_by_date or {}
    kds_by_date = kds_by_date or {}
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
        "hours_per_order", "avg_order_price", "avg_net_sales_plus_tips_per_order",
        "items_sold", "avg_items_per_order", "hours_per_item", "avg_item_price",
        "hourly_hours_per_order", "fulltime_hours_per_order",
        "hourly_hours_per_item", "fulltime_hours_per_item",
        "kds_completed_tickets", "kds_completed_items",
        "kds_median_time_per_item_sec",
        "kds_p90_time_per_item_sec", "kds_p95_time_per_item_sec",
        "kds_p99_time_per_item_sec",
        "kds_pct_items_over_goal", "kds_pct_tickets_late",
        # Forecast-input helper columns (appended last so weekly/period
        # builders that index labor_daily by fixed position are unaffected):
        #   outlier_flag     — Python-computed, BOTH directions; |robust_z| of
        #                      the trend-aware residual exceeds the configured
        #                      z-threshold. Informational only.
        #   forecast_exclude — operator-editable boolean. Auto-defaults TRUE on
        #                      new rows ONLY for anomalous LOWS (down-outliers:
        #                      stock-out / early-close / closed days), never for
        #                      growth. Any existing operator value in the sheet
        #                      is PRESERVED across rebuilds (see
        #                      existing_forecast_exclude). The forecast tab's
        #                      order-seed excludes TRUE days.
        "outlier_flag", "forecast_exclude",
    ]
    existing_forecast_exclude = existing_forecast_exclude or {}
    # Trend-aware, robust outlier detection over the operating days. The
    # expected order count for each day comes from the same weighted-DOW +
    # trend model the live seed uses, so a growth run is absorbed into the
    # expectation instead of read as a string of upward "outliers". Residuals
    # are scored with a robust median/MAD z over the trailing window; only
    # anomalous LOWS auto-exclude (the operator's "we had to close shop /
    # ran out of stock" days). Zero-order days carry no demand signal — they're
    # kept OUT of the residual stats (so they can't pollute the dispersion) and
    # handled directly below as down-outliers (Palmetto operates 7 days/week,
    # so a 0-order complete day is a closure/stock-out, not a normal closed
    # DOW; the codebase models no scheduled closures, hence this choice).
    orders_by_date: dict[str, int] = {
        d: int(sales.get(d, {}).get("order_count", 0) or 0) for d in all_dates
    }
    operating_days = [
        {"date": d, "orders": o} for d, o in orders_by_date.items() if o > 0
    ]
    outlier_stats = compute_outlier_stats(
        operating_days,
        window_weeks=int(outlier_window_weeks),
        z_threshold=float(outlier_z_threshold),
    )
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

        orders_per_hr = round(orders / hourly_h, 1) if hourly_h > 0 else ""

        peak_hour = _peak_hour_orders_per_labor_hour(
            hourly_labor_by_clock_hour=hourly_labor_by_date_hour.get(d, {}),
            orders_by_clock_hour=orders_by_date_hour.get(d, {}),
        )

        if isinstance(orders_per_hr, (int, float)) and saturation_threshold > 0:
            over = "OVER" if orders_per_hr > saturation_threshold else "ok"
        else:
            over = ""

        hours_per_order = round(total_h / orders, 3) if orders > 0 else ""
        avg_order_price = round(net / orders, 2) if orders > 0 else ""
        avg_npt_per_order = round(net_plus_tips / orders, 2) if orders > 0 else ""

        item_day = items_by_date.get(d, {})
        items_sold = int(item_day.get("items_sold", 0) or 0)
        avg_items_per_order = round(items_sold / orders, 2) if orders > 0 and items_sold > 0 else ""
        hours_per_item = round(total_h / items_sold, 3) if items_sold > 0 else ""
        avg_item_price = round(item_day.get("gross_sales_cents", 0) / 100 / items_sold, 2) if items_sold > 0 else ""

        kds_day = kds_by_date.get(d, {})
        kds_tickets = kds_day.get("completed_tickets", "")
        # A day with zero completed KDS tickets (e.g. a closed day whose only
        # tickets were below the 15s floor) carries no meaningful time metrics
        # — blank them all so they read as "no data" rather than a misleading
        # 0.0 (and so weekly/period don't aggregate a spurious zero).
        # completed_tickets==0 comes through as the int 0.
        _has_kds = bool(kds_tickets)
        kds_items = kds_day.get("completed_items", "") if _has_kds else ""
        kds_med_tpi = kds_day.get("median_time_per_item_sec", "") if _has_kds else ""
        kds_p90_tpi = kds_day.get("p90_time_per_item_sec", "") if _has_kds else ""
        kds_p95_tpi = kds_day.get("p95_time_per_item_sec", "") if _has_kds else ""
        kds_p99_tpi = kds_day.get("p99_time_per_item_sec", "") if _has_kds else ""
        kds_pct_late = kds_day.get("pct_tickets_late", "") if _has_kds else ""
        if isinstance(kds_pct_late, float) and kds_pct_late > 0:
            kds_pct_late = f"{kds_pct_late:.2%}"
        elif kds_pct_late == 0.0:
            kds_pct_late = "0.00%"
        # kds_pct_items_over_goal: share of ITEMS whose per-item time exceeded
        # the flat config goal. Computed HERE (model/config layer) from the raw
        # item-weighted distribution so changing the goal recomputes on rebuild
        # without re-backfilling kds_daily.
        _per_item = kds_day.get("per_item_times_json", []) if _has_kds else []
        if isinstance(_per_item, list) and _per_item:
            _over = sum(1 for x in _per_item if float(x) > kds_goal_sec)
            kds_pct_over = f"{(_over / len(_per_item)):.2%}"
        else:
            kds_pct_over = ""

        # Outlier + forecast_exclude (written as TRUE/FALSE text literals;
        # USER_ENTERED coerces them to native booleans / checkbox values).
        if orders <= 0:
            # Closed / no-sales complete day: a down-outlier by definition.
            is_outlier = True
            exclude_default = True
        else:
            stat = outlier_stats.get(d)
            is_outlier = bool(stat["outlier_flag"]) if stat else False
            exclude_default = bool(stat["exclude_default"]) if stat else False
        # Preserve operator edits; new rows default to the DOWN-only auto-exclude.
        fe_val = existing_forecast_exclude.get(d, exclude_default)
        _outlier_flag = "TRUE" if is_outlier else "FALSE"
        _forecast_exclude = "TRUE" if fe_val else "FALSE"

        rows.append([
            _iso_date_for_sheet_cell(d),
            datetime.date.fromisoformat(d).strftime("%a"),
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
            hours_per_order,
            avg_order_price,
            avg_npt_per_order,
            items_sold if items_sold > 0 else "",
            avg_items_per_order,
            hours_per_item,
            avg_item_price,
            round(hourly_h / orders, 3) if orders > 0 else "",
            round(b["ex_h"] / orders, 3) if orders > 0 else "",
            round(hourly_h / items_sold, 3) if items_sold > 0 else "",
            round(b["ex_h"] / items_sold, 3) if items_sold > 0 else "",
            kds_tickets if kds_tickets else "",
            kds_items if kds_items else "",
            round(kds_med_tpi, 1) if isinstance(kds_med_tpi, (int, float)) else "",
            round(kds_p90_tpi, 1) if isinstance(kds_p90_tpi, (int, float)) else "",
            round(kds_p95_tpi, 1) if isinstance(kds_p95_tpi, (int, float)) else "",
            round(kds_p99_tpi, 1) if isinstance(kds_p99_tpi, (int, float)) else "",
            kds_pct_over if kds_pct_over else "",
            kds_pct_late if kds_pct_late else "",
            _outlier_flag,
            _forecast_exclude,
        ])
    return rows


class _KdsAccumulator:
    """Pools daily KDS item distributions so weekly/period rows recompute the
    per-item percentiles + over-goal share EXACTLY (NOT an average-of-averages).

    Exactness:
      * median / p90 / p95 / p99 — EXACT: the per-day item-weighted per-item
        seconds are POOLED (concatenated) and re-percentiled over the whole
        week/period, which is a TRUE percentile of the pooled distribution.
      * pct_items_over_goal = Σ(items over goal) / Σ(items)        — EXACT
      * pct_tickets_late = Σ(late_tickets) / Σ(due_tickets)        — EXACT

    `add(date_iso)` folds in one day from the `kds_by_date` dict (no-op if the
    date has no KDS row). `emit_*` return the rounded value / percent string, or
    "" when there's no data (avoids div-by-zero and blank-week noise). The goal
    for the over-goal share is the flat config target, injected by the caller.
    """

    def __init__(self, kds_by_date: dict[str, dict], goal_sec: float):
        self._kds = kds_by_date or {}
        self.goal = float(goal_sec)
        self.late = 0
        self.due = 0
        self.times: list[float] = []
        self._sorted: list[float] | None = None

    def add(self, date_iso: str) -> None:
        k = self._kds.get(date_iso)
        if not k:
            return
        self.late += int(k.get("late_tickets", 0) or 0)
        self.due += int(k.get("due_tickets", 0) or 0)
        pit = k.get("per_item_times_json")
        if isinstance(pit, list) and pit:
            self.times.extend(float(x) for x in pit)
            self._sorted = None

    def _sorted_times(self) -> list[float]:
        if self._sorted is None:
            self._sorted = sorted(self.times)
        return self._sorted

    def _emit_pct(self, q: float):
        if not self.times:
            return ""
        return round(transactions_backend._percentile(self._sorted_times(), q), 1)

    def emit_median_time_per_item(self):
        return self._emit_pct(50)

    def emit_p90_time_per_item(self):
        return self._emit_pct(90)

    def emit_p95_time_per_item(self):
        return self._emit_pct(95)

    def emit_p99_time_per_item(self):
        return self._emit_pct(99)

    def emit_pct_items_over_goal(self) -> str:
        if not self.times:
            return ""
        over = sum(1 for x in self.times if x > self.goal)
        return f"{(over / len(self.times)):.2%}"

    def emit_pct_late(self) -> str:
        if self.due <= 0:
            return ""
        return f"{(self.late / self.due):.2%}"


def build_labor_period_rows(
    *,
    periods: list[dict],
    labor_daily_rows: list[list],
    saturation_threshold: float = DEFAULT_SATURATION_THRESHOLD,
    kds_by_date: dict[str, dict] | None = None,
    kds_goal_sec: float = 420.0,
) -> list[list]:
    """Aggregate labor_daily rows by pay period.

    Sums the raw $/hour/order columns across the days in each period, then
    recomputes percentages and per-order ratios from those sums (NOT an
    average of per-day percentages — that would mis-weight low-sales days
    against high-sales ones and produce a meaningless number).

    `periods` come from discover_periods() + append_open_period() so the
    open in-progress period is included as the last row, flagged via
    `is_open=Y`. Helps the operator see "where we're trending" before
    the period closes.

    Columns — mirrors labor_daily but with period start/end:
      pay_period_start | pay_period_end | is_open | days_covered
      gross_sales | discounts | net_sales | tip_pool | net_sales_plus_tips
      orders                                  ← sum of completed orders
      hourly_hours | hourly_labor_cost
      fulltime_hours | fulltime_labor_cost
      total_labor_cost
      hourly_pct_of_net_sales | hourly_pct_of_net_sales_plus_tips
      fulltime_pct_of_net_sales | fulltime_pct_of_net_sales_plus_tips
      total_labor_pct_of_net_sales | total_labor_pct_of_net_sales_plus_tips
      tips_pct_of_net_sales | all_in_cost_pct_of_net_sales_plus_tips
      hourly_labor_per_order | fulltime_labor_per_order | total_labor_per_order
      orders_per_labor_hour | peak_hour_orders_per_labor_hour
      over_saturation

    `orders_per_labor_hour` at period grain is aggregate-then-ratio
    (sum(orders)/sum(hourly_hours)), NOT an average of daily ratios — that
    would mis-weight low-sales days against high-sales ones. Same for
    labor_per_order columns.

    `peak_hour_orders_per_labor_hour` aggregates as MAX of the daily peaks:
    the worst hour we observed anywhere in this period. That's the "is any
    hour in this period a staffing alarm" view; averaging or summing peaks
    would dilute the actionable signal.
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
        "hours_per_order", "avg_order_price", "avg_net_sales_plus_tips_per_order",
        "items_sold", "avg_items_per_order", "hours_per_item", "avg_item_price",
        "hourly_hours_per_order", "fulltime_hours_per_order",
        "hourly_hours_per_item", "fulltime_hours_per_item",
        "kds_completed_tickets", "kds_completed_items",
        "kds_median_time_per_item_sec",
        "kds_p90_time_per_item_sec", "kds_p95_time_per_item_sec",
        "kds_p99_time_per_item_sec",
        "kds_pct_items_over_goal", "kds_pct_tickets_late",
    ]
    if len(labor_daily_rows) <= 1:
        return [header]

    # Index labor_daily by date for fast period-bucketing. Field positions
    # match the header in build_labor_daily_rows: date=0, dow=1, gross=2,
    # disc=3, net=4, pool=5, net_plus_tips=6, orders=7, hourly_hours=8,
    # hourly_cost=9, fulltime_hours=10, fulltime_cost=11,
    # peak_hour_orders_per_labor_hour=25, items_sold=30, avg_item_price=33,
    # KDS: kds_completed_tickets=38, kds_completed_items=39 (the per-item
    # percentile / over-goal / late metrics are POOLED from kds_by_date via
    # _KdsAccumulator, not read from these fixed labor_daily positions).
    # `row[0]` is apostrophe-prefixed for Sheets text-literal rendering
    # (`'2026-05-20`); route through coerce_iso_date so the key matches
    # the plain ISO produced by `cursor.isoformat()` below.
    daily_by_date: dict[str, dict[str, float]] = {}
    for row in labor_daily_rows[1:]:
        key = coerce_iso_date(row[0]) or row[0]
        daily_by_date[key] = {
            "gross": float(row[2]),
            "disc": float(row[3]),
            "net": float(row[4]),
            "pool": float(row[5]),
            "orders": int(row[7] or 0),
            "hourly_h": float(row[8]),
            "hourly_c": float(row[9]),
            "fulltime_h": float(row[10]),
            "fulltime_c": float(row[11]),
            "peak": (float(row[25]) if row[25] != "" else None),
            "items_sold": int(row[30]) if len(row) > 30 and row[30] != "" else 0,
            "item_gross_dollars": (
                float(row[33]) * int(row[30])
                if len(row) > 33 and row[33] != "" and row[30] != ""
                else 0.0
            ),
            "kds_tickets": int(row[38]) if len(row) > 38 and row[38] != "" else 0,
            "kds_items": int(row[39]) if len(row) > 39 and row[39] != "" else 0,
        }

    kds_by_date = kds_by_date or {}
    rows: list[list] = [header]
    for p in periods:
        start = p["start"]
        end = p["end"]
        is_open = "Y" if p.get("is_open") else "N"
        gross = disc = net = pool = h_hours = h_cost = ft_hours = ft_cost = 0.0
        orders = 0
        days = 0
        items_sold_sum = 0
        item_gross_sum = 0.0
        kds_tickets_sum = 0
        kds_items_sum = 0
        kds = _KdsAccumulator(kds_by_date, kds_goal_sec)
        peak_max: float | None = None
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
                items_sold_sum += bucket["items_sold"]
                item_gross_sum += bucket["item_gross_dollars"]
                kds_tickets_sum += bucket["kds_tickets"]
                kds_items_sum += bucket["kds_items"]
                # Pool the raw daily KDS intermediates for this date so the
                # period-grain per-item/late metrics are recomputed exactly.
                kds.add(iso)
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
        peak_out: float | str = round(peak_max, 1) if peak_max is not None else ""
        if isinstance(orders_per_hr, (int, float)) and saturation_threshold > 0:
            over = "OVER" if orders_per_hr > saturation_threshold else "ok"
        else:
            over = ""

        p_total_h = h_hours + ft_hours
        rows.append([
            _iso_date_for_sheet_cell(start),
            _iso_date_for_sheet_cell(end),
            is_open, days,
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
            round(p_total_h / orders, 3) if orders > 0 else "",
            round(net / orders, 2) if orders > 0 else "",
            round(net_plus_tips / orders, 2) if orders > 0 else "",
            items_sold_sum if items_sold_sum > 0 else "",
            round(items_sold_sum / orders, 2) if orders > 0 and items_sold_sum > 0 else "",
            round(p_total_h / items_sold_sum, 3) if items_sold_sum > 0 else "",
            round(item_gross_sum / items_sold_sum, 2) if items_sold_sum > 0 else "",
            round(h_hours / orders, 3) if orders > 0 else "",
            round(ft_hours / orders, 3) if orders > 0 else "",
            round(h_hours / items_sold_sum, 3) if items_sold_sum > 0 else "",
            round(ft_hours / items_sold_sum, 3) if items_sold_sum > 0 else "",
            kds_tickets_sum if kds_tickets_sum > 0 else "",
            kds_items_sum if kds_items_sum > 0 else "",
            kds.emit_median_time_per_item(),    # EXACT (pooled item distribution)
            kds.emit_p90_time_per_item(),       # EXACT pooled p90
            kds.emit_p95_time_per_item(),       # EXACT pooled p95
            kds.emit_p99_time_per_item(),       # EXACT pooled p99
            kds.emit_pct_items_over_goal(),     # EXACT Σitems_over_goal/Σitems
            kds.emit_pct_late(),                # EXACT Σlate/Σdue
        ])
    return rows


def build_labor_weekly_rows(
    *,
    labor_daily_rows: list[list],
    saturation_threshold: float = DEFAULT_SATURATION_THRESHOLD,
    kds_by_date: dict[str, dict] | None = None,
    kds_goal_sec: float = 420.0,
) -> list[list]:
    """Aggregate labor_daily rows by ISO calendar week (Monday → Sunday).

    Why Monday-Sunday: Square's dashboard defaults to this convention, ISO
    8601 mandates it, and most restaurant industry "weekly labor%" reports
    use it (Sunday is part of the week ending that day, not the start of
    the next one). To flip to Sunday-Saturday, change `cursor.weekday()`
    to `(cursor.weekday() + 1) % 7` and adjust the +6 offset.

    Like build_labor_period_rows: aggregates raw $/hour/order totals and
    recomputes percentages and per-order ratios from those sums. Never
    averages per-day percentages.

    Includes the current (potentially partial) week as the last row with
    `is_partial=Y` and `days_covered < 7` so the operator can see the
    week-to-date trend. Closed weeks have `is_partial=N`.

    Columns — mirrors labor_period but keyed by ISO week. orders_per_labor_hour
    and labor-per-order ratios are aggregate-then-ratio across the week's
    days; peak_hour_orders_per_labor_hour is the MAX of the daily peaks
    observed within the week; over_saturation is "OVER" iff
    orders_per_labor_hour > config threshold.
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
        "hours_per_order", "avg_order_price", "avg_net_sales_plus_tips_per_order",
        "items_sold", "avg_items_per_order", "hours_per_item", "avg_item_price",
        "hourly_hours_per_order", "fulltime_hours_per_order",
        "hourly_hours_per_item", "fulltime_hours_per_item",
        "kds_completed_tickets", "kds_completed_items",
        "kds_median_time_per_item_sec",
        "kds_p90_time_per_item_sec", "kds_p95_time_per_item_sec",
        "kds_p99_time_per_item_sec",
        "kds_pct_items_over_goal", "kds_pct_tickets_late",
    ]
    if len(labor_daily_rows) <= 1:
        return [header]

    # Same field positions as labor_period (see build_labor_period_rows).
    # `row[0]` is apostrophe-prefixed; normalize back to plain ISO for the
    # dict key so the lookup against `cursor.isoformat()` / iso strings
    # downstream matches.
    daily_by_date: dict[str, dict[str, float]] = {}
    for row in labor_daily_rows[1:]:
        key = coerce_iso_date(row[0]) or row[0]
        daily_by_date[key] = {
            "gross": float(row[2]),
            "disc": float(row[3]),
            "net": float(row[4]),
            "pool": float(row[5]),
            "orders": int(row[7] or 0),
            "hourly_h": float(row[8]),
            "hourly_c": float(row[9]),
            "fulltime_h": float(row[10]),
            "fulltime_c": float(row[11]),
            "peak": (float(row[25]) if row[25] != "" else None),
            "items_sold": int(row[30]) if len(row) > 30 and row[30] != "" else 0,
            "item_gross_dollars": (
                float(row[33]) * int(row[30])
                if len(row) > 33 and row[33] != "" and row[30] != ""
                else 0.0
            ),
            "kds_tickets": int(row[38]) if len(row) > 38 and row[38] != "" else 0,
            "kds_items": int(row[39]) if len(row) > 39 and row[39] != "" else 0,
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

    kds_by_date = kds_by_date or {}
    rows: list[list] = [header]
    for monday in sorted(weeks):
        sunday = monday + datetime.timedelta(days=6)
        iso_year, iso_week, _ = monday.isocalendar()
        iso_label = f"{iso_year}-W{iso_week:02d}"
        is_partial = "Y" if sunday > max_data_date else "N"

        gross = disc = net = pool = h_hours = h_cost = ft_hours = ft_cost = 0.0
        orders = 0
        days = 0
        items_sold_sum = 0
        item_gross_sum = 0.0
        kds_tickets_sum = 0
        kds_items_sum = 0
        kds = _KdsAccumulator(kds_by_date, kds_goal_sec)
        peak_max: float | None = None
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
            items_sold_sum += bucket["items_sold"]
            item_gross_sum += bucket["item_gross_dollars"]
            kds_tickets_sum += bucket["kds_tickets"]
            kds_items_sum += bucket["kds_items"]
            # Pool the raw daily KDS intermediates for this date.
            kds.add(iso_date)
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
        peak_out: float | str = round(peak_max, 1) if peak_max is not None else ""
        if isinstance(orders_per_hr, (int, float)) and saturation_threshold > 0:
            over = "OVER" if orders_per_hr > saturation_threshold else "ok"
        else:
            over = ""

        # iso_label (e.g. "2026-W21") is left as a bare string: verified
        # via the Sheets MCP that "2026-Wnn" survives USER_ENTERED without
        # date-serial coercion (the "W" breaks any date parser). week_start
        # and week_end ARE plain ISO dates and DO get coerced, so they go
        # through _iso_date_for_sheet_cell.
        w_total_h = h_hours + ft_hours
        rows.append([
            iso_label,
            _iso_date_for_sheet_cell(monday.isoformat()),
            _iso_date_for_sheet_cell(sunday.isoformat()),
            is_partial, days,
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
            round(w_total_h / orders, 3) if orders > 0 else "",
            round(net / orders, 2) if orders > 0 else "",
            round(net_plus_tips / orders, 2) if orders > 0 else "",
            items_sold_sum if items_sold_sum > 0 else "",
            round(items_sold_sum / orders, 2) if orders > 0 and items_sold_sum > 0 else "",
            round(w_total_h / items_sold_sum, 3) if items_sold_sum > 0 else "",
            round(item_gross_sum / items_sold_sum, 2) if items_sold_sum > 0 else "",
            round(h_hours / orders, 3) if orders > 0 else "",
            round(ft_hours / orders, 3) if orders > 0 else "",
            round(h_hours / items_sold_sum, 3) if items_sold_sum > 0 else "",
            round(ft_hours / items_sold_sum, 3) if items_sold_sum > 0 else "",
            kds_tickets_sum if kds_tickets_sum > 0 else "",
            kds_items_sum if kds_items_sum > 0 else "",
            kds.emit_median_time_per_item(),    # EXACT (pooled item distribution)
            kds.emit_p90_time_per_item(),       # EXACT pooled p90
            kds.emit_p95_time_per_item(),       # EXACT pooled p95
            kds.emit_p99_time_per_item(),       # EXACT pooled p99
            kds.emit_pct_items_over_goal(),     # EXACT Σitems_over_goal/Σitems
            kds.emit_pct_late(),                # EXACT Σlate/Σdue
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
        has_actuals = bool(p["per_period_adp"])
        emps = sorted(set(p["per_period_ours"]) | set(p["per_period_adp"]))
        for emp in emps:
            ours_c = p["per_period_ours"].get(emp, 0)
            adp_c = p["per_period_adp"].get(emp, 0)
            hrs = p["per_period_hours"].get(emp, 0.0)
            diff_c = ours_c - adp_c
            pct = (diff_c / adp_c) if adp_c else None
            if has_actuals:
                adp_paid_val = round(adp_c / 100, 2)
                diff_val = round(diff_c / 100, 2)
                pct_val = f"{pct:+.1%}" if pct is not None else ("n/a" if p["is_open"] else "—")
                adp_per_hour = round((adp_c / 100 / hrs), 2) if hrs > 0 else 0
                reason = likely_reason(
                    ours_c=ours_c, adp_c=adp_c,
                    is_open=p["is_open"], coverage=p["coverage"],
                )
            else:
                adp_paid_val = "N/A"
                diff_val = "N/A"
                pct_val = "N/A"
                adp_per_hour = "N/A"
                reason = "No earnings data" if not p["is_open"] else "Open period — not yet paid"
            rows.append([
                _iso_date_for_sheet_cell(p["start"]),
                _iso_date_for_sheet_cell(p["end"]),
                p["coverage"], "yes" if p["is_open"] else "no",
                emp, round(hrs, 2),
                round(ours_c / 100, 2),
                adp_paid_val,
                diff_val,
                pct_val,
                round((ours_c / 100 / hrs), 2) if hrs > 0 else 0,
                adp_per_hour,
                reason,
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
                _iso_date_for_sheet_cell(a["date"]),
                dow,
                _iso_date_for_sheet_cell(p["start"]),
                _iso_date_for_sheet_cell(p["end"]),
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
        has_actuals = bool(p["per_period_adp"])
        team_hrs = sum(p["per_period_hours"].values())
        pool_c = sum(a["share_cents"] for a in p["per_day_allocations"])
        our_total_c = sum(p["per_period_ours"].values())
        adp_total_c = sum(p["per_period_adp"].values())
        diff_c = our_total_c - adp_total_c
        if has_actuals:
            flagged = sum(
                1 for emp in (set(p["per_period_ours"]) | set(p["per_period_adp"]))
                if abs(p["per_period_ours"].get(emp, 0) - p["per_period_adp"].get(emp, 0)) >= 100
            )
            adp_paid_val = round(adp_total_c / 100, 2)
            diff_val = round(diff_c / 100, 2)
            flagged_val = flagged
        else:
            adp_paid_val = "N/A"
            diff_val = "N/A"
            flagged_val = "N/A"
        rows.append([
            _iso_date_for_sheet_cell(p["start"]),
            _iso_date_for_sheet_cell(p["end"]),
            p["coverage"], "yes" if p["is_open"] else "no",
            ", ".join(p["check_dates"]) or ("(not yet paid)" if p["is_open"] else ""),
            len(set(p["per_period_ours"]) | set(p["per_period_adp"])),
            round(team_hrs, 2),
            round(pool_c / 100, 2),
            round(our_total_c / 100, 2),
            adp_paid_val,
            diff_val,
            flagged_val,
        ])
    return rows


# ---------- Driver ----------


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", required=True)
    cli.add_argument("--dry-run", action="store_true",
                     help="Print row counts per tab and the first few rows but do not write to Sheets.")
    cli.add_argument("--data-source", choices=["sheets", "bigquery"], default="sheets",
                     help="Where to read raw data from. 'sheets' (default) reads from Google Sheets; "
                          "'bigquery' reads from the bhaga dataset in jarvis-bhaga-prod.")
    args = cli.parse_args()

    # Bootstrap pointer + sheet-derived aliases/exclusions.
    profile = json.loads((STORE_PROFILE_DIR / f"{args.store}.json").read_text())
    from skills.store_profile import load_aliases, load_exclusions
    aliases = load_aliases(args.store)
    excluded = set(load_exclusions(args.store)["permanent"])
    shop_tz = profile["timezone"]["shop_tz"]
    model_sid = resolve_sheet_id("bhaga_model", profile)
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

    # ARCHITECTURE: model sheet reads canonical data from RAW SHEETS only
    # (default) OR from BigQuery when --data-source=bigquery.
    # The orchestrator (daily_refresh.py) is responsible for keeping the raw
    # sheets in sync with the latest local scrapes via tip_ledger_writer.
    #   * shifts/punches  ← BHAGA ADP Raw   > shifts
    #   * transactions    ← BHAGA Square Raw > transactions

    if args.data_source == "bigquery":
        from core.datastore_reader import read_shifts_bq, read_transactions_bq, read_wage_rates_bq
        print("# loading shifts from BigQuery (bhaga.adp_shifts)")
        shifts = read_shifts_bq()
        print("# loading wage_rates from BigQuery (bhaga.adp_wage_rates)")
        wage_rates = read_wage_rates_bq()
    else:
        adp_raw_sid = resolve_sheet_id("bhaga_adp_raw", profile)
        square_raw_sid = resolve_sheet_id("bhaga_square_raw", profile)
        print(f"# loading shifts from raw sheet {adp_raw_sid} (BHAGA ADP Raw > shifts)")
        shifts = read_raw_adp_shifts(adp_raw_sid, account=args.store)
        print(f"# loading wage_rates from raw sheet {adp_raw_sid} (BHAGA ADP Raw > wage_rates)")
        wage_rates = read_raw_adp_rates(adp_raw_sid, account=args.store)

    # Re-resolve employee names through the alias map. Raw-sheet data may
    # contain non-canonical names from backfill runs that predated alias
    # corrections (e.g. "Johnson, Dolce J" instead of "Johnson, Dolce").
    for rec in shifts:
        for key in ("employee_name", "employee_id"):
            if key in rec:
                rec[key] = normalize_employee_name(rec[key], aliases)
    for rec in wage_rates:
        for key in ("employee_name", "employee_id"):
            if key in rec:
                rec[key] = normalize_employee_name(rec[key], aliases)

    # Post-alias deduplication: alias resolution can collapse two raw names
    # into the same canonical employee_id, creating duplicate records for a
    # single (date, employee) pair. Keep only the first occurrence.
    _seen_shifts: set[tuple] = set()
    _deduped_shifts: list[dict] = []
    for rec in shifts:
        key = (rec.get("date"), rec.get("employee_id"))
        if key in _seen_shifts:
            continue
        _seen_shifts.add(key)
        _deduped_shifts.append(rec)
    _n_dup_shifts = len(shifts) - len(_deduped_shifts)
    if _n_dup_shifts:
        print(f"[dedup] removed {_n_dup_shifts} duplicate shift records after alias resolution")
    shifts = _deduped_shifts

    _seen_rates: set[str] = set()
    _deduped_rates: list[dict] = []
    for rec in wage_rates:
        key = rec.get("employee_id", "")
        if key in _seen_rates:
            continue
        _seen_rates.add(key)
        _deduped_rates.append(rec)
    _n_dup_rates = len(wage_rates) - len(_deduped_rates)
    if _n_dup_rates:
        print(f"[dedup] removed {_n_dup_rates} duplicate wage_rate records after alias resolution")
    wage_rates = _deduped_rates

    print(f"#   → {len(shifts)} shift rows")

    if args.data_source == "bigquery":
        print("# loading transactions from BigQuery (bhaga.square_transactions)")
        txns = read_transactions_bq()
    else:
        print(f"# loading transactions from raw sheet {square_raw_sid} (BHAGA Square Raw > transactions)")
        txns = read_raw_square_transactions(square_raw_sid, account=args.store)
    print(f"#   → {len(txns)} transaction rows")

    if args.data_source == "bigquery":
        # Item daily rollup not yet in BQ; skip gracefully
        item_rollup_rows = []
    else:
        print(f"# loading item daily rollup from raw sheet {square_raw_sid} (BHAGA Square Raw > item_daily_rollup)")
        try:
            item_rollup_rows = read_raw_square_item_daily_rollup(square_raw_sid, account=args.store)
        except Exception as exc:  # noqa: BLE001
            print(f"#   WARN: could not read item_daily_rollup (tab may not exist yet): {exc}")
            item_rollup_rows = []
    items_by_date: dict[str, dict] = {r["date_local"]: r for r in item_rollup_rows}
    print(f"#   → {len(items_by_date)} item-day rows")

    if not (shifts and txns):
        print(
            "Empty input: shifts={} txns={}. "
            "If raw sheets are empty, the orchestrator's write_raw_sheets step "
            "needs to run first (or run agents/bhaga/scripts/backfill_from_downloads.py manually).".format(
                len(shifts), len(txns)
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
    square_dates_covered = {t["date_local"] for t in txns}
    barista_shift_counts: dict[str, int] = {}
    for s in shifts:
        if s["employee_id"] in excluded:
            continue
        barista_shift_counts[s["date"]] = barista_shift_counts.get(s["date"], 0) + 1
    # A date is "covered" if Square has transactions for it. Zero-shift days
    # (store closed, holidays like Memorial Day) are legitimate — they get
    # zero-value rows via _fill_calendar_dates. We no longer require a minimum
    # barista shift count to advance data_window_end.
    both_covered = square_dates_covered
    # Drop in-progress dates from the cover-set. Even if both raw sources
    # happen to contain rows for today_ct (e.g. an earlier mid-day debug
    # run mirrored partial data into bhaga_adp_raw / bhaga_square_raw),
    # we MUST NOT advance data_window_end past the last complete day —
    # downstream tabs (labor_daily, labor_weekly, labor_period, the
    # `open_period` synthetic in tip_alloc_*) all read off last_data_date.
    # See agents.bhaga.scripts.daily_refresh.is_refresh_date_complete for
    # the definition of "complete".
    both_covered_complete = {
        d for d in both_covered
        if is_refresh_date_complete(datetime.date.fromisoformat(d))
    }
    in_progress_dropped = sorted(both_covered - both_covered_complete)
    if in_progress_dropped:
        print(
            f"# dropping in-progress dates from data_window_end candidates: "
            f"{', '.join(in_progress_dropped)} (today's pre-21:00-CT data is "
            f"partial — see is_refresh_date_complete)"
        )
    if not both_covered_complete:
        print(
            "ERROR: no COMPLETE date is covered by Square transactions. "
            f"square_dates={len(square_dates_covered)}, "
            f"in_progress_dropped={in_progress_dropped}. "
            "Raw sheets likely stale — run the orchestrator's write_raw_sheets step."
        )
        return 1
    last_data_date = max(both_covered_complete)

    # Surface ADP-incomplete dates that we deliberately excluded so the
    # operator can see why data_window_end did not advance to "today".
    zero_shift_recent = sorted(
        d for d in square_dates_covered
        if d <= last_data_date
        and barista_shift_counts.get(d, 0) == 0
    )
    if zero_shift_recent:
        print(
            f"# zero-shift days included in window (store closed / holiday): "
            + ", ".join(zero_shift_recent)
        )
    print(f"# last_data_date = {last_data_date}")

    # Period derivation is ALGORITHMIC (profile anchor + biweekly cadence),
    # not scraped from each shift's pay_period text field — see
    # discover_periods(). data_start spans the earliest day with any raw
    # data (shifts OR txns) so every overlapping biweekly window is emitted.
    _shift_dates = [s["date"] for s in shifts if s.get("date")]
    _txn_dates = [t["date_local"] for t in txns if t.get("date_local")]
    data_window_start = min(_shift_dates + _txn_dates)
    periods = discover_periods(
        anchor_end_date=profile["adp_run"]["pay_periods_anchor_end_date"],
        pay_frequency=profile["adp_run"].get("pay_frequency", ""),
        data_start=data_window_start,
        last_data_date=last_data_date,
    )
    periods = append_open_period(periods, last_data_date=last_data_date)
    actuals = actual_cc_tips_by_period(None)
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
    labor_tunables = {
        k: _read_config_value(spreadsheet_id=model_sid, store=args.store, key=k)
        for k in LABOR_TUNABLE_KEYS
    }
    labor_tunables = {k: v for k, v in labor_tunables.items() if v is not None}

    # Migrate stale default: if the in-sheet saturation threshold is still
    # the old code default (10) but the store profile has a different
    # calibrated value, adopt the store profile value. This handles the
    # one-time migration from the generic default to the per-store value
    # without overriding intentional operator edits.
    _sat_key = "saturation_orders_per_labor_hour"
    _store_profile_sat = profile.get("labor_config", {}).get(_sat_key)
    if _sat_key in labor_tunables and _store_profile_sat is not None:
        try:
            _in_sheet = float(labor_tunables[_sat_key])
            _from_profile = float(_store_profile_sat)
            if _in_sheet == DEFAULT_SATURATION_THRESHOLD and _from_profile != DEFAULT_SATURATION_THRESHOLD:
                labor_tunables[_sat_key] = str(_store_profile_sat)
                print(f"# migrating {_sat_key}: in-sheet was {_in_sheet} "
                      f"(old code default), store profile has {_store_profile_sat}")
        except (ValueError, TypeError):
            pass

    # Migrate the KDS/staffing target off the OLD 300s (5 min) default to the
    # current flat default (420s = 7 min). Same one-time pattern as saturation:
    # only bumps a value still at the old code default, never an intentional
    # operator edit to some other number.
    _tpi_key = "forecast_target_completion_time_per_item_sec"
    _OLD_TPI_DEFAULT = 300.0
    _new_tpi_default = float(
        profile.get("labor_config", {}).get(_tpi_key, 420)
    )
    if _tpi_key in labor_tunables:
        try:
            _in_sheet_tpi = float(labor_tunables[_tpi_key])
            if _in_sheet_tpi == _OLD_TPI_DEFAULT and _new_tpi_default != _OLD_TPI_DEFAULT:
                labor_tunables[_tpi_key] = str(_new_tpi_default)
                print(f"# migrating {_tpi_key}: in-sheet was {_in_sheet_tpi} "
                      f"(old 5 min default), bumping to {_new_tpi_default} (7 min)")
        except (ValueError, TypeError):
            pass

    store_sat_default = profile.get("labor_config", {}).get(
        "saturation_orders_per_labor_hour", DEFAULT_SATURATION_THRESHOLD,
    )
    try:
        saturation_threshold = float(
            labor_tunables.get(
                "saturation_orders_per_labor_hour",
                store_sat_default,
            )
        )
    except (TypeError, ValueError):
        print(
            f"# WARN: saturation_orders_per_labor_hour in config is "
            f"not a number ({labor_tunables.get('saturation_orders_per_labor_hour')!r}); "
            f"falling back to store default {store_sat_default}."
        )
        saturation_threshold = float(store_sat_default)
    print(f"# saturation threshold = {saturation_threshold} orders/hourly-labor-hour")

    config_rows = build_config_rows(
        profile, last_data_date,
        training_through=training_through,
        review_tunables=review_tunables,
        labor_tunables=labor_tunables,
    )
    daily_rows, daily_summary = build_daily_rows(
        txns=txns, shifts=shifts, excluded=excluded, training_through=training_through,
    )
    # Load KDS daily data from raw sheet (graceful fallback if tab doesn't exist yet)
    kds_by_date: dict[str, dict] = {}
    if args.data_source != "bigquery":
        try:
            from skills.tip_ledger_writer.reader import read_raw_kds_daily
            kds_rows = read_raw_kds_daily(square_raw_sid, account=args.store)
            kds_by_date = {r["date_local"]: r for r in kds_rows}
            print(f"#   → {len(kds_by_date)} KDS-day rows")
        except Exception as exc:  # noqa: BLE001
            print(f"#   WARN: could not read kds_daily (tab may not exist yet): {exc}")

    # Preserve operator-edited forecast_exclude flags from the existing
    # labor_daily tab so the nightly rebuild doesn't clobber their choices.
    existing_forecast_exclude = _read_existing_labor_daily_forecast_exclude(
        spreadsheet_id=model_sid, store=args.store,
    )
    if existing_forecast_exclude:
        _n_excl = sum(1 for v in existing_forecast_exclude.values() if v)
        print(f"#   → preserved forecast_exclude for {len(existing_forecast_exclude)} "
              f"existing labor_daily rows ({_n_excl} TRUE)")

    # Forecast config (incl. the trend-aware robust-outlier knobs) drives both
    # the labor_daily auto-exclusion default and the forecast tab below.
    from core.config_loader import get_forecast_config
    forecast_config = get_forecast_config(config_rows)
    # Flat config target doubles as the goal for kds_pct_items_over_goal — the
    # operational "X% of items took > 7 min" metric. Injected here (model/config
    # layer) so changing the goal recomputes on rebuild without re-backfilling.
    kds_goal_sec = float(forecast_config["forecast_target_completion_time_per_item_sec"])

    labor_daily_rows = build_labor_daily_rows(
        txns=txns, shifts=shifts, wage_rates=wage_rates,
        excluded_from_tip_pool=excluded,
        saturation_threshold=saturation_threshold,
        items_by_date=items_by_date,
        kds_by_date=kds_by_date,
        existing_forecast_exclude=existing_forecast_exclude,
        outlier_window_weeks=forecast_config["forecast_outlier_window_weeks"],
        outlier_z_threshold=forecast_config["forecast_outlier_z_threshold"],
        kds_goal_sec=kds_goal_sec,
    )
    labor_period_rows = build_labor_period_rows(
        periods=periods, labor_daily_rows=labor_daily_rows,
        saturation_threshold=saturation_threshold,
        kds_by_date=kds_by_date,
        kds_goal_sec=kds_goal_sec,
    )
    labor_weekly_rows = build_labor_weekly_rows(
        labor_daily_rows=labor_daily_rows,
        saturation_threshold=saturation_threshold,
        kds_by_date=kds_by_date,
        kds_goal_sec=kds_goal_sec,
    )
    period_rows = build_tip_alloc_period_rows(period_results)
    day_alloc_rows = build_tip_alloc_daily_rows(period_results, daily_summary)
    summary_rows = build_period_summary_rows(period_results)

    # ── Safety sentinel: no row may carry an in-progress date ────────
    # Defends against future regressions in the per-builder filters or in
    # last_data_date bounding. If labor_daily or daily emits a row whose
    # `date` is not yet complete (today_ct before 21:00 CT, or future),
    # we'd be re-publishing the original "partial today" bug — fail loud
    # before touching the Sheet rather than re-painting it. The check is
    # cheap and matches the round-trip sentinel pattern at the end of
    # main().
    for tab_name, rows in (
        ("daily", daily_rows),
        ("labor_daily", labor_daily_rows),
    ):
        for r in rows[1:]:
            # `r[0]` is now apostrophe-prefixed (e.g. "'2026-05-20") so the
            # builders' output renders as plain text in Sheets instead of
            # being coerced to date-serials. Route through coerce_iso_date
            # so the sentinel's filter still works against the underlying
            # ISO date.
            iso = coerce_iso_date(r[0])
            if iso is None:
                continue
            try:
                d = datetime.date.fromisoformat(iso)
            except (ValueError, TypeError):
                continue
            if not is_refresh_date_complete(d):
                raise AssertionError(
                    f"{tab_name} would emit a row for in-progress date {d.isoformat()}; "
                    "this means the per-builder in-progress filter or the "
                    "last_data_date bounding regressed. Refusing to write."
                )

    # labor_daily currency columns (0-indexed against build_labor_daily_rows
    # header): 2=gross_sales, 3=discounts, 4=net_sales, 5=tip_pool,
    # 6=net_sales_plus_tips, 9=hourly_labor_cost, 11=fulltime_labor_cost,
    # 12=total_labor_cost, 21=hourly_labor_per_order,
    # 22=fulltime_labor_per_order, 23=total_labor_per_order,
    # 28=avg_order_price, 29=avg_net_sales_plus_tips_per_order,
    # 33=avg_item_price.
    # (orders=7 is a count; orders_per_labor_hour=24, peak=25 are ratios;
    # over_saturation=26 is a string flag; hours_per_order=27 is a ratio;
    # items_sold=30 is a count; avg_items_per_order=31, hours_per_item=32 are ratios;
    # hourly_hours_per_order=34, fulltime_hours_per_order=35,
    # hourly_hours_per_item=36, fulltime_hours_per_item=37 are ratios;
    # kds_completed_tickets=38, kds_completed_items=39 are counts;
    # kds_median/p90/p95/p99_time_per_item_sec=40-43 are seconds;
    # kds_pct_items_over_goal=44, kds_pct_tickets_late=45 are percentage strings
    # — auto-detected by _percent_column_indices via the "pct" name heuristic.)
    labor_daily_currency = [2, 3, 4, 5, 6, 9, 11, 12, 21, 22, 23, 28, 29, 33]
    # labor_period adds 4 lead columns before the labor_daily layout, so
    # shift every labor_daily currency index by +2 (period header inserts
    # is_open + days_covered between date+dow and gross_sales).
    # KDS columns (40-44 in period) are counts/ratios/seconds, NOT currency.
    labor_period_currency = [4, 5, 6, 7, 8, 11, 13, 14, 23, 24, 25, 30, 31, 35]
    # labor_weekly adds 5 lead columns (iso_week + week_start + week_end +
    # is_partial + days_covered), so shift labor_daily currency by +3.
    # KDS columns (41-45 in weekly) are counts/ratios/seconds, NOT currency.
    labor_weekly_currency = [5, 6, 7, 8, 9, 12, 14, 15, 24, 25, 26, 31, 32, 36]

    # ── Forecast tab ──────────────────────────────────────────────────
    from agents.bhaga.scripts.forecast import (
        FORECAST_CURRENCY_COLS,
        FORECAST_HIDDEN_COLS,
        build_labor_daily_forecast_rows,
        backfill_forecast_errors,
    )

    # Read the existing forecast tab ONCE: it drives freeze-in-place (formula
    # cells come back as evaluated VALUES) AND preserves operator-edited per-row
    # inputs (target_time_per_item_sec, target_hourly_labor_pct) — same idiom as
    # the labor_daily forecast_exclude preservation.
    existing_forecast_grid = _read_existing_forecast_grid(
        spreadsheet_id=model_sid, store=args.store,
    )
    existing_target_by_date = _forecast_grid_col_numeric_map(
        existing_forecast_grid, "target_time_per_item_sec",
    )
    existing_hourly_target_by_date = _forecast_grid_col_numeric_map(
        existing_forecast_grid, "target_hourly_labor_pct",
    )
    if existing_target_by_date:
        print(f"#   → preserved target_time_per_item_sec for "
              f"{len(existing_target_by_date)} existing forecast rows")
    if existing_hourly_target_by_date:
        print(f"#   → preserved target_hourly_labor_pct for "
              f"{len(existing_hourly_target_by_date)} existing forecast rows")
    forecast_rows = build_labor_daily_forecast_rows(
        labor_daily_rows=labor_daily_rows,
        wage_rates=wage_rates,
        config=forecast_config,
        kds_by_date=kds_by_date,
        existing_target_by_date=existing_target_by_date,
        existing_hourly_target_by_date=existing_hourly_target_by_date,
        existing_forecast_rows=existing_forecast_grid,
    )
    # Backfill error columns for past forecast rows where actuals now exist.
    # On the first run there are no past forecasts; on subsequent runs the
    # forecast tab accumulates history.
    forecast_rows = backfill_forecast_errors(
        forecast_rows=forecast_rows,
        labor_daily_rows=labor_daily_rows,
    )

    tab_payloads = [
        {"tab": "config",            "rows": config_rows,      "currency_cols": []},
        {"tab": "daily",             "rows": daily_rows,       "currency_cols": [2, 3, 7]},
        {"tab": "labor_daily",       "rows": labor_daily_rows, "currency_cols": labor_daily_currency},
        {"tab": "labor_weekly",      "rows": labor_weekly_rows, "currency_cols": labor_weekly_currency},
        {"tab": "labor_period",      "rows": labor_period_rows, "currency_cols": labor_period_currency},
        {"tab": "labor_daily_forecast", "rows": forecast_rows,
         "currency_cols": FORECAST_CURRENCY_COLS, "hidden_cols": FORECAST_HIDDEN_COLS},
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
        # Wipe stale formatting from any prior layout before applying the new
        # targeted styling — otherwise an old percentage cell at the same
        # column index renders our 0..2 saturation ratio as "22.00%". See
        # reset_number_format() docstring for the trap. Order matters:
        # reset BEFORE bold + currency so those re-applications win.
        reset_number_format(
            model_sid, token, sheet_id=sheet_id, num_cols=len(p["rows"][0]),
        )
        bold_header_row(model_sid, token, sheet_id=sheet_id)
        if p["currency_cols"]:
            format_currency_columns(
                model_sid, token, sheet_id=sheet_id,
                column_indices=p["currency_cols"], start_row=1,
            )
        # Re-apply PERCENT format to every fraction column (detected by header
        # name) so *_pct_* cells render as e.g. 66.67% instead of a bare 0.6667
        # after reset_number_format wipes formatting. Survives every rebuild.
        percent_cols = _percent_column_indices(p["rows"][0])
        if percent_cols:
            format_percent_columns(
                model_sid, token, sheet_id=sheet_id,
                column_indices=percent_cols, start_row=1,
            )
        # Positively assert NUMBER format on the KDS *_time_per_item_sec columns
        # so stale PERCENT formatting from a prior layout can't leak through
        # (reset_number_format's userEnteredFormat wipe is unreliable for this
        # on isolated rows — see _seconds_column_indices docstring).
        seconds_cols = _seconds_column_indices(p["rows"][0])
        if seconds_cols:
            format_number_columns(
                model_sid, token, sheet_id=sheet_id,
                column_indices=seconds_cols, start_row=1,
            )
        auto_resize_columns(model_sid, token, sheet_id=sheet_id, num_cols=len(p["rows"][0]))
        if p.get("hidden_cols"):
            hide_columns(
                model_sid, token, sheet_id=sheet_id,
                column_indices=p["hidden_cols"],
            )
        print(f"    wrote {p['tab']:<22} ({len(p['rows'])-1} rows)")

    # ── Round-trip sentinel ───────────────────────────────────────
    # After every config write, re-read each date-bearing key and
    # verify it round-trips back to the canonical ISO we wrote. This
    # catches Sheets API regressions (e.g. a future change that breaks
    # the apostrophe-as-text-literal trick) WITHIN THE SAME RUN
    # instead of waiting 24h for the downstream cascade to surface it.
    expected_iso = last_data_date
    try:
        read_back_raw = _read_config_value(
            spreadsheet_id=model_sid, store=args.store, key="data_window_end"
        )
        read_back = coerce_iso_date(read_back_raw)
    except Exception as exc:  # noqa: BLE001
        print(
            f"\n!!! round-trip sentinel: could not re-read data_window_end "
            f"after write: {exc}",
            file=sys.stderr,
        )
        return 2
    if read_back != expected_iso:
        print(
            f"\n!!! round-trip sentinel: data_window_end drift detected "
            f"after write: wrote {expected_iso!r}, read back "
            f"{read_back_raw!r} (coerced={read_back!r}). The apostrophe-"
            f"as-text-literal write trick is no longer working — "
            f"investigate before tonight's cron.",
            file=sys.stderr,
        )
        return 2

    print(f"\nDone. {model_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
