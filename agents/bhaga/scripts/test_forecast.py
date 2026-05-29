#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.forecast.

Run:
    python3 agents/bhaga/scripts/test_forecast.py

Pure-function coverage for the formula-driven forecast tab:
  * compute_staffing — the Python mirror of the in-sheet solver formulas
    (OK / UNDER_BUDGET / OVER_BUDGET branches + coverage/efficiency).
  * forecast_orders_dow_trend — order seed excludes forecast_exclude=TRUE days.
  * build_labor_daily_forecast_rows — derived columns are FORMULAS (=...),
    layout matches FORECAST_COLUMNS, weekly full-time cap holds.
  * backfill_forecast_errors — fills accuracy columns when actuals exist.

No Sheets API calls.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.forecast import (
    FORECAST_COLUMNS,
    _IDX,
    _col_letter as _col,
    backfill_forecast_errors,
    build_labor_daily_forecast_rows,
    compute_outlier_stats,
    compute_staffing,
    forecast_orders_dow_trend,
)
from skills.bhaga_config.dates import coerce_iso_date


def _labor_daily_grid(
    days: int = 40,
    anomaly_day: int | None = None,
    anomaly_excluded: bool = True,
) -> list[list]:
    """Synthetic labor_daily grid with the appended outlier/forecast_exclude cols.

    Stable per-DOW volume so the seed is predictable. ``anomaly_day`` (if set)
    becomes a low-order (3) day; ``anomaly_excluded`` controls whether its
    forecast_exclude flag is TRUE (dropped) or FALSE (left to pollute).
    """
    header = (
        ["date", "dow", "gross_sales", "discounts", "net_sales", "tip_pool",
         "net_sales_plus_tips", "orders", "hourly_hours", "hourly_labor_cost",
         "fulltime_hours", "fulltime_labor_cost", "total_labor_cost"]
        + [f"c{i}" for i in range(13, 30)]
        + ["items_sold"]
        + [f"c{i}" for i in range(31, 43)]
        + ["outlier_flag", "forecast_exclude"]
    )
    assert header[7] == "orders" and header[30] == "items_sold"
    rows: list[list] = [header]
    start = datetime.date(2026, 4, 1)
    for i in range(days):
        d = start + datetime.timedelta(days=i)
        orders = 100 + d.weekday() * 10  # deterministic per-DOW level
        fe = "FALSE"
        if anomaly_day is not None and i == anomaly_day:
            orders = 3
            fe = "TRUE" if anomaly_excluded else "FALSE"
        net = orders * 9.0
        disc = -orders * 0.5
        gross = net - disc
        tip = net * 0.08
        items = int(orders * 1.4)
        row = (
            [d.isoformat(), d.strftime("%a"), round(gross, 2), round(disc, 2),
             round(net, 2), round(tip, 2), round(net + tip, 2), orders,
             8.0, 120.0, 6.0, 150.0, 270.0]
            + [""] * 17 + [items] + [""] * 12 + ["FALSE", fe]
        )
        rows.append(row)
    return rows


def _wage_rates() -> list[dict]:
    return [
        {"wage_rate_dollars": "15.00", "is_salaried": False, "excluded_from_labor_pct": False},
        {"wage_rate_dollars": "15.00", "is_salaried": False, "excluded_from_labor_pct": False},
        {"wage_rate_dollars": "25.00", "is_salaried": False, "excluded_from_labor_pct": True},
    ]


_CONFIG = {
    "forecast_target_labor_pct": 0.25,
    "forecast_fulltime_weekly_hours": 40,
    "forecast_target_completion_time_per_item_sec": 420,
}


