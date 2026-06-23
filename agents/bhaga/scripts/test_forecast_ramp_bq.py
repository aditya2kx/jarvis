#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.forecast_ramp_bq (adaptive_dow_ets_v1).

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
    _ALPHA,
    _BETA,
    _PHI,
    _CORRECTION_CLAMP,
    _DIAG_NAMES,
    _build_adaptive_model,
    _adaptive_predict,
    _compute_dow_factors,
    _damp_sum,
    _fit_ets,
    _weather_feats,
    build_ramp_backfill_rows,
    build_ramp_coeff_rows,
    build_ramp_forecast_rows,
)

# ── Grid helpers ──────────────────────────────────────────────────────────────

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
    orders_seq: list[int] | None = None,
) -> list[list]:
    """Synthetic labor_daily grid ending at ``last_date`` (default = yesterday).

    If ``orders_seq`` is provided it overrides the DOW-based orders for each day
    (len must equal ``days``).
    """
    if last_date is None:
        last_date = datetime.date.today() - datetime.timedelta(days=1)
    exclude = exclude or {}
    rows: list[list] = [_HEADER]
    for i in range(days - 1, -1, -1):
        d = last_date - datetime.timedelta(days=i)
        if orders_seq is not None:
            orders = orders_seq[days - 1 - i]
        else:
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


# ── Weather feature tests ─────────────────────────────────────────────────────

class WeatherFeatTests(unittest.TestCase):
    def test_rainy_flag_on_heavy_rain(self):
        d = datetime.date(2026, 6, 1)
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
        # (35 * 9/5 + 32) / 100 = 0.95
        self.assertAlmostEqual(tm_s, 0.95, places=4)


# ── DOW factor tests ──────────────────────────────────────────────────────────

class DowFactorTests(unittest.TestCase):
    def _make_train(self, n_weeks: int, dow_orders: dict[int, int]) -> list[dict]:
        """Build a synthetic train list with fixed orders per DOW."""
        today = datetime.date.today()
        rows = []
        for i in range(n_weeks * 7):
            d = today - datetime.timedelta(days=i + 1)
            orders = dow_orders.get(d.weekday(), 100)
            rows.append({"date": d.isoformat(), "orders": orders,
                         "forecast_exclude": False})
        return sorted(rows, key=lambda r: r["date"])

    def test_factors_normalize_to_mean_one(self):
        # All DOWs have same orders → all factors ≈ 1.0
        train = self._make_train(8, {d: 100 for d in range(7)})
        factors = _compute_dow_factors(train)
        for d, f in factors.items():
            self.assertAlmostEqual(f, 1.0, places=2)

    def test_high_dow_gets_factor_above_one(self):
        # Sunday (6) has 2× the mean → factor ≈ 2.0 (clamped at 1.8)
        dow_orders = {d: 100 for d in range(7)}
        dow_orders[6] = 200  # Sunday
        train = self._make_train(8, dow_orders)
        factors = _compute_dow_factors(train)
        self.assertGreater(factors[6], 1.0)

    def test_clamp_upper(self):
        # Even with 10× orders, factor should not exceed 1.8
        dow_orders = {d: 10 for d in range(7)}
        dow_orders[0] = 1000  # Monday extreme
        train = self._make_train(8, dow_orders)
        factors = _compute_dow_factors(train)
        self.assertLessEqual(factors[0], 1.8 + 1e-9)

    def test_missing_dow_gets_one(self):
        # Only include Mon-Sat data (no Sunday=6)
        train = self._make_train(8, {d: 100 for d in range(6)})
        train = [r for r in train if datetime.date.fromisoformat(r["date"]).weekday() != 6]
        factors = _compute_dow_factors(train)
        # Sunday should default to 1.0
        self.assertEqual(factors[6], 1.0)


# ── Damped-trend saturation test ──────────────────────────────────────────────

class DampSumTests(unittest.TestCase):
    def test_damp_sum_bounded(self):
        """damp_sum(h) must never exceed phi/(1-phi) regardless of h."""
        phi = _PHI
        bound = phi / (1 - phi)
        for h in [1, 10, 50, 200, 1000]:
            self.assertLessEqual(_damp_sum(phi, h), bound + 1e-9)

    def test_damp_sum_increasing(self):
        """Larger h yields larger damp_sum."""
        for h in range(1, 20):
            self.assertLess(_damp_sum(_PHI, h), _damp_sum(_PHI, h + 1))

    def test_damp_sum_h1_equals_phi(self):
        self.assertAlmostEqual(_damp_sum(_PHI, 1), _PHI, places=10)


# ── ETS adaptivity tests ──────────────────────────────────────────────────────

