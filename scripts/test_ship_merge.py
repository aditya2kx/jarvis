#!/usr/bin/env python3
"""Tests for ship_merge.py — covers the §4 evidence scenarios A-G."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ship_merge as SM


_ALLOWED = frozenset(["aditya2kx"])


def _checks(overrides: dict[str, str] | None = None) -> str:
    """Build a minimal gh pr checks JSON where all checks pass by default."""
    base = [
        {"name": "pr-description", "conclusion": "success", "state": "completed"},
        {"name": "doc-freshness", "conclusion": "success", "state": "completed"},
        {"name": "pr-cost-gate", "conclusion": "success", "state": "completed"},
        {"name": "pytest-changed", "conclusion": "success", "state": "completed"},
        {"name": "secret-scan-staged", "conclusion": "success", "state": "completed"},
        {"name": "Claude review", "conclusion": "failure", "state": "completed"},
        {"name": "Evidence confidence gate (fail if < 95%)", "conclusion": "failure", "state": "completed"},
    ]
    if overrides:
        for chk in base:
            if chk["name"] in overrides:
                chk["conclusion"] = overrides[chk["name"]]
                chk["state"] = "completed"
    return json.dumps(base)


class TestIsShipIntent(unittest.TestCase):
    # Positive cases
    def test_rocket_only(self):
        self.assertTrue(SM.is_ship_intent("🚀"))

    def test_ship_only(self):
        self.assertTrue(SM.is_ship_intent("🚢"))

    def test_rocket_ship_it(self):
        self.assertTrue(SM.is_ship_intent("🚀 ship it"))

    def test_ship_it_rocket(self):
        self.assertTrue(SM.is_ship_intent("ship it 🚀"))

    def test_ship_ship_it(self):
        self.assertTrue(SM.is_ship_intent("🚢 ship it"))

    def test_whitespace_trimmed(self):
        self.assertTrue(SM.is_ship_intent("  🚀  "))

    # Negative cases
    def test_negation_not(self):
        self.assertFalse(SM.is_ship_intent("🚀 not yet"))

    def test_negation_wait(self):
        self.assertFalse(SM.is_ship_intent("🚀 wait for review"))

    def test_negation_hold(self):
        self.assertFalse(SM.is_ship_intent("hold 🚀"))

    def test_thumbs_up_not_ship(self):
        self.assertFalse(SM.is_ship_intent("👍"))

    def test_approved_text_not_ship(self):
        self.assertFalse(SM.is_ship_intent("approved"))

    def test_empty(self):
        self.assertFalse(SM.is_ship_intent(""))

    def test_random_emoji(self):
        self.assertFalse(SM.is_ship_intent("🎉"))

    def test_lgtm_not_ship(self):
        self.assertFalse(SM.is_ship_intent("lgtm"))


class TestIsAuthorized(unittest.TestCase):
    def test_owner_in_allowlist(self):
        self.assertTrue(SM.is_authorized("aditya2kx", "OWNER", _ALLOWED))

    def test_owner_not_in_allowlist(self):
        self.assertFalse(SM.is_authorized("someone-else", "OWNER", _ALLOWED))

    def test_in_allowlist_not_owner(self):
        # Collaborator association — not enough even with correct login.
        self.assertFalse(SM.is_authorized("aditya2kx", "COLLABORATOR", _ALLOWED))

    def test_bot_not_authorized(self):
        self.assertFalse(SM.is_authorized("jarvis-agent-bot328", "COLLABORATOR", _ALLOWED))

    def test_member_not_authorized(self):
        self.assertFalse(SM.is_authorized("aditya2kx", "MEMBER", _ALLOWED))


class TestOnlyEvidenceConfidenceBlocking(unittest.TestCase):
    """Maps directly to §4 scenarios A-G."""

    # Scenario A — happy path: only evidence-confidence red, all else green
    def test_A_happy_path(self):
        result = SM.only_evidence_confidence_blocking(
            _checks(), verdict_header="APPROVE", confidence=82
        )
        self.assertFalse(result.blocked)
        self.assertEqual(result.reason, "")

    # Scenario B — unauthorized author: tested in TestIsAuthorized; no blocking check here
    # (authorization is separate from the blocking predicate)

    # Scenario D — real CI failure (pytest red)
    def test_D_real_ci_failure(self):
        failing = _checks({"pytest-changed": "failure"})
        result = SM.only_evidence_confidence_blocking(
            failing, verdict_header="APPROVE", confidence=82
        )
        self.assertTrue(result.blocked)
        self.assertIn("pytest-changed", result.reason)

    # Scenario D variant — secret scan failing
    def test_D_secret_scan_failing(self):
        failing = _checks({"secret-scan-staged": "failure"})
        result = SM.only_evidence_confidence_blocking(
            failing, verdict_header="APPROVE", confidence=70
        )
        self.assertTrue(result.blocked)
        self.assertIn("secret-scan-staged", result.reason)

    # Scenario E — REQUEST CHANGES verdict
    def test_E_request_changes(self):
        result = SM.only_evidence_confidence_blocking(
            _checks(), verdict_header="REQUEST CHANGES\nsome details", confidence=80
        )
        self.assertTrue(result.blocked)
        self.assertIn("REQUEST CHANGES", result.reason)

    # Scenario F — negation comment: tested via is_ship_intent; blocking predicate not invoked

    # Scenario G — already merged / draft: the workflow exits before calling
    # only_evidence_confidence_blocking when PR is already merged.

    # Edge: confidence already >= 95 (auto-merge should handle it, ship-emoji is a no-op)
    def test_confidence_already_passing(self):
        result = SM.only_evidence_confidence_blocking(
            _checks(), verdict_header="APPROVE", confidence=97
        )
        self.assertTrue(result.blocked)
        self.assertIn("97%", result.reason)

    # Edge: confidence None (reviewer not run) — treated as non-blocking at helper level
    def test_confidence_none_no_real_failures(self):
        result = SM.only_evidence_confidence_blocking(
            _checks(), verdict_header="APPROVE", confidence=None
        )
        self.assertFalse(result.blocked)

    # Edge: malformed JSON
    def test_malformed_checks_json(self):
        result = SM.only_evidence_confidence_blocking(
            "not-json", verdict_header="APPROVE", confidence=80
        )
        self.assertTrue(result.blocked)

    # Edge: Claude check names do not count as real failures even when red
    def test_claude_check_failure_not_real(self):
        checks_with_only_claude_red = json.dumps([
            {"name": "pr-description", "conclusion": "success", "state": "completed"},
            {"name": "Claude review", "conclusion": "failure", "state": "completed"},
            {"name": "Evidence confidence gate (fail if < 95%)", "conclusion": "failure", "state": "completed"},
            {"name": "Gate on Claude verdict (fail if REQUEST CHANGES)", "conclusion": "failure", "state": "completed"},
        ])
        result = SM.only_evidence_confidence_blocking(
            checks_with_only_claude_red, verdict_header="APPROVE", confidence=75
        )
        self.assertFalse(result.blocked)

    # Cost gate failure is a real block
    def test_pr_cost_gate_failure_blocks(self):
        failing = _checks({"pr-cost-gate": "failure"})
        result = SM.only_evidence_confidence_blocking(
            failing, verdict_header="APPROVE", confidence=80
        )
        self.assertTrue(result.blocked)
        self.assertIn("pr-cost-gate", result.reason)


if __name__ == "__main__":
    unittest.main()