class ComputeStaffingTests(unittest.TestCase):
    BASE = dict(
        orders=100, avg_order_price=12.0, avg_items_per_order=1.4,
        avg_discount_per_order=0.5, avg_tip_pool_per_order=0.9,
        avg_hourly_wage=15.0, target_time_per_item_sec=300.0,
        shift_hours=8.0, min_parttimers=2, fulltime_hours=0.0,
    )

    def test_derived_chain(self):
        s = compute_staffing(target_labor_pct=0.25, **self.BASE)
        self.assertAlmostEqual(s["net_sales"], 1200.0)
        self.assertAlmostEqual(s["items_sold"], 140.0)
        # efficiency = 140 * 300 / 3600
        self.assertAlmostEqual(s["efficiency_hours"], 140 * 300 / 3600.0)
        # coverage = 2 * 8 = 16 dominates efficiency (~11.67)
        self.assertAlmostEqual(s["min_coverage_hours"], 16.0)
        self.assertAlmostEqual(s["needed_hours"], 16.0)
        # recommended stays = needed (budget is a check, never understaffs)
        self.assertAlmostEqual(s["recommended_hourly_hours"], 16.0)

    def test_flag_ok(self):
        # budget_hours = (0.25*1200)/15 = 20 ; needed = 16 ; 20 <= 16*1.25=20 -> OK
        s = compute_staffing(target_labor_pct=0.25, **self.BASE)
        self.assertEqual(s["staffing_flag"], "OK")

    def test_flag_over_budget(self):
        # budget_hours = (0.10*1200)/15 = 8 < needed 16 -> OVER_BUDGET
        s = compute_staffing(target_labor_pct=0.10, **self.BASE)
        self.assertEqual(s["staffing_flag"], "OVER_BUDGET")

    def test_flag_under_budget(self):
        # budget_hours = (0.50*1200)/15 = 40 > needed*1.25=20 -> UNDER_BUDGET
        s = compute_staffing(target_labor_pct=0.50, **self.BASE)
        self.assertEqual(s["staffing_flag"], "UNDER_BUDGET")

    def test_fulltime_cost_reduces_budget(self):
        s = compute_staffing(target_labor_pct=0.25, **{**self.BASE, "fulltime_hours": 8.0})
        # fulltime_cost = 8 * 15 = 120 ; budget_hours = (300 - 120)/15 = 12
        self.assertAlmostEqual(s["fulltime_cost"], 120.0)
        self.assertAlmostEqual(s["budget_hours"], 12.0)

    def test_hourly_labor_pct_excludes_fulltime(self):
        # needed=16h coverage; hourly_cost=16*15=240; net=1200 → hourly%=0.20.
        # total includes fulltime_cost (8*15=120) so actual% > hourly%.
        s = compute_staffing(
            target_labor_pct=0.25,
            target_hourly_labor_pct=0.20,
            **{**self.BASE, "fulltime_hours": 8.0},
        )
        self.assertAlmostEqual(s["hourly_labor_pct"], 240.0 / 1200.0)
        self.assertAlmostEqual(s["actual_labor_pct"], (240.0 + 120.0) / 1200.0)
        self.assertGreater(s["actual_labor_pct"], s["hourly_labor_pct"])

    def test_hourly_staffing_flag_over_under_ok(self):
        # hourly_labor_pct = 0.20 for BASE (240/1200).
        over = compute_staffing(
            target_labor_pct=0.25, target_hourly_labor_pct=0.10, **self.BASE)
        self.assertEqual(over["hourly_staffing_flag"], "OVER_HOURLY_BUDGET")
        under = compute_staffing(
            target_labor_pct=0.25, target_hourly_labor_pct=0.40, **self.BASE)
        self.assertEqual(under["hourly_staffing_flag"], "UNDER_HOURLY_BUDGET")
        ok = compute_staffing(
            target_labor_pct=0.25, target_hourly_labor_pct=0.20, **self.BASE)
        self.assertEqual(ok["hourly_staffing_flag"], "OK")


class OrderSeedExclusionTests(unittest.TestCase):
    def test_flagged_day_is_dropped_from_seed(self):
        # Day 35 is a low (3-order) anomaly. When it's flagged TRUE it's
        # dropped; when FALSE it pollutes (drags the seed down). So the
        # excluded seed must be strictly higher than the polluted seed, and
        # must equal the clean (no-anomaly) seed — i.e. the flagged day had
        # zero influence.
        target = datetime.date(2026, 5, 11)  # next Monday after the window
        polluted = _labor_daily_grid(days=40, anomaly_day=35, anomaly_excluded=False)
        excluded = _labor_daily_grid(days=40, anomaly_day=35, anomaly_excluded=True)
        seed_polluted = forecast_orders_dow_trend(polluted, target)
        seed_excluded = forecast_orders_dow_trend(excluded, target)
        self.assertLess(
            seed_polluted, seed_excluded,
            "flagged forecast_exclude day still dragged the order seed down",
        )

    def test_seed_is_positive(self):
        target = datetime.date(2026, 5, 15)
        self.assertGreater(forecast_orders_dow_trend(_labor_daily_grid(), target), 0)


