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

    def test_derived_key_raises_value_error(self):
        """set_config must reject data_window_end — it is a derived value."""
        from core.store_config import set_config
        with self.assertRaises(ValueError) as cm:
            set_config("palmetto", "data_window_end", "2026-06-13",
                       updated_by="accident")
        self.assertIn("data_window_end", str(cm.exception))
        self.assertIn("derived", str(cm.exception))

    def test_normal_key_not_affected_by_guard(self):
        """Non-derived keys still write without error."""
        with patch("core.datastore.load_rows") as mock_load:
            from core.store_config import set_config
            set_config("palmetto", "review_pool_dollars", "50", updated_by="agent")
        mock_load.assert_called_once()


class TestDeleteConfig(unittest.TestCase):
    def test_issues_delete_query(self):
        """delete_config must issue a DELETE for the given store/key."""
        with patch("core.datastore.read_query") as mock_rq:
            from core.store_config import delete_config
            delete_config("palmetto", "data_window_end")
        mock_rq.assert_called_once()
        sql = mock_rq.call_args[0][0]
        self.assertIn("DELETE FROM", sql)
        self.assertIn("palmetto", sql)
        self.assertIn("data_window_end", sql)


class TestResolveDataWindowEnd(unittest.TestCase):
    def test_returns_max_date_from_bq(self):
        """resolve_data_window_end returns MAX(square_transactions.date_local)."""
        with patch("core.datastore.read_query", return_value=[{"m": "2026-06-16"}]):
            from core.store_config import resolve_data_window_end
            result = resolve_data_window_end("palmetto")
        self.assertEqual(result, "2026-06-16")

    def test_returns_none_on_empty_table(self):
        """Empty square_transactions → None (not an error)."""
        with patch("core.datastore.read_query", return_value=[{"m": None}]):
            from core.store_config import resolve_data_window_end
            result = resolve_data_window_end("palmetto")
        self.assertIsNone(result)

    def test_returns_none_on_bq_failure(self):
        """BQ failure → None (caller decides how to handle)."""
        with patch("core.datastore.read_query", side_effect=RuntimeError("BQ down")):
            from core.store_config import resolve_data_window_end
            result = resolve_data_window_end("palmetto")
        self.assertIsNone(result)

    def test_ignores_any_stored_value(self):
        """resolve_data_window_end never reads store_config — only BQ raw."""
        calls = []
        def fake_rq(sql):
            calls.append(sql)
            return [{"m": "2026-06-16"}]
        with patch("core.datastore.read_query", side_effect=fake_rq):
            from core.store_config import resolve_data_window_end
            result = resolve_data_window_end("palmetto")
        self.assertEqual(result, "2026-06-16")
        # Must query square_transactions, never store_config
        for sql in calls:
            self.assertIn("square_transactions", sql)
            self.assertNotIn("store_config", sql)


if __name__ == "__main__":
    unittest.main()
