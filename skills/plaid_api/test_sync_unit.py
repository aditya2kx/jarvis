"""Unit tests for skills/plaid_api (no live Plaid/BQ)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from skills.plaid_api.sync import _pfc, _row_from_txn, purge_item


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


if __name__ == "__main__":
    unittest.main()
