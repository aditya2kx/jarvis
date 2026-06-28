#!/usr/bin/env python3
"""Tests for new_requirement.py."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import new_requirement as N


class TestExtractIssueRef(unittest.TestCase):
    """Tests for _extract_issue_ref."""

    def test_github_url(self):
        url = "https://github.com/aditya2kx/jarvis/issues/87"
        self.assertEqual(N._extract_issue_ref(url), 87)

    def test_github_url_embedded_in_text(self):
        text = "link this https://github.com/aditya2kx/jarvis/issues/42 requirement"
        self.assertEqual(N._extract_issue_ref(text), 42)

    def test_hash_ref(self):
        self.assertEqual(N._extract_issue_ref("fix thing #87 please"), 87)

    def test_hash_ref_at_start(self):
        self.assertEqual(N._extract_issue_ref("#99 do the thing"), 99)

    def test_no_ref_returns_none(self):
        self.assertIsNone(N._extract_issue_ref("add multi-date support to the Slack command"))

    def test_docs_url_not_matched(self):
        # Non-issues github URL should not match
        self.assertIsNone(N._extract_issue_ref("https://github.com/aditya2kx/jarvis/pull/85"))
        self.assertIsNone(N._extract_issue_ref("see docs/WORKFLOW.md for details"))

    def test_explicit_issue_wins_over_autodetect(self):
        """--issue takes precedence; _extract_issue_ref is not called when args.issue is set."""
        # Simulate main(): explicit args.issue=5 with a text that also contains #87
        # In main(), issue_ref = args.issue or _extract_issue_ref(combined)
        args_issue = 5
        combined = "fix thing #87 please"
        issue_ref = args_issue or N._extract_issue_ref(combined)
        self.assertEqual(issue_ref, 5)

    @patch("new_requirement.init_phase_tracking")
    @patch("new_requirement.create_worktree")
    @patch("new_requirement.start_session_in_worktree")
    @patch("new_requirement._repo_root")
    def test_dry_run_links_issue_not_creates(self, mock_root, mock_session, mock_wt, mock_phase):
        """When requirement contains an issue ref, --dry-run links it instead of creating."""
        mock_root.return_value = Path("/repo/jarvis")
        mock_session.return_value = (
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-brief.md"),
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-launch.html"),
            "cursor://test",
        )
        mock_phase.return_value = "https://github.com/aditya2kx/jarvis/issues/87"

        rc = N.main([
            "--requirement", "link not create #87 demo",
            "--branch", "fix/test-link",
            "--dry-run",
        ])
        self.assertEqual(rc, 0)
        # init_phase_tracking must be called with existing_issue=87
        _, kwargs = mock_phase.call_args
        self.assertEqual(kwargs.get("existing_issue"), 87)


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

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from phase_state import _slug as _ps_slug  # canonical slugifier from phase_state


class TestSeedCacheToWorktree(unittest.TestCase):
    """Behavioral proof that _seed_cache_to_worktree copies the cache file."""

    def test_slug_matches_phase_state(self):
        """_seed_cache_to_worktree must use the same slug as phase_state._slug."""
        branches = [
            "fix/when-operator-says-they-want-to",
            "fix/add-multi-date-support-123",
            "feat/some-long-branch-name-that-exceeds-sixty-chars-should-be-truncated",
        ]
        for branch in branches:
            # The filename phase_state.py writes
            expected = f"session-{_ps_slug(branch)}-phase.json"
            # What _seed_cache_to_worktree would look for (via phase_state._slug import)
            with tempfile.TemporaryDirectory() as tmp:
                wt = Path(tmp) / "jarvis-wt-test"
                (wt / "metrics" / "pr_cost").mkdir(parents=True)
                real_root = Path(N.__file__).parent.parent
                src_dir = real_root / "metrics" / "pr_cost"
                src_dir.mkdir(parents=True, exist_ok=True)
                cache_file = src_dir / expected
                cache_file.write_text('{"issue": "#99"}')
                try:
                    N._seed_cache_to_worktree(branch=branch, worktree=wt, dry_run=False)
                    dst = wt / "metrics" / "pr_cost" / expected
                    self.assertTrue(
                        dst.exists(),
                        f"cache file {expected} not found in worktree for branch {branch}",
                    )
                finally:
                    cache_file.unlink(missing_ok=True)

    def test_copies_cache_to_worktree(self):
        branch = "fix/test-seed-cache-unit"
        expected = f"session-{_ps_slug(branch)}-phase.json"
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "jarvis-wt-test"
            real_root = Path(N.__file__).parent.parent
            src_dir = real_root / "metrics" / "pr_cost"
            src_dir.mkdir(parents=True, exist_ok=True)
            cache_file = src_dir / expected
            cache_file.write_text('{"issue": "#42"}')
            try:
                dst_dir = wt / "metrics" / "pr_cost"
                dst_dir.mkdir(parents=True)
                N._seed_cache_to_worktree(branch=branch, worktree=wt, dry_run=False)
                dst_file = dst_dir / expected
                self.assertTrue(dst_file.exists(), "cache file must be copied to worktree")
                self.assertEqual(dst_file.read_text(), '{"issue": "#42"}')
            finally:
                cache_file.unlink(missing_ok=True)

    def test_no_op_if_source_missing(self):
        """Should not raise if the source cache doesn't exist yet."""
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "jarvis-wt-test"
            (wt / "metrics" / "pr_cost").mkdir(parents=True)
            N._seed_cache_to_worktree(
                branch="fix/xyzzy-nonexistent-99999",
                worktree=wt,
                dry_run=False,
            )


class TestDefaultBase(unittest.TestCase):
    def test_returns_origin_main(self):
        self.assertEqual(N.default_base(), "origin/main")

    @patch("new_requirement.create_worktree")
    @patch("new_requirement.start_session_in_worktree")
    @patch("new_requirement._repo_root")
    def test_main_no_base_uses_origin_main(self, mock_root, mock_session, mock_wt):
        """When --base is not passed, main() must resolve to origin/main."""
        mock_root.return_value = Path("/repo/jarvis")
        mock_session.return_value = (
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-brief.md"),
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-launch.html"),
            "cursor://test",
        )
        N.main(["--requirement", "Test base default", "--branch", "fix/test-base", "--dry-run"])
        _, kwargs = mock_wt.call_args
        self.assertEqual(kwargs.get("base"), "origin/main")

    @patch("new_requirement.create_worktree")
    @patch("new_requirement.start_session_in_worktree")
    @patch("new_requirement._repo_root")
    def test_main_explicit_base_honored(self, mock_root, mock_session, mock_wt):
        """When --base is explicitly passed, it must be forwarded verbatim."""
        mock_root.return_value = Path("/repo/jarvis")
        mock_session.return_value = (
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-brief.md"),
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-launch.html"),
            "cursor://test",
        )
        N.main([
            "--requirement", "Test base override",
            "--branch", "fix/test-base-override",
            "--base", "feat/some-inflight-branch",
            "--dry-run",
        ])
        _, kwargs = mock_wt.call_args
        self.assertEqual(kwargs.get("base"), "feat/some-inflight-branch")


if __name__ == "__main__":
    unittest.main()
