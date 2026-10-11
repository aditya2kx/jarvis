"""Unit tests for scripts/cloud_agent.py — no network, no BQ."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cloud_agent as C  # noqa: E402
import pr_cost_ledger as L  # noqa: E402

AID = "bc-01a12b15-18e9-7f9a-9252-af37a985457d"


class MarkerTests(unittest.TestCase):
    def test_marker_with_model(self):
        self.assertEqual(C.parse_marker(f"x\nCursor-Agent: {AID} model=claude-opus-5-5-high\n"),
                         (AID, "claude-opus-5-5-high"))

    def test_marker_without_model_prices_as_opus_5_5(self):
        self.assertEqual(C.parse_marker(f"Cursor-Agent: {AID}"), (AID, C.FALLBACK_MODEL))

    def test_no_marker(self):
        self.assertIsNone(C.parse_marker("Closes #384"))


class CostTests(unittest.TestCase):
    USAGE = {"inputTokens": 1_000_000, "outputTokens": 1_000_000,
             "cacheReadTokens": 10_000_000, "cacheWriteTokens": 1_000_000}

    def test_opus_5_5_rates(self):
        # 4 + 20 + 10×0.2 + 5
        self.assertAlmostEqual(C.cost_usd(self.USAGE, "claude-opus-5-5-high"), 31.0)

    def test_sonnet_5_5_rates(self):
        # 2 + 10 + 10×0.1 + 2.5
        self.assertAlmostEqual(C.cost_usd(self.USAGE, "claude-sonnet-5-5-medium"), 15.5)

    def test_older_generations_keep_their_tiers(self):
        self.assertEqual(L._model_tier("claude-opus-4-8-thinking-high"), "opus")
        self.assertEqual(L._model_tier("claude-sonnet-5-thinking-medium"), "sonnet")


class RecordCostTests(unittest.TestCase):
    def test_records_one_idempotent_row_keyed_by_agent_creation(self):
        def fake_api(method, path, body=None):
            if path.endswith("/usage"):
                return {"totalUsage": {**CostTests.USAGE, "totalTokens": 13_000_000}}
            return {"id": AID, "createdAt": "2026-10-11T14:00:00.000Z"}

        with mock.patch.object(C, "_api", side_effect=fake_api), \
             mock.patch.object(L, "set_meta") as meta, \
             mock.patch.object(L, "record_build_session") as rec:
            out = C.record_cost(385, f"Cursor-Agent: {AID} model=claude-opus-5-5-high", "t", "b")
        meta.assert_called_once_with(385, title="t", branch="b")
        kw = rec.call_args.kwargs
        self.assertEqual(kw["ts"], "2026-10-11T14:00:00.000Z")
        self.assertEqual(kw["tokens"], 13_000_000)
        self.assertAlmostEqual(kw["cost_usd"], 31.0)
        self.assertIn("cost=$31.00", out)

    def test_skips_without_marker(self):
        with mock.patch.object(C, "_api") as api:
            self.assertIn("skipped", C.record_cost(1, "no marker", "t", "b"))
        api.assert_not_called()


class LaunchTests(unittest.TestCase):
    def test_payload(self):
        with mock.patch.object(C, "_api", return_value={"agent": {"id": AID, "url": "u"}, "run": {"id": "r"}}) as api:
            out = C.launch("Add a badge\nmore", 384, model="claude-opus-5-5-high")
        method, path, payload = api.call_args.args
        self.assertEqual((method, path), ("POST", "/agents"))
        self.assertEqual(payload["repos"], [{"url": C.REPO_URL, "startingRef": "main"}])
        self.assertEqual(payload["model"], {"id": "claude-opus-5-5-high"})
        self.assertEqual(payload["name"], "#384 Add a badge")
        self.assertIn("Closes #384", payload["prompt"]["text"])
        self.assertEqual(out, {"id": AID, "url": "u", "run_id": "r"})


if __name__ == "__main__":
    unittest.main()
