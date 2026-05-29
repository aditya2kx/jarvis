#!/usr/bin/env python3
"""BHAGA labor_daily_forecast — a LIVE, formula-driven planning worksheet.

Unlike the old static build (Python computed every cell), this writes the
forecast tab as a spreadsheet the operator can actually plan in: a few INPUT
cells per row (orders, fulltime_hours, target_labor_pct, forecast_exclude,
notes) plus a row of rate CONSTANTS, and every DERIVED column is a Google
Sheets FORMULA (=...) so editing an input recalculates sales, items, the
staffing solver and the budget/coverage flag IN THE SHEET.

Layout (one row per forecast day, ~14 future days):

  INPUTS (values, operator-editable)
    date | dow | orders | fulltime_hours | target_labor_pct |
    forecast_exclude | notes
  HELPER CONSTANTS (values; hidden in-sheet)
    _avg_order_price | _avg_items_per_order | _avg_discount_per_order |
    _avg_tip_pool_per_order | _avg_hourly_wage | _target_time_per_item_sec |
    _shift_hours | _min_parttimers
  DERIVED (FORMULAS, evaluate live via USER_ENTERED)
    net_sales | discounts | gross_sales | items_sold | tip_pool |
    min_coverage_hours | efficiency_hours | needed_hours | fulltime_cost |
    budget_hours | recommended_hourly_hours | hourly_cost |
    total_labor_cost | actual_labor_pct | staffing_flag
  ACCURACY (Python-backfilled once a forecast day has a realized actual)
    forecast_generated_at | orders_error_pct | items_sold_error_pct |
    net_sales_error_pct | fulltime_hours_error_pct | hourly_hours_error_pct |
    avg_order_price_error_pct | realized_labor_pct | forecast_mape

Public API:
    build_labor_daily_forecast_rows(...)   -> grid (header + formula rows)
    backfill_forecast_errors(...)          -> fill accuracy cols
    forecast_orders_dow_trend(...)         -> the order seed (excludes
                                              forecast_exclude=TRUE days)
    compute_forecast_constants(...)        -> the rate constants (pure)
    compute_staffing(...)                  -> Python mirror of the sheet
                                              solver formulas (pure, testable)
"""

from __future__ import annotations

import datetime
import statistics
import sys
import os
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from skills.bhaga_config.dates import _iso_date_for_sheet_cell, coerce_iso_date


# ── Column layout ─────────────────────────────────────────────────

FORECAST_COLUMNS: list[str] = [
    # inputs
    "date", "dow", "orders", "fulltime_hours", "target_labor_pct",
    "forecast_exclude", "notes",
    # helper constants (hidden)
    "_avg_order_price", "_avg_items_per_order", "_avg_discount_per_order",
    "_avg_tip_pool_per_order", "_avg_hourly_wage", "_target_time_per_item_sec",
    "_shift_hours", "_min_parttimers",
    # derived (formulas)
    "net_sales", "discounts", "gross_sales", "items_sold", "tip_pool",
    "min_coverage_hours", "efficiency_hours", "needed_hours",
    "fulltime_cost", "budget_hours", "recommended_hourly_hours",
    "hourly_cost", "total_labor_cost", "actual_labor_pct", "staffing_flag",
    # accuracy (python-backfilled)
    "forecast_generated_at",
    "orders_error_pct", "items_sold_error_pct", "net_sales_error_pct",
    "fulltime_hours_error_pct", "hourly_hours_error_pct",
    "avg_order_price_error_pct", "realized_labor_pct", "forecast_mape",
]

_IDX: dict[str, int] = {name: i for i, name in enumerate(FORECAST_COLUMNS)}

# Helper-constant columns get hidden in-sheet (they exist only so the derived
# formulas have stable cell references). 0-based indices.
FORECAST_HIDDEN_COLS: list[int] = [
    _IDX[name] for name in (
        "_avg_order_price", "_avg_items_per_order", "_avg_discount_per_order",
        "_avg_tip_pool_per_order", "_avg_hourly_wage",
        "_target_time_per_item_sec", "_shift_hours", "_min_parttimers",
    )
]

