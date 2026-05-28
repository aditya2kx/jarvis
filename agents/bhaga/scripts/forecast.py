#!/usr/bin/env python3
"""BHAGA labor_daily_forecast — demand forecasting + staffing solver.

Forecasts orders using DOW+trend, derives sales/items, and recommends
part-time hours within a 3-bound staffing solver (coverage floor,
KDS-based efficiency need, budget ceiling).

Public API:
    forecast_orders_dow_trend(labor_daily_rows, target_date, ...)
    derive_sales_from_orders(forecast_orders, labor_daily_rows, ...)
    compute_shift_envelope(labor_daily_rows, ...)
    solve_hourly_hours(...)
    build_labor_daily_forecast_rows(...)
    backfill_forecast_errors(...)
"""

from __future__ import annotations

import datetime
import statistics
import sys
import os
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from skills.bhaga_config.dates import _iso_date_for_sheet_cell, coerce_iso_date


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
        "kds_shift_start": str(row[len(header) - 5]) if len(row) > len(header) - 5 else "",
        "kds_shift_end": str(row[len(header) - 4]) if len(row) > len(header) - 4 else "",
    }


def _get_parsed_rows(labor_daily_rows: list[list]) -> list[dict]:
    """Parse all labor_daily rows (skip header)."""
    if len(labor_daily_rows) <= 1:
        return []
    header = labor_daily_rows[0]
    parsed = []
    for row in labor_daily_rows[1:]:
        p = _parse_daily_row(row, header)
        if p is not None and p["orders"] > 0:
            parsed.append(p)
    return parsed


def forecast_orders_dow_trend(
    labor_daily_rows: list[list],
    target_date: datetime.date,
    lookback_weeks: int = 6,
    decay: float = 0.8,
) -> float:
    """Forecast orders using weighted DOW average + trend factor.

    DOW weighted average: last `lookback_weeks` same-day-of-week values,
    weighted by exponential decay (most recent = 1.0, then decay, decay^2, ...).

    Trend factor: avg(last 2 weeks orders) / avg(prior 2 weeks orders).
    Capped at [0.85, 1.15].

    Minimum data: 3 weeks for DOW+trend; fewer falls back to simple DOW average.
    """
    parsed = _get_parsed_rows(labor_daily_rows)
    if not parsed:
        return 0.0

    target_dow = target_date.weekday()

    # Collect same-DOW values, most recent first
    same_dow = [
        r for r in sorted(parsed, key=lambda x: x["date"], reverse=True)
        if r["dow"] == target_dow and r["date"] < target_date.isoformat()
    ][:lookback_weeks]

    if not same_dow:
        return 0.0

    # Weighted average
    weights = [decay ** i for i in range(len(same_dow))]
    total_weight = sum(weights)
    dow_avg = sum(r["orders"] * w for r, w in zip(same_dow, weights)) / total_weight

    # Trend factor (need at least 3 weeks of any-DOW data)
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

    if avg_prior <= 0:
        trend = 1.0
    else:
        trend = avg_recent / avg_prior

    # Cap trend factor
    trend = max(0.85, min(1.15, trend))

    return round(dow_avg * trend, 0)