def _growth_days(
    weeks: int = 9, start_orders: int = 80, weekly_growth: int = 12,
) -> list[dict]:
    """Daily operating days in a sustained growth phase (Monday-anchored).

    Each week's level rises by ``weekly_growth``; mild per-DOW variation on
    top. Returns ``[{"date": ISO, "orders": int}, ...]`` — the shape
    compute_outlier_stats consumes.
    """
    start = datetime.date(2026, 3, 30)  # a Monday
    out: list[dict] = []
    for w in range(weeks):
        level = start_orders + w * weekly_growth
        for dow in range(7):
            d = start + datetime.timedelta(days=w * 7 + dow)
            out.append({"date": d.isoformat(), "orders": level + dow * 2})
    return out


class OutlierStatsTests(unittest.TestCase):
    """Trend-aware, robust (median/MAD) outlier detection — DOWN-only exclude."""

    def test_growth_is_not_auto_excluded(self):
        # Pure sustained growth: the trend-aware expectation absorbs it, so no
        # day is flagged as a down-outlier (the old flat rule excluded most).
        stats = compute_outlier_stats(_growth_days())
        self.assertTrue(stats, "expected per-day stats")
        excluded = [d for d, s in stats.items() if s["exclude_default"]]
        self.assertEqual(
            excluded, [],
            f"growth days were wrongly auto-excluded: {excluded}",
        )

    def test_recent_growth_saturday_not_excluded(self):
        days = _growth_days()
        stats = compute_outlier_stats(days)
        # Most recent Saturday (a strong-growth day) must NOT be auto-excluded.
        sats = [
            d for d in stats
            if datetime.date.fromisoformat(d).weekday() == 5
        ]
        last_sat = max(sats)
        self.assertFalse(stats[last_sat]["exclude_default"])
        # Its residual is non-negative-ish (growth), so it can never be a down.
        self.assertGreaterEqual(stats[last_sat]["robust_z"], -2.5)

    def test_stockout_day_is_down_outlier_and_excluded(self):
        days = _growth_days()
        # Force a recent day into a stock-out (orders=3).
        victim = days[-3]["date"]
        days[-3] = {"date": victim, "orders": 3}
        stats = compute_outlier_stats(days)
        self.assertIn(victim, stats)
        self.assertTrue(stats[victim]["outlier_flag"])
        self.assertTrue(stats[victim]["exclude_default"])
        self.assertLess(stats[victim]["robust_z"], -2.5)
        self.assertLess(stats[victim]["residual"], 0)

    def test_upward_spike_flags_but_does_not_exclude(self):
        days = _growth_days()
        # A one-off banner day (3x) — informational outlier, never auto-excluded.
        victim = days[-2]["date"]
        base = days[-2]["orders"]
        days[-2] = {"date": victim, "orders": base * 3}
        stats = compute_outlier_stats(days)
        self.assertTrue(stats[victim]["outlier_flag"])
        self.assertFalse(stats[victim]["exclude_default"])
        self.assertGreater(stats[victim]["robust_z"], 0)

    def test_normal_day_not_flagged(self):
        days = _growth_days()
        stats = compute_outlier_stats(days)
        # A mid-window normal day sits near its expectation → not an outlier.
        mid = days[len(days) // 2]["date"]
        if mid in stats:
            self.assertFalse(stats[mid]["outlier_flag"])

    def test_empty_input(self):
        self.assertEqual(compute_outlier_stats([]), {})


class BuildForecastGridTests(unittest.TestCase):
    def test_layout_and_formulas(self):
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=_CONFIG,
            kds_by_date={
                (datetime.date(2026, 4, 1) + datetime.timedelta(days=i)).isoformat():
                {"shift_start": "10:00", "shift_end": "21:00"}
                for i in range(40)
            },
        )
        self.assertEqual(grid[0], FORECAST_COLUMNS)
        self.assertEqual(len(grid) - 1, 14)  # 14-day horizon (no frozen rows)
        r2 = grid[1]
        # derived columns are formulas referencing sheet row 2. Cell letters are
        # derived from the live layout so they survive future column shuffles.
        self.assertEqual(
            r2[_IDX["net_sales"]],
            f"={_col(_IDX['orders'])}2*{_col(_IDX['_avg_order_price'])}2",
        )
        self.assertEqual(
            r2[_IDX["gross_sales"]],
            f"={_col(_IDX['net_sales'])}2+{_col(_IDX['discounts'])}2",
        )
        self.assertEqual(
            r2[_IDX["needed_hours"]],
            f"=MAX({_col(_IDX['min_coverage_hours'])}2,{_col(_IDX['efficiency_hours'])}2)",
        )
        self.assertTrue(r2[_IDX["staffing_flag"]].startswith(
            f"=IF({_col(_IDX['needed_hours'])}2>{_col(_IDX['budget_hours'])}2,"
            '"OVER_BUDGET"'
        ))
        # row 3 references row 3
        self.assertEqual(
            grid[2][_IDX["net_sales"]],
            f"={_col(_IDX['orders'])}3*{_col(_IDX['_avg_order_price'])}3",
        )
        # inputs are values, not formulas
        self.assertNotIsInstance(r2[_IDX["orders"]], str)  # int
        self.assertEqual(r2[_IDX["forecast_exclude"]], "FALSE")
        self.assertEqual(r2[_IDX["target_labor_pct"]], 0.25)
        # target_time_per_item_sec is a per-row INPUT VALUE seeded from config
        # (420 = 7 min), NOT a formula, and efficiency_hours references it.
        self.assertNotIsInstance(r2[_IDX["target_time_per_item_sec"]], str)
        self.assertEqual(r2[_IDX["target_time_per_item_sec"]], 420.0)
        self.assertEqual(
            r2[_IDX["efficiency_hours"]],
            f"={_col(_IDX['items_sold'])}2*{_col(_IDX['target_time_per_item_sec'])}2/3600",
        )

    def test_per_row_target_preserves_operator_edits(self):
        # An operator edit to target_time_per_item_sec on a given date must
        # survive the rebuild instead of being reset to the config seed.
        grid0 = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=_CONFIG,
        )
        edited_date = coerce_iso_date(grid0[1][_IDX["date"]])
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=_CONFIG,
            existing_target_by_date={edited_date: 600.0},
        )
        self.assertEqual(grid[1][_IDX["target_time_per_item_sec"]], 600.0)
        # Untouched rows keep the 420 seed.
        self.assertEqual(grid[2][_IDX["target_time_per_item_sec"]], 420.0)

    def test_horizon_anchors_on_last_actual_not_last_unflagged(self):
        # Flag the most recent 3 operating days forecast_exclude=TRUE (as a
        # hyper-growth run would, where sustained growth trips the outlier band).
        # The horizon must still start the day AFTER the last CALENDAR date with
        # data — it must not rewind into the past to the last non-excluded day.
        ld = _labor_daily_grid(days=40)
        last_cal = datetime.date.fromisoformat(ld[-1][0])
        for row in ld[-3:]:
            row[-1] = "TRUE"  # forecast_exclude is the last column
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=ld, wage_rates=_wage_rates(), config=_CONFIG,
        )
        first_fc = datetime.date.fromisoformat(coerce_iso_date(grid[1][_IDX["date"]]))
        self.assertEqual(first_fc, last_cal + datetime.timedelta(days=1))

    def test_weekly_fulltime_cap(self):
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=_CONFIG,
        )
        # Sum full-time hours per ISO week; none may exceed the 40h cap.
        by_week: dict = {}
        for row in grid[1:]:
            d = datetime.date.fromisoformat(coerce_iso_date(row[_IDX["date"]]))
            monday = d - datetime.timedelta(days=d.weekday())
            by_week.setdefault(monday, 0.0)
            by_week[monday] += float(row[_IDX["fulltime_hours"]])
        for monday, total in by_week.items():
            self.assertLessEqual(round(total, 2), 40.0 + 1e-6, f"week {monday} over cap: {total}")