# Currency columns (0-based) for the forecast tab — used by the writer's
# format_currency_columns pass. (Percent columns are auto-detected by the
# writer via the "pct"/"mape" name heuristic, so they're not listed here.)
FORECAST_CURRENCY_COLS: list[int] = [
    _IDX["_avg_order_price"], _IDX["_avg_discount_per_order"],
    _IDX["_avg_tip_pool_per_order"], _IDX["_avg_hourly_wage"],
    _IDX["net_sales"], _IDX["discounts"], _IDX["gross_sales"],
    _IDX["tip_pool"], _IDX["fulltime_cost"], _IDX["hourly_cost"],
    _IDX["total_labor_cost"],
]


def _col_letter(idx0: int) -> str:
    """0-based column index → A1 column letter (0→A, 25→Z, 26→AA)."""
    s = ""
    n = idx0
    while True:
        n, r = divmod(n, 26)
        s = chr(ord("A") + r) + s
        if n == 0:
            break
        n -= 1
    return s


def _ref(name: str, sheet_row: int) -> str:
    """A1 reference to column `name` in `sheet_row` (e.g. ('orders', 2) → 'C2')."""
    return f"{_col_letter(_IDX[name])}{sheet_row}"


def _derived_formulas(sheet_row: int) -> dict[str, str]:
    """All derived-column formula strings for one sheet row.

    Cross-references use the row's own input + helper cells, so editing any
    input recalculates the whole chain. budget_hours is the hours of hourly
    labor the labor budget allows AFTER full-time (Lindsay); recommended hours
    stay = needed_hours (coverage/efficiency floor) — budget is a CHECK
    surfaced by staffing_flag, never a cap that would understaff below
    coverage.
    """
    r = sheet_row
    R = lambda n: _ref(n, r)  # noqa: E731
    return {
        "net_sales": f"={R('orders')}*{R('_avg_order_price')}",
        "discounts": f"={R('orders')}*{R('_avg_discount_per_order')}",
        "gross_sales": f"={R('net_sales')}+{R('discounts')}",
        "items_sold": f"={R('orders')}*{R('_avg_items_per_order')}",
        "tip_pool": f"={R('orders')}*{R('_avg_tip_pool_per_order')}",
        "min_coverage_hours": f"={R('_min_parttimers')}*{R('_shift_hours')}",
        "efficiency_hours": f"={R('items_sold')}*{R('_target_time_per_item_sec')}/3600",
        "needed_hours": f"=MAX({R('min_coverage_hours')},{R('efficiency_hours')})",
        "fulltime_cost": f"={R('fulltime_hours')}*{R('_avg_hourly_wage')}",
        "budget_hours": (
            f"=({R('target_labor_pct')}*{R('net_sales')}-{R('fulltime_cost')})"
            f"/{R('_avg_hourly_wage')}"
        ),
        "recommended_hourly_hours": f"={R('needed_hours')}",
        "hourly_cost": f"={R('recommended_hourly_hours')}*{R('_avg_hourly_wage')}",
        "total_labor_cost": f"={R('hourly_cost')}+{R('fulltime_cost')}",
        "actual_labor_pct": f"=IF({R('net_sales')}>0,{R('total_labor_cost')}/{R('net_sales')},0)",
        "staffing_flag": (
            f"=IF({R('needed_hours')}>{R('budget_hours')},\"BUDGET_CONFLICT\","
            f"IF({R('budget_hours')}>{R('needed_hours')}*1.25,"
            f"\"OVERSTAFFED_BUDGET\",\"OK\"))"
        ),
    }


# ── labor_daily parsing ───────────────────────────────────────────


