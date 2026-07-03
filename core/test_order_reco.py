"""Unit tests for core/order_reco.py (Issue #137, Option D).

Stubs core.datastore.read_query and core.store_config.get_config so no live
BQ connection is needed. Asserts the DELETE-then-slot1-then-slot2 statement
order (slot 2's TVF reads slot 1's row back from inventory_order_reco, so
slot 1 MUST land first — see migration 031's module comment).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestRefreshOrderReco(unittest.TestCase):
    def test_deletes_then_inserts_slot1_then_slot2(self):
        calls = []

        def fake_read_query(sql):
            calls.append(sql)
            return []

        with patch("core.datastore.read_query", side_effect=fake_read_query), \
             patch("core.store_config.get_config", return_value=None):
            from core.order_reco import refresh_order_reco
            refresh_order_reco("palmetto")

        self.assertEqual(len(calls), 3, calls)
        self.assertIn("DELETE FROM", calls[0])
        self.assertIn("inventory_order_reco", calls[0])
        self.assertIn("WHERE store = 'palmetto'", calls[0])
        self.assertIn("INSERT INTO", calls[1])
        self.assertIn("tvf_order_reco_slot1", calls[1])
        self.assertIn(", 1,", calls[1])
        self.assertIn("INSERT INTO", calls[2])
        self.assertIn("tvf_order_reco_slot2", calls[2])
        self.assertIn(", 2,", calls[2])

    def test_uses_default_max_tubs_when_unset(self):
        calls = []
        with patch("core.datastore.read_query", side_effect=lambda sql: calls.append(sql) or []), \
             patch("core.store_config.get_config", return_value=None):
            from core.order_reco import refresh_order_reco
            refresh_order_reco("palmetto")
        self.assertTrue(any("(120)" in c for c in calls), calls)

    def test_uses_stored_max_tubs_when_set(self):
        calls = []
        with patch("core.datastore.read_query", side_effect=lambda sql: calls.append(sql) or []), \
             patch("core.store_config.get_config", return_value="140"):
            from core.order_reco import refresh_order_reco
            refresh_order_reco("palmetto")
        self.assertTrue(any("(140)" in c for c in calls), calls)

    def test_scopes_all_statements_to_store(self):
        calls = []
        with patch("core.datastore.read_query", side_effect=lambda sql: calls.append(sql) or []), \
             patch("core.store_config.get_config", return_value=None):
            from core.order_reco import refresh_order_reco
            refresh_order_reco("austin")
        for sql in calls:
            self.assertIn("austin", sql)


if __name__ == "__main__":
    unittest.main()
