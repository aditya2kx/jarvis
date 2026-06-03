#!/usr/bin/env python3
"""Tests for start_pr_session.py."""

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

    def test_make_deeplink_special_chars(self):
        link = S.make_deeplink("feat/my-branch → plan")
        self.assertNotIn("→", link)  # non-ASCII must be encoded

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