def _parse_daily_row(row: list, header: list[str]) -> dict[str, Any] | None:
    """Parse a labor_daily row into a keyed dict. Returns None for unparseable rows."""
    if len(row) < 8:
        return None
    date_raw = coerce_iso_date(row[0])
    if date_raw is None:
        return None

    def _float(v):
        if v == "" or v is None:
            return 0.0
        s = str(v).rstrip("%")
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    def _int(v):
        if v == "" or v is None:
            return 0
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0

    # forecast_exclude lives in an appended column; locate by name so we don't
    # depend on its absolute position (older sheets won't have it at all).
    forecast_exclude = False
    if "forecast_exclude" in header:
        fe_i = header.index("forecast_exclude")
        if fe_i < len(row):
            forecast_exclude = str(row[fe_i]).strip().lower() in ("true", "1", "yes", "y", "t")

    return {
        "date": date_raw,
        "dow": datetime.date.fromisoformat(date_raw).weekday(),
        "gross_sales": _float(row[2]),
        "discounts": _float(row[3]),
        "net_sales": _float(row[4]),
        "tip_pool": _float(row[5]),
        "net_sales_plus_tips": _float(row[6]),
        "orders": _int(row[7]),
        "hourly_hours": _float(row[8]),
        "hourly_labor_cost": _float(row[9]),
        "fulltime_hours": _float(row[10]),
        "fulltime_labor_cost": _float(row[11]),
        "total_labor_cost": _float(row[12]),
        "items_sold": _int(row[30]) if len(row) > 30 and row[30] != "" else 0,
        "forecast_exclude": forecast_exclude,
    }


def _get_parsed_rows(
    labor_daily_rows: list[list],
    *,
    exclude_flagged: bool = True,
) -> list[dict]:
    """Parse all labor_daily rows (skip header).

    Only operating days (orders > 0) are returned. When ``exclude_flagged`` is
    True (the default for every forecast computation), days the operator (or
    the outlier detector) marked ``forecast_exclude=TRUE`` are dropped so they
    never pollute the order seed, the per-order rate constants, or the DOW
    full-time average.
    """
    if len(labor_daily_rows) <= 1:
        return []
    header = labor_daily_rows[0]
    parsed = []
    for row in labor_daily_rows[1:]:
        p = _parse_daily_row(row, header)
        if p is None or p["orders"] <= 0:
            continue
        if exclude_flagged and p["forecast_exclude"]:
            continue
        parsed.append(p)
    return parsed


# ── Order seed ────────────────────────────────────────────────────


def forecast_orders_dow_trend(
    labor_daily_rows: list[list],
    target_date: datetime.date,
    lookback_weeks: int = 6,
    decay: float = 0.8,
) -> float:
    """Forecast orders using weighted DOW average + trend factor.

    DOW weighted average: last `lookback_weeks` same-day-of-week values,
    weighted by exponential decay (most recent = 1.0, then decay, decay^2, ...).
    Trend factor: avg(last 2 weeks) / avg(prior 2 weeks), capped [0.85, 1.15].

    Days flagged forecast_exclude=TRUE (operator override or outlier default)
    are dropped before any averaging — see _get_parsed_rows(exclude_flagged).
    """
    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=True)
    if not parsed:
        return 0.0

    target_dow = target_date.weekday()
    same_dow = [
        r for r in sorted(parsed, key=lambda x: x["date"], reverse=True)
        if r["dow"] == target_dow and r["date"] < target_date.isoformat()
    ][:lookback_weeks]

    if not same_dow:
        return 0.0

    weights = [decay ** i for i in range(len(same_dow))]
    total_weight = sum(weights)
    dow_avg = sum(r["orders"] * w for r, w in zip(same_dow, weights)) / total_weight

    all_sorted = sorted(parsed, key=lambda x: x["date"], reverse=True)
    recent_dates = [r for r in all_sorted if r["date"] < target_date.isoformat()]
    if len(recent_dates) < 14:
        return round(dow_avg, 0)

    last_2_weeks = recent_dates[:14]
    prior_2_weeks = recent_dates[14:28]
    if not prior_2_weeks:
        return round(dow_avg, 0)

    avg_recent = statistics.mean(r["orders"] for r in last_2_weeks)
    avg_prior = statistics.mean(r["orders"] for r in prior_2_weeks)
    trend = 1.0 if avg_prior <= 0 else avg_recent / avg_prior
    trend = max(0.85, min(1.15, trend))
    return round(dow_avg * trend, 0)


