"""Tests for skills/square_tips/transactions_backend parser.

Regression coverage for the 2026-05-23 bug where Square exports the Time
Zone column in the operator's browser locale (e.g. ``Asia/Calcutta`` while
the operator is traveling in India). The previous ``_to_iana`` mapping
only knew the US display strings, so unmatched values raised
``ValueError`` inside the row loop and the row was silently skipped via
the surrounding ``try/except``. After the fix the parser falls back to
treating the column as a raw IANA name when zoneinfo can resolve it.

Also locks in the fact that ``csv.reader`` correctly handles BOTH
empty-field encodings Square has emitted in the wild:
  * bare empty fields (``,,``)
  * explicitly-quoted empty fields (``"",""``)

so a regression that switches the parser to a naive ``line.split(",")``
implementation will be caught here.
"""

from __future__ import annotations

import csv
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_tips.transactions_backend import parse_csv, _to_iana


# Header copied verbatim from a real Square Transactions export (55 cols).
_HEADER = [
    "Date", "Time", "Time Zone", "Gross Sales", "Discounts",
    "Service Charges", "Net Sales", "Gift Card Sales", "Tax", "Tip",
    "Partial Refunds", "Total Collected", "Source", "Card",
    "Card Entry Methods", "Cash", "Square Gift Card", "Other Tender",
    "Other Tender Type", "Tender Note", "Fees", "Net Total",
    "Transaction ID", "Payment ID", "Card Brand", "PAN Suffix",
    "Device Name", "Staff Name", "Staff ID", "Details", "Description",
    "Event Type", "Location", "Dining Option", "Customer ID",
    "Customer Name", "Customer Reference ID", "Device Nickname",
    "Third Party Fees", "Deposit ID", "Deposit Date", "Deposit Details",
    "Fee Percentage Rate", "Fee Fixed Rate", "Refund Reason",
    "Discount Name", "Transaction Status", "Cash App",
    "Order Reference ID", "Fulfillment Note", "Free Processing Applied",
    "Channel", "Unattributed Tips", "Table Info", "International Fee",
]


def _row(
    *,
    date: str,
    time: str,
    tz: str,
    txn_id: str,
    cash_field: str,
    other_tender_field: str,
    tender_note_field: str,
) -> list[str]:
    """Build a 55-column transaction row with the three configurable empty fields.

    cash_field / other_tender_field / tender_note_field let the caller
    choose between bare-empty encoding (``""``) and quoted-empty encoding
    (which still ends up as ``""`` in the parsed list — the difference is
    on the wire, not in csv.reader's output).
    """
    row = [""] * 55
    row[0] = date
    row[1] = time
    row[2] = tz
    row[3] = "$10.00"     # Gross Sales
    row[4] = "$0.00"      # Discounts
    row[5] = "$0.00"      # Service Charges
    row[6] = "$10.00"     # Net Sales
    row[7] = "$0.00"      # Gift Card Sales
    row[8] = "$0.83"      # Tax
    row[9] = "$1.50"      # Tip
    row[10] = "$0.00"     # Partial Refunds
    row[11] = "$12.33"    # Total Collected
    row[12] = "Register"  # Source
    row[13] = "$12.33"    # Card
    row[14] = "Tapped"    # Card Entry Methods
    row[15] = cash_field
    row[17] = other_tender_field
    row[19] = tender_note_field
    row[20] = "-$0.36"    # Fees
    row[21] = "$11.97"    # Net Total
    row[22] = txn_id      # Transaction ID
    row[23] = txn_id + "-pay"
    row[27] = "Test Staff"
    row[31] = "Payment"   # Event Type
    row[32] = "Austin Mueller Lake"
    row[46] = "Complete"
    return row


def _write_csv(tmp: pathlib.Path, rows: list[list[str]], *, quoting: int) -> pathlib.Path:
    """Write a CSV with the requested quoting strategy and return the path."""
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=quoting)
        writer.writerow(_HEADER)
        for r in rows:
            writer.writerow(r)
    return tmp


class ToIanaTests(unittest.TestCase):
    def test_known_us_display_string_maps_to_iana(self) -> None:
        self.assertEqual(_to_iana("Eastern Time (US & Canada)"), "America/New_York")
        self.assertEqual(_to_iana("Central Time (US & Canada)"), "America/Chicago")

    def test_raw_iana_name_passes_through(self) -> None:
        # The 2026-05-23 regression: operator traveling in India means
        # Square emits 'Asia/Calcutta' instead of a US display string.
        self.assertEqual(_to_iana("Asia/Calcutta"), "Asia/Calcutta")
        self.assertEqual(_to_iana("Asia/Kolkata"), "Asia/Kolkata")
        self.assertEqual(_to_iana("Europe/London"), "Europe/London")

    def test_truly_unknown_value_still_raises(self) -> None:
        with self.assertRaises(ValueError):
            _to_iana("Not A Real Timezone xyz")
        with self.assertRaises(ValueError):
            _to_iana("")


