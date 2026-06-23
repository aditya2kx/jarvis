#!/usr/bin/env python3
"""Round-trip tests: square_api.export synthesized CSVs → square_tips parsers.

The whole WA integration contract is "the API path writes CSVs the existing
parsers consume identically to a dashboard export". These tests build fake
Square API objects (Payment / Order / Refund), run the row builders, write a
CSV, and parse it with the REAL ``transactions_backend.parse_csv`` /
``parse_item_sales_csv`` — asserting the canonical record fields the BQ
mappers read.

Run:
    python3 -m pytest skills/square_api/test_export_format.py -q
"""

from __future__ import annotations

import csv
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_api.export import (
    ITEM_HEADER,
    TXN_HEADER,
    _build_item_rows,
    _build_refund_rows,
    _build_transaction_rows,
    _money,
    _tz_label,
    _write_csv,
)
from skills.square_tips.transactions_backend import (
    _COL,
    _ITEM_COL,
    parse_csv,
    parse_item_sales_csv,
)

_DISPLAY_TZ = "America/New_York"
_TZ_LABEL = _tz_label(_DISPLAY_TZ)
_LOCATION = "Austin Mueller Lake"

# 18:30 UTC == 14:30 Eastern == 13:30 Central
_PAYMENT = {
    "id": "PAY1",
    "order_id": "ORD1",
    "created_at": "2026-06-09T18:30:05Z",
    "tip_money": {"amount": 150, "currency": "USD"},
    "total_money": {"amount": 1450, "currency": "USD"},
    "processing_fee": [{"amount_money": {"amount": 36, "currency": "USD"}}],
    "team_member_id": "TM1",
}
_ORDER = {
    "id": "ORD1",
    "created_at": "2026-06-09T18:30:01Z",
    "source": {"name": "Register"},
    "total_discount_money": {"amount": 100, "currency": "USD"},
    "total_tax_money": {"amount": 0, "currency": "USD"},
    "net_amounts": {"total_money": {"amount": 1450, "currency": "USD"}},
    "line_items": [
        {
            "name": "Blue Smoothie (16oz.)",
            "quantity": "1",
            "catalog_object_id": "VAR1",
            "gross_sales_money": {"amount": 1200, "currency": "USD"},
            "total_discount_money": {"amount": 100, "currency": "USD"},
        },
        {
            "name": "Acai Bowl",
            "quantity": "2",
            "catalog_object_id": "VAR2",
            "gross_sales_money": {"amount": 200, "currency": "USD"},
            "total_discount_money": {"amount": 0, "currency": "USD"},
        },
    ],
}
_REFUND = {
    "id": "REF1",
    "payment_id": "PAY0",
    "order_id": "ORDR",
    "created_at": "2026-06-09T20:00:00Z",
    "amount_money": {"amount": 500, "currency": "USD"},
    "processing_fee": [{"amount_money": {"amount": -13, "currency": "USD"}}],
}
_TEAM = {"TM1": "Amy Guerrero"}
_CATEGORIES = {"VAR1": "Health Boost Smoothies", "VAR2": "Bowls"}


def _write_tmp(header: list[str], rows: list[list[str]]) -> pathlib.Path:
    fd, name = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    path = pathlib.Path(name)
    _write_csv(path, header, rows)
    return path


class TestHeaderContracts(unittest.TestCase):
    """Header lengths and parser index alignment."""

    def test_txn_header_has_55_columns_with_parser_indexes(self):
        self.assertEqual(len(TXN_HEADER), 55)
        for name, idx in [("Date", 0), ("Time", 1), ("Time Zone", 2),
                          ("Gross Sales", 3), ("Tip", 9),
                          ("Total Collected", 11), ("Source", 12),
                          ("Net Total", 21), ("Transaction ID", 22),
                          ("Payment ID", 23), ("Staff Name", 27),
                          ("Event Type", 31), ("Location", 32),
                          ("Transaction Status", 46)]:
            self.assertEqual(TXN_HEADER[idx], name)

    def test_item_header_aligns_with_parser_item_col(self):
        self.assertEqual(len(ITEM_HEADER), 31)
        # Every semantic column the parser reads positionally must carry the
        # real dashboard name at that index.
        expectations = {
            "date": "Date", "time": "Time", "time_zone": "Time Zone",
            "category": "Category", "item": "Item", "qty": "Qty",
            "gross_sales": "Gross Sales", "discounts": "Discounts",
            "net_sales": "Net Sales", "transaction_id": "Transaction ID",
            "payment_id": "Payment ID", "event_type": "Event Type",
            "location": "Location", "employee": "Employee",
            "channel": "Channel",
        }
        for key, expected_name in expectations.items():
            idx = _ITEM_COL[key]
            self.assertEqual(
                ITEM_HEADER[idx], expected_name,
                f"_ITEM_COL[{key!r}]={idx} but ITEM_HEADER[{idx}]={ITEM_HEADER[idx]!r}",
            )