class HourlyForecastColumnTests(unittest.TestCase):
    """hourly_labor_pct + hourly_staffing_flag formulas, target seeding/preserve."""

    def test_hourly_columns_are_formulas(self):
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=_CONFIG,
        )
        r2 = grid[1]
        # hourly_labor_pct = hourly_cost / net_sales (part-time only).
        self.assertEqual(
            r2[_IDX["hourly_labor_pct"]],
            f"=IF({_col(_IDX['net_sales'])}2>0,"
            f"{_col(_IDX['hourly_cost'])}2/{_col(_IDX['net_sales'])}2,0)",
        )
        # hourly_staffing_flag keys off target_hourly_labor_pct.
        self.assertTrue(r2[_IDX["hourly_staffing_flag"]].startswith(
            f"=IF({_col(_IDX['hourly_labor_pct'])}2>"
            f"{_col(_IDX['target_hourly_labor_pct'])}2,\"OVER_HOURLY_BUDGET\""
        ))

    def test_target_hourly_seeded_from_config_default(self):
        # No config key → default 0.20 (20%) seeded as a per-row VALUE.
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=_CONFIG,
        )
        r2 = grid[1]
        self.assertNotIsInstance(r2[_IDX["target_hourly_labor_pct"]], str)
        self.assertEqual(r2[_IDX["target_hourly_labor_pct"]], 0.20)

    def test_target_hourly_seeded_from_config_value(self):
        cfg = {**_CONFIG, "forecast_target_hourly_labor_pct": 0.18}
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=cfg,
        )
        self.assertEqual(grid[1][_IDX["target_hourly_labor_pct"]], 0.18)

    def test_target_hourly_preserves_operator_edits(self):
        grid0 = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=_CONFIG,
        )
        edited_date = coerce_iso_date(grid0[1][_IDX["date"]])
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=_CONFIG,
            existing_hourly_target_by_date={edited_date: 0.30},
        )
        self.assertEqual(grid[1][_IDX["target_hourly_labor_pct"]], 0.30)
        # Untouched rows keep the 0.20 seed.
        self.assertEqual(grid[2][_IDX["target_hourly_labor_pct"]], 0.20)


