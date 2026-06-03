#!/usr/bin/env python3
"""Tests for pr_cost_ledger."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pr_cost_ledger as L


class TestPrCostLedger(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = L.LEDGER_DIR
        L.LEDGER_DIR = Path(self._tmpdir.name)

    def tearDown(self):
        L.LEDGER_DIR = self._orig_dir
        self._tmpdir.cleanup()

    def test_validate_happy_path(self):
        L.set_meta(1, title="t", requirement="r")
        L.record_build_session(1, ts="2026-01-01T00:00:00Z", tokens=100, cost_usd=0.5, model="m")
        ok, problems = L.validate(1, require_build=True)
        self.assertTrue(ok)
        self.assertEqual(problems, [])

    def test_validate_no_file(self):
        ok, problems = L.validate(99)
        self.assertFalse(ok)
        self.assertTrue(any("no cost record" in p for p in problems))

    def test_validate_no_requirement_or_title(self):
        rec = L._empty_record(2)
        L.save_record(rec)
        ok, problems = L.validate(2)
        self.assertFalse(ok)
        self.assertTrue(any("requirement/title" in p for p in problems))

    def test_validate_no_cost_surfaces(self):
        L.set_meta(3, title="only title")
        ok, problems = L.validate(3)
        self.assertFalse(ok)
        self.assertTrue(any("unaccounted" in p for p in problems))

    def test_validate_require_build_missing(self):
        L.set_meta(4, title="t")
        L.record_review_run(
            4, ts="x", model="claude-sonnet-4-6", turns=1,
            input_tokens=1, output_tokens=1, cache_read=0, cache_write=0,
            cost_usd=0.1, result="success", run_url="u",
        )
        ok, problems = L.validate(4, require_build=True)
        self.assertFalse(ok)
        self.assertTrue(any("no build sessions" in p for p in problems))

    def test_capture_build_rejects_partial_window(self):
        L.set_meta(10, title="t", branch="feat/x")
        with self.assertRaises(SystemExit) as ctx:
            L.capture_build(10, start="2026-06-01T00:00:00Z", end=None)
        self.assertIn("together", str(ctx.exception))

    def test_record_review_run_round_trip(self):
        L.record_review_run(
            5, ts="2026-06-01T12:00:00Z", model="claude-sonnet-4-6", turns=7,
            input_tokens=10, output_tokens=100, cache_read=1000, cache_write=200,
            cost_usd=0.55, result="success", run_url="https://x/r/1",
        )
        rec = L.load_record(5)
        self.assertEqual(rec["review"]["run_count"], 1)
        run = rec["review"]["runs"][0]
        self.assertEqual(run["tokens"], 1310)
        self.assertEqual(run["cost_usd"], 0.55)
        self.assertEqual(rec["totals"]["cost_usd"], 0.55)

    def test_record_review_run_dedup_by_run_url(self):
        kw = dict(
            ts="2026-06-01T12:00:00Z", model="claude-sonnet-4-6", turns=7,
            input_tokens=10, output_tokens=100, cache_read=0, cache_write=0,
            cost_usd=0.55, result="success", run_url="https://x/r/1",
        )
        L.record_review_run(6, **kw)
        L.record_review_run(6, **kw)
        self.assertEqual(L.load_record(6)["review"]["run_count"], 1)

    def test_record_review_run_dedup_by_ts_when_no_run_url(self):
        kw = dict(
            ts="2026-06-01T12:00:00Z", model="claude-sonnet-4-6", turns=7,
            input_tokens=10, output_tokens=100, cache_read=0, cache_write=0,
            cost_usd=0.55, result="success", run_url=None,
        )
        L.record_review_run(7, **kw)
        L.record_review_run(7, **kw)
        self.assertEqual(L.load_record(7)["review"]["run_count"], 1)

    def test_recompute_totals_build_and_review(self):
        L.record_build_session(8, ts="a", tokens=1000, cost_usd=1.0, model="opus")
        L.record_build_session(8, ts="b", tokens=500, cost_usd=0.5, model="opus")
        L.record_review_run(
            8, ts="c", model="sonnet", turns=1,
            input_tokens=1, output_tokens=1, cache_read=98, cache_write=0,
            cost_usd=0.25, result="success", run_url="u",
        )
        rec = L.load_record(8)
        self.assertEqual(rec["build"]["tokens_total"], 1500)
        self.assertAlmostEqual(rec["build"]["cost_usd_total"], 1.5)
        self.assertEqual(rec["review"]["tokens_total"], 100)
        self.assertAlmostEqual(rec["review"]["cost_usd_total"], 0.25)
        self.assertAlmostEqual(rec["totals"]["cost_usd"], 1.75)

    def test_recommendations_build_dominant_and_max_turns(self):
        rec = L._empty_record(9)
        rec["build"]["sessions"] = [
            {"ts": "a", "model": "claude-opus-4-8", "tokens": 44_000_000, "cost_usd": 30.0},
            {"ts": "b", "model": "claude-opus-4-8", "tokens": 1_000_000, "cost_usd": 2.0},
        ]
        rec["build"]["cost_usd_total"] = 32.0
        rec["review"]["runs"] = [
            {"result": "error_max_turns", "cost_usd": 0.5},
            {"result": "error_max_turns", "cost_usd": 0.5},
            {"result": "success", "cost_usd": 0.5},
        ]
        rec["review"]["run_count"] = 3
        rec["review"]["cost_usd_total"] = 1.5
        rec["totals"]["cost_usd"] = 33.5
        recs = L._recommendations(rec)
        joined = "\n".join(recs)
        self.assertIn("Build is 9", joined)  # build-dominant (>70%)
        self.assertIn("error_max_turns", joined)
        self.assertIn("3×", joined)


if __name__ == "__main__":
    unittest.main()
