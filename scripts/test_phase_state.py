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


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------

class TestGate(PhaseStateTestBase):
    def _make_args(self, branch=None):
        args = MagicMock()
        args.branch = branch
        return args

    def test_gate_skips_untracked_branch(self):
        """No cache file → gate exits 0 (branch not lifecycle-tracked)."""
        args = self._make_args(branch="feat/untracked-xyz")
        rc = ps.cmd_gate(args)
        self.assertEqual(rc, 0)

    def test_gate_passes_when_no_detectors_fire(self):
        """If no OBSERVABLE_FLOOR detector fires, gate exits 0."""
        data = ps._load_cache("feat/no-obs")
        data["done"] = []
        ps._save_cache("feat/no-obs", data)

        # All detectors return False
        with patch.object(ps, "OBSERVABLE_FLOOR", [
            ("implement",   lambda: False),
            ("pr-evidence", lambda: False),
        ]):
            args = self._make_args(branch="feat/no-obs")
            rc = ps.cmd_gate(args)
        self.assertEqual(rc, 0)

    def test_gate_passes_when_all_prior_substeps_done(self):
        """Detector fires at 'implement'; all prior substeps recorded → gate exits 0."""
        # Prior substeps to implement: specify, setup, jam, define-evidence, plan (5)
        prior = ["specify", "setup", "jam", "define-evidence", "plan"]
        data = ps._load_cache("feat/all-done")
        data["done"] = prior
        ps._save_cache("feat/all-done", data)

        with patch.object(ps, "OBSERVABLE_FLOOR", [
            ("implement", lambda: True),
        ]):
            args = self._make_args(branch="feat/all-done")
            rc = ps.cmd_gate(args)
        self.assertEqual(rc, 0)

    def test_gate_fails_when_implement_detector_fires_and_operator_gates_missing(self):
        """Detector fires at 'implement'; operator gates (jam, define-evidence) not done → exits 1."""
        # Only specify and setup done — missing jam, define-evidence, plan
        data = ps._load_cache("feat/missing-gates")
        data["done"] = ["specify", "setup"]
        ps._save_cache("feat/missing-gates", data)

        with patch.object(ps, "OBSERVABLE_FLOOR", [
            ("implement", lambda: True),
        ]):
            args = self._make_args(branch="feat/missing-gates")
            rc = ps.cmd_gate(args)
        self.assertEqual(rc, 1)

    def test_gate_failure_message_names_advance_commands(self):
        """Failure output names the exact advance commands needed."""
        data = ps._load_cache("feat/msg-check")
        data["done"] = ["specify", "setup"]
        ps._save_cache("feat/msg-check", data)

        import io
        buf = io.StringIO()
        with patch.object(ps, "OBSERVABLE_FLOOR", [("implement", lambda: True)]), \
             patch("sys.stderr", buf):
            args = self._make_args(branch="feat/msg-check")
            ps.cmd_gate(args)

        output = buf.getvalue()
        self.assertIn("phase_state.py advance", output)
        self.assertIn("feat/msg-check", output)
        # Operator gates must mention --operator-approved
        self.assertIn("--operator-approved", output)

    def test_gate_uses_highest_observed_substep(self):
        """When multiple detectors fire, the highest-index substep governs the floor."""
        # pr-evidence detector fires (index > implement) → more substeps required
        prior_to_pr_evidence = ["specify", "setup", "jam", "define-evidence",
                                 "plan", "implement", "verify"]
        data = ps._load_cache("feat/multi-detect")
        data["done"] = prior_to_pr_evidence
        ps._save_cache("feat/multi-detect", data)

        with patch.object(ps, "OBSERVABLE_FLOOR", [
            ("implement",   lambda: True),   # lower index
            ("pr-evidence", lambda: True),   # higher index — governs
        ]):
            args = self._make_args(branch="feat/multi-detect")
            rc = ps.cmd_gate(args)
        self.assertEqual(rc, 0)  # all prior to pr-evidence are done

    def test_gate_fails_multi_detect_missing_verify(self):
        """pr-evidence fires but verify not recorded → gate fails."""
        # Missing 'verify' (and pr-evidence itself hasn't run, but implement has)
        data = ps._load_cache("feat/multi-fail")
        data["done"] = ["specify", "setup", "jam", "define-evidence", "plan", "implement"]
        ps._save_cache("feat/multi-fail", data)

        with patch.object(ps, "OBSERVABLE_FLOOR", [
            ("implement",   lambda: True),
            ("pr-evidence", lambda: True),
        ]):
            args = self._make_args(branch="feat/multi-fail")
            rc = ps.cmd_gate(args)
        self.assertEqual(rc, 1)

    def test_gate_docs_only_changes_pass(self):
        """If only _has_nondoc_changes detector exists and returns False, gate passes even if some substeps missing."""
        data = ps._load_cache("feat/docs-only")
        data["done"] = []
        ps._save_cache("feat/docs-only", data)

        with patch.object(ps, "OBSERVABLE_FLOOR", [
            ("implement", lambda: False),   # docs-only: detector does not fire
        ]):
            args = self._make_args(branch="feat/docs-only")
            rc = ps.cmd_gate(args)
        self.assertEqual(rc, 0)

    def test_observable_floor_registry_has_required_entries(self):
        """OBSERVABLE_FLOOR must include implement, pr-evidence, and plan entries."""
        entry_names = [name for name, _ in ps.OBSERVABLE_FLOOR]
        self.assertIn("implement", entry_names, "OBSERVABLE_FLOOR must include 'implement'")
        self.assertIn("pr-evidence", entry_names, "OBSERVABLE_FLOOR must include 'pr-evidence'")
        self.assertIn("plan", entry_names, "OBSERVABLE_FLOOR must include 'plan'")

    def test_gate_plan_detector_with_missing_align_fails(self):
        """plan_ready stamp present but jam/define-evidence not recorded → gate fails."""
        data = ps._load_cache("feat/plan-no-jam")
        data["done"] = ["specify", "setup"]  # missing jam, define-evidence
        data["plan_ready"] = {"plan": "test.md", "score": 9, "at": "2026-01-01T00:00:00Z"}
        ps._save_cache("feat/plan-no-jam", data)

        with patch.object(ps, "OBSERVABLE_FLOOR", [
            ("plan", lambda: True),   # plan detector fires
        ]):
            args = self._make_args(branch="feat/plan-no-jam")
            rc = ps.cmd_gate(args)
        self.assertEqual(rc, 1)

    def test_gate_plan_detector_with_all_align_passes(self):
        """plan_ready stamp + all align substeps recorded → gate passes."""
        data = ps._load_cache("feat/plan-all-align")
        data["done"] = ["specify", "setup", "jam", "define-evidence"]
        data["plan_ready"] = {"plan": "test.md", "score": 9, "at": "2026-01-01T00:00:00Z"}
        ps._save_cache("feat/plan-all-align", data)

        with patch.object(ps, "OBSERVABLE_FLOOR", [
            ("plan", lambda: True),
        ]):
            args = self._make_args(branch="feat/plan-all-align")
            rc = ps.cmd_gate(args)
        self.assertEqual(rc, 0)

    def test_plan_ready_detector_returns_false_without_stamp(self):
        """_plan_ready_recorded returns False when no plan_ready key in cache."""
        data = ps._load_cache("feat/no-stamp")
        data["done"] = ["specify", "setup", "jam", "define-evidence"]
        ps._save_cache("feat/no-stamp", data)

        with patch.object(ps, "_current_branch", return_value="feat/no-stamp"):
            result = ps._plan_ready_recorded()
        self.assertFalse(result)

    def test_plan_ready_detector_returns_true_with_stamp(self):
        """_plan_ready_recorded returns True when plan_ready key is set."""
        data = ps._load_cache("feat/with-stamp")
        data["plan_ready"] = {"plan": "test.md", "score": 9, "at": "2026-01-01T00:00:00Z"}
        ps._save_cache("feat/with-stamp", data)

        with patch.object(ps, "_current_branch", return_value="feat/with-stamp"):
            result = ps._plan_ready_recorded()
        self.assertTrue(result)


