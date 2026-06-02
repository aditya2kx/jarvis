#!/usr/bin/env python3
"""Tests for skills/tip_ledger_writer/writer additive header auto-migration.

These exercise the live `_upsert_tab` against an in-memory fake of the Sheets
API layer (the same surface the real writer calls: `_read_tab`, `_write_range`,
`_clear_range`, `_add_sheet_if_missing`, `refresh_access_token`). No network,
no OTP, no browser.

Coverage:
    * additive append (1 and multiple new cols) -> header widened, existing rows
      padded blank, no raise, migrate log emitted.
    * exact match -> no migration, no raise, no migrate log.
    * rename in place -> raises drift.
    * reorder -> raises drift.
    * removed column (expected shorter than live) -> raises.
    * trailing sidecar note column preserved across an additive widen.
"""

import logging
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.tip_ledger_writer import writer
from skills.tip_ledger_writer.schema import get_tab_spec


def _parse_cell(ref: str) -> tuple[int, int]:
    """'A1' -> (row=1, col=1); 'N15' -> (15, 14). 1-indexed."""
    m = re.match(r"([A-Z]+)(\d+)", ref)
    col_letters, row = m.group(1), int(m.group(2))
    col = 0
    for ch in col_letters:
        col = col * 26 + (ord(ch) - 64)
    return row, col


