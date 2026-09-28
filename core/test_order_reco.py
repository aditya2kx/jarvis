"""Unit tests for core/order_reco.py (Issue #350: one sp_refresh_order_reco CALL).

Stubs core.datastore.get_client so no live BQ connection is needed. The
procedure's own behaviour is covered by core/test_migration_081_sp_refresh_order_reco.py
and the sandbox harness (scripts/order_reco_sandbox.py).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestRefreshOrderRecoProcedure(unittest.TestCase):
    def _client(self):
        client = MagicMock()
        client.query.return_value.result.return_value = []
        return client

    def test_calls_procedure_once_with_params(self):
        client = self._client()
        with patch("core.datastore.get_client", return_value=client):
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
        with patch("core.datastore.get_client", return_value=client):
            from core.order_reco import refresh_order_reco
            with self.assertLogs("core.order_reco", level="ERROR") as logs, \
                 self.assertRaises(RuntimeError):
                refresh_order_reco("palmetto")
        self.assertIn("order_reco_failed run_id=py-", logs.output[0])

    def test_bq_disabled_is_a_noop(self):
        with patch("core.datastore.get_client", return_value=None):
            from core.order_reco import refresh_order_reco
            self.assertIsNone(refresh_order_reco("palmetto"))


if __name__ == "__main__":
    unittest.main()
