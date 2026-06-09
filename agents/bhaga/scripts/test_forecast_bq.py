#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.forecast_bq.

Run:
    python3 -m pytest agents/bhaga/scripts/test_forecast_bq.py -v
"""
from __future__ import annotations

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.forecast_bq import build_forecast_rows
from agents.bhaga.scripts.test_forecast import _labor_daily_grid, _wage_rates


class BuildForecastRowsTests(unittest.TestCase):
    def _rows(self, **kwargs):
        return build_forecast_rows(
            labor_daily_rows=_labor_daily_grid(**kwargs),
            wage_rates=_wage_rates(),
            horizon_days=30,
        )

    def test_returns_30_rows(self):
        rows = self._rows()
        self.assertEqual(len(rows), 30)

    def test_dates_are_strictly_future(self):
        today = datetime.date.today()
        rows = self._rows()
        for r in rows:
            d = datetime.date.fromisoformat(r["date"])
            self.assertGreater(d, today, f"Expected future date, got {d}")

    def test_dates_are_consecutive(self):
        rows = self._rows()
        dates = [datetime.date.fromisoformat(r["date"]) for r in rows]
        for i in range(1, len(dates)):
            self.assertEqual(
                dates[i],
                dates[i - 1] + datetime.timedelta(days=1),
                f"Gap at position {i}",
            )

    def test_forecast_items_derived_from_orders(self):
        from agents.bhaga.scripts.forecast import _get_parsed_rows, compute_forecast_constants
        grid = _labor_daily_grid()
        wage_rates = _wage_rates()
        from statistics import mean
        parsed = _get_parsed_rows(grid, exclude_flagged=True)
        hourly = [float(r["wage_rate_dollars"]) for r in wage_rates
                  if r.get("wage_rate_dollars") and not r.get("is_salaried")
                  and not r.get("excluded_from_labor_pct")]
        avg_wage = mean(hourly) if hourly else 15.0
        recent = sorted(parsed, key=lambda x: x["date"], reverse=True)[:28]
        constants = compute_forecast_constants(recent, avg_hourly_wage=avg_wage)
        ipo = constants["_avg_items_per_order"]

        rows = build_forecast_rows(
            labor_daily_rows=grid, wage_rates=wage_rates, horizon_days=30
        )
        for r in rows:
            expected = round(r["forecast_orders"] * ipo, 1)
            self.assertAlmostEqual(
                r["forecast_items"], expected, places=1,
                msg=f"forecast_items mismatch for {r['date']}"
            )

    def test_excluded_days_dropped_from_seed(self):
        """An anomaly day with forecast_exclude=TRUE should not distort forecasts."""
        grid_clean = _labor_daily_grid()
        grid_dirty = _labor_daily_grid(anomaly_day=5, anomaly_excluded=False)
        grid_excluded = _labor_daily_grid(anomaly_day=5, anomaly_excluded=True)

        rows_clean = build_forecast_rows(
            labor_daily_rows=grid_clean, wage_rates=_wage_rates(), horizon_days=30
        )
        rows_excluded = build_forecast_rows(
            labor_daily_rows=grid_excluded, wage_rates=_wage_rates(), horizon_days=30
        )
        rows_dirty = build_forecast_rows(
            labor_daily_rows=grid_dirty, wage_rates=_wage_rates(), horizon_days=30
        )

        # excluded rows should produce same result as clean (anomaly dropped)
        for a, b in zip(rows_clean, rows_excluded):
            self.assertEqual(
                a["forecast_orders"], b["forecast_orders"],
                f"Excluded anomaly should not affect forecast for {a['date']}"
            )
        # dirty (not excluded) anomaly should differ for at least the affected DOW
        orders_clean = [r["forecast_orders"] for r in rows_clean]
        orders_dirty = [r["forecast_orders"] for r in rows_dirty]
        self.assertNotEqual(
            orders_clean, orders_dirty,
            "Non-excluded anomaly should change at least one forecast value"
        )

    def test_empty_grid_returns_empty(self):
        rows = build_forecast_rows(
            labor_daily_rows=[_labor_daily_grid()[0]],  # header only
            wage_rates=_wage_rates(),
        )
        self.assertEqual(rows, [])

    def test_horizon_configurable(self):
        rows = build_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            horizon_days=7,
        )
        self.assertEqual(len(rows), 7)

    def test_all_fields_present(self):
        rows = self._rows()
        required = {"date", "forecast_orders", "forecast_items", "forecast_generated_at"}
        for r in rows:
            self.assertEqual(set(r.keys()), required, f"Missing fields in {r}")

    def test_forecast_orders_non_negative(self):
        rows = self._rows()
        for r in rows:
            self.assertGreaterEqual(r["forecast_orders"], 0)
            self.assertGreaterEqual(r["forecast_items"], 0.0)


if __name__ == "__main__":
    unittest.main()