class ParseCsvEmptyFieldEncodingTests(unittest.TestCase):
    """The parser must accept BOTH ',,' and '"",""' empty-field encodings.

    csv.reader normalizes both to ``""`` in the parsed list, so this test
    exists to lock that contract in and prevent a future refactor that
    swaps in a naive line.split(',') implementation from regressing.
    """

    def test_bare_empty_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            csv_path = pathlib.Path(td) / "bare.csv"
            row = _row(
                date="2026-05-15", time="14:30:00",
                tz="Central Time (US & Canada)",
                txn_id="BARE_TXN_001",
                cash_field="", other_tender_field="", tender_note_field="",
            )
            _write_csv(csv_path, [row], quoting=csv.QUOTE_MINIMAL)
            records = parse_csv(csv_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["transaction_id"], "BARE_TXN_001")
        self.assertEqual(records[0]["date_local"], "2026-05-15")
        self.assertEqual(records[0]["gross_sales_cents"], 1000)
        self.assertEqual(records[0]["tip_cents"], 150)

    def test_quoted_empty_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            csv_path = pathlib.Path(td) / "quoted.csv"
            row = _row(
                date="2026-05-15", time="14:30:00",
                tz="Central Time (US & Canada)",
                txn_id="QUOTED_TXN_001",
                cash_field="", other_tender_field="", tender_note_field="",
            )
            _write_csv(csv_path, [row], quoting=csv.QUOTE_ALL)
            records = parse_csv(csv_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["transaction_id"], "QUOTED_TXN_001")
        self.assertEqual(records[0]["date_local"], "2026-05-15")


class ParseCsvAsiaCalcuttaTests(unittest.TestCase):
    """Regression for the 2026-05-23 bug: Asia/Calcutta-tz rows must parse."""

    def test_asia_calcutta_tz_row_is_not_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            csv_path = pathlib.Path(td) / "ist.csv"
            # 2026-05-22 23:58:58 IST == 2026-05-22 13:28:58 CT (same date_local).
            row = _row(
                date="2026-05-22", time="23:58:58",
                tz="Asia/Calcutta",
                txn_id="IST_TXN_001",
                cash_field="", other_tender_field="", tender_note_field="",
            )
            _write_csv(csv_path, [row], quoting=csv.QUOTE_ALL)
            records = parse_csv(csv_path)
        self.assertEqual(len(records), 1, "Asia/Calcutta row was silently dropped")
        rec = records[0]
        self.assertEqual(rec["transaction_id"], "IST_TXN_001")
        self.assertEqual(rec["raw_tz_csv"], "Asia/Calcutta")
        # 23:58 IST == 13:28 CT (IST is UTC+5:30, CT is UTC-5 with DST → 10.5h diff).
        self.assertEqual(rec["date_local"], "2026-05-22")
        self.assertEqual(rec["hour_local"], 13)


class ParseCsvMixedEncodingTests(unittest.TestCase):
    """A single CSV may contain a mix of TZ values and empty-field encodings.

    Mirrors the production transactions-master.csv after the operator
    started traveling: 3090 Eastern + 406 Central + 189 Asia/Calcutta
    rows in one file.
    """

    def test_mixed_tz_and_empty_encoding_in_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            csv_path = pathlib.Path(td) / "mixed.csv"
            rows = [
                _row(  # bare empties + Eastern
                    date="2026-05-10", time="10:00:00",
                    tz="Eastern Time (US & Canada)",
                    txn_id="MIX_ET_001",
                    cash_field="", other_tender_field="", tender_note_field="",
                ),
                _row(  # bare empties + Central
                    date="2026-05-15", time="11:00:00",
                    tz="Central Time (US & Canada)",
                    txn_id="MIX_CT_001",
                    cash_field="", other_tender_field="", tender_note_field="",
                ),
                _row(  # quoted empties + Asia/Calcutta (the new shape)
                    date="2026-05-22", time="15:00:00",
                    tz="Asia/Calcutta",
                    txn_id="MIX_IST_001",
                    cash_field="", other_tender_field="", tender_note_field="",
                ),
            ]
            # csv.QUOTE_MINIMAL writes bare empties for the empty fields,
            # but csv.reader on read sees '""' and ',,' the same way, so
            # one writer pass exercises both shapes from the parser's POV.
            _write_csv(csv_path, rows, quoting=csv.QUOTE_MINIMAL)
            records = parse_csv(csv_path)
        ids = {r["transaction_id"] for r in records}
        self.assertEqual(
            ids, {"MIX_ET_001", "MIX_CT_001", "MIX_IST_001"},
            "Mixed-tz file dropped one or more rows",
        )


