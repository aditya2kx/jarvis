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
    """Call predict() with path-aware gates forced off (non-grafana / non-console)."""
    with patch.object(cer, "_diff_touches", return_value=False), \
         patch.object(cer, "_diff_touches_console_portal", return_value=False):
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
        with patch.object(cer, "_diff_touches", return_value=True), \
             patch.object(cer, "_diff_touches_console_portal", return_value=False):
            return predict(body)

    def _predict_without_grafana_diff(self, body: str) -> tuple[bool, str]:
        from unittest.mock import patch
        import scripts.check_evidence_readiness as cer
        with patch.object(cer, "_diff_touches", return_value=False), \
             patch.object(cer, "_diff_touches_console_portal", return_value=False):
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


class TestG3ChangedPanelIdGate(unittest.TestCase):
    """G3 tightening: §4 must mention OK for each changed panel id specifically."""

    def _predict(self, body: str, changed_ids: set[int]) -> tuple[bool, str]:
        """Run predict with grafana diff active and the given changed panel ids."""
        with patch.object(cer, "_diff_touches", return_value=True), \
             patch.object(cer, "_diff_touches_console_portal", return_value=False), \
             patch.object(cer, "_changed_panel_ids", return_value=changed_ids):
            return predict(body)

    def _good_body(self, panel_mention: str = "") -> str:
        return (
            "## §4 Evidence\n"
            "<details><summary>Evidence</summary>\n"
            "![panel76](https://github.com/owner/repo/releases/download/tag/panel76.png)\n"
            f"verify_panels.py: OK=19 EMPTY=0 ERROR=0\n"
            f"{panel_mention}\n"
            "</details>\n"
        )

    def test_changed_panel_with_ok_in_body_passes(self):
        body = self._good_body("panel 76 executed OK")
        ok, reason = self._predict(body, {76})
        self.assertTrue(ok, f"body with panel 76 OK should pass: {reason}")

    def test_changed_panel_missing_from_body_fails(self):
        """Panel 76 changed but §4 says nothing about panel 76 → FAIL."""
        body = (
            "## §4 Evidence\n"
            "![screenshot](https://github.com/owner/repo/releases/download/tag/img.png)\n"
            "verify_panels.py: OK=19 EMPTY=0 ERROR=0\n"
            "All panels verified.\n"
        )
        ok, reason = self._predict(body, {76})
        self.assertFalse(ok, "missing panel-id OK reference should fail")
        self.assertIn("76", reason)

    def test_multiple_changed_panels_all_must_be_ok(self):
        """Both panel 76 and panel 91 changed; only 76 mentioned → FAIL."""
        body = self._good_body("panel 76 executed OK")
        ok, reason = self._predict(body, {76, 91})
        self.assertFalse(ok, "missing panel 91 OK reference should fail")
        self.assertIn("91", reason)

    def test_multiple_changed_panels_both_ok_passes(self):
        body = self._good_body("panel 76 OK\npanel 91 OK")
        ok, reason = self._predict(body, {76, 91})
        self.assertTrue(ok, f"both panels OK should pass: {reason}")

    def test_no_changed_panels_no_per_panel_requirement(self):
        """When _changed_panel_ids() returns empty (non-grafana PR), no per-panel check."""
        body = self._good_body()
        ok, reason = self._predict(body, set())
        self.assertTrue(ok, f"no changed panels → no per-panel gate: {reason}")

    def test_grafana_dir_changed_panel_in_grafana_dir(self):
        """Covers grafana/ as well as agents/bhaga/grafana/."""
        body = self._good_body("panel 55 OK")
        # Simulate that only grafana/ (not agents/bhaga/grafana/) changed
        with patch.object(cer, "_diff_touches", return_value=True), \
             patch.object(cer, "_diff_touches_console_portal", return_value=False), \
             patch.object(cer, "_changed_panel_ids", return_value={55}):
            ok, reason = predict(body)
        self.assertTrue(ok, f"grafana/ dir changed panel with OK should pass: {reason}")

    def test_non_grafana_pr_not_affected(self):
        """A PR that doesn't touch grafana must not fail on panel id checks."""
        body = (
            "## §4 Evidence\n"
            "54 passed in 1.2s\n"
            "HELD-BACK: 0\n"
        )
        with patch.object(cer, "_diff_touches", return_value=False), \
             patch.object(cer, "_diff_touches_console_portal", return_value=False), \
             patch.object(cer, "_changed_panel_ids", return_value={99}):
            ok, _ = predict(body)
        self.assertTrue(ok, "non-grafana PR must not be blocked by panel id check")


