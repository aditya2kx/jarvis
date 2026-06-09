"""Compute the slim BQ forecast (orders + items) for the next N days.

BQ-authoritative replacement for the retired labor_daily_forecast Sheet tab.
Reuses the pure forecast functions in forecast.py; no sheet, no staffing solver.

The returned rows are keyed on `date` (ISO YYYY-MM-DD) and cover
today+1 .. today+horizon_days in store-local (America/Chicago) time.
The nightly pipeline loads them with merge_keys=["date"] writing ONLY this
future window, so past rows freeze at their last 1-day-ahead value and
implicit forecast accuracy is available by joining actuals on the same date.
"""
from __future__ import annotations

import datetime
import statistics
from zoneinfo import ZoneInfo

from agents.bhaga.scripts.forecast import (
    _get_parsed_rows,
    compute_forecast_constants,
    forecast_orders_dow_trend,
)

CT = ZoneInfo("America/Chicago")


def build_forecast_rows(
    *,
    labor_daily_rows: list[list],
    wage_rates: list[dict],
    horizon_days: int = 30,
) -> list[dict]:
    """Return [{date, forecast_orders, forecast_items, forecast_generated_at}] for
    today+1 .. today+horizon_days (store-local Chicago time).

    Args:
        labor_daily_rows: raw Sheet/BQ rows including a header row — same format
            consumed by forecast.py.  Rows with forecast_exclude=TRUE are
            automatically dropped from the seed.
        wage_rates:       list of wage-rate dicts (keys: wage_rate_dollars,
            is_salaried, excluded_from_labor_pct) as returned by the BQ
            reader in update_model_sheet / materialize_model_bq.
        horizon_days:     how many future calendar days to generate (default 30,
            configurable via store profile "forecast_horizon_days").

    Returns:
        Ordered list of row dicts, one per future date, ready for load_rows().
        Returns [] when there are not enough historical rows to forecast.
    """
    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=True)
    if not parsed:
        return []

    hourly = [
        float(r["wage_rate_dollars"])
        for r in wage_rates
        if r.get("wage_rate_dollars")
        and not r.get("is_salaried")
        and not r.get("excluded_from_labor_pct")
    ]
    avg_wage = statistics.mean(hourly) if hourly else 15.0

    recent = sorted(parsed, key=lambda x: x["date"], reverse=True)[:28]
    constants = compute_forecast_constants(recent, avg_hourly_wage=avg_wage)
    items_per_order: float = constants["_avg_items_per_order"]

    today = datetime.datetime.now(CT).date()
    gen = datetime.datetime.now(CT).isoformat(timespec="seconds")

    rows = []
    for i in range(1, horizon_days + 1):
        d = today + datetime.timedelta(days=i)
        orders = max(0, int(round(forecast_orders_dow_trend(labor_daily_rows, d))))
        rows.append(
            {
                "date": d.isoformat(),
                "forecast_orders": orders,
                "forecast_items": round(orders * items_per_order, 1),
                "forecast_generated_at": gen,
            }
        )
    return rows