class FakeSheets:
    """In-memory stand-in for the Google Sheets value API used by writer.py."""

    def __init__(self):
        self.tabs: dict[str, list[list]] = {}

    # mirrors writer._read_tab
    def read_tab(self, spreadsheet_id, tab, token):
        return [list(r) for r in self.tabs.get(tab, [])]

    def _ensure(self, tab, row, col):
        grid = self.tabs.setdefault(tab, [])
        while len(grid) < row:
            grid.append([])
        r = grid[row - 1]
        while len(r) < col:
            r.append("")

    # mirrors writer._write_range
    def write_range(self, spreadsheet_id, range_a1, values, token, value_input_option="RAW"):
        tab, rng = range_a1.split("!")
        r0, c0 = _parse_cell(rng.split(":")[0])
        for dr, rowvals in enumerate(values):
            for dc, val in enumerate(rowvals):
                self._ensure(tab, r0 + dr, c0 + dc)
                self.tabs[tab][r0 + dr - 1][c0 + dc - 1] = val
        return {}

    # mirrors writer._clear_range
    def clear_range(self, spreadsheet_id, range_a1, token):
        tab, rng = range_a1.split("!")
        parts = rng.split(":")
        r0, c0 = _parse_cell(parts[0])
        r1, c1 = _parse_cell(parts[1]) if len(parts) > 1 else (r0, c0)
        grid = self.tabs.get(tab, [])
        for rr in range(r0, r1 + 1):
            if rr - 1 < len(grid):
                row = grid[rr - 1]
                for cc in range(c0, c1 + 1):
                    if cc - 1 < len(row):
                        row[cc - 1] = ""
        return {}

    # mirrors writer._add_sheet_if_missing
    def add_sheet(self, spreadsheet_id, token, tab):
        self.tabs.setdefault(tab, [])


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class AdditiveMigrationTest(unittest.TestCase):
    WORKBOOK = "BHAGA Square Raw"

    def setUp(self):
        self.fake = FakeSheets()
        self.sid = "staging-fake-sheet-id"
        patchers = [
            mock.patch.object(writer, "_read_tab", self.fake.read_tab),
            mock.patch.object(writer, "_write_range", self.fake.write_range),
            mock.patch.object(writer, "_clear_range", self.fake.clear_range),
            mock.patch.object(writer, "_add_sheet_if_missing", self.fake.add_sheet),
            mock.patch.object(writer, "refresh_access_token", lambda *a, **k: "tok"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.log_capture = _LogCapture()
        writer.log.addHandler(self.log_capture)
        _prev_level = writer.log.level
        writer.log.setLevel(logging.INFO)
        self.addCleanup(lambda: writer.log.setLevel(_prev_level))
        self.addCleanup(lambda: writer.log.removeHandler(self.log_capture))

    def _seed(self, tab, header_row, data_rows):
        self.fake.tabs[tab] = [list(header_row)] + [list(r) for r in data_rows]

    def _migrate_lines(self):
        return [m for m in self.log_capture.messages if m.startswith("[schema_migrate]")]

    # ── additive: single new column ───────────────────────────────
    def test_additive_append_single_column(self):
        tab = "daily_rollup"
        full = get_tab_spec(self.WORKBOOK, tab)["header"]  # 7 cols (last: scraped_at_utc)
        old = full[:-1]  # 6 cols — schema appended scraped_at_utc
        self._seed(tab, old, [
            ["2026-05-01", "10", "1000", "100", "900", "0"],
            ["2026-05-02", "5", "500", "50", "450", "0"],
        ])

        summary = writer.write_raw_square_daily_rollup(
            self.sid,
            [{"date_local": "2026-05-03", "txn_count": 7, "gross_sales_cents": 700,
              "tip_cents": 70, "net_sales_cents": 630, "refund_cents": 0}],
            scraped_at_utc="2026-05-29T00:00:00Z",
        )

        grid = self.fake.tabs[tab]
        self.assertEqual(grid[0][:len(full)], full, "header should be widened to full schema")
        # existing untouched row preserved + padded blank for new column
        row_d2 = next(r for r in grid[1:] if r and r[0] == "2026-05-02")
        self.assertEqual(len(row_d2), len(full))
        self.assertEqual(row_d2[len(full) - 1], "", "new column blank for preserved row")
        # new row carries scraped_at stamp in the appended column
        row_d3 = next(r for r in grid[1:] if r and r[0] == "2026-05-03")
        self.assertEqual(row_d3[len(full) - 1], "2026-05-29T00:00:00Z")
        # migrate log emitted with the new column
        lines = self._migrate_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("scraped_at_utc", lines[0])
        self.assertIn("2 existing rows widened", lines[0])
        self.assertEqual(summary["inserted"], 1)

    # ── additive: multiple new columns ────────────────────────────
    def test_additive_append_multiple_columns(self):
        tab = "transactions"
        full = get_tab_spec(self.WORKBOOK, tab)["header"]  # 19 cols
        old = full[:-3]  # schema appended 3 columns
        self._seed(tab, old, [
            ["t1"] + ["x"] * (len(old) - 1),
            ["t2"] + ["y"] * (len(old) - 1),
        ])

        writer.write_raw_square_transactions(
            self.sid,
            [{"transaction_id": "t3", "event_type": "sale"}],
            scraped_at_utc="2026-05-29T00:00:00Z",
        )

        grid = self.fake.tabs[tab]
        self.assertEqual(grid[0][:len(full)], full)
        row_t1 = next(r for r in grid[1:] if r and r[0] == "t1")
        self.assertEqual(len(row_t1), len(full))
        # the 3 appended columns are blank for the preserved row
        self.assertEqual(row_t1[len(old):len(full)], ["", "", ""])
        lines = self._migrate_lines()
        self.assertEqual(len(lines), 1)
        for c in full[-3:]:
            self.assertIn(c, lines[0])

    # ── exact match: no migration ─────────────────────────────────
    def test_exact_match_no_migration(self):
        tab = "daily_rollup"
        full = get_tab_spec(self.WORKBOOK, tab)["header"]
        self._seed(tab, full, [
            ["2026-05-01", "10", "1000", "100", "900", "0", "2026-05-01T00:00:00Z"],
        ])
        writer.write_raw_square_daily_rollup(
            self.sid,
            [{"date_local": "2026-05-02", "txn_count": 5, "gross_sales_cents": 500,
              "tip_cents": 50, "net_sales_cents": 450, "refund_cents": 0}],
            scraped_at_utc="2026-05-29T00:00:00Z",
        )
        self.assertEqual(self._migrate_lines(), [], "no migration expected on exact match")
        self.assertEqual(self.fake.tabs[tab][0], full)

    # ── destructive: rename in place ──────────────────────────────
    def test_rename_in_place_raises(self):
        tab = "daily_rollup"
        full = get_tab_spec(self.WORKBOOK, tab)["header"]
        renamed = list(full)
        renamed[2] = "gross_sales_CENTS_RENAMED"
        self._seed(tab, renamed, [])
        with self.assertRaises(ValueError) as ctx:
            writer.write_raw_square_daily_rollup(
                self.sid, [{"date_local": "2026-05-02"}],
                scraped_at_utc="2026-05-29T00:00:00Z",
            )
        self.assertIn("Header drift", str(ctx.exception))
        self.assertEqual(self._migrate_lines(), [])

    # ── destructive: reorder ──────────────────────────────────────
    def test_reorder_raises(self):
        tab = "daily_rollup"
        full = get_tab_spec(self.WORKBOOK, tab)["header"]
        reordered = list(full)
        reordered[1], reordered[2] = reordered[2], reordered[1]
        self._seed(tab, reordered, [])
        with self.assertRaises(ValueError) as ctx:
            writer.write_raw_square_daily_rollup(
                self.sid, [{"date_local": "2026-05-02"}],
                scraped_at_utc="2026-05-29T00:00:00Z",
            )
        self.assertIn("Header drift", str(ctx.exception))

    # ── destructive: removed column (live longer than expected) ───
    def test_removed_column_raises(self):
        tab = "daily_rollup"
        full = get_tab_spec(self.WORKBOOK, tab)["header"]
        # Live data header is the schema PLUS a legacy column contiguously
        # appended (no blank gap) -> expected is a strict prefix of live, which
        # means a column was dropped from the schema. Must raise.
        live = list(full) + ["legacy_extra_col"]
        self._seed(tab, live, [])
        with self.assertRaises(ValueError) as ctx:
            writer.write_raw_square_daily_rollup(
                self.sid, [{"date_local": "2026-05-02"}],
                scraped_at_utc="2026-05-29T00:00:00Z",
            )
        self.assertIn("Header drift", str(ctx.exception))
        self.assertEqual(self._migrate_lines(), [])

    # ── sidecar note preserved across an additive widen ───────────
    def test_sidecar_note_preserved_across_widen(self):
        tab = "daily_rollup"
        full = get_tab_spec(self.WORKBOOK, tab)["header"]  # 7 cols
        old = full[:-1]  # 6 cols
        note = "operator note: do not edit by hand"
        # Bootstrap convention: blank gap then note ~2 cols past the data header.
        header_row = list(old) + ["", note]
        self._seed(tab, header_row, [
            ["2026-05-01", "10", "1000", "100", "900", "0"],
        ])

        writer.write_raw_square_daily_rollup(
            self.sid,
            [{"date_local": "2026-05-02", "txn_count": 5, "gross_sales_cents": 500,
              "tip_cents": 50, "net_sales_cents": 450, "refund_cents": 0}],
            scraped_at_utc="2026-05-29T00:00:00Z",
        )

        row1 = self.fake.tabs[tab][0]
        self.assertEqual(row1[:len(full)], full, "header widened to full schema")
        # note re-placed one blank column past the widened header (len+2)
        self.assertEqual(row1[len(full)], "", "blank gap between header and note")
        self.assertEqual(row1[len(full) + 1], note, "operator note preserved")
        self.assertEqual(len(self._migrate_lines()), 1)


class _FakeResp:
    def __init__(self, body: str):
        self._b = body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _http_error(code: int, body: str):
    import io
    import urllib.error
    return urllib.error.HTTPError(
        "https://sheets.googleapis.com/x", code, "err", {}, io.BytesIO(body.encode())
    )


class ApiRetryTest(unittest.TestCase):
    """writer._api routes through the shared core.sheets_retry backoff so the
    laptop/non-cloud prod path gets the same 429 resilience as the cloud job."""

    def setUp(self):
        from core import sheets_retry
        self.sheets_retry = sheets_retry
        p = mock.patch.object(sheets_retry.time, "sleep", lambda *_a: None)
        p.start()
        self.addCleanup(p.stop)

    def test_retries_429_then_succeeds(self):
        seq = [
            _http_error(429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}'),
            _FakeResp('{"values": [["a"]]}'),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=seq) as m:
            out = writer._api("https://sheets.googleapis.com/v4/x", "tok")
        self.assertEqual(out, {"values": [["a"]]})
        self.assertEqual(m.call_count, 2)

    def test_gives_up_after_max_attempts(self):
        n = self.sheets_retry.DEFAULT_MAX_ATTEMPTS
        seq = [_http_error(429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}') for _ in range(n)]
        with mock.patch("urllib.request.urlopen", side_effect=seq) as m:
            with self.assertRaises(RuntimeError) as ctx:
                writer._api("https://sheets.googleapis.com/v4/x", "tok")
        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertEqual(m.call_count, n)

    def test_400_raises_without_retry(self):
        seq = [_http_error(400, '{"error": {"code": 400}}')]
        with mock.patch("urllib.request.urlopen", side_effect=seq) as m:
            with self.assertRaises(RuntimeError) as ctx:
                writer._api("https://sheets.googleapis.com/v4/x", "tok")
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertEqual(m.call_count, 1)


class TrainingShiftsWriteTest(unittest.TestCase):
    """write_training_shifts: create-if-missing + idempotent (employee,date) upsert
    for the human-owned per-shift training overlay tab."""

    HEADER = ["employee_name", "date", "note"]

    def setUp(self):
        self.fake = FakeSheets()
        self.sid = "staging-fake-sheet-id"
        for p in [
            mock.patch.object(writer, "_read_tab", self.fake.read_tab),
            mock.patch.object(writer, "_write_range", self.fake.write_range),
            mock.patch.object(writer, "_clear_range", self.fake.clear_range),
            mock.patch.object(writer, "_add_sheet_if_missing", self.fake.add_sheet),
            mock.patch.object(writer, "refresh_access_token", lambda *a, **k: "tok"),
        ]:
            p.start()
            self.addCleanup(p.stop)

    def test_creates_tab_and_writes_header_and_sorted_rows(self):
        res = writer.write_training_shifts(self.sid, [
            {"employee_name": "Ortiz, Ximena", "date": "2026-05-31", "note": "training"},
            {"employee_name": "Flores, Juan", "date": "2026-05-18", "note": "training"},
        ])
        grid = self.fake.tabs["training_shifts"]
        self.assertEqual(grid[0], self.HEADER)
        # sorted by (name, date): Flores before Ortiz
        self.assertEqual([r[:2] for r in grid[1:]],
                         [["Flores, Juan", "2026-05-18"], ["Ortiz, Ximena", "2026-05-31"]])
        self.assertEqual((res["inserted"], res["updated"], res["total_after"]), (2, 0, 2))

    def test_idempotent_upsert_preserves_other_operator_rows(self):
        # Operator already has a hand-added row for a different (emp, date).
        self.fake.tabs["training_shifts"] = [
            list(self.HEADER),
            ["Steele, Isabel", "2026-06-03", "hand-added"],
            ["Ortiz, Ximena", "2026-05-31", "old note"],
        ]
        res = writer.write_training_shifts(self.sid, [
            {"employee_name": "Ortiz, Ximena", "date": "2026-05-31", "note": "new note"},  # update
            {"employee_name": "Ortiz, Ximena", "date": "2026-05-29", "note": "training"},  # insert
        ])
        rows = {(r[0], r[1]): r[2] for r in self.fake.tabs["training_shifts"][1:] if r and r[0]}
        self.assertEqual(rows[("Steele, Isabel", "2026-06-03")], "hand-added")  # preserved
        self.assertEqual(rows[("Ortiz, Ximena", "2026-05-31")], "new note")     # updated in place
        self.assertIn(("Ortiz, Ximena", "2026-05-29"), rows)                    # inserted
        self.assertEqual((res["inserted"], res["updated"]), (1, 1))

    def test_collapses_duplicate_rows_and_clears_trailing(self):
        # Sheet somehow has a stray duplicate (e.g. operator hand-edit). The
        # re-sorted write collapses to one row; the trailing stale row is cleared.
        self.fake.tabs["training_shifts"] = [
            list(self.HEADER),
            ["Ortiz, Ximena", "2026-05-31", "training"],
            ["Ortiz, Ximena", "2026-05-31", "dup"],
        ]
        writer.write_training_shifts(self.sid, [
            {"employee_name": "Ortiz, Ximena", "date": "2026-05-31", "note": "training"},
        ])
        grid = self.fake.tabs["training_shifts"]
        live = [r for r in grid[1:] if r and str(r[0]).strip()]
        self.assertEqual(len(live), 1, "duplicate collapsed to a single row")
        # row 3 (former duplicate) cleared to blanks
        self.assertEqual([c for c in grid[2]], ["", "", ""])

    def test_missing_name_or_date_raises(self):
        with self.assertRaises(ValueError):
            writer.write_training_shifts(self.sid, [{"employee_name": "X, Y"}])
        with self.assertRaises(ValueError):
            writer.write_training_shifts(self.sid, [{"date": "2026-05-18"}])


if __name__ == "__main__":
    unittest.main()