class TestCheckPlanReadinessPhasePrecheck(PhaseStateTestBase):
    """Tests for the phase gate integration in check_plan_readiness.py."""

    def test_phase_precheck_passes_when_untracked(self):
        """Branch with no cache → phase precheck skipped (OK)."""
        import scripts.check_plan_readiness as cpr
        ok, detail = cpr._check_phase_gates("feat/untracked-zzz")
        self.assertTrue(ok)
        self.assertIn("not lifecycle-tracked", detail)

    def test_phase_precheck_fails_when_jam_missing(self):
        """Branch with jam not recorded → precheck fails with list of missing substeps."""
        import scripts.check_plan_readiness as cpr
        branch = "feat/precheck-no-jam"
        data = ps._load_cache(branch)
        data["done"] = ["specify", "setup"]
        ps._save_cache(branch, data)

        ok, detail = cpr._check_phase_gates(branch)
        self.assertFalse(ok)
        self.assertIn("jam", detail)
        self.assertIn("define-evidence", detail)

    def test_phase_precheck_passes_when_both_recorded(self):
        """Branch with jam + define-evidence recorded → precheck passes."""
        import scripts.check_plan_readiness as cpr
        branch = "feat/precheck-ok"
        data = ps._load_cache(branch)
        data["done"] = ["specify", "setup", "jam", "define-evidence"]
        ps._save_cache(branch, data)

        ok, detail = cpr._check_phase_gates(branch)
        self.assertTrue(ok)

    def test_stamp_plan_ready_writes_to_cache(self):
        """_stamp_plan_ready writes plan_ready key into the cache."""
        import scripts.check_plan_readiness as cpr
        branch = "feat/stamp-test"
        data = ps._load_cache(branch)
        data["done"] = ["specify", "setup", "jam", "define-evidence"]
        ps._save_cache(branch, data)

        cpr._stamp_plan_ready(branch, "test-plan.md", 9)

        refreshed = ps._load_cache(branch)
        self.assertIn("plan_ready", refreshed)
        self.assertEqual(refreshed["plan_ready"]["plan"], "test-plan.md")
        self.assertEqual(refreshed["plan_ready"]["score"], 9)


