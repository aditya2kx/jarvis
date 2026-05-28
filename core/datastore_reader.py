"""BigQuery reader that returns data in the same format as the Sheets readers.

Maps BigQuery table columns back to the dict keys that update_model_sheet.py
and the tab builders expect. Handles type normalization (DATE → ISO string,
NULL → 0/"", derived columns like hour_local/dow_local).

Usage:
    from core.datastore_reader import read_shifts_bq, read_wage_rates_bq, read_transactions_bq

    shifts = read_shifts_bq()         # same shape as read_raw_adp_shifts()
    rates = read_wage_rates_bq()      # same shape as read_raw_adp_rates()
    txns = read_transactions_bq()     # same shape as read_raw_square_transactions()
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from core.datastore import read_table

logger = logging.getLogger(__name__)


def _date_to_str(val: Any) -> str:
    """Convert a BigQuery DATE (datetime.date) or string to ISO date string."""
    if val is None:
        return ""
    if isinstance(val, datetime.date):
        return val.isoformat()
    return str(val)


def _ts_to_str(val: Any) -> str:
    """Convert a BigQuery TIMESTAMP to ISO string."""
    if val is None:
        return ""
    if isinstance(val, datetime.datetime):
        return val.isoformat(timespec="seconds")
    return str(val)


def _float_or_zero(val: Any) -> float:
    if val is None:
        return 0.0
    return float(val)


def _int_or_zero(val: Any) -> int:
    if val is None:
        return 0
    return int(val)


def _str_or_empty(val: Any) -> str:
    if val is None:
        return ""
    return str(val)


def _bool_val(val: Any) -> bool:
    if val is None:
        return False
    return bool(val)


def read_shifts_bq() -> list[dict]:
    """Read bhaga.adp_shifts and return in Sheets-reader format.

    BQ columns → Sheets keys:
        date (DATE) → date (str)
        employee_id → employee_id
        canonical_name → employee_name
        raw_employee_name → raw_employee_name
        in_time → in_time
        out_time → out_time
        regular_hours → regular_hours (float)
        ot_hours → ot_hours (float)
        doubletime_hours → doubletime_hours (float)
        total_hours → total_hours (float)
        shift_count → punch_count (int)
        scraped_at_utc → scraped_at_utc (str)
        (missing) → pay_period ("")
    """
    rows = read_table("adp_shifts")
    if not rows:
        return []

    out: list[dict] = []
    for r in rows:
        out.append({
            "date": _date_to_str(r.get("date")),
            "employee_id": _str_or_empty(r.get("employee_id")),
            "employee_name": _str_or_empty(r.get("canonical_name")),
            "raw_employee_name": _str_or_empty(r.get("raw_employee_name")),
            "in_time": _str_or_empty(r.get("in_time")),
            "out_time": _str_or_empty(r.get("out_time")),
            "regular_hours": _float_or_zero(r.get("regular_hours")),
            "ot_hours": _float_or_zero(r.get("ot_hours")),
            "doubletime_hours": _float_or_zero(r.get("doubletime_hours")),
            "total_hours": _float_or_zero(r.get("total_hours")),
            "punch_count": _int_or_zero(r.get("shift_count")),
            "pay_period": "",
            "scraped_at_utc": _ts_to_str(r.get("scraped_at_utc")),
        })
    return out


def read_wage_rates_bq() -> list[dict]:
    """Read bhaga.adp_wage_rates and return in Sheets-reader format.

    BQ columns → Sheets keys:
        employee_id → employee_id + employee_name (same value)
        canonical_name → employee_name
        wage_rate_dollars → wage_rate_dollars (float or None)
        ot_rate_dollars → ot_rate_dollars (float or None)
        is_salaried → is_salaried (bool)
        excluded_from_labor_pct → excluded_from_labor_pct (bool)
        raw_employee_names_json → raw_employee_names_json (list)
        earnings_json → rate_history_json (list)
        scraped_at_utc → scraped_at_utc (str)
        (missing) → multi_rate (False)
    """
    rows = read_table("adp_wage_rates")
    if not rows:
        return []

    out: list[dict] = []
    for r in rows:
        # Parse JSON columns
        raw_names_raw = r.get("raw_employee_names_json")
        if isinstance(raw_names_raw, str) and raw_names_raw:
            try:
                raw_names = json.loads(raw_names_raw)
            except (json.JSONDecodeError, TypeError):
                raw_names = []
        elif isinstance(raw_names_raw, list):
            raw_names = raw_names_raw
        else:
            raw_names = []

        earnings_raw = r.get("earnings_json")
        if isinstance(earnings_raw, str) and earnings_raw:
            try:
                rate_history = json.loads(earnings_raw)
            except (json.JSONDecodeError, TypeError):
                rate_history = []
        elif isinstance(earnings_raw, list):
            rate_history = earnings_raw
        else:
            rate_history = []

        emp_id = _str_or_empty(r.get("employee_id"))
        canonical = _str_or_empty(r.get("canonical_name"))

        out.append({
            "employee_id": emp_id,
            "employee_name": canonical or emp_id,
            "wage_rate_dollars": r.get("wage_rate_dollars"),
            "ot_rate_dollars": r.get("ot_rate_dollars"),
            "is_salaried": _bool_val(r.get("is_salaried")),
            "multi_rate": False,
            "excluded_from_labor_pct": _bool_val(r.get("excluded_from_labor_pct")),
            "rate_history_json": rate_history,
            "raw_employee_names_json": raw_names,
            "scraped_at_utc": _ts_to_str(r.get("scraped_at_utc")),
        })
    return out


def read_transactions_bq() -> list[dict]:
    """Read bhaga.square_transactions and return in Sheets-reader format.

    BQ columns → Sheets keys:
        transaction_id → transaction_id
        date_local (DATE) → date_local (str)
        event_type → event_type
        gross_sales_cents → gross_sales_cents (int)
        discount_cents → discount_cents (int)
        net_sales_cents → net_sales_cents (int)
        tip_cents → tip_cents (int)
        total_collected_cents → total_collected_cents (int)
        net_total_cents → net_total_cents (int)
        source → source
        staff_name → staff_name
        location → location
        created_at_src_iso → created_at_src_iso
        created_at_local_iso → created_at_local_iso
        scraped_at_utc → scraped_at_utc
        (derived) → hour_local (int)
        (derived) → dow_local (int)
    """
    rows = read_table("square_transactions")
    if not rows:
        return []

    out: list[dict] = []
    for r in rows:
        # Derive hour_local and dow_local from created_at_local_iso
        local_iso = _str_or_empty(r.get("created_at_local_iso"))
        hour_local = 0
        dow_local = 0
        if local_iso:
            try:
                dt = datetime.datetime.fromisoformat(local_iso)
                hour_local = dt.hour
                dow_local = dt.weekday()
            except (ValueError, TypeError):
                pass

        out.append({
            "transaction_id": _str_or_empty(r.get("transaction_id")),
            "event_type": _str_or_empty(r.get("event_type")),
            "created_at_src_iso": _str_or_empty(r.get("created_at_src_iso")),
            "created_at_local_iso": local_iso,
            "date_local": _date_to_str(r.get("date_local")),
            "hour_local": hour_local,
            "dow_local": dow_local,
            "gross_sales_cents": _int_or_zero(r.get("gross_sales_cents")),
            "discount_cents": _int_or_zero(r.get("discount_cents")),
            "tip_cents": _int_or_zero(r.get("tip_cents")),
            "net_total_cents": _int_or_zero(r.get("net_total_cents")),
            "total_collected_cents": _int_or_zero(r.get("total_collected_cents")),
            "net_sales_cents": _int_or_zero(r.get("net_sales_cents")),
            "source": _str_or_empty(r.get("source")),
            "staff_name": _str_or_empty(r.get("staff_name")),
            "location": _str_or_empty(r.get("location")),
            "raw_date_csv": "",
            "raw_time_csv": "",
            "raw_tz_csv": "",
            "scraped_at_utc": _ts_to_str(r.get("scraped_at_utc")),
        })
    return out
