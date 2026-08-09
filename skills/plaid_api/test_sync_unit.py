"""Unit tests for skills/plaid_api (no live Plaid/BQ)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from skills.plaid_api.sync import (
    _dedupe_transactions,
    _pfc,
    _row_from_txn,
    purge_item,
    update_item_webhook,
)


class TestPfc(unittest.TestCase):
    def test_extracts_primary_and_detailed(self):
        primary, detailed = _pfc(
            {"personal_finance_category": {"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_RESTAURANT"}}
        )
        self.assertEqual(primary, "FOOD_AND_DRINK")
        self.assertEqual(detailed, "FOOD_AND_DRINK_RESTAURANT")

    def test_missing_pfc(self):
        self.assertEqual(_pfc({}), (None, None))


class TestRowFromTxn(unittest.TestCase):
    def test_maps_core_fields(self):
        row = _row_from_txn(
            {
                "transaction_id": "tx1",
                "account_id": "acc1",
                "date": "2026-07-01",
                "name": "PALMETTO",
                "merchant_name": "Palmetto",
                "amount": 12.34,
                "iso_currency_code": "USD",
                "pending": False,
                "personal_finance_category": {"primary": "GENERAL_MERCHANDISE"},
            },
            "item1",
        )
        self.assertEqual(row["transaction_id"], "tx1")
        self.assertEqual(row["item_id"], "item1")
        self.assertEqual(row["amount"], 12.34)
        self.assertEqual(row["pfc_primary"], "GENERAL_MERCHANDISE")
        self.assertFalse(row["pending"])


class _FakeRow:
    def __init__(self, n: int):
        self.n = n


class TestPurgeItem(unittest.TestCase):
    def _mock_bq(self, txn_n: int, item_n: int) -> MagicMock:
        bq = MagicMock()
        # First two query().result() calls are COUNTs; later are DELETEs.
        count_results = [
            iter([_FakeRow(txn_n)]),
            iter([_FakeRow(item_n)]),
        ]
        delete_results: list = []

        def _query(*_a, **_k):
            job = MagicMock()
            if count_results:
                job.result.return_value = count_results.pop(0)
            else:
                job.result.return_value = iter([])
                delete_results.append(True)
            return job

        bq.query.side_effect = _query
        bq._delete_calls = delete_results  # type: ignore[attr-defined]
        return bq

    @patch("skills.plaid_api.sync._bq_client")
    def test_dry_run_issues_no_delete(self, mock_client):
        bq = self._mock_bq(txn_n=50, item_n=1)
        mock_client.return_value = bq
        out = purge_item("palmetto", "item_sandbox", dry_run=True)
        self.assertEqual(out["transactions_deleted"], 50)
        self.assertTrue(out["item_deleted"])
        self.assertTrue(out["dry_run"])
        # Only the two COUNT queries — no DELETE.
        self.assertEqual(bq.query.call_count, 2)
        for call in bq.query.call_args_list:
            sql = call.args[0] if call.args else ""
            self.assertNotIn("DELETE", sql.upper())

    @patch("skills.plaid_api.sync._bq_client")
    def test_live_deletes_txns_then_item(self, mock_client):
        bq = self._mock_bq(txn_n=50, item_n=1)
        mock_client.return_value = bq
        out = purge_item("palmetto", "item_sandbox", dry_run=False)
        self.assertEqual(out["transactions_deleted"], 50)
        self.assertTrue(out["item_deleted"])
        self.assertFalse(out["dry_run"])
        self.assertEqual(bq.query.call_count, 4)  # 2 COUNT + 2 DELETE
        sqls = [c.args[0] for c in bq.query.call_args_list]
        self.assertIn("DELETE FROM", sqls[2])
        self.assertIn("plaid_transactions", sqls[2])
        self.assertIn("DELETE FROM", sqls[3])
        self.assertIn("plaid_items", sqls[3])


class TestUpdateItemWebhook(unittest.TestCase):
    def test_rejects_non_https(self):
        with self.assertRaises(ValueError):
            update_item_webhook("palmetto", "item1", "http://insecure.example/plaid/webhook")

    @patch("skills.plaid_api.sync.PlaidClient")
    @patch("skills.plaid_api.sync.get_access_token", return_value="access-tok")
    def test_calls_item_webhook_update(self, _tok, mock_client_cls):
        client = MagicMock()
        client.item_webhook_update.return_value = {
            "item": {
                "item_id": "item1",
                "webhook": "https://bhaga-webhook.example/plaid/webhook",
            }
        }
        mock_client_cls.return_value = client
        out = update_item_webhook(
            "palmetto",
            "item1",
            "https://bhaga-webhook.example/plaid/webhook",
        )
        client.item_webhook_update.assert_called_once_with(
            "access-tok",
            "https://bhaga-webhook.example/plaid/webhook",
        )
        self.assertEqual(out["item_id"], "item1")
        self.assertEqual(out["webhook"], "https://bhaga-webhook.example/plaid/webhook")


class TestDedupeTransactions(unittest.TestCase):
    def test_no_op_when_no_extras(self):
        bq = MagicMock()
        job = MagicMock()
        job.result.return_value = iter([_FakeRow(0)])
        bq.query.return_value = job
        self.assertEqual(_dedupe_transactions(bq), 0)
        self.assertEqual(bq.query.call_count, 1)

    def test_rewrites_when_extras(self):
        bq = MagicMock()
        count_job = MagicMock()
        count_job.result.return_value = iter([_FakeRow(3)])
        rewrite_job = MagicMock()
        rewrite_job.result.return_value = iter([])
        bq.query.side_effect = [count_job, rewrite_job]
        self.assertEqual(_dedupe_transactions(bq), 3)
        sql = bq.query.call_args_list[1].args[0]
        self.assertIn("CREATE OR REPLACE TABLE", sql)
        self.assertIn("ROW_NUMBER()", sql)
        self.assertIn("PARTITION BY transaction_id", sql)
        self.assertIn("WHERE rn = 1", sql)
        self.assertNotIn("DELETE FROM", sql)


if __name__ == "__main__":
    unittest.main()
