#!/usr/bin/env python3
"""Tests for post_claude_review_cost.parse_execution."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from post_claude_review_cost import (
    cost_breakdown_usd,
    estimate_cost_usd,
    format_comment,
    parse_execution,
)


class TestParseExecution(unittest.TestCase):
    def test_result_message_drives_cost_and_turns(self):
        messages = [
            {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"},
            {
                "type": "assistant",
                "message": {"usage": {"input_tokens": 1000, "output_tokens": 50}},
            },
            {
                "type": "result",
                "subtype": "success",
                "num_turns": 3,
                "total_cost_usd": 0.42,
                "duration_ms": 12000,
                "usage": {"input_tokens": 5000, "output_tokens": 800},
            },
        ]
        stats = parse_execution(messages)
        self.assertEqual(stats["model"], "claude-sonnet-4-6")
        self.assertEqual(stats["num_turns"], 3)
        self.assertEqual(stats["total_cost_usd"], 0.42)
        self.assertEqual(stats["input_tokens"], 5000)
        self.assertEqual(stats["output_tokens"], 800)
        self.assertEqual(stats["duration_ms"], 12000)

    def test_sums_assistant_when_no_result_usage(self):
        messages = [
            {"type": "system", "subtype": "init", "model": "claude-opus-4-7"},
            {"type": "assistant", "message": {"usage": {"input_tokens": 100, "output_tokens": 10}}},
            {"type": "assistant", "message": {"usage": {"input_tokens": 200, "output_tokens": 20}}},
            {"type": "result", "subtype": "success", "total_cost_usd": 1.0, "num_turns": 2},
        ]
        stats = parse_execution(messages)
        self.assertEqual(stats["input_tokens"], 300)
        self.assertEqual(stats["output_tokens"], 30)
        self.assertEqual(stats["total_cost_usd"], 1.0)


class TestFormatComment(unittest.TestCase):
    def test_includes_reported_cost(self):
        body = format_comment(
            pr_number=3,
            stats={
                "model": "claude-sonnet-4-6",
                "num_turns": 8,
                "input_tokens": 12000,
                "output_tokens": 900,
                "billable_input_tokens": 12000,
                "total_cost_usd": 0.55,
                "duration_ms": 95000,
                "conclusion": "success",
            },
            default_model="claude-sonnet-4-6",
            workflow_run_url="https://example.com/run/1",
            execution_missing=False,
        )
        self.assertIn("**$0.5500**", body)
        self.assertIn("| Turns | 8 |", body)
        self.assertIn("claude-sonnet-4-6", body)


class TestEstimateCost(unittest.TestCase):
    def test_sonnet_estimate_input_and_output(self):
        # 1M uncached input @ $3 + 100k output @ $15/M = 3.0 + 1.5
        est = estimate_cost_usd(
            "claude-sonnet-4-6",
            {"input_tokens": 1_000_000, "output_tokens": 100_000},
        )
        self.assertAlmostEqual(est, 3.0 + 1.5, places=4)

    def test_cache_tiers_priced_below_base_input(self):
        # Cache read is 0.10× and cache write 1.25× base input — NOT full input.
        b = cost_breakdown_usd(
            "claude-sonnet-4-6",
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 1_000_000,
                "cache_creation_input_tokens": 1_000_000,
            },
        )
        self.assertAlmostEqual(b["cache_read"], 0.30, places=4)   # 3.0 * 0.10
        self.assertAlmostEqual(b["cache_write"], 3.75, places=4)  # 3.0 * 1.25
        self.assertAlmostEqual(b["total"], 0.30 + 3.75, places=4)

    def test_regression_does_not_price_cache_as_fresh_input(self):
        # The old bug summed cache+input and priced all at $3/M. Prove the new
        # estimate for a cache-heavy run is far below that naive number.
        stats = {
            "input_tokens": 9,
            "output_tokens": 9_028,
            "cache_read_input_tokens": 366_326,
            "cache_creation_input_tokens": 58_256,
        }
        est = estimate_cost_usd("claude-sonnet-4-6", stats)
        naive_all_input = (9 + 366_326 + 58_256) / 1_000_000 * 3.0  # old method ≈ $1.27
        self.assertLess(est, 0.60)
        self.assertLess(est, naive_all_input)
        # Reconciles with the real reported cost (~$0.4646) within list-price slack.
        self.assertAlmostEqual(est, 0.4646, delta=0.02)

    def test_unknown_model_returns_none(self):
        self.assertIsNone(estimate_cost_usd("gpt-4o", {"input_tokens": 100}))


class TestFormatCompositionAndLabels(unittest.TestCase):
    def test_uncached_label_and_no_billable_row(self):
        body = format_comment(
            pr_number=5,
            stats={
                "model": "claude-sonnet-4-6",
                "num_turns": 9,
                "input_tokens": 9,
                "output_tokens": 9_028,
                "cache_read_input_tokens": 366_326,
                "cache_creation_input_tokens": 58_256,
                "billable_input_tokens": 424_591,
                "total_cost_usd": 0.4646,
                "duration_ms": 172_000,
                "conclusion": "success",
            },
            default_model="claude-sonnet-4-6",
            workflow_run_url=None,
            execution_missing=False,
        )
        self.assertIn("Input tokens (uncached)", body)
        self.assertNotIn("Billable input", body)
        self.assertIn("Cost composition", body)
        self.assertIn("**$0.4646**", body)

    def test_bootstrap_workflow_skips_zero_table(self):
        body = format_comment(
            pr_number=7,
            stats={"num_turns": 0, "input_tokens": 0, "output_tokens": 0},
            default_model="claude-sonnet-4-6",
            workflow_run_url="https://example.com/run/1",
            execution_missing=True,
            skip_reason="bootstrap_workflow",
        )
        self.assertIn("Review did not run", body)
        self.assertNotIn("| Turns | 0 |", body)
        self.assertIn("byte-identical", body)


if __name__ == "__main__":
    unittest.main()
