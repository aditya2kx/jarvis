#!/usr/bin/env python3
"""Unit tests for skills.bhaga_config.dates.

Run:
    python3 skills/bhaga_config/test_dates.py

Covers Layer B (read side) of the seamless_bhaga_refresh fix: the
coerce_iso_date() helper must accept ISO, apostrophe-prefixed,
Sheets-serial integers/floats, and edge cases, and must reject all
junk shapes by returning None.

Also covers the Layer A write helper _iso_date_for_sheet_cell()
idempotency contract — no double-prefix, no crash on None.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.bhaga_config.dates import (
    SHEETS_DATE_EPOCH,
    _iso_date_for_sheet_cell,
    coerce_iso_date,
)


class CoerceIsoDateTests(unittest.TestCase):
    # ── Happy paths ─────────────────────────────────────────────

    def test_plain_iso(self):
        self.assertEqual(coerce_iso_date("2026-05-20"), "2026-05-20")

    def test_apostrophe_prefixed_iso(self):
        # Layer A's own output round-trips cleanly.
        self.assertEqual(coerce_iso_date("'2026-05-20"), "2026-05-20")

    def test_sheets_serial_string(self):
        # 46162 - 25569 = 20593 days from 1970-01-01 = 2026-05-20.
        self.assertEqual(coerce_iso_date("46162"), "2026-05-20")

    def test_sheets_serial_int(self):
        # Some API paths return numeric not stringly.
        self.assertEqual(coerce_iso_date(46162), "2026-05-20")

    def test_sheets_serial_float_string(self):
        self.assertEqual(coerce_iso_date("46162.0"), "2026-05-20")

    def test_sheets_serial_float(self):
        self.assertEqual(coerce_iso_date(46162.0), "2026-05-20")

    def test_whitespace_tolerated(self):
        self.assertEqual(coerce_iso_date(" 2026-05-20 "), "2026-05-20")

    def test_apostrophe_plus_whitespace(self):
        self.assertEqual(coerce_iso_date("  '  2026-05-20  "), "2026-05-20")

    def test_date_object(self):
        self.assertEqual(coerce_iso_date(datetime.date(2026, 5, 20)), "2026-05-20")

    # ── Empty / missing ─────────────────────────────────────────

    def test_empty_string(self):
        self.assertIsNone(coerce_iso_date(""))

    def test_none(self):
        self.assertIsNone(coerce_iso_date(None))

    def test_whitespace_only(self):
        self.assertIsNone(coerce_iso_date("   "))

    # ── Junk / unparseable ──────────────────────────────────────

    def test_banana(self):
        self.assertIsNone(coerce_iso_date("banana"))

    def test_serial_below_floor(self):
        # ~year 1909 — almost certainly drift junk, not a real config cell.
        self.assertIsNone(coerce_iso_date("1"))

    def test_serial_above_ceiling(self):
        # ~year 2173 — out of any realistic store-data range.
        self.assertIsNone(coerce_iso_date("100000"))

    def test_negative_serial(self):
        self.assertIsNone(coerce_iso_date("-46162"))

    def test_trailing_garbage(self):
        self.assertIsNone(coerce_iso_date("46162x"))

    def test_invalid_iso(self):
        # Invalid month/day.
        self.assertIsNone(coerce_iso_date("2026-13-99"))

    def test_partial_iso(self):
        self.assertIsNone(coerce_iso_date("2026-05"))

    # ── Boundary tests ──────────────────────────────────────────

    def test_serial_just_below_floor(self):
        # 40000 → year ~1909, rejected.
        self.assertIsNone(coerce_iso_date("40000"))

    def test_serial_at_floor(self):
        # 40001 → just inside the sanity floor.
        result = coerce_iso_date("40001")
        self.assertIsNotNone(result)
        # Verify it's parseable as a date.
        parsed = datetime.date.fromisoformat(result)
        expected = SHEETS_DATE_EPOCH + datetime.timedelta(days=40001)
        self.assertEqual(parsed, expected)

    def test_serial_just_above_ceiling(self):
        # 80000 → ~year 2119, rejected.
        self.assertIsNone(coerce_iso_date("80000"))


class IsoDateForSheetCellTests(unittest.TestCase):
    def test_iso_string(self):
        self.assertEqual(_iso_date_for_sheet_cell("2026-05-20"), "'2026-05-20")

    def test_date_object(self):
        self.assertEqual(
            _iso_date_for_sheet_cell(datetime.date(2026, 5, 20)),
            "'2026-05-20",
        )

    def test_none(self):
        self.assertEqual(_iso_date_for_sheet_cell(None), "")

    def test_empty_string(self):
        self.assertEqual(_iso_date_for_sheet_cell(""), "")

    def test_whitespace_only(self):
        self.assertEqual(_iso_date_for_sheet_cell("   "), "")

    def test_idempotent_already_prefixed(self):
        # Critical: we must not double-prefix.
        self.assertEqual(_iso_date_for_sheet_cell("'2026-05-20"), "'2026-05-20")

    def test_idempotent_round_trip_through_coerce(self):
        # Round-trip: ISO → write-cell → coerce-back → ISO.
        wrote = _iso_date_for_sheet_cell("2026-05-20")
        self.assertEqual(coerce_iso_date(wrote), "2026-05-20")

    def test_whitespace_stripped(self):
        self.assertEqual(_iso_date_for_sheet_cell("  2026-05-20  "), "'2026-05-20")

    def test_serial_drift_normalized_on_write(self):
        # CRITICAL regression: a read-back serial from a pre-fix corrupt
        # cell (e.g. review_bonus_started_date = "46153") must NOT be
        # written back as the literal text "'46153" — it must normalize
        # to canonical ISO first, otherwise Layer A would persist the
        # drift in text form and defeat its own purpose.
        self.assertEqual(_iso_date_for_sheet_cell("46153"), "'2026-05-11")
        self.assertEqual(_iso_date_for_sheet_cell(46162), "'2026-05-20")


if __name__ == "__main__":
    unittest.main()
