"""Unit tests for core/store_config.py.

Stubs core.datastore.read_query and core.datastore.load_rows so no live BQ
connection is needed.
"""

from __future__ import annotations

import datetime
import unittest
from unittest.mock import call, patch


class TestGetConfig(unittest.TestCase):
    def test_returns_value_when_found(self):
        with patch("core.datastore.read_query", return_value=[{"value": "11.5"}]) as m:
            from core.store_config import get_config
            val = get_config("palmetto", "saturation_orders_per_labor_hour")
        self.assertEqual(val, "11.5")
        sql = m.call_args[0][0]
        self.assertIn("palmetto", sql)
        self.assertIn("saturation_orders_per_labor_hour", sql)

    def test_returns_none_when_not_found(self):
        with patch("core.datastore.read_query", return_value=[]):
            from core.store_config import get_config
            val = get_config("palmetto", "missing_key")
        self.assertIsNone(val)


class TestGetAll(unittest.TestCase):
    def test_returns_dict_of_all_keys(self):
        fake_rows = [
            {"key": "k1", "value": "v1"},
            {"key": "k2", "value": "v2"},
        ]
        with patch("core.datastore.read_query", return_value=fake_rows):
            from core.store_config import get_all
            result = get_all("palmetto")
        self.assertEqual(result, {"k1": "v1", "k2": "v2"})

    def test_empty_returns_empty_dict(self):
        with patch("core.datastore.read_query", return_value=[]):
            from core.store_config import get_all
            result = get_all("palmetto")
        self.assertEqual(result, {})


class TestSetConfig(unittest.TestCase):
    def test_calls_load_rows_with_merge_keys(self):
        with patch("core.datastore.load_rows") as mock_load:
            from core.store_config import set_config
            set_config("palmetto", "review_base_bonus_dollars", "3.0",
                       updated_by="test-agent", notes="test")
        mock_load.assert_called_once()
        args, kwargs = mock_load.call_args
        self.assertEqual(args[0], "store_config")
        self.assertEqual(len(args[1]), 1)
        row = args[1][0]
        self.assertEqual(row["store"], "palmetto")
        self.assertEqual(row["key"], "review_base_bonus_dollars")
        self.assertEqual(row["value"], "3.0")
        self.assertEqual(row["updated_by"], "test-agent")
        self.assertIn("updated_at", row)
        self.assertEqual(kwargs["merge_keys"], ["store", "key"])
        self.assertEqual(kwargs["column_bq_types"]["updated_at"], "TIMESTAMP")

    def test_empty_notes_stored_as_none(self):
        with patch("core.datastore.load_rows") as mock_load:
            from core.store_config import set_config
            set_config("palmetto", "k", "v", updated_by="agent")
        row = mock_load.call_args[0][1][0]
        self.assertIsNone(row["notes"])


if __name__ == "__main__":
    unittest.main()
