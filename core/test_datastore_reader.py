"""Tests for core.datastore_reader BQ → Sheets-shape mappers.

Focused on read_item_daily_bq / read_kds_daily_bq, which feed the per-item
operations metrics (items_sold, KDS percentiles) into materialize_model_bq.
Both must mirror the Sheet readers' dict shape so build_labor_daily_rows can
consume them interchangeably.
"""
from __future__ import annotations

import datetime
import unittest
from unittest import mock

import core.datastore_reader as reader


class TestReadItemDailyBq(unittest.TestCase):
    def test_maps_columns_and_date(self):
        bq_rows = [{
            "date_local": datetime.date(2026, 3, 23),
            "items_sold": 42,
            "units_sold": 50,
            "gross_sales_cents": 12345,
            "avg_item_price_cents": 294,
            "scraped_at_utc": datetime.datetime(2026, 3, 24, 5, 0, 0),
        }]
        with mock.patch.object(reader, "read_table", return_value=bq_rows):
            out = reader.read_item_daily_bq()
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual(r["date_local"], "2026-03-23")
        self.assertEqual(r["items_sold"], 42)
        self.assertEqual(r["units_sold"], 50)
        self.assertEqual(r["gross_sales_cents"], 12345)
        self.assertEqual(r["avg_item_price_cents"], 294)

    def test_nulls_become_zero(self):
        bq_rows = [{"date_local": datetime.date(2026, 3, 23), "items_sold": None,
                    "units_sold": None, "gross_sales_cents": None,
                    "avg_item_price_cents": None, "scraped_at_utc": None}]
        with mock.patch.object(reader, "read_table", return_value=bq_rows):
            out = reader.read_item_daily_bq()
        self.assertEqual(out[0]["items_sold"], 0)
        self.assertEqual(out[0]["gross_sales_cents"], 0)

    def test_empty_table(self):
        with mock.patch.object(reader, "read_table", return_value=[]):
            self.assertEqual(reader.read_item_daily_bq(), [])


class TestReadKdsDailyBq(unittest.TestCase):
    def test_parses_per_item_times_json(self):
        bq_rows = [{
            "date_local": datetime.date(2026, 4, 24),
            "completed_tickets": 30,
            "completed_items": 45,
            "median_time_per_item_sec": 120.5,
            "p90_time_per_item_sec": 300.0,
            "p95_time_per_item_sec": 360.0,
            "p99_time_per_item_sec": 420.0,
            "pct_tickets_late": 0.05,
            "shift_start": "07:00",
            "shift_end": "15:00",
            "late_tickets": 2,
            "due_tickets": 40,
            "per_item_times_json": "[60, 120, 480]",
            "scraped_at_utc": datetime.datetime(2026, 4, 25, 5, 0, 0),
        }]
        with mock.patch.object(reader, "read_table", return_value=bq_rows):
            out = reader.read_kds_daily_bq()
        r = out[0]
        self.assertEqual(r["date_local"], "2026-04-24")
        self.assertEqual(r["completed_items"], 45)
        self.assertEqual(r["per_item_times_json"], [60, 120, 480])
        self.assertEqual(r["late_tickets"], 2)
        self.assertEqual(r["due_tickets"], 40)

    def test_handles_list_and_bad_json(self):
        with mock.patch.object(reader, "read_table", return_value=[
            {"date_local": datetime.date(2026, 4, 24), "per_item_times_json": [1, 2, 3]},
        ]):
            self.assertEqual(reader.read_kds_daily_bq()[0]["per_item_times_json"], [1, 2, 3])
        with mock.patch.object(reader, "read_table", return_value=[
            {"date_local": datetime.date(2026, 4, 24), "per_item_times_json": "not-json"},
        ]):
            self.assertEqual(reader.read_kds_daily_bq()[0]["per_item_times_json"], [])

    def test_empty_table(self):
        with mock.patch.object(reader, "read_table", return_value=[]):
            self.assertEqual(reader.read_kds_daily_bq(), [])


if __name__ == "__main__":
    unittest.main()
