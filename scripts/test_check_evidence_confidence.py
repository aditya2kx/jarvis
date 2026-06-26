#!/usr/bin/env python3
"""Unit tests for scripts/check_evidence_confidence.py."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.check_evidence_confidence import main, parse_score, _has_waiver, _WAIVER_FLOOR


def test_parses_rating_phrasing():
    assert parse_score("### Evidence confidence rating: **85%**") == 85


def test_parses_colon_phrasing():
    assert parse_score("Evidence confidence: 96%") == 96


def test_parses_plain_rating():
    assert parse_score("Evidence confidence rating: 100%") == 100


def test_missing_score_is_none():
    assert parse_score("no score in here") is None


def test_main_fails_below_min():
    assert main(["--text", "Evidence confidence rating: **85%**", "--min", "95"]) == 1


def test_main_passes_at_or_above_min():
    assert main(["--text", "Evidence confidence rating: 96%", "--min", "95"]) == 0


def test_main_missing_is_noop():
    assert main(["--text", "nothing here", "--min", "95"]) == 0


class TestWaiverLogic(unittest.TestCase):
    def test_no_env_vars_no_waiver(self):
        """Without PR_NUMBER/GH_TOKEN set, _has_waiver() returns False."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("PR_NUMBER", "GH_TOKEN")}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(_has_waiver())

    def test_waiver_lowers_floor_to_80(self):
        """Score of 82% passes when a waiver is detected (floor lowered to 80)."""
        with patch(
            "scripts.check_evidence_confidence._has_waiver", return_value=True
        ):
            result = main(["--text", "Evidence confidence rating: 82%", "--min", "95"])
        self.assertEqual(result, 0, "82% with waiver should pass (floor=80)")

    def test_waiver_still_blocks_below_waiver_floor(self):
        """Score below 80% is blocked even with a waiver present."""
        with patch(
            "scripts.check_evidence_confidence._has_waiver", return_value=True
        ):
            result = main(["--text", "Evidence confidence rating: 75%", "--min", "95"])
        self.assertEqual(result, 1, "75% with waiver should still fail (floor=80)")

    def test_no_waiver_still_blocks_below_95(self):
        """Without waiver, score of 90% is blocked at the default 95% floor."""
        with patch(
            "scripts.check_evidence_confidence._has_waiver", return_value=False
        ):
            result = main(["--text", "Evidence confidence rating: 90%", "--min", "95"])
        self.assertEqual(result, 1, "90% without waiver should fail (floor=95)")


class TestHasWaiverParsing(unittest.TestCase):
    """Exercise the real _has_waiver() body-regex / label parsing by mocking only
    the gh api subprocess call (not _has_waiver itself)."""

    def _run_with_payload(self, payload: dict):
        import json
        env = {**os.environ, "PR_NUMBER": "82", "GH_TOKEN": "x"}
        completed = type("R", (), {"returncode": 0, "stdout": json.dumps(payload)})()
        with patch.dict(os.environ, env, clear=True), patch(
            "scripts.check_evidence_confidence.subprocess.run",
            return_value=completed,
        ):
            return _has_waiver()

    def test_body_waiver_phrase_detected(self):
        body = "## §4\nEvidence tier: unit-only (waiver: scripts/docs-only, no runtime path)\n"
        self.assertTrue(self._run_with_payload({"body": body, "labels": []}))

    def test_label_waiver_detected(self):
        self.assertTrue(
            self._run_with_payload({"body": "no tier here", "labels": ["evidence-waiver"]})
        )

    def test_no_waiver_in_body_or_labels(self):
        self.assertFalse(
            self._run_with_payload(
                {"body": "Evidence tier: sandbox-live (scenario: full-live)", "labels": ["bug"]}
            )
        )

    def test_gh_api_failure_returns_false(self):
        env = {**os.environ, "PR_NUMBER": "82", "GH_TOKEN": "x"}
        failed = type("R", (), {"returncode": 1, "stdout": ""})()
        with patch.dict(os.environ, env, clear=True), patch(
            "scripts.check_evidence_confidence.subprocess.run", return_value=failed
        ):
            self.assertFalse(_has_waiver())