def derive_sales_from_orders(
    forecast_orders: float,
    labor_daily_rows: list[list],
    target_date: datetime.date,
    lookback_weeks: int = 4,
) -> dict[str, float]:
    """Derive gross_sales, items_sold, discounts, tips from forecast orders using recent averages.

    Uses 4-week averages of per-order ratios:
        avg_order_price = avg(gross_sales / orders)
        avg_items_per_order = avg(items_sold / orders)
        discount_pct = avg(discounts / gross_sales)
        tip_pct = avg(tip_pool / net_sales)

    Returns dict with: gross_sales, items_sold, discounts, net_sales,
    tip_pool, net_sales_plus_tips, avg_order_price.
    """
    parsed = _get_parsed_rows(labor_daily_rows)
    recent = [
        r for r in sorted(parsed, key=lambda x: x["date"], reverse=True)
        if r["date"] < target_date.isoformat() and r["orders"] > 0
    ][:lookback_weeks * 7]

    if not recent or forecast_orders <= 0:
        return {
            "gross_sales": 0.0, "items_sold": 0, "discounts": 0.0,
            "net_sales": 0.0, "tip_pool": 0.0, "net_sales_plus_tips": 0.0,
            "avg_order_price": 0.0,
        }

    # Per-order ratios
    order_prices = [r["gross_sales"] / r["orders"] for r in recent if r["orders"] > 0]
    items_per_order = [
        r["items_sold"] / r["orders"] for r in recent
        if r["orders"] > 0 and r["items_sold"] > 0
    ]
    discount_pcts = [
        abs(r["discounts"]) / r["gross_sales"] for r in recent
        if r["gross_sales"] > 0
    ]
    tip_pcts = [
        r["tip_pool"] / r["net_sales"] for r in recent
        if r["net_sales"] > 0
    ]

    avg_price = statistics.mean(order_prices) if order_prices else 0.0
    avg_items = statistics.mean(items_per_order) if items_per_order else 0.0
    avg_disc_pct = statistics.mean(discount_pcts) if discount_pcts else 0.0
    avg_tip_pct = statistics.mean(tip_pcts) if tip_pcts else 0.0

    gross = forecast_orders * avg_price
    items = round(forecast_orders * avg_items)
    discounts = gross * avg_disc_pct
    net = gross - discounts
    tips = net * avg_tip_pct

    return {
        "gross_sales": round(gross, 2),
        "items_sold": items,
        "discounts": round(-discounts, 2),
        "net_sales": round(net, 2),
        "tip_pool": round(tips, 2),
        "net_sales_plus_tips": round(net + tips, 2),
        "avg_order_price": round(avg_price, 2),
    }


def compute_shift_envelope(
    labor_daily_rows: list[list],
    target_date: datetime.date,
    lookback_weeks: int = 2,
) -> float:
    """Compute per-DOW shift envelope from KDS shift_start/shift_end in labor_daily.

    Returns shift_envelope_hours for the target DOW, averaged over lookback_weeks.
    """
    parsed = _get_parsed_rows(labor_daily_rows)
    target_dow = target_date.weekday()

    same_dow = [
        r for r in sorted(parsed, key=lambda x: x["date"], reverse=True)
        if r["dow"] == target_dow and r["date"] < target_date.isoformat()
    ][:lookback_weeks]

    envelopes = []
    for r in same_dow:
        # Prefer KDS shift start/end, fall back to hourly_hours as proxy
        start_str = r.get("kds_shift_start", "")
        end_str = r.get("kds_shift_end", "")
        if start_str and end_str and ":" in start_str and ":" in end_str:
            try:
                sh, sm = map(int, start_str.split(":")[:2])
                eh, em = map(int, end_str.split(":")[:2])
                hours = (eh * 60 + em - sh * 60 - sm) / 60.0
                if hours > 0:
                    envelopes.append(hours)
                    continue
            except (ValueError, TypeError):
                pass
        # Fallback: use total hours as a rough proxy for the day length
        total_h = r.get("hourly_hours", 0) + r.get("fulltime_hours", 0)
        if total_h > 0:
            envelopes.append(total_h / 2.0)

    if not envelopes:
        return 12.0  # default 12-hour day
    return statistics.mean(envelopes)


def solve_hourly_hours(
    *,
    forecast_items: int,
    target_time_per_item_sec: float,
    shift_envelope_hours: float,
    fulltime_hours: float,
    fulltime_cost: float,
    target_labor_pct: float,
    net_sales: float,
    avg_hourly_wage: float,
) -> tuple[float, str]:
    """3-bound staffing solver. Returns (recommended_hours, staffing_flag).

    Bound 1 — FLOOR: min_hourly_hours = 2 * shift_envelope_hours
    Bound 2 — NEED: needed_hourly_hours = (items * target_time / 3600) - fulltime_hours
    Bound 3 — CEILING: max_hourly_hours = (target_pct * net - fulltime_cost) / avg_hourly_wage
    """
    # Floor: 2 part-timers for the full shift envelope
    floor_hours = 2.0 * shift_envelope_hours

    # Need: efficiency-based
    total_needed = (forecast_items * target_time_per_item_sec) / 3600.0
    needed_hourly = max(0.0, total_needed - fulltime_hours)

    # Ceiling: budget
    if avg_hourly_wage > 0 and net_sales > 0:
        max_labor_budget = target_labor_pct * net_sales
        available_for_hourly = max_labor_budget - fulltime_cost
        ceiling_hours = available_for_hourly / avg_hourly_wage
    else:
        ceiling_hours = float("inf")

    # Clamp
    flag = ""
    if floor_hours > ceiling_hours:
        flag = "BUDGET_CONFLICT"
        recommended = floor_hours  # safety first
    elif floor_hours > needed_hourly:
        flag = "OVERSTAFFED"
        recommended = floor_hours
    else:
        recommended = min(needed_hourly, ceiling_hours)

    return round(max(recommended, 0), 2), flag


