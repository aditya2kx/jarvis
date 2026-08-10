#!/usr/bin/env python3
"""Unit tests for spawn_cloud_agent.py (mocked HTTP)."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import spawn_cloud_agent as SCA


class TestSeedPrompt(unittest.TestCase):
    def test_includes_jam_and_secrets(self):
        text = SCA.seed_prompt_cloud_jam(
            requirement="add widget",
            branch="fix/i1-add-widget",
            issue=1,
        )
        self.assertIn("jam", text.lower())
        self.assertIn("BHAGA_SECRETS_BACKEND=gcp", text)
        self.assertIn("#1", text)
        self.assertIn("fix/i1-add-widget", text)


class TestSpawnCloudAgent(unittest.TestCase):
    def test_dry_run_no_http(self):
        result = SCA.spawn_cloud_agent(
            prompt_text="hello",
            starting_ref="fix/test",
            dry_run=True,
        )
        self.assertTrue(result.get("dry_run"))
        self.assertEqual(result["agent_id"], "bc-dry-run")
        self.assertIn("prompt", result["payload"])
        model = result["payload"]["model"]
        self.assertEqual(model["id"], SCA.DEFAULT_CLOUD_MODEL_ID)
        self.assertIn({"id": "effort", "value": "medium"}, model["params"])
        self.assertIn({"id": "fast", "value": "false"}, model["params"])

    def test_model_omit_with_empty_dict(self):
        result = SCA.spawn_cloud_agent(
            prompt_text="hello",
            starting_ref="fix/test",
            model={},
            dry_run=True,
        )
        self.assertNotIn("model", result["payload"])

    @patch("spawn_cloud_agent.urllib.request.urlopen")
    def test_spawn_parses_response(self, mock_urlopen):
        body = {
            "agent": {
                "id": "bc-abc",
                "url": "https://cursor.com/agents/bc-abc",
            },
            "run": {"id": "run-1"},
        }
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = SCA.spawn_cloud_agent(
            prompt_text="hello",
            starting_ref="fix/test",
            agent_token="test-key",
            dry_run=False,
        )
        self.assertEqual(result["agent_id"], "bc-abc")
        self.assertEqual(result["run_id"], "run-1")
        self.assertIn("bc-abc", result["url"])

    @patch("spawn_cloud_agent.find_existing_agent_url", return_value="https://cursor.com/agents/bc-old")
    def test_spawn_for_issue_reuses(self, _mock_find):
        result = SCA.spawn_for_issue(
            issue=99,
            branch="fix/i99-x",
            requirement="x",
            ensure_branch=False,
            dry_run=False,
        )
        self.assertTrue(result.get("reused"))
        self.assertEqual(result["url"], "https://cursor.com/agents/bc-old")
        self.assertEqual(result["agent_id"], "bc-old")

    @patch("spawn_cloud_agent._post_issue_comment")
    @patch("spawn_cloud_agent.ensure_remote_branch")
    @patch("spawn_cloud_agent.find_existing_agent_url", return_value=None)
    def test_spawn_for_issue_dry_run(self, _find, mock_branch, mock_comment):
        result = SCA.spawn_for_issue(
            issue=42,
            branch="fix/i42-y",
            requirement="y",
            dry_run=True,
        )
        self.assertTrue(result.get("dry_run"))
        mock_branch.assert_called_once()
        mock_comment.assert_called_once()


class TestAgentUrlRe(unittest.TestCase):
    def test_matches_bc_and_url(self):
        self.assertIsNotNone(SCA.AGENT_URL_RE.search("see bc-deadbeef01 here"))
        self.assertIsNotNone(
            SCA.AGENT_URL_RE.search("https://cursor.com/agents/bc-deadbeef01")
        )


class TestOpenHandoff(unittest.TestCase):
    def test_normalize_and_deeplink(self):
        self.assertEqual(
            SCA.normalize_agent_url("https://cursor.com/agents/bc-abc"),
            "https://cursor.com/agents/bc-abc",
        )
        self.assertEqual(
            SCA.agent_id_from_ref("https://cursor.com/agents/bc-abc"),
            "bc-abc",
        )
        self.assertIn("bcId=bc-abc", SCA.desktop_agent_deeplink("bc-abc"))

    @patch("spawn_cloud_agent.subprocess.run")
    def test_open_desktop_skips_browser_on_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        SCA.open_cloud_agent_handoff(
            url="https://cursor.com/agents/bc-xyz",
            agent_id="bc-xyz",
            dry_run=False,
        )
        self.assertEqual(mock_run.call_count, 1)
        first = mock_run.call_args_list[0].args[0]
        self.assertEqual(first[0], "open")
        self.assertIn("background-agent", first[1])
        self.assertIn("bc-xyz", first[1])

    @patch("spawn_cloud_agent.subprocess.run")
    def test_open_falls_back_to_https_when_desktop_fails(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        SCA.open_cloud_agent_handoff(
            url="https://cursor.com/agents/bc-xyz",
            agent_id="bc-xyz",
            dry_run=False,
        )
        self.assertEqual(mock_run.call_count, 2)
        second = mock_run.call_args_list[1].args[0]
        self.assertEqual(second, ["open", "https://cursor.com/agents/bc-xyz"])

    @patch("spawn_cloud_agent.agent_reachable", side_effect=[True])
    @patch("spawn_cloud_agent.subprocess.check_output")
    def test_find_existing_skips_unreachable(self, mock_out, mock_reach):
        mock_out.return_value = (
            "old https://cursor.com/agents/bc-dead\n"
            "new https://cursor.com/agents/bc-live\n"
        )
        # Walk newest-first: live is reachable → return it (dead never probed).
        url = SCA.find_existing_agent_url(7)
        self.assertEqual(url, "https://cursor.com/agents/bc-live")
        mock_reach.assert_called_once_with("bc-live")

    @patch("spawn_cloud_agent.agent_reachable", return_value=False)
    @patch("spawn_cloud_agent.subprocess.check_output")
    def test_find_existing_all_unreachable(self, mock_out, _reach):
        mock_out.return_value = "https://cursor.com/agents/bc-dead\n"
        self.assertIsNone(SCA.find_existing_agent_url(7))


if __name__ == "__main__":
    unittest.main()
