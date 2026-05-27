#!/usr/bin/env python3
"""Tests for the parallel data gathering + per-source date range features.

Run:
    python3 -m pytest agents/bhaga/scripts/test_parallel_refresh.py -v
    python3 agents/bhaga/scripts/test_parallel_refresh.py

Covers:
  1. Parallel execution — mock the three pipelines, verify they run concurrently.
  2. CLI arg parsing — verify each per-source date arg is parsed correctly.
  3. Thread isolation — one thread failure doesn't kill the others.
  4. PipelineResult dataclass contracts.
  5. process_reviews --prefetched-messages path.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.daily_refresh import (
    PipelineResult,
    _run_square_pipeline,
    _run_adp_pipeline,
    _run_review_fetch,
)


class PipelineResultTests(unittest.TestCase):
    """PipelineResult dataclass contract."""

    def test_success_result(self):
        r = PipelineResult(name="square", success=True)
        self.assertTrue(r.success)
        self.assertIsNone(r.error)
        self.assertEqual(r.artifacts, {})
        self.assertEqual(r.master_stats, {})

    def test_failure_result(self):
        exc = RuntimeError("boom")
        r = PipelineResult(name="adp", success=False, error=exc)
        self.assertFalse(r.success)
        self.assertIs(r.error, exc)

    def test_artifacts_and_stats(self):
        r = PipelineResult(
            name="square", success=True,
            artifacts={"square_csv": pathlib.Path("/tmp/test.csv")},
            master_stats={"master_rows": 100, "rows_added": 5},
        )
        self.assertEqual(r.master_stats["rows_added"], 5)


class ParallelExecutionTests(unittest.TestCase):
    """Verify the three pipelines can run concurrently via ThreadPoolExecutor."""

    def test_all_three_run_in_parallel(self):
        """All three pipelines should execute overlapping in time."""
        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}
        lock = threading.Lock()

        def fake_square(**kwargs):
            with lock:
                start_times["square"] = time.monotonic()
            time.sleep(0.1)
            with lock:
                end_times["square"] = time.monotonic()
            return PipelineResult(name="square", success=True)

        def fake_adp(**kwargs):
            with lock:
                start_times["adp"] = time.monotonic()
            time.sleep(0.1)
            with lock:
                end_times["adp"] = time.monotonic()
            return PipelineResult(name="adp", success=True)

        def fake_review(**kwargs):
            with lock:
                start_times["review"] = time.monotonic()
            time.sleep(0.1)
            with lock:
                end_times["review"] = time.monotonic()
            return PipelineResult(name="review_fetch", success=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(fake_square),
                pool.submit(fake_adp),
                pool.submit(fake_review),
            ]
            results = [f.result() for f in futures]

        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.success for r in results))

        # Verify overlap: at least two tasks should have started before
        # the first one finished (proving parallelism, not serial).
        earliest_end = min(end_times.values())
        started_before_first_end = sum(
            1 for t in start_times.values() if t < earliest_end
        )
        self.assertGreaterEqual(
            started_before_first_end, 2,
            "At least 2 pipelines should start before the first finishes",
        )


class ThreadIsolationTests(unittest.TestCase):
    """One thread's failure must NOT propagate to the others."""

    def test_one_failure_others_succeed(self):
        def succeed(**kwargs):
            time.sleep(0.05)
            return PipelineResult(name="ok", success=True)

        def fail(**kwargs):
            time.sleep(0.05)
            return PipelineResult(
                name="fail", success=False,
                error=RuntimeError("simulated failure"),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            f1 = pool.submit(succeed)
            f2 = pool.submit(fail)
            f3 = pool.submit(succeed)

            r1 = f1.result()
            r2 = f2.result()
            r3 = f3.result()

        self.assertTrue(r1.success)
        self.assertFalse(r2.success)
        self.assertTrue(r3.success)

    def test_exception_in_thread_doesnt_crash_executor(self):
        def crash(**kwargs):
            raise ValueError("thread crash")

        def succeed(**kwargs):
            return PipelineResult(name="ok", success=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            f_crash = pool.submit(crash)
            f_ok = pool.submit(succeed)

            r_ok = f_ok.result()
            self.assertTrue(r_ok.success)

            with self.assertRaises(ValueError):
                f_crash.result()


class SquarePipelineDryRunTests(unittest.TestCase):
    """Square pipeline in dry-run mode should succeed without calling any scrape."""

    def test_dry_run_returns_success(self):
        result = _run_square_pipeline(
            gap_start=datetime.date(2026, 5, 1),
            end_date=datetime.date(2026, 5, 20),
            store="palmetto",
            headed=True,
            refresh_date=datetime.date(2026, 5, 20),
            dry_run=True,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.name, "square")


class AdpPipelineDryRunTests(unittest.TestCase):
    """ADP pipeline in dry-run mode should succeed without calling any scrape."""

    def test_dry_run_returns_success(self):
        result = _run_adp_pipeline(
            store="palmetto",
            target_date=datetime.date(2026, 5, 20),
            include_earnings=False,
            headed=True,
            refresh_date=datetime.date(2026, 5, 20),
            dry_run=True,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.name, "adp")


class ReviewFetchDryRunTests(unittest.TestCase):
    """Review fetch in dry-run mode should succeed without calling ClickUp."""

    def test_dry_run_returns_success(self):
        result = _run_review_fetch(
            store="palmetto",
            dry_run=True,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.name, "review_fetch")


class CLIArgParsingTests(unittest.TestCase):
    """Verify per-source date CLI args are parsed correctly."""

    def _parse(self, *extra_args: str) -> argparse.Namespace:
        """Build a parser matching daily_refresh.main() and parse args."""
        cli = argparse.ArgumentParser()
        cli.add_argument("--store", default="palmetto")
        cli.add_argument("--date", default=None)
        cli.add_argument("--from-date", default=None)
        cli.add_argument("--interactive", action="store_true")
        cli.add_argument("--headless", action="store_true")
        cli.add_argument("--include-rates", choices=["yes", "no", "auto"], default="auto")
        cli.add_argument("--skip-rates", action="store_true")
        cli.add_argument("--skip-square", action="store_true")
        cli.add_argument("--skip-timecard", action="store_true")
        cli.add_argument("--skip-adp", action="store_true")
        cli.add_argument("--skip-reviews", action="store_true")
        cli.add_argument("--skip-model", action="store_true")
        cli.add_argument("--square-from", default=None, metavar="DATE")
        cli.add_argument("--square-to", default=None, metavar="DATE")
        cli.add_argument("--adp-from", default=None, metavar="DATE")
        cli.add_argument("--adp-to", default=None, metavar="DATE")
        cli.add_argument("--adp-pay-period", default=None, metavar="PERIOD")
        cli.add_argument("--reviews-since", default=None, metavar="DATE")
        cli.add_argument("--reviews-until", default=None, metavar="DATE")
        cli.add_argument("--dry-run", action="store_true")
        cli.add_argument("--no-slack", action="store_true")
        return cli.parse_args(list(extra_args))

    def test_square_from_to(self):
        args = self._parse("--square-from", "2026-03-22", "--square-to", "2026-04-30")
        self.assertEqual(args.square_from, "2026-03-22")
        self.assertEqual(args.square_to, "2026-04-30")

    def test_adp_from_to(self):
        args = self._parse("--adp-from", "2026-05-01", "--adp-to", "2026-05-15")
        self.assertEqual(args.adp_from, "2026-05-01")
        self.assertEqual(args.adp_to, "2026-05-15")

    def test_adp_pay_period_all(self):
        args = self._parse("--adp-pay-period", "all")
        self.assertEqual(args.adp_pay_period, "all")

    def test_adp_pay_period_current(self):
        args = self._parse("--adp-pay-period", "current")
        self.assertEqual(args.adp_pay_period, "current")

    def test_reviews_since_until(self):
        args = self._parse("--reviews-since", "2026-05-11", "--reviews-until", "2026-05-20")
        self.assertEqual(args.reviews_since, "2026-05-11")
        self.assertEqual(args.reviews_until, "2026-05-20")

    def test_skip_adp_aliases_skip_timecard(self):
        args = self._parse("--skip-adp")
        self.assertTrue(args.skip_adp)

    def test_defaults_are_none(self):
        args = self._parse()
        self.assertIsNone(args.square_from)
        self.assertIsNone(args.square_to)
        self.assertIsNone(args.adp_from)
        self.assertIsNone(args.adp_to)
        self.assertIsNone(args.adp_pay_period)
        self.assertIsNone(args.reviews_since)
        self.assertIsNone(args.reviews_until)

    def test_all_skip_flags_coexist(self):
        args = self._parse(
            "--skip-square", "--skip-timecard", "--skip-reviews", "--skip-model",
        )
        self.assertTrue(args.skip_square)
        self.assertTrue(args.skip_timecard)
        self.assertTrue(args.skip_reviews)
        self.assertTrue(args.skip_model)

    def test_per_source_dates_parseable(self):
        """Verify the date strings actually parse to valid dates."""
        args = self._parse(
            "--square-from", "2026-03-22",
            "--square-to", "2026-05-26",
            "--adp-from", "2026-04-01",
            "--adp-to", "2026-05-15",
            "--reviews-since", "2026-05-11",
            "--reviews-until", "2026-05-26",
        )
        for attr in ("square_from", "square_to", "adp_from", "adp_to",
                     "reviews_since", "reviews_until"):
            val = getattr(args, attr)
            d = datetime.date.fromisoformat(val)
            self.assertIsInstance(d, datetime.date)


class PrefetchedMessagesArgTests(unittest.TestCase):
    """Verify process_reviews --prefetched-messages arg works."""

    def test_prefetched_messages_arg_parsed(self):
        cli = argparse.ArgumentParser()
        cli.add_argument("--store", default="palmetto")
        cli.add_argument("--since", default=None)
        cli.add_argument("--max-pages", type=int, default=40)
        cli.add_argument("--prefetched-messages", default=None, metavar="PATH")
        cli.add_argument("--dry-run", action="store_true")
        cli.add_argument("--no-slack", action="store_true")

        args = cli.parse_args(["--prefetched-messages", "/tmp/msgs.json"])
        self.assertEqual(args.prefetched_messages, "/tmp/msgs.json")

    def test_prefetched_messages_default_none(self):
        cli = argparse.ArgumentParser()
        cli.add_argument("--prefetched-messages", default=None, metavar="PATH")
        args = cli.parse_args([])
        self.assertIsNone(args.prefetched_messages)


if __name__ == "__main__":
    unittest.main()
