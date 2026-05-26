#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.update_model_sheet.

Run:
    python3 agents/bhaga/scripts/test_update_model_sheet.py

Covers Layer A of the seamless_bhaga_refresh fix: every date-bearing
config row must be emitted with a leading apostrophe so the Sheets API
keeps it as a text literal under valueInputOption=USER_ENTERED.

These are pure-function tests against ``build_config_rows`` — no
Sheets API calls. Only the inputs build_config_rows actually consumes
need to be supplied.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from zoneinfo import ZoneInfo

from agents.bhaga.scripts.daily_refresh import CT
from agents.bhaga.scripts.update_model_sheet import (
    _DATE_CONFIG_KEYS,
    _fill_calendar_dates,
    build_config_rows,
    build_daily_rows,
    build_labor_daily_rows,
    build_labor_period_rows,
    build_labor_weekly_rows,
    build_period_summary_rows,
    build_tip_alloc_daily_rows,
    build_tip_alloc_period_rows,
)
from skills.bhaga_config.dates import _iso_date_for_sheet_cell, coerce_iso_date


def _minimal_profile() -> dict:
    """Smallest dict shape that satisfies build_config_rows' key access."""
    return {
        "display_name": "BHAGA Palmetto",
        "store_id": "PAL-001",
        "legal_entity": "BHAGA LLC",
        "timezone": {
            "shop_tz": "America/Chicago",
            "square_account_display_tz": "America/Chicago",
        },
        "employees": {
            "excluded_from_tip_pool_and_labor_pct": ["Lindsay"],
        },
        "adp_run": {
            "pay_frequency": "biweekly",
            "wage_rate_report_name": "Earnings & Hours V1",
        },
        "calibration": {
            "first_data_window": {"start": "2026-03-22"},
        },
        "google_sheets": {
            "bhaga_adp_raw": {"url": "https://docs.google.com/spreadsheets/d/raw_adp"},
            "bhaga_square_raw": {"url": "https://docs.google.com/spreadsheets/d/raw_sq"},
            "bhaga_review_raw": {"url": "https://docs.google.com/spreadsheets/d/raw_rev"},
        },
    }


def _row_value(rows: list[list], key: str):
    """Return the value cell for the row whose key column equals `key`."""
    for r in rows:
        if r and r[0] == key:
            return r[1]
    raise AssertionError(f"row {key!r} not found in build_config_rows output")


