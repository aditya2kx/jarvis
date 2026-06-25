#!/usr/bin/env python3
"""Tests for start_pr_session.py."""

import html
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pr_cost_ledger as L
import start_pr_session as S


class TestStartPrSession(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = L.LEDGER_DIR
        L.LEDGER_DIR = Path(self._tmpdir.name)

    def tearDown(self):
        L.LEDGER_DIR = self._orig_dir
        self._tmpdir.cleanup()

    def test_make_deeplink_encodes_text(self):
        link = S.make_deeplink("Hello PR #15 & test")
        self.assertTrue(link.startswith("cursor://anysphere.cursor-deeplink/prompt?text="))
        self.assertIn("Hello", link)
        self.assertNotIn(" ", link)  # spaces must be percent-encoded
        self.assertIn("mode=agent", link)
        self.assertIn("model=claude-4.6-sonnet-medium-thinking", link)

    def test_make_deeplink_jam_handoff(self):
        link = S.make_deeplink(
            "jam seed",
            mode=S.DEFAULT_JAM_HANDOFF_MODE,
            model=S.DEFAULT_JAM_HANDOFF_MODEL,
        )
        self.assertIn("mode=ask", link)
        self.assertIn("claude-opus-4-8-thinking-high", link)

    def test_seed_prompt_jam_no_implement(self):
        p = S.seed_prompt_jam(
            "fix/foo",
            brief_rel="metrics/pr_cost/session-fix-foo-brief.md",
            requirement="Debug ADP issues",
        )
        self.assertIn("Ask mode", p)
        self.assertIn("Do NOT implement", p)
        self.assertIn("jam", p.lower())
        self.assertNotIn("implement the requirement", p)

    def test_make_deeplink_no_model_when_disabled(self):
        link = S.make_deeplink("hello", model=None)
        self.assertNotIn("model=", link)

    def test_make_deeplink_rejects_overlong_text(self):
        with self.assertRaises(ValueError):
            S.make_deeplink("x" * 3000)

    def test_seed_prompt_is_short(self):
        req = (
            "Dashboard on a free visualization tool (Looker Studio / Chartio) — "
            "shareable with team, backed by BigQuery."
        )
        p = S.seed_prompt(16, brief_rel="metrics/pr_cost/PR-16-brief.md", requirement=req)
        link = S.make_deeplink(p)
        self.assertLess(len(link), S._MAX_DEEPLINK_CHARS)
        self.assertIn("PR-16-brief.md", p)
        self.assertIn("Dashboard", p)
        self.assertIn("implement", p)  # PR continuation handoff

    def test_seed_prompt_provisional_delegates_to_jam(self):
        p = S.seed_prompt(
            "fix/new-req",
            brief_rel="metrics/pr_cost/session-fix-new-req-brief.md",
            requirement="Add widget",
        )
        self.assertIn("Ask mode", p)
        self.assertIn("Do NOT implement", p)

    def test_truncate_requirement(self):
        long = "x" * 200
        t = S._truncate_requirement(long, max_len=50)
        self.assertLessEqual(len(t), 50)
        self.assertTrue(t.endswith("…"))

    def test_write_launch_html(self):
        link = S.make_deeplink("hello")
        brief = Path(self._tmpdir.name) / "PR-99-brief.md"
        brief.write_text("# brief", encoding="utf-8")
        out = S.write_launch_html(99, link, brief_path=brief, seed_text="PR #99 seed")
        text = out.read_text()
        self.assertIn("Open new chat for PR #99", text)
        self.assertIn(html.escape(link, quote=True), text)
        self.assertIn("PR #99 seed", text)
        self.assertIn("Test PR #99", text)  # anti-placeholder warning

    def test_generate_brief_writes_file(self):
        with patch.object(S, "_gh", return_value=""):
            brief = S.generate_brief(42, requirement="Build the thing", title="t", branch="feat/b")
        brief_path = L.LEDGER_DIR / "PR-42-brief.md"
        self.assertTrue(brief_path.exists())
        text = brief_path.read_text()
        self.assertIn("PR #42", text)
        self.assertIn("Build the thing", text)
        self.assertIn("feat/b", text)
        self.assertIn("Sonnet", text)  # model routing reminder present
        self.assertIn("pr_cost_ledger.py sync", text)  # cost gate reminder present

    def test_generate_brief_uses_ledger_meta(self):
        L.set_meta(43, title="Existing title", branch="feat/existing", requirement="Existing req")
        with patch.object(S, "_gh", return_value=""):
            brief = S.generate_brief(43)
        self.assertIn("Existing req", brief)
        self.assertIn("feat/existing", brief)

    def test_generate_brief_prior_pr_reference(self):
        L.record_build_session(10, ts="2026-01-01T00:00:00Z", tokens=1000, cost_usd=5.0, model="opus")
        L.set_meta(10, title="Prior PR title")
        with patch.object(S, "_gh", return_value=""):
            brief = S.generate_brief(11, requirement="Next feature", branch="feat/next")
        self.assertIn("PR #10", brief)

    def test_main_exits_0(self):
        with patch.object(S, "_gh", return_value=""), \
             patch.object(S, "generate_brief", return_value="# PR #99 session brief\n"), \
             patch("builtins.print"):
            rc = S.main(["--pr", "99"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
