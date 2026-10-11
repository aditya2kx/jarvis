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
                         (AID, "claude-opus-5-5-high", []))

    def test_marker_without_model_prices_as_opus_5_5(self):
        self.assertEqual(C.parse_marker(f"Cursor-Agent: {AID}"), (AID, C.FALLBACK_MODEL, []))

    def test_marker_with_prs(self):
        self.assertEqual(C.parse_marker(f"Cursor-Agent: {AID} model=m prs=385,386"), (AID, "m", [385, 386]))

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

    def test_prs_splits_cost_by_diff_size_one_row_per_pr(self):
        def fake_api(method, path, body=None):
            if path.endswith("/usage"):
                return {"totalUsage": {**CostTests.USAGE, "totalTokens": 13_000_000}}
            return {"id": AID, "createdAt": "2026-10-11T14:00:00.000Z"}

        gh = {385: {"additions": 700, "deletions": 200}, 386: {"additions": 80, "deletions": 20,
              "title": "ci: review", "head": {"ref": "cursor/x"}}}
        with mock.patch.object(C, "_api", side_effect=fake_api), \
             mock.patch.object(C, "_gh_pr", side_effect=gh.get), \
             mock.patch.object(L, "set_meta") as meta, \
             mock.patch.object(L, "record_build_session") as rec:
            out = C.record_cost(385, f"Cursor-Agent: {AID} model=claude-opus-5-5-high prs=385,386", "t", "b")
        meta.assert_any_call(385, title="t", branch="b")
        meta.assert_any_call(386, title="ci: review", branch="cursor/x")
        rows = {c.args[0]: c.kwargs for c in rec.call_args_list}
        self.assertAlmostEqual(rows[385]["cost_usd"], 27.9)
        self.assertAlmostEqual(rows[386]["cost_usd"], 3.1)
        self.assertEqual({r["ts"] for r in rows.values()}, {"2026-10-11T14:00:00.000Z"})
        self.assertIn("PR #386 10%", out)

    def test_each_agent_marker_gets_its_own_row(self):
        later = "bc-90191966-7bee-57e1-b3e9-67d50b0b4de0"
        created = {AID: "2026-10-11T14:00:00.000Z", later: "2026-10-11T21:48:00.000Z"}

        def fake_api(method, path, body=None):
            agent = path.split("/")[2]
            if path.endswith("/usage"):
                return {"totalUsage": {**CostTests.USAGE, "totalTokens": 13_000_000}}
            return {"id": agent, "createdAt": created[agent]}

        body = f"Cursor-Agent: {AID} model=claude-opus-5-5-high prs=387\nCursor-Agent: {later} model=claude-sonnet-5-5-medium"
        with mock.patch.object(C, "_api", side_effect=fake_api), \
             mock.patch.object(L, "set_meta"), mock.patch.object(L, "record_build_session") as rec:
            out = C.record_cost(387, body, "t", "b")
        rows = {c.kwargs["note"]: c.kwargs for c in rec.call_args_list}
        self.assertEqual({c.args[0] for c in rec.call_args_list}, {387})
        self.assertAlmostEqual(rows[AID]["cost_usd"], 31.0)
        self.assertAlmostEqual(rows[later]["cost_usd"], 15.5)
        self.assertEqual(rows[later]["ts"], "2026-10-11T21:48:00.000Z")
        self.assertEqual(len(out.splitlines()), 2)

    def test_split_shares_equal_when_sizes_unknown(self):
        self.assertEqual(C.split_shares({1: 0, 2: 0}), {1: 0.5, 2: 0.5})

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
        self.assertEqual(payload["mode"], "plan")
        self.assertNotIn("workOnCurrentBranch", payload)

    def test_pr_continues_on_the_prs_own_branch(self):
        with mock.patch.object(C, "_api", return_value={"agent": {"id": AID, "url": "u"}, "run": {"id": "r"}}) as api:
            C.launch("continue PR #388 from the handoff note above", None, pr=388)
        payload = api.call_args.args[2]
        self.assertEqual(payload["repos"], [{"url": C.REPO_URL, "prUrl": f"{C.REPO_URL}/pull/388"}])
        self.assertIs(payload["workOnCurrentBranch"], True)
        self.assertEqual(payload["mode"], "agent")
        self.assertEqual(payload["name"], "PR #388 continue PR #388 from the handoff note above")
        self.assertIn("handoff.py in --pr 388", payload["prompt"]["text"])


if __name__ == "__main__":
    unittest.main()