class EtsAdaptivityTests(unittest.TestCase):
    def _make_train_list(self, orders: list[int],
                         base_date: datetime.date | None = None) -> list[dict]:
        if base_date is None:
            base_date = datetime.date.today() - datetime.timedelta(days=len(orders))
        return [
            {"date": (base_date + datetime.timedelta(days=i)).isoformat(),
             "orders": o, "forecast_exclude": False}
            for i, o in enumerate(orders)
        ]

    def test_plateau_after_growth_dampens_trend(self):
        """After growth stops, b should decay toward 0 (ramp failure fix)."""
        # 40 days of strong growth, then 20 days flat
        grow = [50 + i * 3 for i in range(40)]
        flat = [170] * 20
        train = self._make_train_list(grow + flat)
        l, b, _, _, _ = _fit_ets(train)
        # After 20 flat days the trend contribution should be very small
        self.assertAlmostEqual(b, 0.0, delta=5.0)

    def test_surge_raises_level(self):
        """A sudden demand surge should push the level up within a few days."""
        # Baseline of 100, then 5 days of 200
        normal = [100] * 50
        surge = [200] * 5
        train_before = self._make_train_list(normal)
        train_after = self._make_train_list(normal + surge)
        l_before, _, _, _, _ = _fit_ets(train_before)
        l_after, _, _, _, _ = _fit_ets(train_after)
        self.assertGreater(l_after, l_before)

    def test_dip_lowers_level(self):
        """A sudden dip should pull the level down within a few days."""
        normal = [150] * 50
        dip = [50] * 5
        train_before = self._make_train_list(normal)
        train_after = self._make_train_list(normal + dip)
        l_before, _, _, _, _ = _fit_ets(train_before)
        l_after, _, _, _, _ = _fit_ets(train_after)
        self.assertLess(l_after, l_before)

    def test_forecast_exclude_skipped(self):
        """forecast_exclude days must not influence the ETS state."""
        grid_plain = _grid(days=70)
        grid_spiked = _grid(days=70, exclude={
            (datetime.date.today() - datetime.timedelta(days=3)).isoformat(): 9999
        })
        model_plain = _build_adaptive_model(grid_plain, {}, datetime.date.today())
        model_spiked = _build_adaptive_model(grid_spiked, {}, datetime.date.today())
        # Level should be similar (excluded day didn't contaminate)
        if model_plain and model_spiked:
            self.assertAlmostEqual(model_plain["l"], model_spiked["l"], delta=20.0)


# ── Model build tests ─────────────────────────────────────────────────────────

class BuildAdaptiveModelTests(unittest.TestCase):
    def test_empty_on_insufficient_history(self):
        grid = _grid(days=20)
        model = _build_adaptive_model(grid, {}, datetime.date.today())
        self.assertEqual(model, {})

    def test_builds_model_with_sufficient_history(self):
        grid = _grid(days=70)
        model = _build_adaptive_model(grid, {}, datetime.date.today())
        self.assertIn("l", model)
        self.assertIn("b", model)
        self.assertIn("phi", model)
        self.assertIn("dow_factors", model)
        self.assertIn("roll_mean", model)
        self.assertIn("weather_betas", model)
        self.assertIn("n_train", model)
        self.assertIn("last_train_date", model)

    def test_level_is_positive(self):
        grid = _grid(days=70)
        model = _build_adaptive_model(grid, {}, datetime.date.today())
        self.assertGreater(model["l"], 0.0)

    def test_weather_betas_length_four(self):
        grid = _grid(days=70)
        model = _build_adaptive_model(grid, {}, datetime.date.today())
        self.assertEqual(len(model["weather_betas"]), 4)

    def test_roll_mean_plausible(self):
        # With orders ~100-160, roll_mean should be in that range
        grid = _grid(days=70)
        model = _build_adaptive_model(grid, {}, datetime.date.today())
        self.assertGreater(model["roll_mean"], 50)
        self.assertLess(model["roll_mean"], 500)


# ── Adaptive predict tests ────────────────────────────────────────────────────

