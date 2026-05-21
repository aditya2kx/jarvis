#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.process_reviews.

Run:
    python3 agents/bhaga/scripts/test_process_reviews.py

Covers Layer B's read-side defense in process_reviews — the
``_resolve_data_window_end`` helper must accept ISO,
apostrophe-prefixed, or Sheets-serial values (silently coerced) and
must raise a clear, operator-actionable error on truly bad junk.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.process_reviews import _resolve_data_window_end


class ResolveDataWindowEndTests(unittest.TestCase):
    def test_iso_passes_through(self):
        d = _resolve_data_window_end({"data_window_end": "2026-05-20"})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_apostrophe_prefixed_stripped(self):
        # Layer A's own output round-trips cleanly through the helper.
        d = _resolve_data_window_end({"data_window_end": "'2026-05-20"})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_serial_silently_recovered(self):
        # 46162 == 2026-05-20 in Sheets serial. Layer B promises silent
        # recovery on this branch.
        d = _resolve_data_window_end({"data_window_end": "46162"})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_whitespace_tolerated(self):
        d = _resolve_data_window_end({"data_window_end": "  2026-05-20  "})
        self.assertEqual(d, datetime.date(2026, 5, 20))

    def test_garbage_raises_clear_error(self):
        with self.assertRaises(RuntimeError) as cm:
            _resolve_data_window_end({"data_window_end": "banana"})
        # The literal bad cell value MUST appear in the error so the
        # operator can grep for it in the sheet.
        self.assertIn("banana", str(cm.exception))

    def test_missing_key_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            _resolve_data_window_end({})
        self.assertIn("data_window_end", str(cm.exception))

    def test_empty_value_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            _resolve_data_window_end({"data_window_end": ""})
        self.assertIn("data_window_end", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