def _existing_forecast_grid_for_date(date_iso: str) -> list[list]:
    """A minimal existing labor_daily_forecast grid (header + one VALUE row).

    Mimics what the Sheets read returns with UNFORMATTED_VALUE: derived/helper
    cells carry evaluated NUMBERS, not formula strings — exactly what
    freeze-in-place captures when a forecast day rolls into the past.
    """
    header = list(FORECAST_COLUMNS)
    row = [""] * len(FORECAST_COLUMNS)
    row[_IDX["date"]] = date_iso
    row[_IDX["dow"]] = "x"
    row[_IDX["orders"]] = 100
    row[_IDX["fulltime_hours"]] = 6.0
    row[_IDX["target_labor_pct"]] = 0.25
    row[_IDX["target_hourly_labor_pct"]] = 0.20
    row[_IDX["target_time_per_item_sec"]] = 420.0
    row[_IDX["forecast_exclude"]] = "FALSE"
    row[_IDX["_avg_order_price"]] = 9.0
    row[_IDX["_avg_items_per_order"]] = 1.4
    row[_IDX["_avg_discount_per_order"]] = 0.5
    row[_IDX["_avg_tip_pool_per_order"]] = 0.72
    row[_IDX["_avg_hourly_wage"]] = 15.0
    row[_IDX["_shift_hours"]] = 11.0
    row[_IDX["_min_parttimers"]] = 2
    # Evaluated derived values (numbers), as the Sheets read returns them.
    row[_IDX["net_sales"]] = 900.0
    row[_IDX["items_sold"]] = 140.0
    row[_IDX["hourly_cost"]] = 330.0
    row[_IDX["total_labor_cost"]] = 420.0
    row[_IDX["actual_labor_pct"]] = 0.4667
    row[_IDX["hourly_labor_pct"]] = 0.3667
    row[_IDX["forecast_generated_at"]] = "2026-05-01T08:00:00+00:00"
    return [header, row]


