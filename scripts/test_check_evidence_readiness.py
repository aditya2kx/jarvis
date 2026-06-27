#!/usr/bin/env python3
"""Unit tests for scripts/check_evidence_readiness.py."""
from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import scripts.check_evidence_readiness as cer
from scripts.check_evidence_readiness import predict


def _predict_no_grafana_diff(body: str) -> tuple[bool, str]:
    """Call predict() with _diff_touches forced False (non-grafana-diff context)."""
    with patch.object(cer, "_diff_touches", return_value=False):
        return predict(body)


class TestPredictWaiverAndTier(unittest.TestCase):
    def test_unit_only_waiver_passes(self):
        body = "Evidence tier: unit-only (waiver: scripts-only, no runtime path modified)"
        ok, reason = _predict_no_grafana_diff(body)
        self.assertTrue(ok, reason)
        self.assertIn("waiver", reason.lower())

    def test_sandbox_live_passes(self):
        body = "Evidence tier: sandbox-live\nscenario: full-live"
        ok, reason = _predict_no_grafana_diff(body)
        self.assertTrue(ok, reason)

    def test_sandbox_e2e_passes(self):
        body = "Evidence tier: sandbox-e2e"
        ok, reason = _predict_no_grafana_diff(body)
        self.assertTrue(ok, reason)


class TestPredictRealExecMarkers(unittest.TestCase):
    def test_held_back_marker_passes(self):
        body = "## §4 Evidence\n```\nHELD-BACK: 0\n```"
        ok, reason = _predict_no_grafana_diff(body)
        self.assertTrue(ok, reason)

    def test_cloud_run_marker_passes(self):
        body = "## §4 Evidence\nCloud Run invocation: OK\n54 passed"
        ok, reason = _predict_no_grafana_diff(body)
        self.assertTrue(ok, reason)

    def test_sandbox_marker_passes(self):
        body = "## §4 Evidence\nsandbox e2e run passed."
        ok, reason = _predict_no_grafana_diff(body)
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
        ok, reason = _predict_no_grafana_diff(body)
        self.assertFalse(ok, "pytest-only evidence should fail the predictor")
        self.assertIn("pytest", reason.lower())

    def test_pytest_with_real_marker_passes(self):
        body = (
            "## §4 Evidence\n"
            "54 passed\n"
            "HELD-BACK: 0 (from Cloud Run logs)\n"
        )
        ok, reason = _predict_no_grafana_diff(body)
        self.assertTrue(ok, reason)


class TestPredictEmptyBody(unittest.TestCase):
    def test_empty_body_passes(self):
        """Empty or unclear §4 should not block (predictor only blocks confidently)."""
        ok, reason = _predict_no_grafana_diff("")
        self.assertTrue(ok, reason)

    def test_no_section4_no_markers_passes(self):
        body = "Some description without evidence section."
        ok, reason = _predict_no_grafana_diff(body)
        self.assertTrue(ok, reason)


class TestG3GrafanaPathAwareCheck(unittest.TestCase):
    """G3: when the diff touches agents/bhaga/grafana/, §4 must have screenshot URL + verify_panels."""

    def _predict_with_grafana_diff(self, body: str) -> tuple[bool, str]:
        from unittest.mock import patch
        import scripts.check_evidence_readiness as cer
        with patch.object(cer, "_diff_touches", return_value=True):
            return predict(body)

    def _predict_without_grafana_diff(self, body: str) -> tuple[bool, str]:
        from unittest.mock import patch
        import scripts.check_evidence_readiness as cer
        with patch.object(cer, "_diff_touches", return_value=False):
            return predict(body)

    def test_grafana_diff_without_screenshot_fails(self):
        body = (
            "## §4 Evidence\n"
            "<details><summary>Evidence</summary>\n"
            "verify_panels output: OK=19\n"
            "No screenshot here.\n"
            "</details>\n"
        )
        ok, reason = self._predict_with_grafana_diff(body)
        self.assertFalse(ok, "grafana diff without screenshot URL should fail")
        self.assertIn("screenshot", reason.lower())

    def test_grafana_diff_without_verify_panels_fails(self):
        body = (
            "## §4 Evidence\n"
            "<details><summary>Evidence</summary>\n"
            "![panel51](https://github.com/owner/repo/releases/download/tag/panel51.png)\n"
            "Panel query ran. No panel runner output included here.\n"
            "</details>\n"
        )
        ok, reason = self._predict_with_grafana_diff(body)
        self.assertFalse(ok, "grafana diff without verify_panels should fail")
        self.assertIn("verify_panels", reason.lower())

    def test_grafana_diff_with_both_passes(self):
        body = (
            "## §4 Evidence\n"
            "<details><summary>Evidence</summary>\n"
            "![panel51](https://github.com/owner/repo/releases/download/tag/panel51.png)\n"
            "verify_panels.py: OK=19 EMPTY=0 ERROR=0\n"
            "</details>\n"
        )
        ok, reason = self._predict_with_grafana_diff(body)
        self.assertTrue(ok, f"grafana diff with screenshot + verify_panels.py should pass: {reason}")

    def test_no_grafana_diff_skips_grafana_check(self):
        """When diff doesn't touch grafana, the grafana check doesn't apply."""
        body = "## §4 Evidence\n54 passed in 1.2s\n"
        ok, _ = self._predict_without_grafana_diff(body)
        # The pytest-only check might still fail, but not the grafana check
        # (verify by checking that predict with grafana diff WOULD fail this body)
        ok_with_grafana, reason_with = self._predict_with_grafana_diff(body)
        self.assertFalse(ok_with_grafana, "same body SHOULD fail when grafana diff active")

    def test_grafana_diff_with_waiver_but_no_screenshot_still_fails(self):
        """unit-only waiver does NOT override the grafana evidence requirement."""
        body = (
            "Evidence tier: unit-only (waiver: additive view)\n"
            "## §4 Evidence\n"
            "<details><summary>Evidence</summary>\n"
            "verify_panels.py: OK=19 EMPTY=0 ERROR=0\n"
            "No screenshot URL here.\n"
            "</details>\n"
        )
        ok, reason = self._predict_with_grafana_diff(body)
        self.assertFalse(ok, "waiver should NOT bypass grafana screenshot requirement")
        self.assertIn("screenshot", reason.lower())


if __name__ == "__main__":
    unittest.main()
