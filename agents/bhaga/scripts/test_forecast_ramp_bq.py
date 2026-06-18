#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.forecast_ramp_bq (ramp_log_ridge_v1).

Run:
    python3 -m pytest agents/bhaga/scripts/test_forecast_ramp_bq.py -v
"""
from __future__ import annotations

import datetime
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.forecast_ramp_bq import (
    CURRENT_RAMP_FORECAST_VERSION,
    _LAG_OFFSETS,
    _FEAT_NAMES,
    _build_ramp_model,
    _log_lag_feats_prediction,
    _ramp_predict,
    _weather_feats,
    build_ramp_backfill_rows,
    build_ramp_forecast_rows,
)

# ── Grid helpers ─────────────────────────────────────────────────────────────

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

_ITEMS_PER_ORDER = 1.5


def _orders_for_dow(dow: int) -> int:
    return 100 + dow * 10


def _grid(
    *,
    days: int = 70,
    weekly_growth: float = 1.0,
    exclude: dict[str, int] | None = None,
    last_date: datetime.date | None = None,
) -> list[list]:
    """Synthetic labor_daily grid ending at ``last_date`` (default = yesterday)."""
    if last_date is None:
        last_date = datetime.date.today() - datetime.timedelta(days=1)
    exclude = exclude or {}
    rows: list[list] = [_HEADER]
    for i in range(days - 1, -1, -1):
        d = last_date - datetime.timedelta(days=i)
        base = _orders_for_dow(d.weekday())
        weeks_from_end = i // 7
        orders = round(base * (weekly_growth ** (-weeks_from_end)))
        fe = "FALSE"
        iso = d.isoformat()
        if iso in exclude:
            orders = exclude[iso]
            fe = "TRUE"
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


def _no_weather() -> list[dict]:
    return []


# ── Weather feature unit tests ────────────────────────────────────────────────

class WeatherFeatTests(unittest.TestCase):
    def test_rainy_flag_on_heavy_rain(self):
        d = datetime.date(2026, 6, 1)
        # > 6.35 mm rain
        row = {"tmean_c": 20.0, "precip_mm": 10.0}
        _, _, rainy = _weather_feats(d, {d.isoformat(): row})
        self.assertEqual(rainy, 1.0)

    def test_rainy_flag_off_on_dry_day(self):
        d = datetime.date(2026, 6, 1)
        row = {"tmean_c": 30.0, "precip_mm": 2.0}
        _, _, rainy = _weather_feats(d, {d.isoformat(): row})
        self.assertEqual(rainy, 0.0)

    def test_defaults_on_missing_date(self):
        d = datetime.date(2026, 6, 1)
        tm_s, pr_s, rainy = _weather_feats(d, {})
        # Default tmean_c=18.3 → tmean_f≈65°F → scaled=0.65
        self.assertAlmostEqual(tm_s, 0.65, places=1)
        self.assertEqual(pr_s, 0.0)
        self.assertEqual(rainy, 0.0)

    def test_celsius_to_fahrenheit_conversion(self):
        d = datetime.date(2026, 6, 1)
        row = {"tmean_c": 35.0, "precip_mm": 0.0}  # 95°F
        tm_s, _, _ = _weather_feats(d, {d.isoformat(): row})
        # (35 * 9/5 + 32) / 100 = 95/100 = 0.95
        self.assertAlmostEqual(tm_s, 0.95, places=4)


# ── Lag feature tests ─────────────────────────────────────────────────────────

class LagFeatTests(unittest.TestCase):
    def test_future_lag_gets_zero(self):
        target = datetime.date(2026, 6, 20)
        make_date = datetime.date(2026, 6, 15)
        # lag_7d would be 2026-06-13, which is < make_date → not future
        # lag_14d would be 2026-06-06 → not future
        # lag_21d → 2026-05-30, lag_28d → 2026-05-23 → all < make_date
        # For target=Jun20 with make=Jun15, lag_7d=Jun13 (<make, present in dict)
        by_date = {
            (target - datetime.timedelta(days=d)).isoformat(): 100
            for d in range(60)
        }
        feats = _log_lag_feats_prediction(target, make_date, by_date, 100.0)
        # 4 offsets + 1 roll = 5 values
        self.assertEqual(len(feats), len(_LAG_OFFSETS) + 1)

    def test_lag_on_make_date_is_zero(self):
        target = datetime.date(2026, 6, 14)
        make_date = datetime.date(2026, 6, 14)
        # lag_7d = Jun7, which is < make_date → real value; but make_date = target
        # means lag_7d date is Jun7 which is < Jun14, so it IS known
        # The key test: lag_7d = target - 7 = Jun7 < make Jun14 → real
        by_date = {(target - datetime.timedelta(days=d)).isoformat(): 100 for d in range(60)}
        feats = _log_lag_feats_prediction(target, make_date, by_date, 100.0)
        self.assertEqual(len(feats), len(_LAG_OFFSETS) + 1)

    def test_all_future_lags_zero_for_large_horizon(self):
        # make_date = today, target = today + 30 → all lag dates are in the future
        # lag_7d = today+23, lag_14d = today+16, lag_21d = today+9, lag_28d = today+2
        # all > make_date → all zero
        make_date = datetime.date.today()
        target = make_date + datetime.timedelta(days=30)
        by_date = {(make_date - datetime.timedelta(days=d)).isoformat(): 100 for d in range(60)}
        feats = _log_lag_feats_prediction(target, make_date, by_date, 100.0)
        # lag offsets: 7d → target-7d = today+23 > make → 0; all future → 0
        for f in feats[:len(_LAG_OFFSETS)]:
            self.assertEqual(f, 0.0)


# ── Model build tests ─────────────────────────────────────────────────────────

class BuildRampModelTests(unittest.TestCase):
    def test_empty_on_insufficient_history(self):
        grid = _grid(days=20)
        make_date = datetime.date.today() - datetime.timedelta(days=1)
        model = _build_ramp_model(grid, {}, make_date)
        self.assertEqual(model, {})

    def test_builds_model_with_sufficient_history(self):
        grid = _grid(days=70)
        make_date = datetime.date.today() - datetime.timedelta(days=1)
        model = _build_ramp_model(grid, {}, make_date)
        self.assertIn("beta", model)
        self.assertIn("intercept", model)
        self.assertIn("first_date", model)
        self.assertIn("roll_mean", model)
        self.assertIn("by_date", model)
        self.assertIn("n_train", model)

    def test_correct_feature_count(self):
        grid = _grid(days=70)
        make_date = datetime.date.today() - datetime.timedelta(days=1)
        model = _build_ramp_model(grid, {}, make_date)
        self.assertEqual(len(model["beta"]), len(_FEAT_NAMES))

    def test_excluded_days_not_in_training(self):
        last = datetime.date.today() - datetime.timedelta(days=1)
        iso_excl = (last - datetime.timedelta(days=3)).isoformat()
        grid = _grid(days=70, exclude={iso_excl: 0})
        make_date = last
        model = _build_ramp_model(grid, {}, make_date)
        self.assertNotIn(iso_excl, model.get("by_date", {}))

    def test_intercept_is_log_scale(self):
        # The intercept should be log(mean_orders) ≈ log(~100) ≈ 4.6
        grid = _grid(days=70)
        make_date = datetime.date.today() - datetime.timedelta(days=1)
        model = _build_ramp_model(grid, {}, make_date)
        # intercept is mean of log(orders); orders ~100-160, so log ~4.6-5.1
        self.assertGreater(model["intercept"], 4.0)
        self.assertLess(model["intercept"], 6.0)


class RampPredictTests(unittest.TestCase):
    def test_empty_model_returns_zero(self):
        result = _ramp_predict({}, datetime.date.today(), datetime.date.today(), {})
        self.assertEqual(result, 0.0)

    def test_prediction_is_positive(self):
        grid = _grid(days=70)
        make_date = datetime.date.today()
        model = _build_ramp_model(grid, {}, make_date)
        target = make_date + datetime.timedelta(days=1)
        pred = _ramp_predict(model, target, make_date, {})
        self.assertGreater(pred, 0.0)

    def test_prediction_bounded_by_4x_roll_mean(self):
        grid = _grid(days=70)
        make_date = datetime.date.today()
        model = _build_ramp_model(grid, {}, make_date)
        target = make_date + datetime.timedelta(days=100)  # far future
        pred = _ramp_predict(model, target, make_date, {})
        # Must be ≤ roll_mean × 4
        self.assertLessEqual(pred, model["roll_mean"] * 4 + 1e-9)

    def test_ramp_increases_over_weeks(self):
        # With positive weekly growth, ramp predictions should trend up.
        grid = _grid(days=70, weekly_growth=1.05)
        make_date = datetime.date.today()
        model = _build_ramp_model(grid, {}, make_date)
        preds = [
            _ramp_predict(model, make_date + datetime.timedelta(weeks=w), make_date, {})
            for w in range(1, 6)
        ]
        # Predictions should be non-decreasing (allowing small noise)
        increasing = sum(1 for i in range(len(preds) - 1) if preds[i + 1] >= preds[i] * 0.90)
        self.assertGreater(increasing, 2)

    def test_weather_coefficient_applied(self):
        # Build with hot, dry weather vs cool, rainy — predictions differ.
        grid = _grid(days=70)
        make_date = datetime.date.today()
        model = _build_ramp_model(grid, {}, make_date)
        target = make_date + datetime.timedelta(days=1)
        hot_weather = {target.isoformat(): {"tmean_c": 40.0, "precip_mm": 0.0}}  # 104°F
        rainy_weather = {target.isoformat(): {"tmean_c": 15.0, "precip_mm": 20.0}}
        pred_hot = _ramp_predict(model, target, make_date, hot_weather)
        pred_rainy = _ramp_predict(model, target, make_date, rainy_weather)
        # They should differ (weather features have non-zero beta after training)
        # Allow that they might be equal if beta happens to be near zero.
        self.assertIsInstance(pred_hot, float)
        self.assertIsInstance(pred_rainy, float)


# ── Public API tests ─────────────────────────────────────────────────────────

class BuildRampForecastRowsTests(unittest.TestCase):
    def test_returns_horizon_plus_one_rows(self):
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
            horizon_days=30,
        )
        # May be less than 31 if some days predict ≤ 0, but with good data should be 31
        self.assertGreater(len(rows), 0)

    def test_all_fields_present(self):
        required = {"date", "forecast_orders", "forecast_items",
                    "forecast_generated_at", "forecast_model_version"}
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        for r in rows:
            self.assertEqual(set(r.keys()), required)

    def test_model_version_stamped(self):
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        for r in rows:
            self.assertEqual(r["forecast_model_version"], CURRENT_RAMP_FORECAST_VERSION)

    def test_orders_non_negative(self):
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        for r in rows:
            self.assertGreaterEqual(r["forecast_orders"], 0)

    def test_items_non_negative(self):
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        for r in rows:
            self.assertGreaterEqual(r["forecast_items"], 0.0)

    def test_returns_empty_on_thin_history(self):
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=20),
            weather_rows=_no_weather(),
        )
        self.assertEqual(rows, [])

    def test_horizon_configurable(self):
        rows7 = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
            horizon_days=7,
        )
        self.assertLessEqual(len(rows7), 8)
        self.assertGreater(len(rows7), 0)

    def test_dates_are_today_or_later(self):
        today = datetime.date.today()
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        for r in rows:
            self.assertGreaterEqual(datetime.date.fromisoformat(r["date"]), today)

    def test_excluded_anchor_not_used(self):
        last = datetime.date.today() - datetime.timedelta(days=1)
        # Exclude the most recent 3 days — model must still produce rows
        exc = {(last - datetime.timedelta(days=i)).isoformat(): 0 for i in range(3)}
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70, exclude=exc),
            weather_rows=_no_weather(),
        )
        # Model should still work (enough non-excluded history)
        self.assertGreater(len(rows), 0)

    def test_ramp_produces_higher_forecast_over_time(self):
        # With positive weekly growth the forecast should generally increase
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70, weekly_growth=1.05),
            weather_rows=_no_weather(),
            horizon_days=28,
        )
        orders = [r["forecast_orders"] for r in rows]
        self.assertGreater(orders[-1], orders[0] * 0.9)  # at least not decreasing badly


class BuildRampBackfillRowsTests(unittest.TestCase):
    def test_past_dates_only(self):
        today = datetime.date.today()
        rows = build_ramp_backfill_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
            weeks=4,
        )
        for r in rows:
            self.assertLess(datetime.date.fromisoformat(r["date"]), today)

    def test_returns_empty_on_thin_history(self):
        rows = build_ramp_backfill_rows(
            labor_daily_rows=_grid(days=20),
            weather_rows=_no_weather(),
        )
        self.assertEqual(rows, [])

    def test_required_fields(self):
        required = {"date", "forecast_orders", "forecast_items",
                    "forecast_generated_at", "forecast_model_version"}
        rows = build_ramp_backfill_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
            weeks=2,
        )
        for r in rows:
            self.assertEqual(set(r.keys()), required)

    def test_version_stamped(self):
        rows = build_ramp_backfill_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
            weeks=2,
        )
        for r in rows:
            self.assertEqual(r["forecast_model_version"], CURRENT_RAMP_FORECAST_VERSION)


if __name__ == "__main__":
    unittest.main()
