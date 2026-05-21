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
    build_config_rows,
    build_daily_rows,
    build_labor_daily_rows,
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
        date_col = [r[0] for r in rows[1:]]
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
        date_col = [r[0] for r in rows[1:]]
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
        date_col = [r[0] for r in rows[1:]]
        self.assertIn("2026-05-21", date_col)

    def test_daily_tab_drops_in_progress_date(self):
        rows, _summary = build_daily_rows(
            txns=self._txns(["2026-05-20", "2026-05-21"]),
            shifts=self._shifts(["2026-05-20", "2026-05-21"]),
            excluded=set(),
            now_ct=self.NOW_MID_DAY,
        )
        date_col = [r[0] for r in rows[1:]]
        self.assertIn("2026-05-20", date_col)
        self.assertNotIn("2026-05-21", date_col)


if __name__ == "__main__":
    unittest.main()