class BuildConfigRowsApostropheTests(unittest.TestCase):
    def test_data_window_end_has_apostrophe_prefix(self):
        rows = build_config_rows(_minimal_profile(), "2026-05-20")
        self.assertEqual(_row_value(rows, "data_window_end"), "'2026-05-20")

    def test_data_window_start_has_apostrophe_prefix(self):
        rows = build_config_rows(_minimal_profile(), "2026-05-20")
        self.assertEqual(_row_value(rows, "data_window_start"), "'2026-03-22")

    def test_review_bonus_started_date_default_has_apostrophe(self):
        rows = build_config_rows(_minimal_profile(), "2026-05-20")
        self.assertEqual(
            _row_value(rows, "review_bonus_started_date"),
            "'2026-05-11",
        )

    def test_review_bonus_started_date_operator_override_has_apostrophe(self):
        rows = build_config_rows(
            _minimal_profile(),
            "2026-05-20",
            review_tunables={"review_bonus_started_date": "2026-06-01"},
        )
        self.assertEqual(
            _row_value(rows, "review_bonus_started_date"),
            "'2026-06-01",
        )

    def test_review_bonus_started_date_serial_readback_normalized(self):
        # CRITICAL: when the tunable read-back happens against a
        # pre-fix corrupt cell holding a Sheets serial (e.g. 46153 for
        # 2026-05-11), build_config_rows must normalize back to
        # canonical ISO before writing — otherwise we'd write "'46153"
        # as text and just persist the drift forever.
        rows = build_config_rows(
            _minimal_profile(),
            "2026-05-20",
            review_tunables={"review_bonus_started_date": "46153"},
        )
        self.assertEqual(
            _row_value(rows, "review_bonus_started_date"),
            "'2026-05-11",
        )

    def test_training_excluded_date_has_apostrophe(self):
        rows = build_config_rows(
            _minimal_profile(),
            "2026-05-20",
            training_through={"Doe, Jane": datetime.date(2026, 5, 15)},
        )
        self.assertEqual(
            _row_value(rows, "training_excluded:Doe, Jane"),
            "'2026-05-15",
        )

    def test_non_date_config_rows_unchanged(self):
        # The saturation tunable is a numeric string; we must NOT
        # apostrophe-sprinkle it (otherwise Sheets shows literal "'4"
        # which makes the named-range formula non-numeric and every
        # over_saturation flag becomes #NAME?).
        rows = build_config_rows(_minimal_profile(), "2026-05-20")
        sat = _row_value(rows, "saturation_orders_per_labor_hour")
        self.assertFalse(
            sat.startswith("'"),
            f"saturation value {sat!r} must NOT have apostrophe prefix",
        )
        # And it must still be parseable as a number — otherwise the
        # `over_saturation` named-range formula breaks.
        float(sat)
        # Store name must not be apostrophe-prefixed either.
        self.assertEqual(_row_value(rows, "store"), "BHAGA Palmetto")

    def test_all_date_keys_in_module_registry_have_apostrophe(self):
        # Future-proofing: if someone adds a new date key to build_config_rows
        # without updating _DATE_CONFIG_KEYS, the round-trip sentinel at the
        # end of main() won't verify it. This guard test catches the inverse —
        # if a key IS in _DATE_CONFIG_KEYS it had better be apostrophe-wrapped.
        rows = build_config_rows(_minimal_profile(), "2026-05-20")
        for key in _DATE_CONFIG_KEYS:
            with self.subTest(key=key):
                v = _row_value(rows, key)
                self.assertTrue(
                    v.startswith("'"),
                    f"{key} value {v!r} must start with apostrophe",
                )
                # And the stripped form must be parseable as ISO.
                self.assertIsNotNone(coerce_iso_date(v))


class IsoDateHelperDirectTests(unittest.TestCase):
    """Sanity-check the helper directly from the writer's perspective."""

    def test_iso_string_input(self):
        self.assertEqual(_iso_date_for_sheet_cell("2026-05-20"), "'2026-05-20")

    def test_date_object_input(self):
        self.assertEqual(
            _iso_date_for_sheet_cell(datetime.date(2026, 5, 20)),
            "'2026-05-20",
        )

    def test_none_emits_empty(self):
        # build_config_rows defaults to "" when input is None, not "'".
        self.assertEqual(_iso_date_for_sheet_cell(None), "")

    def test_idempotent_no_double_prefix(self):
        # If for some reason a caller hands us an already-prefixed value
        # (e.g. round-trip through the sheet), we must NOT add a second '.
        self.assertEqual(_iso_date_for_sheet_cell("'2026-05-20"), "'2026-05-20")