def _forecast_fulltime_hours_dow(
    labor_daily_rows: list[list],
    target_date: datetime.date,
    weekly_cap: float,
    lookback_weeks: int = 4,
) -> float:
    """Forecast fulltime hours from historical DOW average, capped weekly."""
    parsed = _get_parsed_rows(labor_daily_rows)
    target_dow = target_date.weekday()

    same_dow = [
        r for r in sorted(parsed, key=lambda x: x["date"], reverse=True)
        if r["dow"] == target_dow and r["date"] < target_date.isoformat()
    ][:lookback_weeks]

    if not same_dow:
        return weekly_cap / 7.0

    avg_ft = statistics.mean(r["fulltime_hours"] for r in same_dow)
    daily_cap = weekly_cap / 7.0
    return min(round(avg_ft, 2), daily_cap)


def build_labor_daily_forecast_rows(
    *,
    labor_daily_rows: list[list],
    wage_rates: list[dict],
    config: dict,
    horizon_days: int = 14,
) -> list[list]:
    """Build the full labor_daily_forecast tab grid.

    The forecast tab has the same columns as labor_daily, PLUS appended columns:
        target_labor_pct, staffing_flag, forecast_generated_at,
        orders_error_pct, items_sold_error_pct, net_sales_error_pct,
        fulltime_hours_error_pct, hourly_hours_error_pct,
        avg_order_price_error_pct, actual_labor_pct, forecast_mape

    Error columns are left blank for future dates; filled once actuals exist
    (via backfill_forecast_errors).
    """
    target_labor_pct = config.get("forecast_target_labor_pct", 0.25)
    fulltime_weekly_hours = config.get("forecast_fulltime_weekly_hours", 40.0)
    target_time_per_item = config.get("forecast_target_completion_time_per_item_sec", 300.0)

    # Compute avg hourly wage from wage_rates
    hourly_wages = [
        float(r["wage_rate_dollars"])
        for r in wage_rates
        if r.get("wage_rate_dollars")
        and not r.get("is_salaried")
        and not r.get("excluded_from_labor_pct")
    ]
    avg_hourly_wage = statistics.mean(hourly_wages) if hourly_wages else 15.0

    # Fulltime cost per hour (from rates of excluded/salaried employees)
    ft_wages = [
        float(r["wage_rate_dollars"])
        for r in wage_rates
        if r.get("wage_rate_dollars")
        and (r.get("is_salaried") or r.get("excluded_from_labor_pct"))
    ]
    avg_ft_wage = statistics.mean(ft_wages) if ft_wages else 20.0

    # Labor_daily header + forecast-specific columns
    if len(labor_daily_rows) <= 1:
        base_header = ["date", "dow"]
    else:
        base_header = list(labor_daily_rows[0])

    forecast_extra_cols = [
        "target_labor_pct", "staffing_flag", "forecast_generated_at",
        "orders_error_pct", "items_sold_error_pct", "net_sales_error_pct",
        "fulltime_hours_error_pct", "hourly_hours_error_pct",
        "avg_order_price_error_pct", "actual_labor_pct", "forecast_mape",
    ]
    header = base_header + forecast_extra_cols
    rows: list[list] = [header]

    # Determine the start date for forecast: day after last actual
    parsed = _get_parsed_rows(labor_daily_rows)
    if not parsed:
        return rows

    last_actual_date = max(r["date"] for r in parsed)
    start_date = datetime.date.fromisoformat(last_actual_date) + datetime.timedelta(days=1)
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    for i in range(horizon_days):
        target_date = start_date + datetime.timedelta(days=i)

        # Forecast orders
        forecast_orders = forecast_orders_dow_trend(labor_daily_rows, target_date)

        # Derive sales/items
        derived = derive_sales_from_orders(forecast_orders, labor_daily_rows, target_date)

        # Forecast fulltime hours
        ft_hours = _forecast_fulltime_hours_dow(
            labor_daily_rows, target_date, fulltime_weekly_hours,
        )
        ft_cost = ft_hours * avg_ft_wage

        # Shift envelope
        envelope = compute_shift_envelope(labor_daily_rows, target_date)

        # Solve hourly hours
        hourly_hours, flag = solve_hourly_hours(
            forecast_items=derived["items_sold"],
            target_time_per_item_sec=target_time_per_item,
            shift_envelope_hours=envelope,
            fulltime_hours=ft_hours,
            fulltime_cost=ft_cost,
            target_labor_pct=target_labor_pct,
            net_sales=derived["net_sales"],
            avg_hourly_wage=avg_hourly_wage,
        )

        hourly_cost = hourly_hours * avg_hourly_wage
        total_cost = hourly_cost + ft_cost
        net = derived["net_sales"]
        pool = derived["tip_pool"]
        net_plus_tips = net + pool
        orders = int(forecast_orders)
        items_sold = derived["items_sold"]
        gross = derived["gross_sales"]
        disc = derived["discounts"]

        def _pct(num: float, denom: float) -> str:
            return f"{(num / denom):.2%}" if denom > 0 else "0.00%"

        def _per_order(cost: float):
            return round(cost / orders, 2) if orders > 0 else ""

        orders_per_hr = round(orders / hourly_hours, 1) if hourly_hours > 0 else ""
        total_h = hourly_hours + ft_hours
        hours_per_order = round(total_h / orders, 3) if orders > 0 else ""
        avg_order_price = round(net / orders, 2) if orders > 0 else ""
        avg_npt = round(net_plus_tips / orders, 2) if orders > 0 else ""
        avg_items_per_order = round(items_sold / orders, 2) if orders > 0 and items_sold > 0 else ""
        hours_per_item = round(total_h / items_sold, 3) if items_sold > 0 else ""

        # Build row matching labor_daily columns + forecast extras
        row = [
            _iso_date_for_sheet_cell(target_date.isoformat()),
            target_date.strftime("%a"),
            round(gross, 2),
            round(disc, 2),
            round(net, 2),
            round(pool, 2),
            round(net_plus_tips, 2),
            orders,
            round(hourly_hours, 2),
            round(hourly_cost, 2),
            round(ft_hours, 2),
            round(ft_cost, 2),
            round(total_cost, 2),
            _pct(hourly_cost, net),
            _pct(hourly_cost, net_plus_tips),
            _pct(ft_cost, net),
            _pct(ft_cost, net_plus_tips),
            _pct(total_cost, net),
            _pct(total_cost, net_plus_tips),
            _pct(pool, net),
            _pct(total_cost + pool, net_plus_tips),
            _per_order(hourly_cost),
            _per_order(ft_cost),
            _per_order(total_cost),
            orders_per_hr,
            "",  # peak_hour — requires intra-day data, blank for forecast
            "",  # over_saturation — blank for forecast
            hours_per_order,
            avg_order_price,
            avg_npt,
            items_sold if items_sold > 0 else "",
            avg_items_per_order,
            hours_per_item,
            "",  # avg_item_price — not forecast
            round(hourly_hours / orders, 3) if orders > 0 else "",
            round(ft_hours / orders, 3) if orders > 0 else "",
            round(hourly_hours / items_sold, 3) if items_sold > 0 else "",
            round(ft_hours / items_sold, 3) if items_sold > 0 else "",
            "",  # kds_completed_tickets
            "",  # kds_completed_items
            "",  # kds_avg_time_per_item_sec
            "",  # kds_median_time_per_item_sec
            "",  # kds_pct_tickets_late
            # Forecast-specific columns
            target_labor_pct,
            flag,
            generated_at,
            "",  # orders_error_pct
            "",  # items_sold_error_pct
            "",  # net_sales_error_pct
            "",  # fulltime_hours_error_pct
            "",  # hourly_hours_error_pct
            "",  # avg_order_price_error_pct
            "",  # actual_labor_pct
            "",  # forecast_mape
        ]
        rows.append(row)

    return rows


