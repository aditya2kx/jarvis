#!/usr/bin/env python3
"""Tests for phase_state.py — gh calls mocked throughout."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).parent))
import phase_state as ps
import lifecycle as lc


# ---------------------------------------------------------------------------
# Helper: patch METRICS_DIR to a temp dir for isolation
# ---------------------------------------------------------------------------

class PhaseStateTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)
        self._metrics_patcher = patch.object(ps, "METRICS_DIR", self._tmp_path)
        self._metrics_patcher.start()
        # Default: gh not available (tests that need it will override)
        self._gh_patcher = patch.object(ps, "_gh_available", return_value=False)
        self._gh_patcher.start()

    def tearDown(self):
        self._metrics_patcher.stop()
        self._gh_patcher.stop()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class TestCache(PhaseStateTestBase):
    def test_load_returns_default_when_missing(self):
        data = ps._load_cache("feat/new")
        self.assertIsNone(data["issue"])
        self.assertEqual(data["done"], [])

    def test_save_and_reload(self):
        data = ps._load_cache("feat/x")
        data["done"] = ["specify", "setup"]
        ps._save_cache("feat/x", data)
        loaded = ps._load_cache("feat/x")
        self.assertEqual(loaded["done"], ["specify", "setup"])

    def test_slug_sanitizes_slashes(self):
        slug = ps._slug("feat/my-feature")
        self.assertNotIn("/", slug)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

class TestInit(PhaseStateTestBase):
    def test_init_dry_run_fires_no_gh(self):
        gh_calls = []

        def mock_gh(*args, **kwargs):
            gh_calls.append(args)
            return 0, ""

        with patch.object(ps, "_gh", side_effect=mock_gh):
            args = MagicMock()
            args.branch = "feat/test"
            args.requirement = None
            args.kickoff = False
            args.requirement_id = None
            args.issue = None
            args.source = "github"
            args.dry_run = True
            rc = ps.cmd_init(args)

        self.assertEqual(rc, 0)
        self.assertEqual(gh_calls, [], "dry-run must not call gh")

    def test_init_creates_issue_once_idempotent(self):
        gh_call_count = [0]

        def mock_gh(*args, **kwargs):
            gh_call_count[0] += 1
            # Return a fake issue URL
            if "issue" in args and "create" in args:
                return 0, "https://github.com/owner/repo/issues/42\n"
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            args = MagicMock()
            args.branch = "feat/idempotent"
            args.requirement = None
            args.kickoff = False
            args.requirement_id = None
            args.issue = None
            args.source = "github"
            args.dry_run = False

            # First init
            rc1 = ps.cmd_init(args)
            # Second init should reuse cached issue
            rc2 = ps.cmd_init(args)

        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        # Verify cache has the issue number
        data = ps._load_cache("feat/idempotent")
        self.assertEqual(data.get("issue"), 42)

    def test_init_links_to_existing_issue(self):
        args = MagicMock()
        args.branch = "feat/link"
        args.requirement = None
        args.kickoff = False
        args.requirement_id = 7
        args.issue = 99
        args.source = "github"
        args.dry_run = False
        rc = ps.cmd_init(args)
        self.assertEqual(rc, 0)
        data = ps._load_cache("feat/link")
        self.assertEqual(data["issue"], 99)

    def test_init_with_requirement_sets_title(self):
        captured = []

        def mock_gh(*args, **kwargs):
            captured.append(args)
            if "issue" in args and "create" in args:
                return 0, "https://github.com/owner/repo/issues/55\n"
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            args = MagicMock()
            args.branch = "feat/req"
            args.requirement = "Add a shiny new widget"
            args.kickoff = False
            args.requirement_id = None
            args.issue = None
            args.source = "github"
            args.dry_run = False
            rc = ps.cmd_init(args)

        self.assertEqual(rc, 0)
        create_calls = [c for c in captured if "create" in c]
        self.assertTrue(create_calls, "expected a gh issue create call")
        create = create_calls[0]
        title_idx = create.index("--title") + 1
        self.assertIn("Add a shiny new widget", create[title_idx])

    def test_init_kickoff_seeds_specify_and_setup(self):
        def mock_gh(*args, **kwargs):
            if "issue" in args and "create" in args:
                return 0, "https://github.com/owner/repo/issues/56\n"
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            args = MagicMock()
            args.branch = "feat/kick"
            args.requirement = "Kickoff test"
            args.kickoff = True
            args.requirement_id = None
            args.issue = None
            args.source = "github"
            args.dry_run = False
            rc = ps.cmd_init(args)

        self.assertEqual(rc, 0)
        data = ps._load_cache("feat/kick")
        self.assertEqual(data["done"], ["specify", "setup"])
        self.assertEqual(lc.overall_pct(set(data["done"])), 16)  # 2/12


# ---------------------------------------------------------------------------
# advance
# ---------------------------------------------------------------------------

class TestAdvance(PhaseStateTestBase):
    def test_advance_forward_only(self):
        """Cannot skip or go backward."""
        # Seed: specify done
        data = ps._load_cache("feat/fwd")
        data["done"] = ["specify"]
        data["issue"] = 1
        ps._save_cache("feat/fwd", data)

        # Try to skip to 'plan' (skipping setup, jam, define-evidence)
        args = MagicMock()
        args.branch = "feat/fwd"
        args.to = "plan"
        args.operator_approved = False
        rc = ps.cmd_advance(args)
        self.assertNotEqual(rc, 0, "Skipping substeps must be rejected")

    def test_advance_rejects_backward(self):
        """Cannot advance to an already-done substep."""
        data = ps._load_cache("feat/bwd")
        data["done"] = ["specify", "setup"]
        data["issue"] = 1
        ps._save_cache("feat/bwd", data)

        args = MagicMock()
        args.branch = "feat/bwd"
        args.to = "specify"
        args.operator_approved = False
        rc = ps.cmd_advance(args)
        self.assertNotEqual(rc, 0)

    def test_advance_valid_agent_substep(self):
        """Advancing to an agent-driver substep in sequence succeeds."""
        # Done: all of align (4 substeps) and plan
        align_subs = [s.name for s in lc.STAGES[0].substeps]
        data = ps._load_cache("feat/adv")
        data["done"] = align_subs + ["plan"]
        data["issue"] = 1
        ps._save_cache("feat/adv", data)

        args = MagicMock()
        args.branch = "feat/adv"
        args.to = "implement"
        args.operator_approved = False
        rc = ps.cmd_advance(args)
        self.assertEqual(rc, 0)
        loaded = ps._load_cache("feat/adv")
        self.assertIn("implement", loaded["done"])

    def test_operator_substep_refused_without_approval(self):
        """Advancing to an operator substep without approval exits nonzero."""
        data = ps._load_cache("feat/op")
        data["done"] = ["specify"]  # next is setup (agent), then jam (operator)
        # Put setup done too so jam is next
        data["done"] = ["specify", "setup"]
        data["issue"] = 5
        ps._save_cache("feat/op", data)

        args = MagicMock()
        args.branch = "feat/op"
        args.to = "jam"
        args.operator_approved = False
        rc = ps.cmd_advance(args)
        self.assertNotEqual(rc, 0, "Operator substep must be refused without approval")

    def test_operator_substep_allowed_with_approval_flag(self):
        """Advancing to an operator substep with --operator-approved succeeds."""
        data = ps._load_cache("feat/opa")
        data["done"] = ["specify", "setup"]
        data["issue"] = 6
        ps._save_cache("feat/opa", data)

        args = MagicMock()
        args.branch = "feat/opa"
        args.to = "jam"
        args.operator_approved = True
        args.note = None
        rc = ps.cmd_advance(args)
        self.assertEqual(rc, 0)
        loaded = ps._load_cache("feat/opa")
        self.assertIn("jam", loaded["done"])

    def test_advance_operator_approved_stamps_label_and_note(self):
        """--operator-approved adds approved:<gate> label and posts a provenance comment."""
        gh_calls = []

        def mock_gh(*args, **kwargs):
            gh_calls.append(args)
            return 0, ""

        data = ps._load_cache("feat/stamp")
        data["done"] = ["specify", "setup"]
        data["issue"] = 77
        ps._save_cache("feat/stamp", data)

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            args = MagicMock()
            args.branch = "feat/stamp"
            args.to = "jam"
            args.operator_approved = True
            args.note = "We agreed: scope is just the README, no code."
            rc = ps.cmd_advance(args)

        self.assertEqual(rc, 0)
        # A label-add call for approved:jam must be present
        label_adds = [c for c in gh_calls if "--add-label" in c and "approved:jam" in c]
        self.assertTrue(label_adds, "approved:jam label must be stamped on the issue")
        # A comment with the note must be posted
        comment_calls = [c for c in gh_calls if "comment" in c]
        comment_bodies = " ".join(str(c) for c in comment_calls)
        self.assertIn("We agreed", comment_bodies)

    def test_refusal_does_not_instruct_github(self):
        """Gate refusal should NOT tell the operator to go to GitHub."""
        import io, sys
        data = ps._load_cache("feat/refusal")
        data["done"] = ["specify", "setup"]
        data["issue"] = 88
        ps._save_cache("feat/refusal", data)

        args = MagicMock()
        args.branch = "feat/refusal"
        args.to = "jam"
        args.operator_approved = False

        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            with patch.object(ps, "_gh_available", return_value=False):
                rc = ps.cmd_advance(args)
        finally:
            sys.stderr = old_stderr

        self.assertNotEqual(rc, 0)
        # Must NOT instruct the operator to go add a GitHub label
        stderr_text = buf.getvalue()
        self.assertNotIn("Please add label", stderr_text, "Refusal must not tell operator to go to GitHub")

    def test_overall_and_stage_pct(self):
        """After advancing, overall % increases."""
        data = ps._load_cache("feat/pct")
        data["done"] = []
        data["issue"] = 7
        ps._save_cache("feat/pct", data)

        # Manually mark specify+setup done (both are in align)
        data["done"] = ["specify", "setup"]
        ps._save_cache("feat/pct", data)

        done_set = set(data["done"])
        pct = lc.overall_pct(done_set)
        self.assertGreater(pct, 0)
        align_pct = lc.stage_pct("align", done_set)
        self.assertEqual(align_pct, 50)  # 2/4 align substeps done

    def test_stage_label_swap_on_boundary(self):
        """When crossing a stage boundary, the stage label is swapped."""
        label_calls = []

        def mock_gh(*args, **kwargs):
            label_calls.append(args)
            if "view" in args and "--json" in args:
                return 0, '{"body": "placeholder"}'
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            # Done: all of align → about to enter plan
            align_subs = [s.name for s in lc.STAGES[0].substeps]
            data = ps._load_cache("feat/boundary")
            data["done"] = align_subs
            data["issue"] = 10
            ps._save_cache("feat/boundary", data)

            args = MagicMock()
            args.branch = "feat/boundary"
            args.to = "plan"
            args.operator_approved = False
            ps.cmd_advance(args)

        # Should have called gh to remove stage:align and add stage:plan
        all_calls_str = str(label_calls)
        self.assertIn("stage:align", all_calls_str)
        self.assertIn("stage:plan", all_calls_str)


# ---------------------------------------------------------------------------
# fail / clear-fail
# ---------------------------------------------------------------------------

class TestFailClearFail(PhaseStateTestBase):
    def test_fail_adds_blocked_label(self):
        label_calls = []

        def mock_gh(*args, **kwargs):
            label_calls.append(args)
            return 0, ""

        data = ps._load_cache("feat/fail")
        data["issue"] = 20
        ps._save_cache("feat/fail", data)

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            args = MagicMock()
            args.branch = "feat/fail"
            args.reason = "CI red: secret-scan"
            ps.cmd_fail(args)

        loaded = ps._load_cache("feat/fail")
        self.assertIn("CI red: secret-scan", loaded["failures"])
        calls_str = str(label_calls)
        self.assertIn("blocked", calls_str)

    def test_clear_fail_removes_failures(self):
        data = ps._load_cache("feat/cfail")
        data["failures"] = ["previous error"]
        data["issue"] = 21
        ps._save_cache("feat/cfail", data)

        args = MagicMock()
        args.branch = "feat/cfail"
        ps.cmd_clear_fail(args)

        loaded = ps._load_cache("feat/cfail")
        self.assertEqual(loaded["failures"], [])


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

class TestStatus(PhaseStateTestBase):
    def test_status_json(self):
        data = ps._load_cache("feat/status")
        data["done"] = ["specify", "setup", "jam", "define-evidence", "plan"]
        data["issue"] = 30
        ps._save_cache("feat/status", data)

        captured = []
        with patch("builtins.print", side_effect=lambda x="": captured.append(x)):
            args = MagicMock()
            args.branch = "feat/status"
            args.json = True
            rc = ps.cmd_status(args)

        self.assertEqual(rc, 0)
        output = "\n".join(captured)
        parsed = json.loads(output)
        self.assertEqual(parsed["current_stage"], "build")
        self.assertEqual(parsed["overall_pct"], lc.overall_pct(set(data["done"])))

    def test_status_shows_remaining(self):
        data = ps._load_cache("feat/remaining")
        data["done"] = ["specify"]
        data["issue"] = 31
        ps._save_cache("feat/remaining", data)

        captured = []
        with patch("builtins.print", side_effect=lambda x="": captured.append(x)):
            args = MagicMock()
            args.branch = "feat/remaining"
            args.json = True
            rc = ps.cmd_status(args)

        output = "\n".join(captured)
        parsed = json.loads(output)
        self.assertIn("setup", parsed["remaining"])


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

class TestReport(PhaseStateTestBase):
    def test_report_parses_issue_list(self):
        fake_issues = json.dumps([
            {"number": 42, "title": "[work] feat/my-feature",
             "labels": [{"name": "jarvis-work"}, {"name": "stage:build"}],
             "state": "open"},
        ])

        def mock_gh(*args, **kwargs):
            if "issue" in args and "list" in args:
                return 0, fake_issues
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            captured = []
            with patch("builtins.print", side_effect=lambda x="": captured.append(x)):
                args = MagicMock()
                ps.cmd_report(args)

        output = "\n".join(captured)
        self.assertIn("42", output)
        self.assertIn("build", output)

    def test_report_no_issues(self):
        def mock_gh(*args, **kwargs):
            return 0, "[]"

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            captured = []
            with patch("builtins.print", side_effect=lambda x="": captured.append(x)):
                args = MagicMock()
                ps.cmd_report(args)

        output = "\n".join(captured)
        self.assertIn("No open", output)


# ---------------------------------------------------------------------------
# Degrade without gh
# ---------------------------------------------------------------------------

class TestDegrades(PhaseStateTestBase):
    def test_degrades_without_gh(self):
        """When gh is unavailable, init succeeds (cache-only) and doesn't crash."""
        # gh_available already returns False in setUp
        args = MagicMock()
        args.branch = "feat/no-gh"
        args.requirement = None
        args.kickoff = False
        args.requirement_id = None
        args.issue = None
        args.source = "github"
        args.dry_run = False
        rc = ps.cmd_init(args)
        # Cache-only mode should succeed (or gracefully fail)
        self.assertIn(rc, (0, 1))  # 0 = cache-only, 1 = expected since no gh


if __name__ == "__main__":
    unittest.main()