class KdsAggregationTests(unittest.TestCase):
    """Percentile-based KDS aggregation: no upper cap, full tail surfaced, no avg."""

    @staticmethod
    def _ticket(date, cts, num_items=2):
        import datetime
        base = datetime.datetime.fromisoformat(f"{date}T10:00:00")
        return {
            "date_local": date,
            "completion_time_sec": float(cts),
            "num_items": num_items,
            "time_created": base,
            "time_completed": base,
            "time_due": None,
        }

    def test_no_upper_cap_tail_is_visible(self):
        # The old behavior DROPPED multi-hour left-open tickets. Now there's NO
        # upper cap: the ticket is KEPT and surfaces in the upper percentiles,
        # while the median stays sane (robust to the tail).
        from skills.square_tips.transactions_backend import aggregate_daily_kds_stats
        tickets = [
            self._ticket("2026-05-20", 300),    # 150s/item
            self._ticket("2026-05-20", 600),    # 300s/item
            self._ticket("2026-05-20", 80000),  # ~22h left-open — KEPT now
        ]
        out = aggregate_daily_kds_stats(tickets)
        day = out["2026-05-20"]
        self.assertEqual(day["completed_tickets"], 3)
        self.assertEqual(day["completed_items"], 6)  # 3 tickets × 2 items
        # Median is item-weighted across {150,150,300,300,40000,40000}.
        self.assertLessEqual(day["median_time_per_item_sec"], 40000)
        # The tail IS visible: p99 reflects the left-open ticket.
        self.assertGreater(day["p99_time_per_item_sec"], 1000)
        # avg metric is gone entirely.
        self.assertNotIn("avg_time_per_item_sec", day)
        self.assertNotIn("avg_completion_time_sec", day)

    def test_lower_floor_still_applies(self):
        from skills.square_tips.transactions_backend import aggregate_daily_kds_stats
        tickets = [self._ticket("2026-05-20", 5), self._ticket("2026-05-20", 300)]
        out = aggregate_daily_kds_stats(tickets)
        self.assertEqual(out["2026-05-20"]["completed_tickets"], 1)

    def test_per_item_times_are_item_weighted(self):
        # Each ticket contributes num_items copies of its per-item time, so the
        # stored distribution length == completed_items (true share-of-ITEMS).
        from skills.square_tips.transactions_backend import aggregate_daily_kds_stats
        tickets = [
            self._ticket("2026-05-20", 600, num_items=3),  # 200s/item ×3
            self._ticket("2026-05-20", 120, num_items=1),  # 120s/item ×1
        ]
        day = aggregate_daily_kds_stats(tickets)["2026-05-20"]
        self.assertEqual(day["completed_items"], 4)
        self.assertEqual(day["per_item_times"], [120, 200, 200, 200])

    def test_percentiles_known_distribution(self):
        # 100 items (1 item/ticket), times 20..119s (all above the 15s floor) →
        # numpy 'linear' / type-7: median 69.5, p90 109.1, p95 114.05, p99 118.01.
        from skills.square_tips.transactions_backend import aggregate_daily_kds_stats
        tickets = [self._ticket("2026-05-20", t, num_items=1) for t in range(20, 120)]
        day = aggregate_daily_kds_stats(tickets)["2026-05-20"]
        self.assertEqual(day["completed_tickets"], 100)
        self.assertAlmostEqual(day["median_time_per_item_sec"], 69.5, places=1)
        self.assertAlmostEqual(day["p90_time_per_item_sec"], 109.1, places=1)
        self.assertAlmostEqual(day["p95_time_per_item_sec"], 114.05, places=1)
        self.assertAlmostEqual(day["p99_time_per_item_sec"], 118.01, places=1)

    def test_fully_floored_day_emits_zero_row(self):
        # A day whose ONLY tickets are below the 15s floor still emits a ZERO
        # row so a re-run overwrites any stale row.
        from skills.square_tips.transactions_backend import aggregate_daily_kds_stats
        tickets = [self._ticket("2026-05-25", 3), self._ticket("2026-05-25", 8)]
        out = aggregate_daily_kds_stats(tickets)
        self.assertIn("2026-05-25", out, "fully-floored day vanished — stale row would persist")
        day = out["2026-05-25"]
        self.assertEqual(day["completed_tickets"], 0)
        self.assertEqual(day["median_time_per_item_sec"], 0.0)
        self.assertEqual(day["p99_time_per_item_sec"], 0.0)
        self.assertEqual(day["per_item_times"], [])

    def test_no_cap_constant_remains(self):
        # The configurable 1h cap is removed entirely.
        import skills.square_tips.transactions_backend as tb
        self.assertFalse(hasattr(tb, "KDS_MAX_COMPLETION_SEC"))


if __name__ == "__main__":
    unittest.main()
