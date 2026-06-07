#!/usr/bin/env python3
"""Tests for backfill_from_downloads fresh-scrape --replace plumbing.

A fresh full-history scrape must TRUNCATE-then-load each BQ raw table (replace
mode) rather than MERGE-upsert, both because the scrape owns the whole window
and because a single scrape batch can legitimately carry duplicate natural keys
(e.g. ADP earnings line-items) that would trip the MERGE one-source-row rule.
The module-level load_rows wrapper injects replace=True when _REPLACE_TABLES is
set (from --replace / BHAGA_RAW_REPLACE).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import backfill_from_downloads as bfd


class TestReplaceWrapper(unittest.TestCase):
    def tearDown(self):
        bfd._REPLACE_TABLES = False

    def test_wrapper_injects_replace_when_enabled(self):
        captured = {}
        with mock.patch.object(bfd, "_ds_load_rows",
                               side_effect=lambda *a, **k: captured.update(kwargs=k) or 1):
            bfd._REPLACE_TABLES = True
            bfd.load_rows("adp_earnings", [{"x": 1}], merge_keys=["x"])
        self.assertTrue(captured["kwargs"].get("replace"))

    def test_wrapper_leaves_merge_when_disabled(self):
        captured = {}
        with mock.patch.object(bfd, "_ds_load_rows",
                               side_effect=lambda *a, **k: captured.update(kwargs=k) or 1):
            bfd._REPLACE_TABLES = False
            bfd.load_rows("adp_earnings", [{"x": 1}], merge_keys=["x"])
        self.assertNotIn("replace", captured["kwargs"])

    def test_wrapper_does_not_override_explicit_replace(self):
        captured = {}
        with mock.patch.object(bfd, "_ds_load_rows",
                               side_effect=lambda *a, **k: captured.update(kwargs=k) or 1):
            bfd._REPLACE_TABLES = True
            bfd.load_rows("adp_earnings", [{"x": 1}], replace=False)
        self.assertFalse(captured["kwargs"].get("replace"))


if __name__ == "__main__":
    unittest.main()
