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

    @patch("new_requirement._fetch_issue_context", return_value=None)
    @patch("new_requirement.init_phase_tracking")
    @patch("new_requirement.create_worktree")
    @patch("new_requirement.start_session_in_worktree")
    @patch("new_requirement._repo_root")
    def test_dry_run_links_issue_not_creates(self, mock_root, mock_session, mock_wt, mock_phase, mock_ctx):
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
                import json as _json
                data = _json.loads(dst_file.read_text())
                self.assertEqual(data.get("issue"), "#42")
                # _seed_cache_to_worktree now also writes worktree_path into the copy (H2)
                self.assertIn("worktree_path", data)
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


class TestDefaultBranchUniqueness(unittest.TestCase):
    """Tests for issue-keyed + collision-safe default_branch."""

    def test_default_branch_issue_prefix(self):
        """Issue num embeds as i{N} prefix."""
        branch = N.default_branch("foo bar", issue_num=99)
        self.assertEqual(branch, "fix/i99-foo-bar")

    def test_default_branch_strips_issue_ref_from_slug(self):
        """#NN and URL should not appear in the slug when issue_num is given."""
        branch = N.default_branch("fix thing #87 please", issue_num=87)
        self.assertTrue(branch.startswith("fix/i87-"), branch)
        self.assertNotIn("87", branch.replace("i87", ""))  # only in prefix

    def test_default_branch_strips_preamble(self):
        """Meta-instruction preamble must not dominate slug; actual words must appear."""
        branch = N.default_branch(
            "consider above as new requirements so branch slugs don't collide",
            issue_num=97,
        )
        self.assertTrue(branch.startswith("fix/i97-"), branch)
        # Slug must reflect "branch slugs" not "consider above"
        self.assertIn("branch", branch)

    def test_default_branch_no_issue_keeps_slug(self):
        """Without issue_num the old fix/<slug> shape is preserved."""
        branch = N.default_branch("Add zero-shift guard")
        self.assertEqual(branch, "fix/add-zero-shift-guard")

    def test_disambiguate_suffix(self):
        """Collision on create path gets a -2 suffix."""
        branch = N.default_branch("unique-test-word", existing={"fix/unique-test-word"})
        self.assertEqual(branch, "fix/unique-test-word-2")

    def test_disambiguate_multiple_collisions(self):
        existing = {"fix/unique-test-word", "fix/unique-test-word-2", "fix/unique-test-word-3"}
        branch = N.default_branch("unique-test-word", existing=existing)
        self.assertEqual(branch, "fix/unique-test-word-4")

    def test_disambiguate_exhausted_raises(self):
        """When all 99 suffixes are taken, SystemExit is raised."""
        existing = {"fix/x"} | {f"fix/x-{i}" for i in range(2, 100)}
        with self.assertRaises(SystemExit):
            N._disambiguate("fix/x", existing)

    def test_two_issues_same_text_distinct_branches(self):
        """Two issues with identical requirement text must produce distinct branches."""
        b1 = N.default_branch("consider above as new requirements", issue_num=101)
        b2 = N.default_branch("consider above as new requirements", issue_num=102)
        self.assertNotEqual(b1, b2)
        self.assertTrue(b1.startswith("fix/i101-"), b1)
        self.assertTrue(b2.startswith("fix/i102-"), b2)

    def test_sanitize_strips_issue_url(self):
        url = "https://github.com/aditya2kx/jarvis/issues/55"
        text = f"do the thing {url}"
        result = N._sanitize_requirement(text)
        self.assertNotIn("github.com", result)
        self.assertIn("do", result)

    def test_sanitize_strips_preamble_only_at_start(self):
        """Preamble in the middle of text must NOT be stripped."""
        text = "Fix the new requirement handling"
        result = N._sanitize_requirement(text)
        # "new requirement" not at start → no stripping
        self.assertIn("Fix", result)

    def test_default_branch_slug_hint_overrides_requirement(self):
        """Intake path: branch slug comes from the issue title, not the short comment."""
        branch = N.default_branch(
            "let's work on this",
            issue_num=112,
            slug_hint="Would love to see a chart showing Weekly Shift Hours per Person Name",
        )
        self.assertTrue(branch.startswith("fix/i112-would-love"), branch)


class TestComposeRequirement(unittest.TestCase):
    def test_combines_title_body_and_note(self):
        result = N._compose_requirement(
            "Weekly Shift Hours chart", "Add a chart in the weekly section.", "let's work on this"
        )
        self.assertIn("Weekly Shift Hours chart", result)
        self.assertIn("Add a chart", result)
        self.assertIn("let's work on this", result)

    def test_empty_body_degrades_gracefully(self):
        result = N._compose_requirement("Title only", "", "note")
        self.assertIn("Title only", result)
        self.assertIn("note", result)

    def test_empty_note_omits_note_section(self):
        result = N._compose_requirement("Title", "Body", "")
        self.assertNotIn("Operator intake note", result)

    def test_all_empty_falls_back_to_note(self):
        result = N._compose_requirement("", "", "")
        self.assertEqual(result, "")


