#!/usr/bin/env python3
"""Tests for check_repo_default_branch.py (incident 2026-07-01 regression guard)."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import check_repo_default_branch as m


def _fake_run(returncode: int, stdout: str, stderr: str = ""):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)
    return run


class TestDetectRepo(unittest.TestCase):
    def test_ssh_url(self):
        with patch("subprocess.run", side_effect=_fake_run(0, "git@github.com:aditya2kx/jarvis.git\n")):
            self.assertEqual(m._detect_repo(), "aditya2kx/jarvis")

    def test_https_url(self):
        with patch("subprocess.run", side_effect=_fake_run(0, "https://github.com/aditya2kx/jarvis.git\n")):
            self.assertEqual(m._detect_repo(), "aditya2kx/jarvis")

    def test_non_github_remote(self):
        with patch("subprocess.run", side_effect=_fake_run(0, "https://gitlab.com/foo/bar.git\n")):
            self.assertIsNone(m._detect_repo())


class TestGetDefaultBranch(unittest.TestCase):
    def test_success(self):
        with patch("subprocess.run", side_effect=_fake_run(0, "main\n")):
            self.assertEqual(m.get_default_branch("aditya2kx/jarvis"), "main")

    def test_gh_failure_raises(self):
        with patch("subprocess.run", side_effect=_fake_run(1, "", "HTTP 404")):
            with self.assertRaises(RuntimeError):
                m.get_default_branch("aditya2kx/jarvis")


class TestMain(unittest.TestCase):
    def test_matches_expect_exits_zero(self):
        with patch("sys.argv", ["check_repo_default_branch.py"]), \
             patch.object(m, "_detect_repo", return_value="aditya2kx/jarvis"), \
             patch.object(m, "get_default_branch", return_value="main"):
            self.assertEqual(m.main(), 0)

    def test_drifted_default_branch_exits_nonzero(self):
        """Regression test for the 2026-07-01 incident: default branch drifted
        to a feature branch, so a bare `gh pr create` silently targeted it."""
        with patch("sys.argv", ["check_repo_default_branch.py"]), \
             patch.object(m, "_detect_repo", return_value="aditya2kx/jarvis"), \
             patch.object(m, "get_default_branch",
                           return_value="fix/i101-combine-related-tasks-1-retrospective-protocol"):
            self.assertEqual(m.main(), 1)

    def test_undetectable_repo_exits_nonzero(self):
        with patch("sys.argv", ["check_repo_default_branch.py"]), \
             patch.object(m, "_detect_repo", return_value=None):
            self.assertEqual(m.main(), 1)

    def test_gh_error_exits_nonzero(self):
        with patch("sys.argv", ["check_repo_default_branch.py"]), \
             patch.object(m, "_detect_repo", return_value="aditya2kx/jarvis"), \
             patch.object(m, "get_default_branch", side_effect=RuntimeError("boom")):
            self.assertEqual(m.main(), 1)


if __name__ == "__main__":
    unittest.main()