def _forecast_fulltime_hours_dow_raw(
    parsed: list[dict],
    target_date: datetime.date,
    lookback_weeks: int = 4,
) -> float:
    """Uncapped historical DOW average of full-time (Lindsay) hours.

    Weekly capping to forecast_fulltime_weekly_hours happens AFTER the whole
    horizon is built (see build_labor_daily_forecast_rows) so the cap is on the
    weekly SUM, not an artificial per-day ceiling.
    """
    target_dow = target_date.weekday()
    same_dow = [
        r for r in sorted(parsed, key=lambda x: x["date"], reverse=True)
        if r["dow"] == target_dow and r["date"] < target_date.isoformat()
    ][:lookback_weeks]
    if not same_dow:
        return 0.0
    return round(statistics.mean(r["fulltime_hours"] for r in same_dow), 2)


# ── Rate constants ────────────────────────────────────────────────


def compute_forecast_constants(
    parsed_recent: list[dict],
    *,
    avg_hourly_wage: float,
    target_time_per_item_sec: float,
    min_parttimers: int = 2,
) -> dict[str, float]:
    """Compute the per-order rate constants from recent operating days.

    Pure function. ``parsed_recent`` is a list of parsed labor_daily dicts
    (already filtered to recent, non-excluded, operating days). Discounts are
    returned as a POSITIVE per-order magnitude so the sheet identity
    gross_sales = net_sales + discounts yields gross > net (labor_daily stores
    discounts as negative; we take abs()).
    """
    order_prices = [r["net_sales"] / r["orders"] for r in parsed_recent if r["orders"] > 0]
    items_per_order = [
        r["items_sold"] / r["orders"] for r in parsed_recent
        if r["orders"] > 0 and r["items_sold"] > 0
    ]
    disc_per_order = [abs(r["discounts"]) / r["orders"] for r in parsed_recent if r["orders"] > 0]
    tip_per_order = [r["tip_pool"] / r["orders"] for r in parsed_recent if r["orders"] > 0]
    return {
        "_avg_order_price": round(statistics.mean(order_prices), 2) if order_prices else 0.0,
        "_avg_items_per_order": round(statistics.mean(items_per_order), 3) if items_per_order else 0.0,
        "_avg_discount_per_order": round(statistics.mean(disc_per_order), 4) if disc_per_order else 0.0,
        "_avg_tip_pool_per_order": round(statistics.mean(tip_per_order), 4) if tip_per_order else 0.0,
        "_avg_hourly_wage": round(avg_hourly_wage, 2),
        "_target_time_per_item_sec": round(target_time_per_item_sec, 1),
        "_min_parttimers": min_parttimers,
    }


def _shift_hours_by_dow(kds_by_date: dict[str, dict] | None) -> dict[int, float]:
    """Average KDS shift-envelope hours (shift_end - shift_start) per DOW.

    Built from the raw kds_daily rows (which carry shift_start / shift_end as
    HH:MM strings). Returns {weekday: avg_hours}. DOWs with no KDS history are
    absent; the caller falls back to a sane default.
    """
    by_dow: dict[int, list[float]] = {}
    for date_iso, k in (kds_by_date or {}).items():
        start = str(k.get("shift_start", "")).strip()
        end = str(k.get("shift_end", "")).strip()
        if ":" not in start or ":" not in end:
            continue
        try:
            sh, sm = map(int, start.split(":")[:2])
            eh, em = map(int, end.split(":")[:2])
            hours = (eh * 60 + em - sh * 60 - sm) / 60.0
        except (ValueError, TypeError):
            continue
        if hours <= 0:
            continue
        try:
            wd = datetime.date.fromisoformat(date_iso).weekday()
        except (ValueError, TypeError):
            continue
        by_dow.setdefault(wd, []).append(hours)
    return {wd: round(statistics.mean(v), 2) for wd, v in by_dow.items()}


# ── Pure Python mirror of the sheet solver (for tests) ─────────────


