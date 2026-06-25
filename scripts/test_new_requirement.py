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


import shutil
import tempfile


class TestSeedCacheToWorktree(unittest.TestCase):
    """Behavioral proof that _seed_cache_to_worktree copies the cache file."""

    def test_copies_cache_to_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "jarvis-wt-test"
            # Create source cache in the REAL repo's metrics/pr_cost/
            real_root = Path(N.__file__).parent.parent
            src_dir = real_root / "metrics" / "pr_cost"
            src_dir.mkdir(parents=True, exist_ok=True)
            branch = "fix/test-seed-cache-unit"
            cache_name = "session-fix-test-seed-cache-unit-phase.json"
            cache_file = src_dir / cache_name
            cache_file.write_text('{"issue": "#42"}')
            try:
                dst_dir = wt / "metrics" / "pr_cost"
                dst_dir.mkdir(parents=True)
                N._seed_cache_to_worktree(branch=branch, worktree=wt, dry_run=False)
                dst_file = dst_dir / cache_name
                self.assertTrue(dst_file.exists(), "cache file must be copied to worktree")
                self.assertEqual(dst_file.read_text(), '{"issue": "#42"}')
            finally:
                cache_file.unlink(missing_ok=True)

    def test_no_op_if_source_missing(self):
        """Should not raise if the source cache doesn't exist yet."""
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "jarvis-wt-test"
            (wt / "metrics" / "pr_cost").mkdir(parents=True)
            # Branch that has no corresponding cache file
            N._seed_cache_to_worktree(
                branch="fix/xyzzy-nonexistent-99999",
                worktree=wt,
                dry_run=False,
            )


if __name__ == "__main__":
    unittest.main()
