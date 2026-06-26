#!/usr/bin/env python3
"""Unit tests for scripts/check_evidence_readiness.py."""
from __future__ import annotations

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.check_evidence_readiness import predict


class TestPredictWaiverAndTier(unittest.TestCase):
    def test_unit_only_waiver_passes(self):
        body = "Evidence tier: unit-only (waiver: scripts-only, no runtime path modified)"
        ok, reason = predict(body)
        self.assertTrue(ok, reason)
        self.assertIn("waiver", reason.lower())

    def test_sandbox_live_passes(self):
        body = "Evidence tier: sandbox-live\nscenario: full-live"
        ok, reason = predict(body)
        self.assertTrue(ok, reason)

    def test_sandbox_e2e_passes(self):
        body = "Evidence tier: sandbox-e2e"
        ok, reason = predict(body)
        self.assertTrue(ok, reason)


class TestPredictRealExecMarkers(unittest.TestCase):
    def test_held_back_marker_passes(self):
        body = "## §4 Evidence\n```\nHELD-BACK: 0\n```"
        ok, reason = predict(body)
        self.assertTrue(ok, reason)

    def test_cloud_run_marker_passes(self):
        body = "## §4 Evidence\nCloud Run invocation: OK\n54 passed"
        ok, reason = predict(body)
        self.assertTrue(ok, reason)

    def test_sandbox_marker_passes(self):
        body = "## §4 Evidence\nsandbox e2e run passed."
        ok, reason = predict(body)
        self.assertTrue(ok, reason)


class TestPredictPytestOnly(unittest.TestCase):
    def test_pytest_only_fails(self):
        body = (
            "## §4 Evidence\n"
            "```\n"
            "54 passed in 1.2s\n"
            "PASSED agents/bhaga/scripts/test_process_reviews.py::SomeTest\n"
            "```"
        )
        ok, reason = predict(body)
        self.assertFalse(ok, "pytest-only evidence should fail the predictor")
        self.assertIn("pytest", reason.lower())

    def test_pytest_with_real_marker_passes(self):
        body = (
            "## §4 Evidence\n"
            "54 passed\n"
            "HELD-BACK: 0 (from Cloud Run logs)\n"
        )
        ok, reason = predict(body)
        self.assertTrue(ok, reason)


class TestPredictEmptyBody(unittest.TestCase):
    def test_empty_body_passes(self):
        """Empty or unclear §4 should not block (predictor only blocks confidently)."""
        ok, reason = predict("")
        self.assertTrue(ok, reason)

    def test_no_section4_no_markers_passes(self):
        body = "Some description without evidence section."
        ok, reason = predict(body)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
