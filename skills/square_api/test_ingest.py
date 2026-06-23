#!/usr/bin/env python3
"""Unit tests for skills/square_api/ingest — API -> in-memory -> BQ row pipeline."""

from __future__ import annotations

import datetime
import io
import csv
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_api.export import (
    TXN_HEADER, ITEM_HEADER,
    _money, _build_transaction_rows, _build_refund_rows, _build_item_rows,
)
from skills.square_tips.transactions_backend import (
    parse_transaction_rows, parse_item_rows, DEFAULT_SHOP_TZ,
)


# ── Fixtures ───────────────────────────────────────────────────────

DISPLAY_TZ = "America/New_York"
TZ_LABEL = "Eastern Time (US & Canada)"


def _make_payment(
    *,
    payment_id: str = "PAY1",
    order_id: str = "ORD1",
    created_at: str = "2026-06-01T20:00:00Z",  # 4pm CT = 5pm ET
    tip_cents: int = 200,
    total_cents: int = 1400,
    fees_cents: int = 42,
    team_member_id: str = "",
) -> dict:
    return {
        "id": payment_id,
        "order_id": order_id,
        "created_at": created_at,
        "tip_money": {"amount": tip_cents, "currency": "USD"},
        "total_money": {"amount": total_cents, "currency": "USD"},
        "processing_fee": [{"amount_money": {"amount": fees_cents}}],
        "team_member_id": team_member_id,
    }


def _make_order(
    *,
    order_id: str = "ORD1",
    gross_cents: int = 1200,
    discount_cents: int = 0,
    tax_cents: int = 0,
    source_name: str = "Register",
    line_items: list | None = None,
) -> dict:
    li = line_items or [{
        "name": "Smoothie",
        "quantity": "1",
        "gross_sales_money": {"amount": gross_cents},
        "total_discount_money": {"amount": discount_cents},
        "catalog_object_id": "CAT1",
    }]
    return {
        "id": order_id,
        "source": {"name": source_name},
        "line_items": li,
        "total_discount_money": {"amount": discount_cents},
        "total_tax_money": {"amount": tax_cents},
        "net_amounts": {"total_money": {"amount": gross_cents}},
    }


def _make_refund(
    *,
    refund_id: str = "REF1",
    payment_id: str = "PAY1",
    order_id: str = "ORD1",
    created_at: str = "2026-06-01T21:00:00Z",
    amount_cents: int = 500,
) -> dict:
    return {
        "id": refund_id,
        "payment_id": payment_id,
        "order_id": order_id,
        "created_at": created_at,
        "amount_money": {"amount": amount_cents},
        "processing_fee": [],
    }


# ── Parser round-trip tests ────────────────────────────────────────

class TestParserInMemoryRoundTrip(unittest.TestCase):
    """parse_transaction_rows([header]+rows) == parse_csv(same data in file)."""

    def _build_txn_rows_for_date(self, date_str: str) -> list[list[str]]:
        payments = [_make_payment(created_at=f"{date_str}T20:00:00Z")]
        orders = {_make_order()["id"]: _make_order()}
        return _build_transaction_rows(
            payments, orders, {}, "Palmetto", DISPLAY_TZ, TZ_LABEL
        )

    def test_payment_row_parses_to_correct_date_local(self):
        txn_rows = self._build_txn_rows_for_date("2026-06-01")
        records = parse_transaction_rows([TXN_HEADER] + txn_rows, shop_tz=DEFAULT_SHOP_TZ)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event_type"], "Payment")
        # 2026-06-01T20:00:00Z = 4pm CT = date_local 2026-06-01
        self.assertEqual(records[0]["date_local"], "2026-06-01")

    def test_round_trip_matches_parse_csv(self):
        """parse_transaction_rows == parse_csv for the same data."""
        from skills.square_tips.transactions_backend import parse_csv
        import tempfile, pathlib

        txn_rows = self._build_txn_rows_for_date("2026-06-01")
        in_memory_records = parse_transaction_rows([TXN_HEADER] + txn_rows, shop_tz=DEFAULT_SHOP_TZ)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            w = csv.writer(f)
            w.writerow(TXN_HEADER)
            for r in txn_rows:
                w.writerow(r)
            tmp = f.name

        file_records = parse_csv(pathlib.Path(tmp), shop_tz=DEFAULT_SHOP_TZ)
        os.unlink(tmp)
        self.assertEqual(in_memory_records, file_records)

    def test_refund_row_has_negative_amounts(self):
        payments = [_make_payment()]
        orders = {_make_order()["id"]: _make_order()}
        refunds = [_make_refund(amount_cents=500)]
        ref_rows = _build_refund_rows(refunds, orders, {}, "Palmetto", DISPLAY_TZ, TZ_LABEL)
        records = parse_transaction_rows([TXN_HEADER] + ref_rows, shop_tz=DEFAULT_SHOP_TZ)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event_type"], "Refund")
        self.assertLess(records[0]["gross_sales_cents"], 0)

    def test_tip_cents_parsed_correctly(self):
        payments = [_make_payment(tip_cents=200)]
        orders = {"ORD1": _make_order(gross_cents=1200)}
        txn_rows = _build_transaction_rows(payments, orders, {}, "Palmetto", DISPLAY_TZ, TZ_LABEL)
        records = parse_transaction_rows([TXN_HEADER] + txn_rows, shop_tz=DEFAULT_SHOP_TZ)
        self.assertEqual(records[0]["tip_cents"], 200)

    def test_timezone_et_to_ct_conversion(self):
        """Midnight ET (00:00:00 ET) should be 23:00:00 previous day CT."""
        midnight_et_utc = "2026-06-02T04:00:00Z"  # midnight ET
        payments = [_make_payment(created_at=midnight_et_utc)]
        orders = {"ORD1": _make_order()}
        txn_rows = _build_transaction_rows(payments, orders, {}, "Palmetto", DISPLAY_TZ, TZ_LABEL)
        records = parse_transaction_rows([TXN_HEADER] + txn_rows, shop_tz=DEFAULT_SHOP_TZ)
        # 00:00 ET on Jun 2 = 23:00 CT on Jun 1
        self.assertEqual(records[0]["date_local"], "2026-06-01")


