"""Unit tests for skills/plaid_api (no live Plaid/BQ)."""
from __future__ import annotations

import unittest

from skills.plaid_api.sync import _pfc, _row_from_txn


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


if __name__ == "__main__":
    unittest.main()