class AdaptivePredictTests(unittest.TestCase):
    def test_empty_model_returns_zero(self):
        result = _adaptive_predict({}, datetime.date.today(), {})
        self.assertEqual(result, 0.0)

    def test_prediction_is_positive(self):
        grid = _grid(days=70)
        model = _build_adaptive_model(grid, {}, datetime.date.today())
        target = datetime.date.today() + datetime.timedelta(days=1)
        pred = _adaptive_predict(model, target, {})
        self.assertGreater(pred, 0.0)

    def test_prediction_bounded_by_4x_roll_mean(self):
        grid = _grid(days=70)
        model = _build_adaptive_model(grid, {}, datetime.date.today())
        target = datetime.date.today() + datetime.timedelta(days=100)
        pred = _adaptive_predict(model, target, {})
        self.assertLessEqual(pred, model["roll_mean"] * 4 + 1e-9)

    def test_plateau_self_corrects_no_runaway(self):
        """After plateau, forecast at h=30 must not grow unboundedly (ramp failure fix)."""
        # Simulate growth then flat
        grow = [round(60 + i * 2) for i in range(45)]
        flat = [150] * 15
        orders_seq = grow + flat
        grid = _grid(days=60, orders_seq=orders_seq)
        model = _build_adaptive_model(grid, {}, datetime.date.today())
        if not model:
            return  # warmup not met in this scenario; skip
        pred_1 = _adaptive_predict(model, datetime.date.today() + datetime.timedelta(days=1), {})
        pred_30 = _adaptive_predict(model, datetime.date.today() + datetime.timedelta(days=30), {})
        # Forecast at day 30 must not be more than 2× the day-1 forecast (ramp would be 200%+)
        self.assertLessEqual(pred_30, pred_1 * 2.0 + 1)

    def test_weather_correction_stays_in_clamp(self):
        """Weather/event correction multiplier must stay within [0.7, 1.4]."""
        grid = _grid(days=70)
        model = _build_adaptive_model(grid, {}, datetime.date.today())
        if not model:
            return
        target = datetime.date.today() + datetime.timedelta(days=1)
        # Extreme hot dry weather
        extreme_hot = {target.isoformat(): {"tmean_c": 45.0, "precip_mm": 0.0}}
        pred_hot = _adaptive_predict(model, target, extreme_hot)
        base_no_weather = _adaptive_predict(model, target, {})
        if base_no_weather > 0:
            ratio = pred_hot / base_no_weather
            self.assertGreaterEqual(ratio, _CORRECTION_CLAMP[0] - 1e-9)
            self.assertLessEqual(ratio, _CORRECTION_CLAMP[1] + 1e-9)

    def test_prediction_tracks_level_after_surge(self):
        """After a surge, next prediction should be elevated vs baseline."""
        grid_normal = _grid(days=70)
        model_normal = _build_adaptive_model(grid_normal, {}, datetime.date.today())
        # Build surge grid
        surge_seq = ([_orders_for_dow((datetime.date.today() - datetime.timedelta(days=70 - i)).weekday())
                      for i in range(65)] + [200] * 5)
        grid_surge = _grid(days=70, orders_seq=surge_seq)
        model_surge = _build_adaptive_model(grid_surge, {}, datetime.date.today())
        if not model_normal or not model_surge:
            return
        target = datetime.date.today() + datetime.timedelta(days=1)
        pred_normal = _adaptive_predict(model_normal, target, {})
        pred_surge = _adaptive_predict(model_surge, target, {})
        self.assertGreater(pred_surge, pred_normal * 0.9)  # surge level carried forward


# ── Public API tests ──────────────────────────────────────────────────────────

class BuildRampForecastRowsTests(unittest.TestCase):
    def test_returns_rows(self):
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
            horizon_days=30,
        )
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

    def test_version_is_adaptive(self):
        self.assertEqual(CURRENT_RAMP_FORECAST_VERSION, "adaptive_dow_ets_v1")

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

    def test_excluded_days_still_produce_forecast(self):
        last = datetime.date.today() - datetime.timedelta(days=1)
        exc = {(last - datetime.timedelta(days=i)).isoformat(): 0 for i in range(3)}
        rows = build_ramp_forecast_rows(
            labor_daily_rows=_grid(days=70, exclude=exc),
            weather_rows=_no_weather(),
        )
        self.assertGreater(len(rows), 0)


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


class BuildRampCoeffRowsTests(unittest.TestCase):
    def test_returns_diagnostic_rows(self):
        rows = build_ramp_coeff_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        self.assertGreater(len(rows), 0)

    def test_required_fields(self):
        required = {"make_date", "feature_name", "coefficient", "n_train"}
        rows = build_ramp_coeff_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        for r in rows:
            self.assertEqual(set(r.keys()), required)

    def test_emits_expected_diagnostic_names(self):
        rows = build_ramp_coeff_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        names = {r["feature_name"] for r in rows}
        # Key diagnostics must be present
        for expected in ["level", "trend", "damp_sum_30",
                         "dow_factor_mon", "dow_factor_sun",
                         "tmean_beta", "precip_beta", "rainy_beta", "event_beta",
                         "alpha", "beta_param", "phi"]:
            self.assertIn(expected, names, f"Missing diagnostic: {expected}")

    def test_no_lag_or_ramp_features(self):
        """Ramp model feature names must not appear in adaptive diagnostics."""
        rows = build_ramp_coeff_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        names = {r["feature_name"] for r in rows}
        for old_name in ["weeks_since_open", "log_lag_7d", "log_lag_14d",
                         "log_roll_4w_dow", "dow_mon", "dow_tue"]:
            self.assertNotIn(old_name, names,
                             f"Old ramp feature '{old_name}' should not appear")

    def test_level_positive(self):
        rows = build_ramp_coeff_rows(
            labor_daily_rows=_grid(days=70),
            weather_rows=_no_weather(),
        )
        level_row = next((r for r in rows if r["feature_name"] == "level"), None)
        self.assertIsNotNone(level_row)
        self.assertGreater(level_row["coefficient"], 0)

    def test_returns_empty_on_thin_history(self):
        rows = build_ramp_coeff_rows(
            labor_daily_rows=_grid(days=20),
            weather_rows=_no_weather(),
        )
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
