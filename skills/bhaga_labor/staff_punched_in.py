"""Point-in-time staff headcounts from ADP punches (hourly / fulltime / total).

Bucket rules mirror ``build_labor_daily_rows`` in update_model_sheet.py and
DOMAIN.md §1: hourly = tipped baristas; fulltime = manager / salaried /
excluded_from_labor_pct.
"""

from __future__ import annotations

from typing import Literal

Bucket = Literal["hourly", "fulltime"]


def classify_employee_bucket(
    employee_name: str,
    wage_rates_by_name: dict[str, dict],
    excluded_from_tip_pool: set[str],
) -> Bucket:
    """Classify one employee into the labor-model hourly or fulltime bucket."""
    if employee_name in excluded_from_tip_pool:
        return "fulltime"
    row = wage_rates_by_name.get(employee_name, {})
    if row.get("is_salaried") or row.get("excluded_from_labor_pct"):
        return "fulltime"
    return "hourly"


def index_punches_by_date(punches: list[dict]) -> dict[str, list[dict]]:
    """Group punch rows by shop-local ``date``."""
    out: dict[str, list[dict]] = {}
    for p in punches:
        d = p.get("date") or ""
        if not d:
            continue
        out.setdefault(d, []).append(p)
    return out


def _time_hhmmss_from_item_sold_at(item_sold_at_local: str) -> tuple[str, str]:
    """Return (date_local, HH:MM:SS) from ``YYYY-MM-DDTHH:MM:SS``."""
    if "T" not in item_sold_at_local:
        raise ValueError(f"expected ISO local datetime, got {item_sold_at_local!r}")
    date_part, time_part = item_sold_at_local.split("T", 1)
    return date_part, time_part[:8]


def _norm_hhmmss(t: str) -> str:
    """Normalize ADP HH:MM or item HH:MM:SS to comparable HH:MM:SS."""
    t = (t or "").strip()
    if len(t) == 5 and t[2] == ":":
        return f"{t}:00"
    return t[:8] if len(t) >= 8 else t


def _punch_covers_time(punch: dict, time_hhmmss: str) -> bool:
    in_t = _norm_hhmmss(punch.get("in_time") or "")
    out_t = _norm_hhmmss(punch.get("out_time") or "")
    at_t = _norm_hhmmss(time_hhmmss)
    if not in_t or not out_t or not at_t:
        return False
    return in_t <= at_t <= out_t


def count_staff_punched_in_at(
    *,
    item_sold_at_local: str,
    punches: list[dict],
    wage_rates: list[dict],
    excluded_from_tip_pool: set[str],
    punches_by_date: dict[str, list[dict]] | None = None,
) -> dict[str, int]:
    """Count distinct employees punched in at the item's sale time.

    Returns:
        staff_punched_in_hourly_count
        staff_punched_in_fulltime_count
        staff_punched_in_total_count
    """
    date_local, time_hhmmss = _time_hhmmss_from_item_sold_at(item_sold_at_local)
    rates_by_name = {r["employee_name"]: r for r in wage_rates if r.get("employee_name")}

    if punches_by_date is None:
        punches_by_date = index_punches_by_date(punches)
    day_punches = punches_by_date.get(date_local, [])

    hourly: set[str] = set()
    fulltime: set[str] = set()
    for p in day_punches:
        if not _punch_covers_time(p, time_hhmmss):
            continue
        emp = p.get("employee_name") or ""
        if not emp:
            continue
        bucket = classify_employee_bucket(emp, rates_by_name, excluded_from_tip_pool)
        if bucket == "fulltime":
            fulltime.add(emp)
        else:
            hourly.add(emp)

    h = len(hourly)
    f = len(fulltime)
    return {
        "staff_punched_in_hourly_count": h,
        "staff_punched_in_fulltime_count": f,
        "staff_punched_in_total_count": h + f,
    }