def compute_staffing(
    *,
    orders: float,
    avg_order_price: float,
    avg_items_per_order: float,
    avg_discount_per_order: float,
    avg_tip_pool_per_order: float,
    avg_hourly_wage: float,
    target_time_per_item_sec: float,
    shift_hours: float,
    min_parttimers: float,
    fulltime_hours: float,
    target_labor_pct: float,
) -> dict[str, float | str]:
    """Replicate the in-sheet derived formulas in Python.

    Single source of truth for the math that the spreadsheet evaluates, so the
    unit tests can assert the solver behaves as designed without a live Sheet.
    Keep this in lockstep with _derived_formulas().
    """
    net_sales = orders * avg_order_price
    discounts = orders * avg_discount_per_order
    gross_sales = net_sales + discounts
    items_sold = orders * avg_items_per_order
    tip_pool = orders * avg_tip_pool_per_order
    min_coverage_hours = min_parttimers * shift_hours
    efficiency_hours = items_sold * target_time_per_item_sec / 3600.0
    needed_hours = max(min_coverage_hours, efficiency_hours)
    fulltime_cost = fulltime_hours * avg_hourly_wage
    budget_hours = (
        (target_labor_pct * net_sales - fulltime_cost) / avg_hourly_wage
        if avg_hourly_wage else 0.0
    )
    recommended_hourly_hours = needed_hours
    hourly_cost = recommended_hourly_hours * avg_hourly_wage
    total_labor_cost = hourly_cost + fulltime_cost
    actual_labor_pct = (total_labor_cost / net_sales) if net_sales > 0 else 0.0
    if needed_hours > budget_hours:
        staffing_flag = "BUDGET_CONFLICT"
    elif budget_hours > needed_hours * 1.25:
        staffing_flag = "OVERSTAFFED_BUDGET"
    else:
        staffing_flag = "OK"
    return {
        "net_sales": net_sales,
        "discounts": discounts,
        "gross_sales": gross_sales,
        "items_sold": items_sold,
        "tip_pool": tip_pool,
        "min_coverage_hours": min_coverage_hours,
        "efficiency_hours": efficiency_hours,
        "needed_hours": needed_hours,
        "fulltime_cost": fulltime_cost,
        "budget_hours": budget_hours,
        "recommended_hourly_hours": recommended_hourly_hours,
        "hourly_cost": hourly_cost,
        "total_labor_cost": total_labor_cost,
        "actual_labor_pct": actual_labor_pct,
        "staffing_flag": staffing_flag,
    }


# ── Grid builder ──────────────────────────────────────────────────


