#!/usr/bin/env python3
"""Tests for post_claude_review_cost.parse_execution."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from post_claude_review_cost import estimate_cost_usd, format_comment, parse_execution


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
    def test_sonnet_estimate(self):
        est = estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 100_000)
        self.assertAlmostEqual(est, 3.0 + 1.5, places=4)


if __name__ == "__main__":
    unittest.main()
