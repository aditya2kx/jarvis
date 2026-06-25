#!/usr/bin/env python3
"""Tests for scripts/verify.py — gate selection, exit codes, CI parity."""

from __future__ import annotations

import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts/ to path so we can import verify directly
sys.path.insert(0, str(Path(__file__).parent))

import verify as v  # noqa: E402  (after sys.path tweak)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_gate_names(mode: str) -> list[str]:
    """Return the names of gates selected for a given mode."""
    return [g.name for g in v.GATES if mode in g.modes]


def _make_fake_pr_subprocess(pr_number: str = "42"):
    """Return a mock subprocess.run that returns a fake PR number."""
    def fake_run(cmd, **kwargs):
        result = MagicMock()
        if "gh" in cmd and "pr" in cmd and "view" in cmd:
            result.returncode = 0
            result.stdout = pr_number + "\n"
        else:
            result.returncode = 1
            result.stdout = ""
        result.stderr = ""
        return result
    return fake_run


def _make_no_pr_subprocess():
    """Return a mock subprocess.run that signals no open PR."""
    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = ""
        return result
    return fake_run


# ---------------------------------------------------------------------------
# Gate selection
# ---------------------------------------------------------------------------

class TestGateSelection(unittest.TestCase):
    def test_fast_mode_gate_selection(self):
        names = _run_gate_names("fast")
        self.assertIn("secret-scan-staged", names)
        self.assertIn("doc-freshness", names)
        self.assertIn("pytest-changed", names)
        # full-only gates must NOT appear in fast
        self.assertNotIn("pytest-full", names)
        self.assertNotIn("pr-description", names)
        self.assertNotIn("pr-review-replies", names)
        self.assertNotIn("doc-freshness-base", names)
        self.assertNotIn("plan-readiness", names)
        self.assertNotIn("secret-scan-full", names)

    def test_full_mode_gate_selection(self):
        names = _run_gate_names("full")
        self.assertIn("secret-scan-full", names)
        self.assertIn("doc-freshness-base", names)
        self.assertIn("pytest-full", names)
        self.assertIn("plan-readiness", names)
        self.assertIn("pr-description", names)
        self.assertIn("pr-review-replies", names)
        # fast-only gates must NOT appear in full
        self.assertNotIn("pytest-changed", names)
        self.assertNotIn("doc-freshness", names)
        self.assertNotIn("secret-scan-staged", names)

    def test_all_gates_have_valid_modes(self):
        valid = {"fast", "full"}
        for gate in v.GATES:
            self.assertTrue(gate.modes.issubset(valid),
                            f"Gate {gate.name!r} has invalid modes: {gate.modes}")
            self.assertGreater(len(gate.modes), 0,
                               f"Gate {gate.name!r} has no modes")


# ---------------------------------------------------------------------------
# Hard vs nudge exit codes
# ---------------------------------------------------------------------------

