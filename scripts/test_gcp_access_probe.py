"""Unit tests for scripts/gcp_access_probe.py — no live GCP."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gcp_access_probe import RECIPES, classify, format_report  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_github_actions_wins_even_with_adc(self):
        surface = classify(
            env={"GITHUB_ACTIONS": "true", "CURSOR_AGENT": "1"},
            gcloud_on_path=False,
            adc_ok=True,
        )
        self.assertEqual(surface, "github_actions_wif")

    def test_adc_ready_on_laptop(self):
        surface = classify(
            env={},
            gcloud_on_path=True,
            adc_ok=True,
        )
        self.assertEqual(surface, "adc_ready")

    def test_cursor_cloud_without_adc(self):
        surface = classify(
            env={"CURSOR_AGENT": "1"},
            gcloud_on_path=False,
            adc_ok=False,
        )
        self.assertEqual(surface, "cursor_cloud_no_adc")

    def test_gcloud_without_adc_not_cursor(self):
        surface = classify(
            env={},
            gcloud_on_path=True,
            adc_ok=False,
        )
        self.assertEqual(surface, "gcloud_cli_no_adc")

    def test_empty_machine(self):
        surface = classify(
            env={},
            gcloud_on_path=False,
            adc_ok=False,
        )
        self.assertEqual(surface, "no_gcp_identity")

    def test_every_surface_has_a_recipe(self):
        for name in (
            "github_actions_wif",
            "adc_ready",
            "cursor_cloud_no_adc",
            "gcloud_cli_no_adc",
            "no_gcp_identity",
        ):
            self.assertIn(name, RECIPES)

    def test_report_does_not_look_like_a_secret(self):
        text = format_report(
            "cursor_cloud_no_adc",
            gcloud_on_path=False,
            adc_ok=False,
            adc_detail="DefaultCredentialsError",
        )
        self.assertIn("surface=cursor_cloud_no_adc", text)
        self.assertIn("docs/contributing/gcp-access.md", text)
        self.assertNotIn("BEGIN PRIVATE", text)


if __name__ == "__main__":
    unittest.main()
