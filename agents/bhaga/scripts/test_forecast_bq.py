#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.forecast_bq (2026-06-10 algorithm).

Model under test:
    forecast(day) = most-recent same-weekday actual × growth ** weeks_apart
    growth        = mean(orders, last 7 actual days) / mean(prior 7), clamped
    excluded/closed anchor days are skipped a WHOLE WEEK at a time (DOW kept)

Grids are anchored to *today* (last row = yesterday) so the same-weekday anchor
is always one week back — deterministic regardless of when the suite runs.

Run:
    python3 -m pytest agents/bhaga/scripts/test_forecast_bq.py -v
"""
from __future__ import annotations

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.forecast_bq import build_backfill_rows, build_forecast_rows

_HEADER = (
    ["date", "dow", "gross_sales", "discounts", "net_sales", "tip_pool",
     "net_sales_plus_tips", "orders", "hourly_hours", "hourly_labor_cost",
     "fulltime_hours", "fulltime_labor_cost", "total_labor_cost"]
    + [f"c{i}" for i in range(13, 30)]
    + ["items_sold"]
    + [f"c{i}" for i in range(31, 43)]
    + ["outlier_flag", "forecast_exclude"]
)
assert _HEADER[7] == "orders" and _HEADER[30] == "items_sold"

_ITEMS_PER_ORDER = 1.4


def _orders_for_dow(dow: int) -> int:
    """Deterministic per-DOW order level (Mon=100 … Sun=160)."""
    return 100 + dow * 10


def _grid(
    *,
    days: int = 56,
    weekly_growth: float = 1.0,
    exclude: dict[str, int] | None = None,  # iso_date -> override orders, flagged excluded
    spike: dict[str, int] | None = None,    # iso_date -> override orders, NOT excluded
    last_date: datetime.date | None = None,
) -> list[list]:
    """Synthetic labor_daily grid ending at ``last_date`` (default = yesterday CT).

    ``weekly_growth`` multiplies the per-DOW level by growth**week_index so the
    most recent week is the largest — lets us test the growth multiplier.
    """
    if last_date is None:
        last_date = datetime.date.today() - datetime.timedelta(days=1)
    exclude = exclude or {}
    spike = spike or {}
    rows: list[list] = [_HEADER]
    for i in range(days - 1, -1, -1):
        d = last_date - datetime.timedelta(days=i)
        weeks_from_end = i // 7  # 0 = most recent week
        base = _orders_for_dow(d.weekday())
        orders = round(base * (weekly_growth ** (-weeks_from_end)))
        fe = "FALSE"
        iso = d.isoformat()
        if iso in exclude:
            orders = exclude[iso]
            fe = "TRUE"
        elif iso in spike:
            orders = spike[iso]
        items = int(orders * _ITEMS_PER_ORDER)
        net = orders * 9.0
        row = (
            [iso, d.strftime("%a"), round(net * 1.05, 2), round(-net * 0.05, 2),
             round(net, 2), round(net * 0.08, 2), round(net * 1.08, 2), orders,
             8.0, 120.0, 6.0, 150.0, 270.0]
            + [""] * 17 + [items] + [""] * 12 + ["FALSE", fe]
        )
        rows.append(row)
    return rows


class BuildForecastRowsTests(unittest.TestCase):
    def test_returns_30_rows(self):
        self.assertEqual(len(build_forecast_rows(labor_daily_rows=_grid())), 30)

    def test_horizon_configurable(self):
        self.assertEqual(len(build_forecast_rows(labor_daily_rows=_grid(), horizon_days=7)), 7)

    def test_dates_are_strictly_future_and_consecutive(self):
        today = datetime.date.today()
        rows = build_forecast_rows(labor_daily_rows=_grid())
        dates = [datetime.date.fromisoformat(r["date"]) for r in rows]
        self.assertGreater(dates[0], today)
        for i in range(1, len(dates)):
            self.assertEqual(dates[i], dates[i - 1] + datetime.timedelta(days=1))

    def test_all_fields_present(self):
        required = {"date", "forecast_orders", "forecast_items", "forecast_generated_at"}
        for r in build_forecast_rows(labor_daily_rows=_grid()):
            self.assertEqual(set(r.keys()), required)

    def test_orders_non_negative(self):
        for r in build_forecast_rows(labor_daily_rows=_grid()):
            self.assertGreaterEqual(r["forecast_orders"], 0)
            self.assertGreaterEqual(r["forecast_items"], 0.0)

    def test_empty_grid_returns_empty(self):
        self.assertEqual(build_forecast_rows(labor_daily_rows=[_HEADER]), [])

    def test_flat_history_anchors_to_same_weekday(self):
        """No growth → each forecast day == last week's same-weekday actual."""
        rows = build_forecast_rows(labor_daily_rows=_grid(weekly_growth=1.0))
        for r in rows:
            dow = datetime.date.fromisoformat(r["date"]).weekday()
            self.assertEqual(r["forecast_orders"], _orders_for_dow(dow),
                             msg=f"{r['date']} should anchor to same-weekday actual")

    def test_items_come_from_anchor_day(self):
        """forecast_items = anchor's actual items (× growth), not a global ratio."""
        rows = build_forecast_rows(labor_daily_rows=_grid(weekly_growth=1.0))
        for r in rows:
            dow = datetime.date.fromisoformat(r["date"]).weekday()
            expected_items = float(int(_orders_for_dow(dow) * _ITEMS_PER_ORDER))
            self.assertAlmostEqual(r["forecast_items"], expected_items, places=1)

    def test_growth_multiplier_lifts_forecast(self):
        """A rising 2-week trend pushes the forecast above last week's actual."""
        flat = build_forecast_rows(labor_daily_rows=_grid(weekly_growth=1.0))
        rising = build_forecast_rows(labor_daily_rows=_grid(weekly_growth=1.10))
        # First future day (1 week out) should be strictly higher under growth.
        self.assertGreater(rising[0]["forecast_orders"], flat[0]["forecast_orders"])

    def test_growth_is_clamped(self):
        """An extreme week-over-week jump is capped (no runaway compounding)."""
        rows = build_forecast_rows(labor_daily_rows=_grid(weekly_growth=3.0))
        dow = datetime.date.fromisoformat(rows[0]["date"]).weekday()
        anchor = _orders_for_dow(dow)
        # growth clamped to 1.20, so 1-week-out forecast ≤ anchor × 1.20 (+rounding).
        self.assertLessEqual(rows[0]["forecast_orders"], round(anchor * 1.20) + 1)

    def test_excluded_anchor_skips_a_whole_week(self):
        """If last week's same weekday is excluded, use the week before (same DOW)."""
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        # Find the most recent occurrence (<= yesterday) of the first future weekday.
        first_future = today + datetime.timedelta(days=1)
        last_wk = first_future - datetime.timedelta(days=7)
        # Exclude that day with a wild value; forecast must ignore it (skip to the
        # prior same-weekday week, ~normal level — NOT anywhere near 999).
        grid = _grid(weekly_growth=1.0, exclude={last_wk.isoformat(): 999})
        rows = build_forecast_rows(labor_daily_rows=grid)
        normal = _orders_for_dow(first_future.weekday())
        self.assertLess(rows[0]["forecast_orders"], normal * 1.5,
                        msg="Excluded wild anchor value must not be used")
        self.assertGreater(rows[0]["forecast_orders"], normal * 0.5,
                        msg="Forecast should fall back to a normal same-weekday level")


class BuildBackfillRowsTests(unittest.TestCase):
    def test_backfill_dates_are_past(self):
        today = datetime.date.today()
        rows = build_backfill_rows(labor_daily_rows=_grid(), weeks=4)
        self.assertTrue(rows, "expected some backfill rows")
        for r in rows:
            self.assertLess(datetime.date.fromisoformat(r["date"]), today)

    def test_backfill_empty_grid(self):
        self.assertEqual(build_backfill_rows(labor_daily_rows=[_HEADER]), [])

    def test_backfill_has_required_fields(self):
        required = {"date", "forecast_orders", "forecast_items", "forecast_generated_at"}
        for r in build_backfill_rows(labor_daily_rows=_grid(), weeks=4):
            self.assertEqual(set(r.keys()), required)


if __name__ == "__main__":
    unittest.main()
