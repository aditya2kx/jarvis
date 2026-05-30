"""Tests for item-line parsing (parse_item_sales_csv extensions)."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_tips.transactions_backend import parse_item_sales_csv

_HEADER = (
    "Date,Time,Time Zone,Category,Item,Qty,Price Point Name,SKU,"
    "Modifiers Applied,Gross Sales,Discounts,Net Sales,Tax,Transaction ID,"
    "Payment ID,Device Name,Col16,Col17,Event Type,Location,Col20,Col21,"
    "Col22,Col23,Unit,Count,Col26,Employee,Col28,Channel"
)


def _row(txn: str, item: str, time: str = "19:30:00") -> str:
    return (
        f"2026-05-26,{time},Eastern Time (US & Canada),Smoothies,{item},1,,,,"
        f"11.95,0.00,11.95,0.00,{txn},PAY,,,,Payment,Austin Mueller Lake,,,,,,"
        f",1,,Cashier A,,Austin Mueller Lake"
    )


def _natural_key(r: dict) -> tuple:
    return (r["transaction_id"], r["item_name"], r["item_sold_at_local"], r["line_seq"])


def _parse_csv_text(lines: list[str]) -> list[dict]:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8"
    ) as f:
        f.write(_HEADER + "\n")
        f.write("\n".join(lines) + "\n")
        path = pathlib.Path(f.name)
    try:
        return parse_item_sales_csv(path, shop_tz="America/Chicago")
    finally:
        path.unlink(missing_ok=True)

_FIXTURE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "agents/bhaga/scripts/fixtures/item_sales/items-sample.csv"
)


class ItemLinesParseTests(unittest.TestCase):
    def test_line_seq_and_sold_at_local(self):
        recs = parse_item_sales_csv(_FIXTURE, shop_tz="America/Chicago")
        self.assertEqual(len(recs), 3)
        # The two TXN001 lines share (txn, item, sold_at) → per-group seq 0,1.
        self.assertEqual(recs[0]["line_seq"], 0)
        self.assertEqual(recs[1]["line_seq"], 1)
        # The TXN002 line is a different group → counter resets to 0.
        self.assertEqual(recs[2]["line_seq"], 0)
        self.assertEqual(recs[2]["transaction_id"], "TXN002")
        self.assertEqual(recs[0]["item_sold_at_local"], "2026-05-26T18:30:00")
        self.assertEqual(recs[0]["transaction_id"], "TXN001")

    def test_duplicate_item_names_distinct_keys(self):
        recs = parse_item_sales_csv(_FIXTURE, shop_tz="America/Chicago")
        keys = {
            (r["transaction_id"], r["item_name"], r["item_sold_at_local"], r["line_seq"])
            for r in recs
        }
        self.assertEqual(len(keys), 3)
        dup_lines = [r for r in recs if r["transaction_id"] == "TXN001"]
        self.assertEqual(len(dup_lines), 2)
        self.assertNotEqual(dup_lines[0]["line_seq"], dup_lines[1]["line_seq"])

    def test_refund_event_type_retained(self):
        recs = parse_item_sales_csv(_FIXTURE, shop_tz="America/Chicago")
        refunds = [r for r in recs if r["event_type"] == "Refund"]
        self.assertEqual(len(refunds), 1)
        payments = [r for r in recs if r["event_type"] == "Payment"]
        self.assertEqual(len(payments), 2)


class LineSeqExportStabilityTests(unittest.TestCase):
    """line_seq must depend only on the natural-key group, not file position.

    Regression guard: a file-global counter would assign the SAME physical line
    a different line_seq when other lines precede it in a differently-windowed
    export, breaking the natural key and duplicating rows on replay.
    """

    def test_seq_independent_of_intervening_rows(self):
        # The two TXN_DUP lines are split by an unrelated line in between.
        recs = _parse_csv_text([
            _row("TXN_DUP", "Latte"),
            _row("TXN_OTHER", "Mocha"),
            _row("TXN_DUP", "Latte"),
        ])
        dup = sorted(
            (r["line_seq"] for r in recs if r["transaction_id"] == "TXN_DUP")
        )
        self.assertEqual(dup, [0, 1])

    def test_same_line_stable_key_across_windows(self):
        # Two exports that both contain the shared TXN_DUP/Latte line but with
        # different surrounding rows must produce an identical natural key for it.
        export_a = _parse_csv_text([
            _row("TXN_A1", "Espresso"),
            _row("TXN_DUP", "Latte"),
        ])
        export_b = _parse_csv_text([
            _row("TXN_B1", "Tea"),
            _row("TXN_B2", "Drip"),
            _row("TXN_DUP", "Latte"),
        ])
        key_a = next(_natural_key(r) for r in export_a if r["transaction_id"] == "TXN_DUP")
        key_b = next(_natural_key(r) for r in export_b if r["transaction_id"] == "TXN_DUP")
        self.assertEqual(key_a, key_b)


if __name__ == "__main__":
    unittest.main()