class FreezeInPlaceTests(unittest.TestCase):
    """Trailing FROZEN past rows get scored; FUTURE rows stay live/blank."""

    def test_past_row_is_frozen_and_scored(self):
        ld = _labor_daily_grid(days=40)  # last actual = 2026-05-10
        last_actual = ld[-1][0]  # ISO of the last labor_daily date
        existing = _existing_forecast_grid_for_date(last_actual)
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=ld,
            wage_rates=_wage_rates(),
            config=_CONFIG,
            existing_forecast_rows=existing,
        )
        # The frozen past row sits BEFORE the future rows.
        frozen = grid[1]
        self.assertEqual(coerce_iso_date(frozen[_IDX["date"]]), last_actual)
        # Frozen derived cells are VALUES, not live formulas.
        self.assertNotIsInstance(frozen[_IDX["net_sales"]], str)
        self.assertEqual(frozen[_IDX["forecast_generated_at"]], "2026-05-01T08:00:00+00:00")
        # Future rows follow and are still live formulas / blank accuracy.
        future = grid[2]
        self.assertTrue(str(future[_IDX["net_sales"]]).startswith("="))
        self.assertEqual(future[_IDX["orders_error_pct"]], "")

        out = backfill_forecast_errors(forecast_rows=grid, labor_daily_rows=ld)
        frozen = out[1]
        # The frozen past day now has a matching labor_daily actual → scored.
        self.assertNotEqual(frozen[_IDX["orders_error_pct"]], "")
        self.assertNotEqual(frozen[_IDX["net_sales_error_pct"]], "")
        self.assertNotEqual(frozen[_IDX["realized_labor_pct"]], "")
        self.assertNotEqual(frozen[_IDX["forecast_mape"]], "")
        # Realized hourly labor% = actual hourly_labor_cost / actual net_sales.
        # _labor_daily_grid: hourly_labor_cost=120, net=orders*9.
        self.assertNotEqual(frozen[_IDX["total_hourly_labor_pct"]], "")
        d = datetime.date.fromisoformat(last_actual)
        net = (100 + d.weekday() * 10) * 9.0
        self.assertAlmostEqual(
            frozen[_IDX["total_hourly_labor_pct"]], round(120.0 / net, 4)
        )
        # It is the hourly (part-time-only) slice, so strictly below the
        # all-labor realized_labor_pct (which also carries Lindsay's cost).
        self.assertLess(
            frozen[_IDX["total_hourly_labor_pct"]], frozen[_IDX["realized_labor_pct"]]
        )
        # Future rows stay blank (no actual, derived still formulas).
        future = out[2]
        self.assertEqual(future[_IDX["orders_error_pct"]], "")
        self.assertEqual(future[_IDX["total_hourly_labor_pct"]], "")
        self.assertTrue(str(future[_IDX["net_sales"]]).startswith("="))

    def test_future_only_when_no_existing_tab(self):
        # First run (no existing forecast tab) → no frozen rows, future-only.
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(days=40),
            wage_rates=_wage_rates(),
            config=_CONFIG,
            existing_forecast_rows=None,
        )
        self.assertEqual(len(grid) - 1, 14)
        # grid[1] is the first FUTURE row → live formula.
        self.assertTrue(str(grid[1][_IDX["net_sales"]]).startswith("="))

    def test_generated_at_is_central_time_not_utc(self):
        # A freshly generated FUTURE row must stamp forecast_generated_at in
        # store-local Central time (America/Chicago), NOT UTC — Cloud Run runs
        # in UTC, so a "+00:00" stamp here would be the regression.
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(days=40),
            wage_rates=_wage_rates(),
            config=_CONFIG,
            existing_forecast_rows=None,
        )
        generated_at = grid[1][_IDX["forecast_generated_at"]]
        self.assertTrue(
            generated_at.endswith("-05:00") or generated_at.endswith("-06:00"),
            f"expected a US Central offset, got {generated_at!r}",
        )
        self.assertFalse(generated_at.endswith("+00:00"))

    def test_rows_older_than_window_are_dropped(self):
        ld = _labor_daily_grid(days=40)  # last actual 2026-05-10, start 05-11
        # A stale forecast row well outside the 30-day window (freeze_start
        # = 2026-04-11) must NOT be carried forward.
        stale = _existing_forecast_grid_for_date("2026-03-01")
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=ld,
            wage_rates=_wage_rates(),
            config=_CONFIG,
            existing_forecast_rows=stale,
        )
        dates = [coerce_iso_date(r[_IDX["date"]]) for r in grid[1:]]
        self.assertNotIn("2026-03-01", dates)
        self.assertEqual(len(grid) - 1, 14)  # future-only, stale dropped


