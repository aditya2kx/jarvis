#!/usr/bin/env python3
"""Tests for new_requirement.py."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import new_requirement as N


class TestNewRequirement(unittest.TestCase):
    def test_slug_branch_part(self):
        self.assertEqual(
            N._slug_branch_part("Fix cost report titles and de-contaminate"),
            "fix-cost-report-titles-and-de",
        )

    def test_default_branch(self):
        self.assertEqual(
            N.default_branch("Add zero-shift guard"),
            "fix/add-zero-shift-guard",
        )

    def test_default_worktree_path(self):
        root = Path("/Users/me/projects/jarvis")
        p = N.default_worktree_path(root, "fix/cost-ledger-decontamination")
        self.assertEqual(p, Path("/Users/me/projects/jarvis-wt-fix-cost-ledger-decontamination"))

    @patch("new_requirement.subprocess.run")
    def test_branch_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(N._branch_exists(Path("/tmp"), "fix/foo"))
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(N._branch_exists(Path("/tmp"), "fix/missing"))

    @patch("new_requirement.create_worktree")
    @patch("new_requirement.start_session_in_worktree")
    @patch("new_requirement._repo_root")
    def test_main_dry_run(self, mock_root, mock_session, mock_wt):
        mock_root.return_value = Path("/repo/jarvis")
        mock_session.return_value = (
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-brief.md"),
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-launch.html"),
            "cursor://test",
        )
        rc = N.main(["--requirement", "Test requirement", "--branch", "fix/test-req", "--dry-run"])
        self.assertEqual(rc, 0)
        mock_wt.assert_called_once()
        mock_session.assert_called_once()
        _, kwargs = mock_session.call_args
        self.assertEqual(kwargs.get("mode"), N.S.DEFAULT_JAM_HANDOFF_MODE)
        self.assertEqual(kwargs.get("model"), N.S.DEFAULT_JAM_HANDOFF_MODEL)


if __name__ == "__main__":
    unittest.main()
