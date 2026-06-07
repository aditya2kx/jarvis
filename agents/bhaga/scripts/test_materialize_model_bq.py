"""Tests for materialize_model_bq — conservation check and load_model_rows."""
from __future__ import annotations

import datetime
import importlib
import sys
import types
import unittest
from unittest import mock


def _load_module():
    """Import materialize_model_bq with BQ disabled so no real client is needed."""
    import os
    os.environ.setdefault("BHAGA_DATASTORE", "disabled")
    import agents.bhaga.scripts.materialize_model_bq as m
    return m


class TestAssertConservation(unittest.TestCase):
    """_assert_conservation raises on drift, passes on balance, skips open periods."""

    def _make_period(
        self,
        *,
        start: str = "2026-04-07",
        end: str = "2026-04-20",
        is_open: bool = False,
        per_period_ours: dict | None = None,
        per_day_allocations: list | None = None,
    ) -> dict:
        if per_period_ours is None:
            per_period_ours = {"Alice": 5000, "Bob": 5000}  # cents
        if per_day_allocations is None:
            # pool_cents == sum(per_period_ours) — balanced
            per_day_allocations = [
                {"date": start, "employee": "Alice", "hours": 8.0, "share_cents": 5000},
                {"date": start, "employee": "Bob",   "hours": 8.0, "share_cents": 5000},
            ]
        return {
            "start": start, "end": end, "is_open": is_open,
            "coverage": "full", "check_dates": [],
            "per_period_ours": per_period_ours,
            "per_period_hours": {"Alice": 8.0, "Bob": 8.0},
            "per_day_allocations": per_day_allocations,
            "per_period_adp": {},
        }

    def test_balanced_passes(self):
        m = _load_module()
        m._assert_conservation([self._make_period()])  # should not raise

    def test_unbalanced_raises(self):
        m = _load_module()
        period = self._make_period(
            per_period_ours={"Alice": 5000, "Bob": 5000},  # total 10000
            per_day_allocations=[
                {"date": "2026-04-07", "employee": "Alice", "hours": 8.0, "share_cents": 4900},
                {"date": "2026-04-07", "employee": "Bob",   "hours": 8.0, "share_cents": 4900},
            ],  # pool = 9800, drift = 200 cents
        )
        with self.assertRaises(RuntimeError) as ctx:
            m._assert_conservation([period])
        self.assertIn("conservation violated", str(ctx.exception))
        self.assertIn("2026-04-07", str(ctx.exception))

    def test_within_rounding_tolerance_passes(self):
        """1-cent rounding difference should not raise."""
        m = _load_module()
        period = self._make_period(
            per_period_ours={"Alice": 5001},
            per_day_allocations=[
                {"date": "2026-04-07", "employee": "Alice", "hours": 8.0, "share_cents": 5000},
            ],
        )
        m._assert_conservation([period])  # 1 cent diff is OK

    def test_open_period_skipped(self):
        """Open periods are in-progress; conservation is not enforced."""
        m = _load_module()
        period = self._make_period(
            is_open=True,
            per_period_ours={"Alice": 9999},
            per_day_allocations=[
                {"date": "2026-04-07", "employee": "Alice", "hours": 8.0, "share_cents": 1},
            ],
        )
        m._assert_conservation([period])  # should not raise for open period

    def test_multiple_periods_fail_on_second(self):
        m = _load_module()
        good = self._make_period(start="2026-04-07", end="2026-04-20")
        bad = self._make_period(
            start="2026-03-24", end="2026-04-06",
            per_period_ours={"Alice": 9999},
            per_day_allocations=[{"date": "2026-03-24", "employee": "Alice", "hours": 8.0, "share_cents": 1}],
        )
        with self.assertRaises(RuntimeError):
            m._assert_conservation([good, bad])