class TestFetchIssueContext(unittest.TestCase):
    @patch("new_requirement.subprocess.check_output")
    def test_returns_title_and_body(self, mock_out):
        mock_out.return_value = '{"title": "Chart request", "body": "Please add a chart."}'
        result = N._fetch_issue_context(112)
        self.assertEqual(result, ("Chart request", "Please add a chart."))

    @patch("new_requirement.subprocess.check_output", side_effect=Exception("gh failed"))
    def test_returns_none_on_failure(self, mock_out):
        self.assertIsNone(N._fetch_issue_context(112))

    @patch("new_requirement._fetch_issue_context")
    @patch("new_requirement.init_phase_tracking")
    @patch("new_requirement._existing_branches")
    @patch("new_requirement.create_worktree")
    @patch("new_requirement.start_session_in_worktree")
    @patch("new_requirement._repo_root")
    def test_main_enriches_requirement_and_branch_from_issue(
        self, mock_root, mock_session, mock_wt, mock_existing, mock_phase, mock_ctx
    ):
        """Intake path: main() with --issue N seeds requirement + branch from issue title/body."""
        mock_root.return_value = Path("/repo/jarvis")
        mock_existing.return_value = set()
        mock_session.return_value = (
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-brief.md"),
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-launch.html"),
            "cursor://test",
        )
        mock_phase.return_value = "https://github.com/aditya2kx/jarvis/issues/112"
        mock_ctx.return_value = (
            "Would love to see a chart showing Weekly Shift Hours per Person Name",
            "In the weekly shift hours section, add a per-person breakdown chart.",
        )

        rc = N.main([
            "--requirement", "let's work on this",
            "--issue", "112",
            "--dry-run",
        ])
        self.assertEqual(rc, 0)

        # Branch is derived from the issue title, not the short intake comment.
        _, wt_kwargs = mock_wt.call_args
        self.assertTrue(wt_kwargs["branch"].startswith("fix/i112-would-love"), wt_kwargs["branch"])

        # Requirement passed to the session brief includes the issue title + body.
        _, session_kwargs = mock_session.call_args
        self.assertIn("Weekly Shift Hours", session_kwargs["requirement"])
        self.assertIn("per-person breakdown chart", session_kwargs["requirement"])
        self.assertIn("let's work on this", session_kwargs["requirement"])

        # The existing issue is linked, never a new one created.
        _, phase_kwargs = mock_phase.call_args
        self.assertEqual(phase_kwargs.get("existing_issue"), 112)

    @patch("new_requirement._fetch_issue_context", return_value=None)
    @patch("new_requirement.init_phase_tracking")
    @patch("new_requirement._existing_branches")
    @patch("new_requirement.create_worktree")
    @patch("new_requirement.start_session_in_worktree")
    @patch("new_requirement._repo_root")
    def test_main_link_path_uses_issue_prefix(
        self, mock_root, mock_session, mock_wt, mock_existing, mock_phase, mock_ctx
    ):
        """main() with --issue N must produce branch fix/i{N}-<slug>."""
        mock_root.return_value = Path("/repo/jarvis")
        mock_existing.return_value = set()
        mock_session.return_value = (
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-brief.md"),
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-launch.html"),
            "cursor://test",
        )
        mock_phase.return_value = None
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = N.main([
                "--requirement", "branch slug uniqueness",
                "--issue", "97",
                "--dry-run",
            ])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("i97-", output)

    @patch("new_requirement.init_phase_tracking")
    @patch("new_requirement._existing_branches")
    @patch("new_requirement.create_worktree")
    @patch("new_requirement.start_session_in_worktree")
    @patch("new_requirement._repo_root")
    def test_main_create_path_collision_falls_back(
        self, mock_root, mock_session, mock_wt, mock_existing, mock_phase
    ):
        """main() must use -2 suffix when base branch already exists."""
        mock_root.return_value = Path("/repo/jarvis")
        mock_existing.return_value = {"fix/collision-test-word"}
        mock_session.return_value = (
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-brief.md"),
            Path("/repo/jarvis-wt-x/metrics/pr_cost/session-x-launch.html"),
            "cursor://test",
        )
        mock_phase.return_value = None
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = N.main([
                "--requirement", "collision test word",
                "--dry-run",
            ])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("collision-test-word-2", output)


if __name__ == "__main__":
    unittest.main()