class TestTransactionsRoundTrip(unittest.TestCase):
    def setUp(self):
        rows = _build_transaction_rows(
            [_PAYMENT], {"ORD1": _ORDER}, _TEAM, _LOCATION, _DISPLAY_TZ, _TZ_LABEL)
        rows += _build_refund_rows(
            [_REFUND], {"ORD1": _ORDER}, _TEAM, _LOCATION, _DISPLAY_TZ, _TZ_LABEL)
        self.path = _write_tmp(TXN_HEADER, rows)
        self.records = parse_csv(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_payment_record_fields(self):
        payment = next(r for r in self.records if r["event_type"] == "Payment")
        self.assertEqual(payment["transaction_id"], "ORD1")
        self.assertEqual(payment["gross_sales_cents"], 1400)   # 1200 + 200
        self.assertEqual(payment["discount_cents"], -100)
        self.assertEqual(payment["tip_cents"], 150)
        self.assertEqual(payment["total_collected_cents"], 1450)
        self.assertEqual(payment["net_total_cents"], 1414)     # 1450 - 36 fee
        self.assertEqual(payment["source"], "Register")
        self.assertEqual(payment["staff_name"], "Amy Guerrero")
        self.assertEqual(payment["location"], _LOCATION)

    def test_timezone_conversion_display_et_to_shop_ct(self):
        payment = next(r for r in self.records if r["event_type"] == "Payment")
        # 18:30:05 UTC = 14:30:05 ET (raw CSV) = 13:30:05 CT (shop-local)
        self.assertEqual(payment["raw_time_csv"], "14:30:05")
        self.assertEqual(payment["raw_tz_csv"], _TZ_LABEL)
        self.assertEqual(payment["date_local"], "2026-06-09")
        self.assertEqual(payment["hour_local"], 13)
        self.assertTrue(payment["created_at_src_iso"].startswith("2026-06-09T14:30:05"))
        self.assertTrue(payment["created_at_local_iso"].startswith("2026-06-09T13:30:05"))

    def test_refund_record_negative_amounts(self):
        refund = next(r for r in self.records if r["event_type"] == "Refund")
        self.assertEqual(refund["transaction_id"], "ORDR")
        self.assertEqual(refund["gross_sales_cents"], -500)
        self.assertEqual(refund["total_collected_cents"], -500)
        self.assertEqual(refund["net_total_cents"], -487)      # -500 + 13 fee reversal
        self.assertEqual(refund["tip_cents"], 0)


class TestItemsRoundTrip(unittest.TestCase):
    def setUp(self):
        rows = _build_item_rows(
            [_PAYMENT], {"ORD1": _ORDER}, _TEAM, _CATEGORIES,
            _LOCATION, _DISPLAY_TZ, _TZ_LABEL)
        self.path = _write_tmp(ITEM_HEADER, rows)
        self.records = parse_item_sales_csv(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_one_row_per_line_item(self):
        self.assertEqual(len(self.records), 2)

    def test_line_item_fields(self):
        smoothie = next(r for r in self.records if "Smoothie" in r["item_name"])
        self.assertEqual(smoothie["category"], "Health Boost Smoothies")
        self.assertEqual(smoothie["qty_sold"], 1)
        self.assertEqual(smoothie["gross_sales_cents"], 1200)
        self.assertEqual(smoothie["discount_cents"], -100)
        self.assertEqual(smoothie["net_sales_cents"], 1100)
        self.assertEqual(smoothie["transaction_id"], "ORD1")
        self.assertEqual(smoothie["payment_id"], "PAY1")
        self.assertEqual(smoothie["event_type"], "Payment")
        self.assertEqual(smoothie["employee"], "Amy Guerrero")
        self.assertEqual(smoothie["channel"], _LOCATION)

    def test_item_time_is_payment_time_not_order_time(self):
        # Payment created_at is 18:30:05Z; order created_at is 18:30:01Z.
        # The dashboard rows carry the payment time — and item_sold_at_local
        # is part of the BQ natural key, so this must match the scrape.
        rec = self.records[0]
        self.assertEqual(rec["time_local"], "13:30:05")
        self.assertEqual(rec["item_sold_at_local"], "2026-06-09T13:30:05")


class TestMoneyFormat(unittest.TestCase):
    def test_money_rendering(self):
        self.assertEqual(_money(0), "$0.00")
        self.assertEqual(_money(1350), "$13.50")
        self.assertEqual(_money(-345), "-$3.45")
        self.assertEqual(_money(123456), "$1234.56")


if __name__ == "__main__":
    unittest.main()