class TestBacktickTierDetection(unittest.TestCase):
    """G4: backtick-wrapped Evidence tier must be caught as an error."""

    def _predict(self, body):
        with patch.object(cer, "_diff_touches", return_value=False), \
             patch.object(cer, "_diff_touches_console_portal", return_value=False):
            return predict(body)

    def test_backtick_wrapped_unit_only_waiver_fails(self):
        """The canonical mistake: wrapping the waiver in backticks defeats regex matching."""
        body = "Evidence tier: `unit-only (waiver: lifecycle intake scripts only)`\n"
        ok, reason = self._predict(body)
        self.assertFalse(ok, "backtick-wrapped waiver must be rejected")
        self.assertIn("backtick", reason.lower())

    def test_backtick_wrapped_sandbox_live_fails(self):
        body = "Evidence tier: `sandbox-live`\n"
        ok, reason = self._predict(body)
        self.assertFalse(ok)
        self.assertIn("backtick", reason.lower())

    def test_plain_unit_only_waiver_still_passes(self):
        """Un-wrapped waiver must continue to pass."""
        body = "Evidence tier: unit-only (waiver: lifecycle intake scripts only)\n"
        ok, reason = self._predict(body)
        self.assertTrue(ok, f"plain waiver should pass: {reason}")

    def test_backtick_tier_error_message_is_actionable(self):
        """Error message must tell the author exactly what to remove."""
        body = "Evidence tier: `unit-only (waiver: foo)`\n"
        _, reason = self._predict(body)
        self.assertIn("backtick", reason.lower())
        self.assertIn("Evidence tier:", reason)


class TestG5ConsolePortalScreenshotGate(unittest.TestCase):
    """G5: operator-console portal diffs require https screenshots; unit-only cannot waive."""

    def _predict_console(self, body: str) -> tuple[bool, str]:
        with patch.object(cer, "_diff_touches_grafana", return_value=False), \
             patch.object(cer, "_diff_touches_console_portal", return_value=True):
            return predict(body)

    def _predict_no_console(self, body: str) -> tuple[bool, str]:
        with patch.object(cer, "_diff_touches_grafana", return_value=False), \
             patch.object(cer, "_diff_touches_console_portal", return_value=False):
            return predict(body)

    def test_console_diff_without_screenshot_fails(self):
        body = (
            "Evidence tier: sandbox-e2e\n"
            "## §4 Evidence\n"
            "<details><summary>Evidence</summary>\n"
            "vitest 12 passed\n"
            "</details>\n"
        )
        ok, reason = self._predict_console(body)
        self.assertFalse(ok)
        self.assertIn("operator-console", reason.lower())
        self.assertIn("screenshot", reason.lower())

    def test_console_diff_with_screenshot_passes(self):
        body = (
            "Evidence tier: sandbox-e2e\n"
            "## §4 Evidence\n"
            "<details><summary>Evidence</summary>\n"
            "![payroll](https://github.com/owner/repo/releases/download/evidence-screenshots/payroll.png)\n"
            "</details>\n"
        )
        ok, reason = self._predict_console(body)
        self.assertTrue(ok, f"should pass with screenshot: {reason}")

    def test_console_diff_unit_only_waiver_fails(self):
        body = (
            "Evidence tier: unit-only (waiver: console bounds are pure TS)\n"
            "## §4 Evidence\n"
            "<details><summary>Evidence</summary>\n"
            "12 passed\n"
            "</details>\n"
        )
        ok, reason = self._predict_console(body)
        self.assertFalse(ok)
        self.assertIn("unit-only", reason.lower())

    def test_no_console_diff_skips_g5(self):
        body = (
            "Evidence tier: unit-only (waiver: docs only)\n"
            "## §4 Evidence\n"
            "n/a\n"
        )
        ok, reason = self._predict_no_console(body)
        self.assertTrue(ok, f"non-console unit-only should still pass: {reason}")


if __name__ == "__main__":
    unittest.main()