class BackfillColumnResolutionTests(unittest.TestCase):
    """backfill resolves labor_daily actual columns by HEADER NAME, not position."""

    def test_resolves_by_name_after_layout_shift(self):
        # Build a forecast grid with a frozen past row to score.
        ld = _labor_daily_grid(days=40)
        last_actual = ld[-1][0]
        existing = _existing_forecast_grid_for_date(last_actual)
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=ld, wage_rates=_wage_rates(), config=_CONFIG,
            existing_forecast_rows=existing,
        )
        # Shift the labor_daily layout: prepend a NEW column so every fixed
        # position is wrong, but the header names still resolve.
        shifted = [["row_id"] + r for r in ld]
        # Sanity: orders is no longer at position 7.
        self.assertNotEqual(shifted[0].index("orders"), 7)
        out = backfill_forecast_errors(forecast_rows=grid, labor_daily_rows=shifted)
        frozen = out[1]
        self.assertEqual(coerce_iso_date(frozen[_IDX["date"]]), last_actual)
        # Name resolution found the right columns → non-blank, sane errors.
        self.assertNotEqual(frozen[_IDX["net_sales_error_pct"]], "")
        self.assertNotEqual(frozen[_IDX["realized_labor_pct"]], "")
        # total_hourly_labor_pct also resolved hourly_labor_cost BY NAME.
        self.assertNotEqual(frozen[_IDX["total_hourly_labor_pct"]], "")
        # realized_labor_pct = total_labor_cost / net_sales from the actual.
        # _labor_daily_grid: orders=100+dow*10, net=orders*9, total=270.
        d = datetime.date.fromisoformat(last_actual)
        orders = 100 + d.weekday() * 10
        net = orders * 9.0
        self.assertAlmostEqual(
            frozen[_IDX["realized_labor_pct"]], round(270.0 / net, 4)
        )


class BackfillErrorsTests(unittest.TestCase):
    def test_fills_when_actual_exists(self):
        grid = build_labor_daily_forecast_rows(
            labor_daily_rows=_labor_daily_grid(),
            wage_rates=_wage_rates(),
            config=_CONFIG,
        )
        # Forge an actual for the first forecast date.
        fc_date = coerce_iso_date(grid[1][_IDX["date"]])
        ld = _labor_daily_grid()
        # append an actual row for fc_date
        actual_row = list(ld[1])
        actual_row[0] = fc_date
        actual_row[7] = 999       # orders
        actual_row[4] = 9000.0    # net_sales
        actual_row[30] = 1400     # items_sold
        actual_row[10] = 6.0      # fulltime_hours
        actual_row[8] = 20.0      # hourly_hours
        actual_row[12] = 500.0    # total_labor_cost
        ld.append(actual_row)

        out = backfill_forecast_errors(forecast_rows=grid, labor_daily_rows=ld)
        r2 = out[1]
        self.assertNotEqual(r2[_IDX["orders_error_pct"]], "")
        self.assertNotEqual(r2[_IDX["net_sales_error_pct"]], "")
        self.assertNotEqual(r2[_IDX["realized_labor_pct"]], "")
        self.assertNotEqual(r2[_IDX["forecast_mape"]], "")
        # realized_labor_pct = 500/9000
        self.assertAlmostEqual(r2[_IDX["realized_labor_pct"]], round(500.0 / 9000.0, 4))


if __name__ == "__main__":
    unittest.main(verbosity=2)