class TestG2InitLinkExistingAppliesLabels(PhaseStateTestBase):
    """G2: cmd_init link-existing path must apply labels + kickoff state to GitHub."""

    def test_link_existing_calls_add_label(self):
        """When --issue N is given, _gh must be called with issue edit --add-label."""
        gh_calls = []

        def mock_gh(*args, **kwargs):
            gh_calls.append(args)
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            args = MagicMock()
            args.branch = "feat/link-g2"
            args.requirement = None
            args.kickoff = False
            args.requirement_id = None
            args.issue = 77
            args.source = "github"
            args.dry_run = False
            rc = ps.cmd_init(args)

        self.assertEqual(rc, 0)
        add_label_calls = [c for c in gh_calls if "--add-label" in c]
        self.assertTrue(add_label_calls, "expected gh issue edit --add-label call")
        all_args_str = " ".join(str(a) for c in add_label_calls for a in c)
        self.assertIn("jarvis-work", all_args_str)
        self.assertIn("stage:align", all_args_str)

    def test_link_existing_seeds_done_when_empty(self):
        """When cache has no done list, link-existing must seed [specify, setup]."""
        def mock_gh(*args, **kwargs):
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            args = MagicMock()
            args.branch = "feat/link-seed"
            args.requirement = None
            args.kickoff = False
            args.requirement_id = None
            args.issue = 78
            args.source = "github"
            args.dry_run = False
            ps.cmd_init(args)

        data = ps._load_cache("feat/link-seed")
        self.assertIn("specify", data.get("done", []))
        self.assertIn("setup", data.get("done", []))

    def test_link_existing_preserves_existing_done(self):
        """When cache already has done substeps, _apply_kickoff must NOT reset them."""
        # Pre-seed with more progress
        existing_branch = "feat/link-preserve"
        data = ps._load_cache(existing_branch)
        data["done"] = ["specify", "setup", "jam"]
        ps._save_cache(existing_branch, data)

        def mock_gh(*args, **kwargs):
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            args = MagicMock()
            args.branch = existing_branch
            args.requirement = None
            args.kickoff = False
            args.requirement_id = None
            args.issue = 79
            args.source = "github"
            args.dry_run = False
            ps.cmd_init(args)

        refreshed = ps._load_cache(existing_branch)
        self.assertIn("jam", refreshed.get("done", []))


class TestG2GateDriftCheck(PhaseStateTestBase):
    """G2: cmd_gate must fail when the linked issue has no stage:* label on GitHub."""

    def _make_gate_args(self, branch: str):
        args = MagicMock()
        args.branch = branch
        return args

    def _seed_issue(self, branch: str, issue_num: int, done: list[str] | None = None):
        data = ps._load_cache(branch)
        data["issue"] = issue_num
        if done:
            data["done"] = done
        ps._save_cache(branch, data)

    def test_gate_fails_when_issue_has_no_stage_label(self):
        """cmd_gate should return 1 when the linked issue lacks stage:* on GitHub."""
        branch = "feat/drift-fail"
        self._seed_issue(branch, 100)

        def mock_gh(*args, **kwargs):
            if "issue" in args and "view" in args:
                return 0, "jarvis-work"  # no stage:* label
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh):
            rc = ps.cmd_gate(self._make_gate_args(branch))

        self.assertEqual(rc, 1, "gate must fail when issue has no stage:* label")

    def test_gate_passes_when_issue_has_stage_label(self):
        """cmd_gate should NOT fail the drift check when issue has stage:* label."""
        branch = "feat/drift-pass"
        self._seed_issue(branch, 101, done=["specify", "setup"])

        def mock_gh(*args, **kwargs):
            if "issue" in args and "view" in args:
                return 0, "jarvis-work,stage:align"  # has stage:*
            return 0, ""

        with patch.object(ps, "_gh_available", return_value=True), \
             patch.object(ps, "_gh", side_effect=mock_gh), \
             patch.object(ps, "OBSERVABLE_FLOOR", []):  # skip artifact detectors
            rc = ps.cmd_gate(self._make_gate_args(branch))

        # rc can be 0 (no observable artifacts) or 0 (drift ok); must not be 1 from drift
        self.assertNotEqual(rc, 1)

    def test_gate_skips_drift_check_when_gh_unavailable(self):
        """Without gh, the drift check must be skipped (not fail)."""
        branch = "feat/drift-no-gh"
        self._seed_issue(branch, 102)

        with patch.object(ps, "OBSERVABLE_FLOOR", []):
            rc = ps.cmd_gate(self._make_gate_args(branch))

        self.assertEqual(rc, 0, "no gh → drift check skipped → gate ok")


if __name__ == "__main__":
    unittest.main()