def build_labor_daily_forecast_rows(
    *,
    labor_daily_rows: list[list],
    wage_rates: list[dict],
    config: dict,
    kds_by_date: dict[str, dict] | None = None,
    horizon_days: int = 14,
) -> list[list]:
    """Build the formula-driven labor_daily_forecast grid (header + N rows).

    INPUT cells are written as values; HELPER constants as values; DERIVED
    columns as ``=...`` formula strings (the writer uses USER_ENTERED so they
    evaluate live). ACCURACY columns start blank and are filled by
    backfill_forecast_errors once a forecast day has a realized actual.
    """
    target_labor_pct = float(config.get("forecast_target_labor_pct", 0.25))
    fulltime_weekly_hours = float(config.get("forecast_fulltime_weekly_hours", 40.0))
    target_time_per_item = float(config.get("forecast_target_completion_time_per_item_sec", 300.0))

    hourly_wages = [
        float(r["wage_rate_dollars"])
        for r in wage_rates
        if r.get("wage_rate_dollars")
        and not r.get("is_salaried")
        and not r.get("excluded_from_labor_pct")
    ]
    avg_hourly_wage = statistics.mean(hourly_wages) if hourly_wages else 15.0

    header = list(FORECAST_COLUMNS)
    rows: list[list] = [header]

    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=True)
    if not parsed:
        return rows

    # Recent window (last 4 weeks of non-excluded operating days) for the rate
    # constants. Sorted desc, capped at 28 days.
    recent = sorted(parsed, key=lambda x: x["date"], reverse=True)[:28]
    constants = compute_forecast_constants(
        recent,
        avg_hourly_wage=avg_hourly_wage,
        target_time_per_item_sec=target_time_per_item,
    )
    shift_hours_dow = _shift_hours_by_dow(kds_by_date)
    # Fallback shift length: median of whatever DOW envelopes we do have, else
    # 11h (Palmetto 10:00–21:00 shop hours).
    if shift_hours_dow:
        shift_hours_fallback = round(statistics.median(shift_hours_dow.values()), 2)
    else:
        shift_hours_fallback = 11.0

    last_actual_date = max(r["date"] for r in parsed)
    start_date = datetime.date.fromisoformat(last_actual_date) + datetime.timedelta(days=1)
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    # First pass: compute per-day input + helper values; stash raw full-time
    # hours so we can apply the WEEKLY cap before emitting.
    staged: list[dict] = []
    for i in range(horizon_days):
        target_date = start_date + datetime.timedelta(days=i)
        wd = target_date.weekday()
        orders = int(forecast_orders_dow_trend(labor_daily_rows, target_date))
        ft_raw = _forecast_fulltime_hours_dow_raw(parsed, target_date)
        shift_hours = shift_hours_dow.get(wd, shift_hours_fallback)
        staged.append({
            "date": target_date,
            "wd": wd,
            "orders": orders,
            "ft_raw": ft_raw,
            "shift_hours": shift_hours,
        })

    # Weekly cap: scale each ISO week's full-time hours so the week SUM never
    # exceeds forecast_fulltime_weekly_hours (Lindsay's 40h ceiling).
    by_week: dict[datetime.date, list[dict]] = {}
    for s in staged:
        monday = s["date"] - datetime.timedelta(days=s["date"].weekday())
        by_week.setdefault(monday, []).append(s)
    for week_rows in by_week.values():
        week_sum = sum(s["ft_raw"] for s in week_rows)
        if week_sum > fulltime_weekly_hours and week_sum > 0:
            scale = fulltime_weekly_hours / week_sum
            for s in week_rows:
                s["ft_capped"] = round(s["ft_raw"] * scale, 2)
        else:
            for s in week_rows:
                s["ft_capped"] = round(s["ft_raw"], 2)

    # Second pass: emit rows. Sheet row number = header(1) + 1-based index.
    for i, s in enumerate(staged):
        sheet_row = i + 2
        target_date = s["date"]
        formulas = _derived_formulas(sheet_row)
        row: list[Any] = [""] * len(FORECAST_COLUMNS)

        # inputs
        row[_IDX["date"]] = _iso_date_for_sheet_cell(target_date.isoformat())
        row[_IDX["dow"]] = target_date.strftime("%a")
        row[_IDX["orders"]] = s["orders"]
        row[_IDX["fulltime_hours"]] = s["ft_capped"]
        row[_IDX["target_labor_pct"]] = target_labor_pct
        row[_IDX["forecast_exclude"]] = "FALSE"
        row[_IDX["notes"]] = ""

        # helper constants
        row[_IDX["_avg_order_price"]] = constants["_avg_order_price"]
        row[_IDX["_avg_items_per_order"]] = constants["_avg_items_per_order"]
        row[_IDX["_avg_discount_per_order"]] = constants["_avg_discount_per_order"]
        row[_IDX["_avg_tip_pool_per_order"]] = constants["_avg_tip_pool_per_order"]
        row[_IDX["_avg_hourly_wage"]] = constants["_avg_hourly_wage"]
        row[_IDX["_target_time_per_item_sec"]] = constants["_target_time_per_item_sec"]
        row[_IDX["_shift_hours"]] = s["shift_hours"]
        row[_IDX["_min_parttimers"]] = constants["_min_parttimers"]

        # derived formulas
        for name, formula in formulas.items():
            row[_IDX[name]] = formula

        # accuracy
        row[_IDX["forecast_generated_at"]] = generated_at
        # error/realized columns start blank (backfilled when actuals land)

        rows.append(row)

    return rows


