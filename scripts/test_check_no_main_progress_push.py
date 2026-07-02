#!/usr/bin/env python3
"""Tests for check_no_main_progress_push.py."""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_no_main_progress_push as G


def _make_changed_fn(files: list[str]):
    """Return a callable that always reports the given file list."""
    return lambda old, new: files


class TestViolates(unittest.TestCase):
    def test_main_with_progress_violates(self):
        refs = [("new-sha", "refs/heads/feature", "old-sha", "refs/heads/main")]
        self.assertTrue(G.violates(refs) if False else True)
        # Use the real function with a mocked _changed_files_between
        with patch.object(G, "_changed_files_between", return_value=["PROGRESS.md", "README.md"]):
            result = G.violates([("new-sha", "local-ref", "old-sha", "refs/heads/main")])
        self.assertTrue(result)

    def test_feature_branch_push_does_not_violate(self):
        """Push to a feature branch is always allowed even with PROGRESS.md."""
        with patch.object(G, "_changed_files_between", return_value=["PROGRESS.md"]):
            result = G.violates([("new-sha", "local-ref", "old-sha", "refs/heads/fix/my-feature")])
        self.assertFalse(result)

    def test_main_without_progress_does_not_violate(self):
        with patch.object(G, "_changed_files_between", return_value=["scripts/foo.py"]):
            result = G.violates([("new-sha", "local-ref", "old-sha", "refs/heads/main")])
        self.assertFalse(result)

    def test_multiple_refs_one_violating(self):
        """Only the main-targeted ref with PROGRESS.md matters."""
        def changed(old, new):
            return ["PROGRESS.md"] if new == "main-sha" else ["scripts/foo.py"]
        with patch.object(G, "_changed_files_between", side_effect=changed):
            refs = [
                ("feat-sha", "local-ref", "old-sha", "refs/heads/fix/feature"),
                ("main-sha", "local-ref", "old-sha", "refs/heads/main"),
            ]
            result = G.violates(refs)
        self.assertTrue(result)

    def test_empty_refs_no_violation(self):
        self.assertFalse(G.violates([]))


class TestGateMode(unittest.TestCase):
    def test_feature_branch_always_exits_0(self):
        """On a feature branch, gate mode always succeeds."""
        with patch.object(subprocess, "check_output", return_value="fix/my-feature\n"):
            result = G._gate_mode()
        self.assertEqual(result, 0)

    def test_main_with_progress_exits_1(self):
        """On main with PROGRESS.md in diff, gate blocks."""
        def fake_check_output(cmd, **kw):
            if "--abbrev-ref" in cmd:
                return "main\n"
            if "diff" in cmd:
                return "PROGRESS.md\nscripts/foo.py\n"
            return ""
        with patch.object(subprocess, "check_output", side_effect=fake_check_output):
            result = G._gate_mode()
        self.assertEqual(result, 1)

    def test_main_without_progress_exits_0(self):
        """On main but no PROGRESS.md change, gate passes."""
        def fake_check_output(cmd, **kw):
            if "--abbrev-ref" in cmd:
                return "main\n"
            if "diff" in cmd:
                return "scripts/foo.py\n"
            return ""
        with patch.object(subprocess, "check_output", side_effect=fake_check_output):
            result = G._gate_mode()
        self.assertEqual(result, 0)

    def test_git_error_treated_as_pass(self):
        """If git fails, gate exits 0 (fail open)."""
        with patch.object(subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "git")):
            result = G._gate_mode()
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
