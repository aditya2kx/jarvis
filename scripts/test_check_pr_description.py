#!/usr/bin/env python3
"""Unit tests for scripts/check_pr_description.py — G1 screenshot gate + existing checks."""
from __future__ import annotations

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.check_pr_description import _check_body, _IMG_REF_RE, _LOCAL_IMG_RE

_SKELETON_BODY = """
## 1. What is the change
Added the feature.

## 2. Motivation
Fixes #42.

## 3. Design / Approach
Used the pattern.

## 4. End-to-end test

<details><summary>Evidence</summary>

Real output here:
$ python3 scripts/verify.py --full
All 19 panels OK. First=DoorDash last=2026-06-26.

</details>

## 5. Backward compatibility
Additive only — no breaking changes.

## 6. Checklist
- [x] Tests added
- [x] Docs updated
- [x] verify.py --full passes
"""


def _body_with_evidence(evidence_inner: str) -> str:
    """Wrap evidence content in the §4 details block."""
    return _SKELETON_BODY.replace(
        "Real output here:\n"
        "$ python3 scripts/verify.py --full\n"
        "All 19 panels OK. First=DoorDash last=2026-06-26.\n",
        evidence_inner,
    )


class TestG1LocalScreenshotRejected(unittest.TestCase):
    """G1: local file paths in §4 evidence must be rejected."""

    def _errors_for_evidence(self, evidence_inner: str) -> list[str]:
        body = _body_with_evidence(evidence_inner)
        return _check_body(body)

    def test_local_tmp_path_fails(self):
        evidence = (
            "Panel 51 screenshot:\n"
            "![panel51](/tmp/panel51.png)\n"
            "verify_panels output: OK=19\n"
        )
        errors = self._errors_for_evidence(evidence)
        g1_errors = [e for e in errors if "/tmp/panel51.png" in e or "local path" in e.lower()]
        self.assertTrue(g1_errors, f"Expected G1 error for /tmp path, got: {errors}")

    def test_relative_path_fails(self):
        evidence = (
            "![shot](./screenshots/panel51.png)\n"
            "verify_panels: OK=19\n"
        )
        errors = self._errors_for_evidence(evidence)
        g1_errors = [e for e in errors if "local path" in e.lower() or "./screenshots" in e]
        self.assertTrue(g1_errors, f"Expected G1 error for ./ path, got: {errors}")

    def test_home_path_fails(self):
        evidence = (
            "![x](~/Desktop/shot.png)\n"
            "verify_panels: OK\n"
        )
        errors = self._errors_for_evidence(evidence)
        g1_errors = [e for e in errors if "local path" in e.lower() or "~/Desktop" in e]
        self.assertTrue(g1_errors, f"Expected G1 error for ~/Desktop path, got: {errors}")

    def test_https_github_releases_passes(self):
        evidence = (
            "Panel 51:\n"
            "![panel51](https://github.com/aditya2kx/jarvis/releases/download/"
            "evidence-screenshots/panel51-20260627.png)\n"
            "verify_panels: OK=19\n"
        )
        errors = self._errors_for_evidence(evidence)
        g1_errors = [e for e in errors if "local path" in e.lower() or "screenshot" in e.lower()]
        self.assertFalse(g1_errors, f"https GitHub URL should pass G1, got: {g1_errors}")

    def test_https_user_attachments_passes(self):
        evidence = (
            "![panel](https://github.com/user-attachments/assets/abc123.png)\n"
            "verify_panels output: panel 51 OK\n"
        )
        errors = self._errors_for_evidence(evidence)
        g1_errors = [e for e in errors if "local path" in e.lower()]
        self.assertFalse(g1_errors, f"user-attachments URL should pass G1, got: {g1_errors}")

    def test_no_images_no_g1_error(self):
        evidence = (
            "No images, just text output:\n"
            "verify_panels: OK=19\n"
        )
        errors = self._errors_for_evidence(evidence)
        g1_errors = [e for e in errors if "local path" in e.lower()]
        self.assertFalse(g1_errors, f"No images should produce no G1 error, got: {g1_errors}")


class TestRequiredIssueLink(unittest.TestCase):
    """Issue #123: PR body must assert Closes/Fixes/Resolves #N."""

    def test_skeleton_body_with_fixes_passes(self):
        errors = _check_body(_SKELETON_BODY)
        link_errors = [e for e in errors if "tracking issue" in e.lower()]
        self.assertFalse(link_errors, f"Expected no issue-link error, got: {link_errors}")

    def test_closes_keyword_passes(self):
        body = _SKELETON_BODY.replace("Fixes #42.", "Closes #123.")
        errors = _check_body(body)
        link_errors = [e for e in errors if "tracking issue" in e.lower()]
        self.assertFalse(link_errors)

    def test_resolves_keyword_passes(self):
        body = _SKELETON_BODY.replace("Fixes #42.", "Resolves #99.")
        errors = _check_body(body)
        link_errors = [e for e in errors if "tracking issue" in e.lower()]
        self.assertFalse(link_errors)

    def test_bare_refs_mention_fails(self):
        """A 'Refs #N' mention does not assert implementation — must fail."""
        body = _SKELETON_BODY.replace("Fixes #42.", "Refs #42 for background.")
        errors = _check_body(body)
        link_errors = [e for e in errors if "tracking issue" in e.lower()]
        self.assertTrue(link_errors, "Expected an issue-link error for a bare Refs mention")

    def test_bare_hash_mention_fails(self):
        body = _SKELETON_BODY.replace("Fixes #42.", "See #42 for context.")
        errors = _check_body(body)
        link_errors = [e for e in errors if "tracking issue" in e.lower()]
        self.assertTrue(link_errors, "Expected an issue-link error for a bare #N mention")

    def test_no_issue_reference_at_all_fails(self):
        body = _SKELETON_BODY.replace("Fixes #42.", "No issue reference here.")
        errors = _check_body(body)
        link_errors = [e for e in errors if "tracking issue" in e.lower()]
        self.assertTrue(link_errors)


class TestImgRegexes(unittest.TestCase):
    """Verify the regex patterns work as expected."""

    def test_img_ref_extracts_src(self):
        text = "![alt text](/tmp/foo.png)"
        matches = _IMG_REF_RE.findall(text)
        self.assertEqual(matches, ["/tmp/foo.png"])

    def test_img_ref_extracts_https(self):
        text = "![x](https://github.com/user/repo/releases/download/tag/file.png)"
        matches = _IMG_REF_RE.findall(text)
        self.assertEqual(matches, ["https://github.com/user/repo/releases/download/tag/file.png"])

    def test_local_img_matches_tmp(self):
        self.assertTrue(_LOCAL_IMG_RE.match("/tmp/foo.png"))

    def test_local_img_matches_relative(self):
        self.assertTrue(_LOCAL_IMG_RE.match("./bar.png"))

    def test_local_img_matches_home(self):
        self.assertTrue(_LOCAL_IMG_RE.match("~/Desktop/x.png"))

    def test_local_img_does_not_match_https(self):
        self.assertIsNone(_LOCAL_IMG_RE.match("https://example.com/img.png"))


if __name__ == "__main__":
    unittest.main()
