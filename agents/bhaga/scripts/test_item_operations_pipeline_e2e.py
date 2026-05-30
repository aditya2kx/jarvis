"""E2E: item_operations builder + upsert scoped to gap window (mocked sheets)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


class ItemOperationsPipelineE2ETests(unittest.TestCase):
    @mock.patch("agents.bhaga.scripts.item_operations.write_model_item_operations")
    def test_gap_window_filters_upsert_batch(self, mock_write):
        mock_write.return_value = {"inserted": 1, "updated": 0, "total_after": 1}

        item_lines = [
            {
                "date_local": "2026-05-19",
                "item_sold_at_local": "2026-05-19T10:00:00",
                "item_name": "Old",
                "category": "X",
                "qty_sold": 1,
                "gross_sales_cents": 100,
                "discount_cents": 0,
                "net_sales_cents": 100,
                "event_type": "Payment",
                "transaction_id": "OLD",
                "line_seq": 0,
            },
            {
                "date_local": "2026-05-20",
                "item_sold_at_local": "2026-05-20T10:00:00",
                "item_name": "New",
                "category": "X",
                "qty_sold": 1,
                "gross_sales_cents": 200,
                "discount_cents": 0,
                "net_sales_cents": 200,
                "event_type": "Payment",
                "transaction_id": "NEW",
                "line_seq": 0,
            },
        ]

        from agents.bhaga.scripts.item_operations import refresh_item_operations_tab
        import datetime
        from zoneinfo import ZoneInfo

        now = datetime.datetime(2026, 5, 21, 22, 0, tzinfo=ZoneInfo("America/Chicago"))
        summary = refresh_item_operations_tab(
            model_sid="model",
            square_raw_sid="sq",
            adp_raw_sid="adp",
            store="palmetto",
            excluded_from_tip_pool=set(),
            punches=[],
            wage_rates=[],
            item_lines=item_lines,
            date_from="2026-05-20",
            date_to="2026-05-20",
            dry_run=False,
            now_ct=now,
        )
        self.assertEqual(summary.get("inserted"), 1)

        self.assertTrue(mock_write.called)
        records = mock_write.call_args[0][1]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["transaction_id"], "NEW")


if __name__ == "__main__":
    unittest.main()
