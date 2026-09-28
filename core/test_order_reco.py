"""Unit tests for core/order_reco.py (Issue #137, Option D + #215 N-slots; #350 procedure).

Stubs core.datastore.read_query and core.store_config.get_config so no live
BQ connection is needed. Asserts slot1-then-slot_n-then-DELETE-old order
(later slots read prior rows from inventory_order_reco; Issue #261).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestRefreshOrderRecoLegacy(unittest.TestCase):
    def test_inserts_then_deletes_prior_generation(self):
        calls = []

        def fake_read_query(sql):
            calls.append(sql)
            if "vw_order_reco_next_dates" in sql:
                return [{"slot": 1}, {"slot": 2}, {"slot": 3}]
            return []

        with patch("core.datastore.read_query", side_effect=fake_read_query), \
             patch("core.store_config.get_config", return_value=None):
            from core.order_reco import _refresh_legacy
            _refresh_legacy("palmetto")

        self.assertEqual(len(calls), 5, calls)  # next_dates + 3 inserts + DELETE old
        self.assertIn("vw_order_reco_next_dates", calls[0])
        self.assertIn("tvf_order_reco_slot1", calls[1])
        self.assertIn(", 1,", calls[1])
        self.assertIn("tvf_order_reco_slot_n", calls[2])
        self.assertIn("(120, 2)", calls[2])
        self.assertIn("tvf_order_reco_slot_n", calls[3])
        self.assertIn("(120, 3)", calls[3])
        self.assertIn("DELETE FROM", calls[4])
        self.assertIn("refreshed_at !=", calls[4])

    def test_clears_only_when_no_next_dates(self):
        calls = []

        def fake_read_query(sql):
            calls.append(sql)
            if "vw_order_reco_next_dates" in sql:
                return []
            return []

        with patch("core.datastore.read_query", side_effect=fake_read_query), \
             patch("core.store_config.get_config", return_value=None):
            from core.order_reco import _refresh_legacy
            _refresh_legacy("palmetto")

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
            from core.order_reco import _refresh_legacy
            _refresh_legacy("palmetto")
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
            from core.order_reco import _refresh_legacy
            _refresh_legacy("palmetto")
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
            from core.order_reco import _refresh_legacy
            _refresh_legacy("austin")
        for sql in calls:
            if "vw_order_reco_next_dates" in sql:
                continue
            self.assertIn("austin", sql)


class TestRefreshOrderRecoProcedure(unittest.TestCase):
    def _client(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.query.return_value.result.return_value = []
        return client

    def test_calls_procedure_once_with_params(self):
        client = self._client()
        with patch("core.datastore.get_client", return_value=client), \
             patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("BHAGA_ORDER_RECO_LEGACY", None)
            from core.order_reco import refresh_order_reco
            run_id = refresh_order_reco("austin", trigger="nightly")
        self.assertEqual(client.query.call_count, 1)
        sql = client.query.call_args.args[0]
        self.assertIn("sp_refresh_order_reco", sql)
        params = {p.name: p.value for p in client.query.call_args.kwargs["job_config"].query_parameters}
        self.assertEqual(params["store"], "austin")
        self.assertEqual(params["trigger"], "nightly")
        self.assertEqual(params["run_id"], run_id)
        self.assertTrue(run_id.startswith("py-"))

    def test_failure_logs_breadcrumb_and_raises(self):
        client = self._client()
        client.query.return_value.result.side_effect = RuntimeError("boom")
        with patch("core.datastore.get_client", return_value=client), \
             patch.dict("os.environ", {"BHAGA_ORDER_RECO_LEGACY": "0"}):
            from core.order_reco import refresh_order_reco
            with self.assertLogs("core.order_reco", level="ERROR") as logs, \
                 self.assertRaises(RuntimeError):
                refresh_order_reco("palmetto")
        self.assertIn("order_reco_failed run_id=py-", logs.output[0])

    def test_legacy_env_uses_tvf_chain(self):
        with patch.dict("os.environ", {"BHAGA_ORDER_RECO_LEGACY": "1"}), \
             patch("core.order_reco._refresh_legacy") as legacy, \
             patch("core.datastore.get_client") as get_client:
            from core.order_reco import refresh_order_reco
            self.assertIsNone(refresh_order_reco("palmetto"))
        legacy.assert_called_once_with("palmetto")
        get_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