class TestExitCodes(unittest.TestCase):
    def _run_with_mock(self, mode, mock_fn, plan_path=None, strict=False):
        """Run verify.run() with subprocess.run mocked."""
        with patch("verify.subprocess.run", side_effect=mock_fn), \
             patch("verify._get_pr_number", return_value=None), \
             patch("verify._print_phase_state"):
            return v.run(mode, plan_path, strict)

    def test_hard_gate_fails_nonzero(self):
        """A hard gate returning nonzero makes run() return 1."""
        def always_fail(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stdout = "error output"
            m.stderr = ""
            return m

        with patch("verify._changed_test_files", return_value=["scripts/test_verify.py"]):
            code = self._run_with_mock("fast", always_fail)
        self.assertEqual(code, 1)

    def test_nudge_gate_does_not_fail(self):
        """A nudge gate (doc-freshness) failing must NOT cause run() to return 1
        when all hard gates pass."""
        call_log = []

        def selective_fail(cmd, **kwargs):
            m = MagicMock()
            cmd_str = " ".join(cmd)
            if "check_doc_freshness" in cmd_str:
                m.returncode = 1
                m.stdout = "doc out of date"
                m.stderr = ""
                call_log.append("freshness-fail")
            elif "git" in cmd and "diff" in cmd:
                # secret-scan-staged: returns empty diff = no secrets
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            else:
                m.returncode = 0
                m.stdout = "passed"
                m.stderr = ""
            return m

        with patch("verify._changed_test_files", return_value=["scripts/test_verify.py"]):
            code = self._run_with_mock("fast", selective_fail)
        self.assertEqual(code, 0, "Nudge gate failure should not cause overall failure")
        self.assertIn("freshness-fail", call_log)

    def test_strict_promotes_doc_freshness_to_hard(self):
        """--strict must make doc-freshness a hard gate."""
        def freshness_fails(cmd, **kwargs):
            m = MagicMock()
            cmd_str = " ".join(cmd)
            if "check_doc_freshness" in cmd_str:
                m.returncode = 1
                m.stdout = "stale"
                m.stderr = ""
            elif "git" in cmd and "diff" in cmd:
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            else:
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            return m

        with patch("verify._changed_test_files", return_value=["scripts/test_verify.py"]):
            code = self._run_with_mock("fast", freshness_fails, strict=True)
        self.assertEqual(code, 1, "--strict should make doc-freshness failure cause exit 1")

    def test_verify_eq_0_env_skips_all(self):
        """VERIFY=0 must skip all gates and return 0."""
        with patch.dict(os.environ, {"VERIFY": "0"}):
            with patch("verify._print_phase_state"):
                code = v.run("full", None, False)
        self.assertEqual(code, 0)


# ---------------------------------------------------------------------------
# PR gates skipped when no PR
# ---------------------------------------------------------------------------

class TestPRGateSkipping(unittest.TestCase):
    def test_pr_gates_skipped_without_pr(self):
        """pr-description and pr-review-replies must be SKIP when no PR is open."""
        skipped = []
        ran = []

        def tracking_run(cmd, **kwargs):
            m = MagicMock()
            name = " ".join(cmd)
            if "check_pr_description" in name or "check_pr_review_replies" in name:
                ran.append(name)
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            elif cmd[0] == "rg":
                m.returncode = 1  # no secrets
                m.stdout = ""
                m.stderr = ""
            else:
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            return m

        with patch("verify.subprocess.run", side_effect=tracking_run), \
             patch("verify._get_pr_number", return_value=None), \
             patch("verify._print_phase_state"):
            code = v.run("full", None, False)

        self.assertEqual(ran, [], "PR gates must not run when there is no open PR")
        self.assertEqual(code, 0)

    def test_pr_gates_run_with_pr(self):
        """pr-description and pr-review-replies must run when a PR exists."""
        ran = []

        def tracking_run(cmd, **kwargs):
            m = MagicMock()
            name = " ".join(cmd)
            if "check_pr_description" in name or "check_pr_review_replies" in name:
                ran.append(name)
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            elif cmd[0] == "rg":
                m.returncode = 1
                m.stdout = ""
                m.stderr = ""
            else:
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            return m

        with patch("verify.subprocess.run", side_effect=tracking_run), \
             patch("verify._get_pr_number", return_value="99"), \
             patch("verify._print_phase_state"):
            v.run("full", None, False)

        self.assertTrue(any("check_pr_description" in r for r in ran),
                        "check_pr_description must run when PR exists")
        self.assertTrue(any("check_pr_review_replies" in r for r in ran),
                        "check_pr_review_replies must run when PR exists")


# ---------------------------------------------------------------------------
# Plan gate skipped when no plan path
# ---------------------------------------------------------------------------

class TestPlanGateSkipping(unittest.TestCase):
    def test_plan_gate_skipped_without_path(self):
        ran = []

        def tracking_run(cmd, **kwargs):
            m = MagicMock()
            if "check_plan_readiness" in " ".join(cmd):
                ran.append(cmd)
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            if cmd[0] == "rg":
                m.returncode = 1
            return m

        with patch("verify.subprocess.run", side_effect=tracking_run), \
             patch("verify._get_pr_number", return_value=None), \
             patch("verify._print_phase_state"):
            v.run("full", None, False)

        self.assertEqual(ran, [], "plan-readiness must not run without --plan")

    def test_plan_gate_runs_with_path(self):
        ran = []

        def tracking_run(cmd, **kwargs):
            m = MagicMock()
            if "check_plan_readiness" in " ".join(cmd):
                ran.append(cmd)
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            if cmd[0] == "rg":
                m.returncode = 1
            return m

        with patch("verify.subprocess.run", side_effect=tracking_run), \
             patch("verify._get_pr_number", return_value=None), \
             patch("verify._print_phase_state"):
            v.run("full", "/tmp/test.plan.md", False)

        self.assertTrue(any("check_plan_readiness" in " ".join(r) for r in ran))
        self.assertTrue(any("/tmp/test.plan.md" in " ".join(r) for r in ran))


# ---------------------------------------------------------------------------
# CI parity — the local harness must cover the scripts CI runs
# ---------------------------------------------------------------------------

class TestCIParity(unittest.TestCase):
    def test_ci_parity(self):
        """Verify that GATES in --full mode covers all scripts that CI runs.

        CI_SCRIPT_NAMES is the authoritative list; if a script is removed from
        CI, update CI_SCRIPT_NAMES.  pr_cost_ledger.py is intentionally excluded
        because the cost gate is handled by pr-workflow.mdc, not verify.py.
        """
        full_gate_scripts: set[str] = set()
        for gate in v.GATES:
            if "full" in gate.modes:
                for token in gate.argv:
                    if token.endswith(".py"):
                        full_gate_scripts.add(token.split("/")[-1])

        for script in v.CI_SCRIPT_NAMES:
            if script == "pr_cost_ledger.py":
                continue  # intentionally excluded (see module docstring)
            self.assertIn(
                script, full_gate_scripts,
                f"CI runs {script!r} but verify.py --full doesn't cover it. "
                "Add a Gate or update CI_SCRIPT_NAMES."
            )


# ---------------------------------------------------------------------------
# Secret scan detection
# ---------------------------------------------------------------------------

class TestSecretScan(unittest.TestCase):
    def test_secret_scan_pattern_in_constant(self):
        """The SECRET_PATTERN constant must include the key signatures from CONTRIBUTING.md."""
        self.assertIn("AIza", v.SECRET_PATTERN)
        self.assertIn("-----BEGIN", v.SECRET_PATTERN)
        self.assertIn("password", v.SECRET_PATTERN)

    def test_secret_scan_uses_git_diff(self):
        """secret-scan gates must use 'git diff' (diff-based, not whole-repo scan)."""
        staged_gate = next(g for g in v.GATES if g.name == "secret-scan-staged")
        full_gate = next(g for g in v.GATES if g.name == "secret-scan-full")
        self.assertEqual(staged_gate.argv[:3], ["git", "diff", "--cached"])
        self.assertEqual(full_gate.argv[:2], ["git", "diff"])


if __name__ == "__main__":
    unittest.main()