class TestLoadModelRows(unittest.TestCase):
    """load_model_rows converts header+rows, coerces, and calls load_rows."""

    def _patch_load_rows(self, m):
        """Patch core.datastore.load_rows to capture calls."""
        calls = []
        original = m.load_rows

        def fake_load_rows(table, rows, *, merge_keys, column_bq_types=None):
            calls.append({"table": table, "rows": rows, "merge_keys": merge_keys})
            return len(rows)

        m.load_rows = fake_load_rows
        return calls, original

    def tearDown(self):
        pass

    def test_dry_run_returns_zero_without_writing(self):
        m = _load_module()
        header = ["date", "orders"]
        rows = [header, ["2026-01-01", 42]]
        result = m.load_model_rows("model_daily", rows, dry_run=True)
        self.assertEqual(result, 0)

    def test_empty_input_returns_zero(self):
        m = _load_module()
        self.assertEqual(m.load_model_rows("model_daily", [], dry_run=True), 0)
        self.assertEqual(m.load_model_rows("model_daily", [["date"]], dry_run=True), 0)

    def test_coerces_percent_string(self):
        """String percentages should be converted to floats."""
        m = _load_module()
        materialized_at = datetime.datetime(2026, 1, 1, 0, 0, 0)
        row = {"total_labor_pct_of_net_sales": "28.5%"}
        coerced = m._coerce("model_labor_daily", row, materialized_at)
        self.assertAlmostEqual(coerced["total_labor_pct_of_net_sales"], 0.285)

    def test_coerces_date_string(self):
        """ISO date strings should become date objects."""
        m = _load_module()
        materialized_at = datetime.datetime(2026, 1, 1, 0, 0, 0)
        row = {"date": "'2026-04-07"}  # apostrophe prefix from Sheets
        coerced = m._coerce("model_labor_daily", row, materialized_at)
        self.assertEqual(coerced["date"], datetime.date(2026, 4, 7))

    def test_coerces_blank_to_none(self):
        m = _load_module()
        materialized_at = datetime.datetime(2026, 1, 1, 0, 0, 0)
        row = {"orders": ""}
        coerced = m._coerce("model_daily", row, materialized_at)
        self.assertIsNone(coerced["orders"])

    def test_stamps_materialized_at(self):
        m = _load_module()
        materialized_at = datetime.datetime(2026, 6, 4, 12, 0, 0)
        row = {"date": "2026-06-04"}
        coerced = m._coerce("model_daily", row, materialized_at)
        self.assertEqual(coerced["materialized_at_utc"], materialized_at)

    def test_bool_coercion(self):
        m = _load_module()
        materialized_at = datetime.datetime(2026, 1, 1)
        for truthy in ("TRUE", "true", "1", "yes"):
            row = {"over_saturation": truthy}
            coerced = m._coerce("model_labor_daily", row, materialized_at)
            self.assertTrue(coerced["over_saturation"], f"Expected True for {truthy!r}")
        row = {"over_saturation": "FALSE"}
        coerced = m._coerce("model_labor_daily", row, materialized_at)
        self.assertFalse(coerced["over_saturation"])


class TestReplaceMode(unittest.TestCase):
    """load_model_rows(replace=True) truncates before loading (ghost-row guard)."""

    def test_replace_issues_delete_before_load(self):
        import core.datastore as ds
        m = _load_module()
        delete_sql = []
        loaded = []

        def fake_read_query(sql):
            if sql.strip().upper().startswith("DELETE"):
                delete_sql.append(sql)
            return []

        def fake_load_rows(table, rows, *, merge_keys, column_bq_types=None):
            loaded.append((table, len(rows)))
            return len(rows)

        with mock.patch.object(ds, "read_query", fake_read_query), \
             mock.patch.object(m, "load_rows", fake_load_rows), \
             mock.patch.object(m, "_col_type_hints", return_value={}):
            rows = [["period_start", "employee"], ["2026-05-04", "Bob"]]
            n = m.load_model_rows("model_review_bonus_period", rows, replace=True)

        self.assertEqual(n, 1)
        self.assertEqual(len(delete_sql), 1, "expected exactly one DELETE before load")
        self.assertIn("model_review_bonus_period", delete_sql[0])
        self.assertEqual(loaded, [("model_review_bonus_period", 1)])

    def test_replace_dry_run_skips_delete(self):
        import core.datastore as ds
        m = _load_module()
        called = []
        with mock.patch.object(ds, "read_query", lambda sql: called.append(sql) or []):
            n = m.load_model_rows(
                "model_review_bonus_period",
                [["period_start", "employee"], ["2026-05-04", "Bob"]],
                replace=True, dry_run=True,
            )
        self.assertEqual(n, 0)
        self.assertEqual(called, [], "dry-run must not issue a DELETE")


if __name__ == "__main__":
    unittest.main()
