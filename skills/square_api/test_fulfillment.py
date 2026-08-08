#!/usr/bin/env python3
"""Unit tests for skills.square_api.fulfillment ops-clock extraction."""

from __future__ import annotations

import unittest

from skills.square_api.fulfillment import (
    enrich_transaction_record,
    extract_fulfillment_fields,
)


class TestExtractFulfillmentFields(unittest.TestCase):
    def test_scheduled_pickup_uses_pickup_at_for_ops(self):
        # Madeline: placed 12:15am CT, pickup_at 8:20am CT (13:20Z during CDT)
        order = {
            "id": "ORD1",
            "created_at": "2026-07-31T05:15:17Z",
            "closed_at": "2026-07-31T13:07:55Z",
            "fulfillments": [
                {
                    "type": "PICKUP",
                    "state": "COMPLETED",
                    "pickup_details": {
                        "schedule_type": "SCHEDULED",
                        "pickup_at": "2026-07-31T13:20:00Z",
                        "ready_at": "2026-07-31T13:07:53Z",
                        "picked_up_at": "2026-07-31T13:07:55Z",
                        "placed_at": "2026-07-31T05:15:18Z",
                    },
                }
            ],
        }
        f = extract_fulfillment_fields(order, shop_tz="America/Chicago")
        self.assertEqual(f["fulfillment_type"], "PICKUP")
        self.assertEqual(f["schedule_type"], "SCHEDULED")
        self.assertEqual(f["pickup_at_utc"], "2026-07-31T13:20:00Z")
        self.assertEqual(f["ops_date_local"], "2026-07-31")
        self.assertEqual(f["ops_hour_local"], 8)  # 8:20am CT, not midnight
        self.assertIn("T08:20:00", f["ops_at_local_iso"])

    def test_scheduled_delivery_uses_deliver_at(self):
        order = {
            "created_at": "2026-07-27T06:10:00Z",
            "closed_at": "2026-07-27T14:43:00Z",
            "fulfillments": [
                {
                    "type": "DELIVERY",
                    "state": "COMPLETED",
                    "delivery_details": {
                        "schedule_type": "SCHEDULED",
                        "deliver_at": "2026-07-27T14:45:00Z",
                        "ready_at": "2026-07-27T14:43:00Z",
                        "courier_pickup_at": "2026-07-27T14:44:00Z",
                    },
                }
            ],
        }
        f = extract_fulfillment_fields(order, shop_tz="America/Chicago")
        self.assertEqual(f["fulfillment_type"], "DELIVERY")
        self.assertEqual(f["ops_hour_local"], 9)  # 9:45am CT
        self.assertEqual(f["deliver_at_utc"], "2026-07-27T14:45:00Z")

    def test_register_no_fulfillment_falls_back_to_closed_at(self):
        order = {
            "created_at": "2026-07-31T18:00:00Z",
            "closed_at": "2026-07-31T18:05:00Z",
            "fulfillments": [],
        }
        f = extract_fulfillment_fields(order, shop_tz="America/Chicago")
        self.assertIsNone(f["schedule_type"])
        self.assertEqual(f["ops_hour_local"], 13)  # 1:05pm CDT
        self.assertEqual(f["closed_at_utc"], "2026-07-31T18:05:00Z")

    def test_enrich_without_order_uses_created_at_local(self):
        rec = {
            "transaction_id": "T1",
            "created_at_local_iso": "2026-07-31T14:30:00-05:00",
        }
        enrich_transaction_record(rec, None, shop_tz="America/Chicago")
        self.assertEqual(rec["ops_hour_local"], 14)
        self.assertEqual(rec["ops_date_local"], "2026-07-31")


if __name__ == "__main__":
    unittest.main()