def backfill_forecast_errors(
    *,
    forecast_rows: list[list],
    labor_daily_rows: list[list],
) -> list[list]:
    """Fill error columns for forecast rows where actuals now exist.

    Error formula: (actual - forecast) / actual as signed percentage.
    forecast_mape = mean absolute percentage error across orders + items + net_sales.

    Returns updated forecast_rows (modifies in place and returns).
    """
    if len(forecast_rows) <= 1 or len(labor_daily_rows) <= 1:
        return forecast_rows

    # Build actuals index
    actual_by_date: dict[str, dict] = {}
    for row in labor_daily_rows[1:]:
        d = coerce_iso_date(row[0])
        if d is None:
            continue
        try:
            actual_by_date[d] = {
                "orders": int(row[7] or 0),
                "net_sales": float(row[4]),
                "items_sold": int(row[30]) if len(row) > 30 and row[30] != "" else 0,
                "fulltime_hours": float(row[10]),
                "hourly_hours": float(row[8]),
                "total_labor_cost": float(row[12]),
            }
        except (ValueError, IndexError):
            continue

    forecast_header = forecast_rows[0]
    # Find column indices for error fields
    base_col_count = len(forecast_header) - 11  # 11 forecast-specific columns

    for row in forecast_rows[1:]:
        d = coerce_iso_date(row[0])
        if d is None or d not in actual_by_date:
            continue

        actual = actual_by_date[d]
        forecast_orders = int(row[7] or 0) if row[7] != "" else 0
        forecast_net = float(row[4]) if row[4] != "" else 0.0
        forecast_items = int(row[30]) if len(row) > 30 and row[30] != "" else 0
        forecast_ft_h = float(row[10]) if row[10] != "" else 0.0
        forecast_hourly_h = float(row[8]) if row[8] != "" else 0.0

        def _err(actual_val, forecast_val):
            if actual_val == 0:
                return ""
            return f"{((actual_val - forecast_val) / actual_val):.1%}"

        orders_err = _err(actual["orders"], forecast_orders)
        items_err = _err(actual["items_sold"], forecast_items)
        net_err = _err(actual["net_sales"], forecast_net)
        ft_err = _err(actual["fulltime_hours"], forecast_ft_h)
        hourly_err = _err(actual["hourly_hours"], forecast_hourly_h)
        price_err = ""
        if actual["orders"] > 0 and forecast_orders > 0:
            actual_price = actual["net_sales"] / actual["orders"]
            forecast_price = forecast_net / forecast_orders if forecast_orders > 0 else 0
            price_err = _err(actual_price, forecast_price)

        # Actual labor %
        actual_labor_pct = ""
        if actual["net_sales"] > 0:
            actual_labor_pct = f"{actual['total_labor_cost'] / actual['net_sales']:.2%}"

        # MAPE across orders + items + net_sales
        mape_values = []
        for a, f in [
            (actual["orders"], forecast_orders),
            (actual["items_sold"], forecast_items),
            (actual["net_sales"], forecast_net),
        ]:
            if a != 0:
                mape_values.append(abs((a - f) / a))
        mape = f"{statistics.mean(mape_values):.1%}" if mape_values else ""

        # Fill error columns (at end of row)
        row[base_col_count + 3] = orders_err
        row[base_col_count + 4] = items_err
        row[base_col_count + 5] = net_err
        row[base_col_count + 6] = ft_err
        row[base_col_count + 7] = hourly_err
        row[base_col_count + 8] = price_err
        row[base_col_count + 9] = actual_labor_pct
        row[base_col_count + 10] = mape

    return forecast_rows
