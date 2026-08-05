"""Unit tests for core/order_reco.py (Issue #137, Option D + #215 N-slots).

Stubs core.datastore.read_query and core.store_config.get_config so no live
BQ connection is needed. Asserts DELETE-then-slot1-then-slot_n order (later
slots read prior rows from inventory_order_reco).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestRefreshOrderReco(unittest.TestCase):
    def test_deletes_then_inserts_slot1_then_slot_n(self):
        calls = []

        def fake_read_query(sql):
            calls.append(sql)
            if "vw_order_reco_next_dates" in sql:
                return [{"slot": 1}, {"slot": 2}, {"slot": 3}]
            return []

        with patch("core.datastore.read_query", side_effect=fake_read_query), \
             patch("core.store_config.get_config", return_value=None):
            from core.order_reco import refresh_order_reco
            refresh_order_reco("palmetto")

        self.assertEqual(len(calls), 5, calls)  # next_dates + DELETE + 3 inserts
        self.assertIn("vw_order_reco_next_dates", calls[0])
        self.assertIn("DELETE FROM", calls[1])
        self.assertIn("inventory_order_reco", calls[1])
        self.assertIn("tvf_order_reco_slot1", calls[2])
        self.assertIn(", 1,", calls[2])
        self.assertIn("tvf_order_reco_slot_n", calls[3])
        self.assertIn("(120, 2)", calls[3])
        self.assertIn("tvf_order_reco_slot_n", calls[4])
        self.assertIn("(120, 3)", calls[4])

    def test_clears_only_when_no_next_dates(self):
        calls = []

        def fake_read_query(sql):
            calls.append(sql)
            if "vw_order_reco_next_dates" in sql:
                return []
            return []

        with patch("core.datastore.read_query", side_effect=fake_read_query), \
             patch("core.store_config.get_config", return_value=None):
            from core.order_reco import refresh_order_reco
            refresh_order_reco("palmetto")

        self.assertEqual(len(calls), 2, calls)
        self.assertIn("DELETE FROM", calls[1])
        self.assertTrue(all("INSERT" not in c for c in calls))

    def test_uses_default_max_tubs_when_unset(self):
        calls = []

        def fake_read_query(sql):
            calls.append(sql)
            if "vw_order_reco_next_dates" in sql:
                return [{"slot": 1}, {"slot": 2}]
            return []

        with patch("core.datastore.read_query", side_effect=fake_read_query), \
             patch("core.store_config.get_config", return_value=None):
            from core.order_reco import refresh_order_reco
            refresh_order_reco("palmetto")
        self.assertTrue(any("(120)" in c or "(120, 2)" in c for c in calls), calls)

    def test_uses_stored_max_tubs_when_set(self):
        calls = []

        def fake_read_query(sql):
            calls.append(sql)
            if "vw_order_reco_next_dates" in sql:
                return [{"slot": 1}, {"slot": 2}]
            return []

        with patch("core.datastore.read_query", side_effect=fake_read_query), \
             patch("core.store_config.get_config", return_value="140"):
            from core.order_reco import refresh_order_reco
            refresh_order_reco("palmetto")
        self.assertTrue(any("(140)" in c or "(140, 2)" in c for c in calls), calls)

    def test_scopes_all_statements_to_store(self):
        calls = []

        def fake_read_query(sql):
            calls.append(sql)
            if "vw_order_reco_next_dates" in sql:
                return [{"slot": 1}, {"slot": 2}]
            return []

        with patch("core.datastore.read_query", side_effect=fake_read_query), \
             patch("core.store_config.get_config", return_value=None):
            from core.order_reco import refresh_order_reco
            refresh_order_reco("austin")
        for sql in calls:
            if "vw_order_reco_next_dates" in sql:
                continue
            self.assertIn("austin", sql)


if __name__ == "__main__":
    unittest.main()