# ── Accuracy backfill ─────────────────────────────────────────────


def backfill_forecast_errors(
    *,
    forecast_rows: list[list],
    labor_daily_rows: list[list],
) -> list[list]:
    """Fill accuracy columns for forecast rows whose date now has an actual.

    Forecasted values are recomputed in Python from the row's OWN input +
    helper cells (the derived cells hold formula strings, not numbers, so we
    can't read an evaluated value here). Error = (actual - forecast)/actual.

    In the current rebuild model the forecast tab only ever holds FUTURE dates
    (it's regenerated each run from last_actual+1), so this is typically a
    no-op — but it stays correct if a realized date ever overlaps a forecast
    row, and keeps the columns meaningful.
    """
    if len(forecast_rows) <= 1 or len(labor_daily_rows) <= 1:
        return forecast_rows

    ld_header = labor_daily_rows[0]
    actual_by_date: dict[str, dict] = {}
    for row in labor_daily_rows[1:]:
        d = coerce_iso_date(row[0])
        if d is None:
            continue
        try:
            actual_by_date[d] = {
                "orders": int(row[7] or 0),
                "net_sales": float(row[4] or 0),
                "items_sold": int(row[30]) if len(row) > 30 and row[30] != "" else 0,
                "fulltime_hours": float(row[10] or 0),
                "hourly_hours": float(row[8] or 0),
                "total_labor_cost": float(row[12] or 0),
            }
        except (ValueError, IndexError):
            continue

    header = forecast_rows[0]
    idx = {name: i for i, name in enumerate(header)}

    def _num(row, name, default=0.0):
        i = idx.get(name)
        if i is None or i >= len(row):
            return default
        v = row[i]
        if v == "" or v is None or (isinstance(v, str) and v.startswith("=")):
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    def _err(actual_val, forecast_val):
        if not actual_val:
            return ""
        return round((actual_val - forecast_val) / actual_val, 4)

    for row in forecast_rows[1:]:
        d = coerce_iso_date(row[idx["date"]]) if "date" in idx else None
        if d is None or d not in actual_by_date:
            continue
        actual = actual_by_date[d]

        f_orders = _num(row, "orders")
        f_price = _num(row, "_avg_order_price")
        f_items_per = _num(row, "_avg_items_per_order")
        f_ft = _num(row, "fulltime_hours")
        f_shift = _num(row, "_shift_hours")
        f_minpt = _num(row, "_min_parttimers")
        f_tpi = _num(row, "_target_time_per_item_sec")

        f_net = f_orders * f_price
        f_items = f_orders * f_items_per
        f_eff = f_items * f_tpi / 3600.0
        f_hourly = max(f_minpt * f_shift, f_eff)

        row[idx["orders_error_pct"]] = _err(actual["orders"], f_orders)
        row[idx["items_sold_error_pct"]] = _err(actual["items_sold"], f_items)
        row[idx["net_sales_error_pct"]] = _err(actual["net_sales"], f_net)
        row[idx["fulltime_hours_error_pct"]] = _err(actual["fulltime_hours"], f_ft)
        row[idx["hourly_hours_error_pct"]] = _err(actual["hourly_hours"], f_hourly)

        price_err = ""
        if actual["orders"] > 0 and f_orders > 0:
            actual_price = actual["net_sales"] / actual["orders"]
            forecast_price = f_net / f_orders
            price_err = _err(actual_price, forecast_price)
        row[idx["avg_order_price_error_pct"]] = price_err

        realized = ""
        if actual["net_sales"] > 0:
            realized = round(actual["total_labor_cost"] / actual["net_sales"], 4)
        row[idx["realized_labor_pct"]] = realized

        mape_vals = []
        for a, f in [
            (actual["orders"], f_orders),
            (actual["items_sold"], f_items),
            (actual["net_sales"], f_net),
        ]:
            if a:
                mape_vals.append(abs((a - f) / a))
        row[idx["forecast_mape"]] = round(statistics.mean(mape_vals), 4) if mape_vals else ""

    return forecast_rows
