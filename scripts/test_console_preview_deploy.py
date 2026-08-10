#!/usr/bin/env python3
"""Unit tests for console_preview_deploy.py (no gcloud)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import console_preview_deploy as CPD


class TestPreviewUrl(unittest.TestCase):
    def test_tag_and_url(self):
        self.assertEqual(CPD.tag_for_pr(234), "pr234")
        url = CPD.preview_url("pr234")
        self.assertTrue(url.startswith("https://pr234---operator-console-"))
        self.assertIn(".us-central1.run.app", url)
        self.assertIn(CPD.PROJECT_NUMBER, url)

    def test_dry_run_deploy_prints_url(self):
        with patch.dict(os.environ, {"GCP_PROJECT": "test-proj"}, clear=False):
            with patch("console_preview_deploy._git_sha", return_value="abcd1234ef00"):
                with patch("console_preview_deploy.build_image") as mock_build:
                    with patch("console_preview_deploy.deploy_tagged") as mock_deploy:
                        rc = CPD.main(["--pr", "234", "--dry-run"])
        self.assertEqual(rc, 0)
        mock_build.assert_called_once()
        mock_deploy.assert_called_once()
        self.assertEqual(mock_deploy.call_args.kwargs["tag"], "pr234")

    def test_remove_tags(self):
        with patch.dict(os.environ, {"GCP_PROJECT": "test-proj"}, clear=False):
            with patch("console_preview_deploy.remove_tags") as mock_rm:
                rc = CPD.main(["--pr", "234", "--remove-tags", "--dry-run"])
        self.assertEqual(rc, 0)
        mock_rm.assert_called_once()
        self.assertEqual(mock_rm.call_args.kwargs["tag"], "pr234")

    def test_verify_accepts_iap_302(self):
        err = __import__("urllib.error").error.HTTPError(
            url="https://example/",
            code=302,
            msg="Found",
            hdrs={"Location": "https://accounts.google.com/o/oauth2/v2/auth?x=1"},
            fp=None,
        )

        class _Opener:
            def open(self, *a, **k):
                raise err

        with patch("urllib.request.build_opener", return_value=_Opener()):
            CPD.verify_preview_url("https://pr234---operator-console-x.a.run.app/")

    def test_verify_rejects_404(self):
        err = __import__("urllib.error").error.HTTPError(
            url="https://example/",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )

        class _Opener:
            def open(self, *a, **k):
                raise err

        with patch("urllib.request.build_opener", return_value=_Opener()):
            with self.assertRaises(SystemExit) as cm:
                CPD.verify_preview_url("https://bad.example/")
        self.assertIn("404", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