class InProgressDateFilterTests(unittest.TestCase):
    """labor_daily and daily must NOT emit rows for in-progress dates.

    The production failure mode: a mid-day refresh on 2026-05-21 wrote a
    partial 5/21 row into labor_daily / labor_weekly / labor_period.
    With the filter wired in, the builders drop the in-progress date
    before constructing rows; downstream weekly/period tabs naturally
    inherit the filter because they reduce over labor_daily rows.

    `now_ct` is injected so tests are stable regardless of wall-clock.
    """

    NOW_MID_DAY = datetime.datetime(2026, 5, 21, 13, 0, 0, tzinfo=CT)
    NOW_POST_CLOSE = datetime.datetime(2026, 5, 21, 21, 30, 0, tzinfo=CT)

    def _txns(self, dates: list[str]) -> list[dict]:
        """Minimum txn shape consumed by transactions_backend.aggregate_daily_sales.

        Mirrors every key the aggregator dereferences: date_local, hour_local,
        event_type, gross_sales_cents, discount_cents, total_collected_cents,
        tip_cents. One $5.00 sale + $1.00 tip per date.
        """
        out = []
        for d in dates:
            out.append({
                "date_local": d,
                "hour_local": 10,
                "event_type": "Payment",
                "gross_sales_cents": 500,
                "discount_cents": 0,
                "total_collected_cents": 600,
                "tip_cents": 100,
            })
        return out

    def _shifts(self, dates: list[str]) -> list[dict]:
        out = []
        for d in dates:
            out.append({
                "date": d,
                "employee_name": "Test Barista",
                "employee_id": "barista-1",
                "in_time": "08:00",
                "out_time": "14:00",
                "regular_hours": 6.0,
                "ot_hours": 0.0,
                "doubletime_hours": 0.0,
                "total_hours": 6.0,
            })
        return out

    def _wage_rates(self) -> list[dict]:
        return [{
            "employee_name": "Test Barista",
            "wage_rate_dollars": "12.00",
            "ot_rate_dollars": "18.00",
            "is_salaried": False,
            "excluded_from_labor_pct": False,
        }]

    def test_labor_daily_drops_in_progress_date(self):
        # 5/20 is complete (past), 5/21 is in-progress (today, before 21:00 CT).
        rows = build_labor_daily_rows(
            txns=self._txns(["2026-05-20", "2026-05-21"]),
            shifts=self._shifts(["2026-05-20", "2026-05-21"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_MID_DAY,
        )
        # r[0] is apostrophe-prefixed ('2026-05-20). Normalize via coerce_iso_date
        # so the in-progress-filter assertion stays independent of cell encoding.
        date_col = [coerce_iso_date(r[0]) for r in rows[1:]]
        self.assertIn("2026-05-20", date_col)
        self.assertNotIn(
            "2026-05-21", date_col,
            f"in-progress 5/21 row leaked into labor_daily: {date_col}",
        )

    def test_labor_daily_emits_all_complete_dates(self):
        # Two past dates → both must be present.
        rows = build_labor_daily_rows(
            txns=self._txns(["2026-05-19", "2026-05-20"]),
            shifts=self._shifts(["2026-05-19", "2026-05-20"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_MID_DAY,
        )
        date_col = [coerce_iso_date(r[0]) for r in rows[1:]]
        self.assertEqual(sorted(date_col), ["2026-05-19", "2026-05-20"])

    def test_labor_daily_emits_today_after_21_00(self):
        # At 21:30 CT today_ct is complete — the cron's nightly path.
        # Regression guard: do NOT silently drop today's row at the
        # canonical cron firing time.
        rows = build_labor_daily_rows(
            txns=self._txns(["2026-05-20", "2026-05-21"]),
            shifts=self._shifts(["2026-05-20", "2026-05-21"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        date_col = [coerce_iso_date(r[0]) for r in rows[1:]]
        self.assertIn("2026-05-21", date_col)

    def test_daily_tab_drops_in_progress_date(self):
        rows, _summary = build_daily_rows(
            txns=self._txns(["2026-05-20", "2026-05-21"]),
            shifts=self._shifts(["2026-05-20", "2026-05-21"]),
            excluded=set(),
            now_ct=self.NOW_MID_DAY,
        )
        date_col = [coerce_iso_date(r[0]) for r in rows[1:]]
        self.assertIn("2026-05-20", date_col)
        self.assertNotIn("2026-05-21", date_col)


class RowBuilderDateApostropheTests(unittest.TestCase):
    """Every date-bearing cell across the data tabs must land as an
    apostrophe-prefixed ISO string ('YYYY-MM-DD) so Google Sheets keeps
    the value as plain text instead of coercing it to a date-serial
    integer (the 46162 vs 2026-05-20 bug).

    Mirrors the BuildConfigRowsApostropheTests guard but at the per-data-
    row-builder layer, where the original `8771f25` fix did NOT reach.
    Reuses the same NOW_POST_CLOSE fixture so today_ct (2026-05-21) is
    "complete" and survives the in-progress filter; that's the only way
    to get a freshly-built row out of the daily/labor builders.
    """

    NOW_POST_CLOSE = datetime.datetime(2026, 5, 21, 21, 30, 0, tzinfo=CT)

    def _txns(self, dates: list[str]) -> list[dict]:
        return [{
            "date_local": d,
            "hour_local": 10,
            "event_type": "Payment",
            "gross_sales_cents": 500,
            "discount_cents": 0,
            "total_collected_cents": 600,
            "tip_cents": 100,
        } for d in dates]

    def _shifts(self, dates: list[str]) -> list[dict]:
        return [{
            "date": d,
            "employee_name": "Test Barista",
            "employee_id": "barista-1",
            "in_time": "08:00",
            "out_time": "14:00",
            "regular_hours": 6.0,
            "ot_hours": 0.0,
            "doubletime_hours": 0.0,
            "total_hours": 6.0,
        } for d in dates]

    def _wage_rates(self) -> list[dict]:
        return [{
            "employee_name": "Test Barista",
            "wage_rate_dollars": "12.00",
            "ot_rate_dollars": "18.00",
            "is_salaried": False,
            "excluded_from_labor_pct": False,
        }]

    def _assert_apostrophe_iso(self, cell, label: str) -> None:
        self.assertIsInstance(cell, str, f"{label}: not a str ({cell!r})")
        self.assertTrue(
            cell.startswith("'"),
            f"{label}: missing apostrophe prefix ({cell!r}) — Sheets will "
            f"coerce this to a date-serial",
        )
        # And the stripped form must still round-trip to ISO.
        coerced = coerce_iso_date(cell)
        self.assertIsNotNone(
            coerced, f"{label}: stripped value did not parse as ISO ({cell!r})"
        )

    def test_daily_rows_date_column_is_apostrophe_prefixed(self):
        rows, _summary = build_daily_rows(
            txns=self._txns(["2026-05-20", "2026-05-21"]),
            shifts=self._shifts(["2026-05-20", "2026-05-21"]),
            excluded=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        self.assertGreater(len(rows), 1, "no data rows emitted")
        for r in rows[1:]:
            self._assert_apostrophe_iso(r[0], "daily.date")

    def test_labor_daily_rows_date_column_is_apostrophe_prefixed(self):
        rows = build_labor_daily_rows(
            txns=self._txns(["2026-05-20", "2026-05-21"]),
            shifts=self._shifts(["2026-05-20", "2026-05-21"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        self.assertGreater(len(rows), 1)
        for r in rows[1:]:
            self._assert_apostrophe_iso(r[0], "labor_daily.date")

    def test_labor_period_rows_boundary_columns_are_apostrophe_prefixed(self):
        labor_daily = build_labor_daily_rows(
            txns=self._txns(["2026-05-20", "2026-05-21"]),
            shifts=self._shifts(["2026-05-20", "2026-05-21"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        periods = [{
            "start": "2026-05-18", "end": "2026-05-31",
            "check_dates": [], "variants": [], "is_open": True,
        }]
        rows = build_labor_period_rows(
            periods=periods, labor_daily_rows=labor_daily,
        )
        self.assertGreater(len(rows), 1)
        for r in rows[1:]:
            self._assert_apostrophe_iso(r[0], "labor_period.pay_period_start")
            self._assert_apostrophe_iso(r[1], "labor_period.pay_period_end")

    def test_labor_period_aggregator_finds_apostrophe_prefixed_daily_dates(self):
        # Regression guard: build_labor_period_rows indexes labor_daily by
        # row[0]. If the dict key kept the apostrophe but the period-loop
        # looked up with `cursor.isoformat()` (no apostrophe), every period
        # would aggregate ZERO days — totals would be $0 and days_covered=0.
        # This test catches that without depending on real period data.
        labor_daily = build_labor_daily_rows(
            txns=self._txns(["2026-05-20"]),
            shifts=self._shifts(["2026-05-20"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        periods = [{
            "start": "2026-05-18", "end": "2026-05-31",
            "check_dates": [], "variants": [], "is_open": True,
        }]
        rows = build_labor_period_rows(
            periods=periods, labor_daily_rows=labor_daily,
        )
        # days_covered is column index 3 (start, end, is_open, days_covered, ...).
        self.assertEqual(rows[1][3], 1,
                         f"period aggregator did not match the labor_daily "
                         f"date through the apostrophe-prefix layer; "
                         f"row={rows[1]!r}")

    def test_labor_weekly_rows_week_start_and_week_end_are_apostrophe_prefixed(self):
        labor_daily = build_labor_daily_rows(
            txns=self._txns(["2026-05-19", "2026-05-20"]),
            shifts=self._shifts(["2026-05-19", "2026-05-20"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        rows = build_labor_weekly_rows(labor_daily_rows=labor_daily)
        self.assertGreater(len(rows), 1)
        for r in rows[1:]:
            # iso_week ("2026-W21") is left plain — verified safe via MCP.
            self.assertFalse(
                r[0].startswith("'"),
                f"iso_week should NOT have apostrophe prefix ({r[0]!r})",
            )
            self.assertRegex(r[0], r"^\d{4}-W\d{2}$")
            self._assert_apostrophe_iso(r[1], "labor_weekly.week_start")
            self._assert_apostrophe_iso(r[2], "labor_weekly.week_end")

    def test_labor_weekly_aggregator_finds_apostrophe_prefixed_daily_dates(self):
        # Same regression as the period aggregator: the daily_by_date dict
        # must be keyed on the normalized ISO so weekly bucketing finds
        # the right day. days_covered (column 4) must be 1, not 0.
        labor_daily = build_labor_daily_rows(
            txns=self._txns(["2026-05-20"]),
            shifts=self._shifts(["2026-05-20"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        rows = build_labor_weekly_rows(labor_daily_rows=labor_daily)
        self.assertGreater(len(rows), 1)
        # days_covered is column index 4 (iso_week, week_start, week_end,
        # is_partial, days_covered, ...).
        self.assertEqual(rows[1][4], 1,
                         f"weekly aggregator did not match the labor_daily "
                         f"date through the apostrophe-prefix layer; "
                         f"row={rows[1]!r}")

    def test_tip_alloc_period_rows_boundary_columns_are_apostrophe_prefixed(self):
        period_results = [{
            "start": "2026-05-04",
            "end": "2026-05-17",
            "check_dates": ["2026-05-22"],
            "is_open": False,
            "coverage": "full",
            "per_period_ours": {"Doe, Jane": 12345},
            "per_period_hours": {"Doe, Jane": 30.0},
            "per_day_allocations": [],
            "per_period_adp": {"Doe, Jane": 12345},
        }]
        rows = build_tip_alloc_period_rows(period_results)
        self.assertGreater(len(rows), 1)
        for r in rows[1:]:
            self._assert_apostrophe_iso(r[0], "tip_alloc_period.period_start")
            self._assert_apostrophe_iso(r[1], "tip_alloc_period.period_end")

    def test_tip_alloc_daily_rows_date_and_period_columns_are_apostrophe_prefixed(self):
        period_results = [{
            "start": "2026-05-04",
            "end": "2026-05-17",
            "check_dates": ["2026-05-22"],
            "is_open": False,
            "coverage": "full",
            "per_period_ours": {},
            "per_period_hours": {},
            "per_day_allocations": [
                {"date": "2026-05-15", "employee": "Doe, Jane",
                 "hours": 6.0, "share_cents": 1500},
            ],
            "per_period_adp": {},
        }]
        daily_summary = {
            "2026-05-15": {
                "pool_cents": 5000, "sales_cents": 50000,
                "team_hours": 12.0, "txn_count": 25,
            },
        }
        rows = build_tip_alloc_daily_rows(period_results, daily_summary)
        self.assertGreater(len(rows), 1)
        for r in rows[1:]:
            self._assert_apostrophe_iso(r[0], "tip_alloc_daily.date")
            self._assert_apostrophe_iso(r[2], "tip_alloc_daily.period_start")
            self._assert_apostrophe_iso(r[3], "tip_alloc_daily.period_end")

    def test_period_summary_rows_boundary_columns_are_apostrophe_prefixed(self):
        period_results = [{
            "start": "2026-05-04",
            "end": "2026-05-17",
            "check_dates": ["2026-05-22"],
            "is_open": False,
            "coverage": "full",
            "per_period_ours": {"Doe, Jane": 12345},
            "per_period_hours": {"Doe, Jane": 30.0},
            "per_day_allocations": [
                {"date": "2026-05-15", "employee": "Doe, Jane",
                 "hours": 6.0, "share_cents": 1500},
            ],
            "per_period_adp": {"Doe, Jane": 12345},
        }]
        rows = build_period_summary_rows(period_results)
        self.assertGreater(len(rows), 1)
        for r in rows[1:]:
            self._assert_apostrophe_iso(r[0], "period_summary.period_start")
            self._assert_apostrophe_iso(r[1], "period_summary.period_end")


class ZeroShiftDayTests(unittest.TestCase):
    """A day with 0 ADP shifts and 0 Square transactions must:
    - not block data_window_end advancement (covered by the range-based
      gate in main(), not the row builders — tested here at the builder
      layer via _fill_calendar_dates),
    - produce valid rows in daily, labor_daily, labor_weekly, labor_period
      with all-zero values,
    - not crash on any division.
    """

    NOW_POST_CLOSE = datetime.datetime(2026, 5, 22, 21, 30, 0, tzinfo=CT)

    def _txns(self, dates: list[str]) -> list[dict]:
        return [{
            "date_local": d,
            "hour_local": 10,
            "event_type": "Payment",
            "gross_sales_cents": 500,
            "discount_cents": 0,
            "total_collected_cents": 600,
            "tip_cents": 100,
        } for d in dates]

    def _shifts(self, dates: list[str]) -> list[dict]:
        return [{
            "date": d,
            "employee_name": "Test Barista",
            "employee_id": "barista-1",
            "in_time": "08:00",
            "out_time": "14:00",
            "regular_hours": 6.0,
            "ot_hours": 0.0,
            "doubletime_hours": 0.0,
            "total_hours": 6.0,
        } for d in dates]

    def _wage_rates(self) -> list[dict]:
        return [{
            "employee_name": "Test Barista",
            "wage_rate_dollars": "12.00",
            "ot_rate_dollars": "18.00",
            "is_salaried": False,
            "excluded_from_labor_pct": False,
        }]

    def test_fill_calendar_dates_fills_gaps(self):
        result = _fill_calendar_dates(["2026-05-19", "2026-05-22"])
        self.assertEqual(result, [
            "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22",
        ])

    def test_fill_calendar_dates_no_gap(self):
        result = _fill_calendar_dates(["2026-05-19", "2026-05-20"])
        self.assertEqual(result, ["2026-05-19", "2026-05-20"])

    def test_fill_calendar_dates_single_date(self):
        result = _fill_calendar_dates(["2026-05-20"])
        self.assertEqual(result, ["2026-05-20"])

    def test_fill_calendar_dates_empty(self):
        result = _fill_calendar_dates([])
        self.assertEqual(result, [])

    def test_daily_emits_zero_row_for_gap_day(self):
        # Data on 5/19 and 5/21 but NOT 5/20 (store closed).
        # The gap day 5/20 must still appear with all-zero values.
        rows, summary = build_daily_rows(
            txns=self._txns(["2026-05-19", "2026-05-21"]),
            shifts=self._shifts(["2026-05-19", "2026-05-21"]),
            excluded=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        date_col = [coerce_iso_date(r[0]) for r in rows[1:]]
        self.assertIn("2026-05-20", date_col,
                      "closed day 2026-05-20 missing from daily rows")
        # Find the gap-day row and verify it has zeros.
        gap_row = next(r for r in rows[1:] if coerce_iso_date(r[0]) == "2026-05-20")
        self.assertEqual(gap_row[2], 0)   # gross_sales
        self.assertEqual(gap_row[3], 0)   # tip_pool
        self.assertEqual(gap_row[8], 0)   # txn_count

    def test_daily_gap_day_no_division_crash(self):
        # Exercises the per_hour and tips_pct divisions with 0 values.
        rows, _summary = build_daily_rows(
            txns=self._txns(["2026-05-19", "2026-05-21"]),
            shifts=self._shifts(["2026-05-19", "2026-05-21"]),
            excluded=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        self.assertGreater(len(rows), 1)

    def test_labor_daily_emits_zero_row_for_gap_day(self):
        rows = build_labor_daily_rows(
            txns=self._txns(["2026-05-19", "2026-05-21"]),
            shifts=self._shifts(["2026-05-19", "2026-05-21"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        date_col = [coerce_iso_date(r[0]) for r in rows[1:]]
        self.assertIn("2026-05-20", date_col,
                      "closed day 2026-05-20 missing from labor_daily rows")
        gap_row = next(r for r in rows[1:] if coerce_iso_date(r[0]) == "2026-05-20")
        self.assertEqual(gap_row[7], 0)    # orders
        self.assertEqual(gap_row[8], 0)    # hourly_hours
        self.assertEqual(gap_row[9], 0)    # hourly_labor_cost

    def test_labor_daily_gap_day_no_division_crash(self):
        rows = build_labor_daily_rows(
            txns=self._txns(["2026-05-19", "2026-05-21"]),
            shifts=self._shifts(["2026-05-19", "2026-05-21"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        self.assertGreater(len(rows), 1)

    def test_labor_weekly_includes_gap_day_contribution(self):
        # Gap day is within a week with real data — the week row must
        # still aggregate correctly without crashing.
        labor_daily = build_labor_daily_rows(
            txns=self._txns(["2026-05-19", "2026-05-21"]),
            shifts=self._shifts(["2026-05-19", "2026-05-21"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        rows = build_labor_weekly_rows(labor_daily_rows=labor_daily)
        self.assertGreater(len(rows), 1)
        # days_covered (column 4) should be 3 (19, 20, 21)
        # because the gap day now has a labor_daily row.
        total_days = sum(r[4] for r in rows[1:])
        self.assertEqual(total_days, 3)

    def test_labor_period_includes_gap_day(self):
        labor_daily = build_labor_daily_rows(
            txns=self._txns(["2026-05-19", "2026-05-21"]),
            shifts=self._shifts(["2026-05-19", "2026-05-21"]),
            wage_rates=self._wage_rates(),
            excluded_from_tip_pool=set(),
            now_ct=self.NOW_POST_CLOSE,
        )
        periods = [{
            "start": "2026-05-18", "end": "2026-05-31",
            "check_dates": [], "variants": [], "is_open": True,
        }]
        rows = build_labor_period_rows(
            periods=periods, labor_daily_rows=labor_daily,
        )
        self.assertGreater(len(rows), 1)
        # days_covered = 3 (19, 20, 21 are within the period and have rows)
        self.assertEqual(rows[1][3], 3)


if __name__ == "__main__":
    unittest.main()
