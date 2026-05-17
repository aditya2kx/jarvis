#!/usr/bin/env python3
"""skills/tip_ledger_writer/reader - Read raw BHAGA sheets back into records.

The companion to writer.py. Each `read_raw_*` function pulls a tab from one
of the three BHAGA raw workbooks, validates the header against the schema,
and returns list[dict] records with types coerced back from the wire format
(everything in Google Sheets ends up as a string).

API summary:
    read_raw_adp_shifts(spreadsheet_id, *, account="palmetto")
    read_raw_adp_punches(spreadsheet_id, *, account="palmetto")
    read_raw_adp_rates(spreadsheet_id, *, account="palmetto")
    read_raw_square_transactions(spreadsheet_id, *, account="palmetto")
    read_raw_square_daily_rollup(spreadsheet_id, *, account="palmetto")

Type coercion is conservative: empty strings stay as empty strings (not 0)
unless the column is canonically numeric. JSON-encoded columns
(rate_history_json, raw_employee_names_json) are decoded into Python lists/dicts.

These readers are what `update_model_sheet` (post-refactor) consumes — local
files become a write cache only; raw sheets are the model sheet's SOT.
"""

from __future__ import annotations

import json
import sys
import os
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import refresh_access_token
from skills.tip_ledger_writer.schema import get_tab_spec
from skills.tip_ledger_writer.writer import _read_tab


# Columns whose string value should become int. Anything *_cents is integer
# cents; *_count is row count; explicit ints are in this set.
_INT_COLUMNS = {
    "gross_sales_cents", "discount_cents", "tip_cents",
    "net_total_cents", "total_collected_cents",
    "net_sales_cents", "refund_cents",
    "txn_count", "transaction_count",
    "hour_local", "dow_local",
    "punch_idx_in_day", "punch_count",
}
# Columns whose string value should become float. Hours and dollar amounts
# typed as decimal.
_FLOAT_COLUMNS = {
    "regular_hours", "ot_hours", "doubletime_hours", "total_hours",
    "wage_rate_dollars", "ot_rate_dollars",
}
_BOOL_COLUMNS = {"is_salaried", "multi_rate", "excluded_from_labor_pct"}
_JSON_COLUMNS = {"rate_history_json", "raw_employee_names_json"}


def _coerce_cell(col: str, raw: Any) -> Any:
    """Convert a raw cell value to the canonical Python type for `col`."""
    if raw is None:
        raw = ""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        # Google Sheets API can return numbers as ints/floats when the column
        # is numeric — just hand them through.
        if col in _INT_COLUMNS:
            return int(raw)
        if col in _FLOAT_COLUMNS:
            return float(raw)
        return raw
    s = str(raw).strip()
    if col in _INT_COLUMNS:
        return int(s) if s else 0
    if col in _FLOAT_COLUMNS:
        return float(s) if s else 0.0
    if col in _BOOL_COLUMNS:
        return s.upper() == "TRUE"
    if col in _JSON_COLUMNS:
        if not s:
            return [] if col.endswith("_names_json") or col == "rate_history_json" else None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return s  # leave as-is for human debugging
    return s


def _read_raw_tab(
    spreadsheet_id: str, workbook_title: str, tab_name: str, *, account: str,
) -> list[dict]:
    """Generic reader: pull a tab, validate header, return list[dict]."""
    spec = get_tab_spec(workbook_title, tab_name)
    expected_header = spec["header"]
    token = refresh_access_token(account)
    rows = _read_tab(spreadsheet_id, tab_name, token)
    if not rows:
        return []
    header = rows[0]
    # Tolerate header rows that have extra trailing cells (e.g. the inline
    # "notes" cell at column N+2 added by bootstrap_sheets) — match the prefix.
    if header[: len(expected_header)] != expected_header:
        raise ValueError(
            f"{workbook_title} > {tab_name} header drift.\n"
            f"  expected: {expected_header}\n"
            f"  got     : {header[: len(expected_header)]}"
        )
    out: list[dict] = []
    for raw_row in rows[1:]:
        if not raw_row or all(str(c or "").strip() == "" for c in raw_row[: len(expected_header)]):
            continue
        padded = list(raw_row) + [""] * (len(expected_header) - len(raw_row))
        rec: dict[str, Any] = {}
        for col, raw_val in zip(expected_header, padded):
            rec[col] = _coerce_cell(col, raw_val)
        out.append(rec)
    return out


def read_raw_adp_shifts(spreadsheet_id: str, *, account: str = "palmetto") -> list[dict]:
    """Return all rows of BHAGA ADP Raw > shifts as list[dict].

    Each record matches the schema header: date, employee_id, employee_name,
    raw_employee_name, in_time, out_time, regular_hours, ot_hours,
    doubletime_hours, total_hours, punch_count, pay_period, scraped_at_utc.
    """
    return _read_raw_tab(spreadsheet_id, "BHAGA ADP Raw", "shifts", account=account)


def read_raw_adp_punches(spreadsheet_id: str, *, account: str = "palmetto") -> list[dict]:
    """Return all rows of BHAGA ADP Raw > punches as list[dict] (per-punch detail)."""
    return _read_raw_tab(spreadsheet_id, "BHAGA ADP Raw", "punches", account=account)


def read_raw_adp_rates(spreadsheet_id: str, *, account: str = "palmetto") -> list[dict]:
    """Return all rows of BHAGA ADP Raw > wage_rates as list[dict].

    rate_history_json and raw_employee_names_json columns are JSON-decoded.
    """
    return _read_raw_tab(spreadsheet_id, "BHAGA ADP Raw", "wage_rates", account=account)


def read_raw_square_transactions(spreadsheet_id: str, *, account: str = "palmetto") -> list[dict]:
    """Return all rows of BHAGA Square Raw > transactions as list[dict].

    *_cents columns are int; hour_local/dow_local are int. Identical shape
    to what `square_tips.transactions_backend.parse_csv` returns, so callers
    can use either source interchangeably.
    """
    return _read_raw_tab(spreadsheet_id, "BHAGA Square Raw", "transactions", account=account)


def read_raw_square_daily_rollup(spreadsheet_id: str, *, account: str = "palmetto") -> list[dict]:
    """Return all rows of BHAGA Square Raw > daily_rollup as list[dict]."""
    return _read_raw_tab(spreadsheet_id, "BHAGA Square Raw", "daily_rollup", account=account)