class TestParserItemRowsRoundTrip(unittest.TestCase):
    def test_item_row_round_trip_matches_parse_item_sales_csv(self):
        from skills.square_tips.transactions_backend import parse_item_sales_csv
        import tempfile, pathlib

        payments = [_make_payment()]
        orders = {"ORD1": _make_order()}
        item_rows = _build_item_rows(payments, orders, {}, {}, "Palmetto", DISPLAY_TZ, TZ_LABEL)
        in_memory = parse_item_rows([ITEM_HEADER] + item_rows, shop_tz=DEFAULT_SHOP_TZ)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            w = csv.writer(f)
            w.writerow(ITEM_HEADER)
            for r in item_rows:
                w.writerow(r)
            tmp = f.name

        file_records = parse_item_sales_csv(pathlib.Path(tmp), shop_tz=DEFAULT_SHOP_TZ)
        os.unlink(tmp)
        self.assertEqual(in_memory, file_records)

    def test_item_name_extracted(self):
        payments = [_make_payment()]
        orders = {"ORD1": _make_order()}
        item_rows = _build_item_rows(payments, orders, {}, {}, "Palmetto", DISPLAY_TZ, TZ_LABEL)
        records = parse_item_rows([ITEM_HEADER] + item_rows, shop_tz=DEFAULT_SHOP_TZ)
        self.assertEqual(records[0]["item_name"], "Smoothie")

    def test_item_sold_at_local_uses_payment_timestamp(self):
        payments = [_make_payment(created_at="2026-06-01T20:00:00Z")]
        orders = {"ORD1": _make_order()}
        item_rows = _build_item_rows(payments, orders, {}, {}, "Palmetto", DISPLAY_TZ, TZ_LABEL)
        records = parse_item_rows([ITEM_HEADER] + item_rows, shop_tz=DEFAULT_SHOP_TZ)
        self.assertTrue(records[0]["item_sold_at_local"].startswith("2026-06-01T"))


class TestIngestWindowBQ(unittest.TestCase):
    """Test ingest_window calls load_rows with the right tables and merge keys."""

    def setUp(self):
        self.payments = [_make_payment()]
        self.refunds = []
        self.orders = {"ORD1": _make_order()}

    def _mock_client(self):
        client = MagicMock()
        client.get_paginated.side_effect = lambda path, **kw: (
            self.payments if "/payments" in path
            else self.refunds if "/refunds" in path
            else []
        )
        client.post.side_effect = lambda path, body=None, **kw: (
            {"orders": [self.orders.get(oid, {}) for oid in body.get("order_ids", [])]}
            if "/orders/batch" in path
            else {"objects": [], "related_objects": []}
        )
        client.post_paginated.return_value = []
        client.get.return_value = {"locations": [{"id": "LOC1", "name": "Palmetto"}]}
        return client

    def _mock_profile(self):
        return {
            "timezone": {
                "square_account_display_tz": "America/New_York",
                "shop_tz": "America/Chicago",
            },
            "square": {
                "location_id": "LOC1",
                "oauth_secret": "square_palmetto_oauth",
                "application_id": "sq0idp-test",
            },
        }

    def test_ingest_window_calls_load_rows_for_all_tables(self):
        from skills.square_api.ingest import ingest_window

        load_calls = []

        def fake_load_rows(table, rows, *, merge_keys, column_bq_types=None, **kw):
            load_calls.append(table)
            return len(rows)

        with (
            patch("skills.square_api.ingest.SquareClient", return_value=self._mock_client()),
            patch("agents.bhaga.scripts.backfill_bigquery.load_store_profile",
                  return_value=self._mock_profile()),
            patch("core.datastore.load_rows", side_effect=fake_load_rows),
            patch("skills.square_api.ingest._persist_location_id"),
        ):
            counts = ingest_window(
                start_date=datetime.date(2026, 6, 1),
                end_date=datetime.date(2026, 6, 1),
                store="palmetto",
                profile=self._mock_profile(),
                client=self._mock_client(),
            )

        self.assertIn("square_transactions", counts)
        self.assertIn("square_daily_rollup", counts)
        self.assertIn("square_item_lines", counts)
        self.assertIn("square_item_daily", counts)
        tables_loaded = set(load_calls)
        self.assertIsSubset(
            {"square_transactions", "square_daily_rollup", "square_item_lines", "square_item_daily"},
            tables_loaded,
        )

    def assertIsSubset(self, subset, superset):
        missing = subset - superset
        if missing:
            self.fail(f"Expected {missing} to be in {superset}")


if __name__ == "__main__":
    unittest.main()
