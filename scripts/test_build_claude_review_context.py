#!/usr/bin/env python3
"""Tests for build_claude_review_context (pure helpers)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_claude_review_context as bc
from build_claude_review_context import (
    _ref_resolves,
    delta_paths_since,
    effective_delta,
    expand_paths,
    paired_test_candidates,
    write_manifest,
)


class TestPairedTests(unittest.TestCase):
    def test_scripts_module_gets_test_module(self):
        cands = paired_test_candidates("agents/bhaga/scripts/sandbox_e2e.py")
        self.assertIn("agents/bhaga/scripts/test_sandbox_e2e.py", cands)

    def test_non_py_returns_empty(self):
        self.assertEqual(paired_test_candidates("README.md"), [])

    def test_test_file_returns_empty(self):
        self.assertEqual(paired_test_candidates("agents/bhaga/scripts/test_foo.py"), [])


class TestExpandPaths(unittest.TestCase):
    def test_dedupes_and_tags_reasons(self):
        # head ref fake — only rubric may exist in real repo; use empty diff
        planned = expand_paths([], "HEAD")
        paths = [p for p, _ in planned]
        self.assertIn(".github/claude-review-guidelines.md", paths)


class TestDeltaSinceLastReview(unittest.TestCase):
    def test_all_zero_ref_does_not_resolve(self):
        self.assertFalse(_ref_resolves("0" * 40))

    def test_empty_ref_does_not_resolve(self):
        self.assertFalse(_ref_resolves(""))

    def test_delta_empty_when_prev_head_unresolvable(self):
        # First review (no prior head) → [] meaning "review whole PR".
        self.assertEqual(delta_paths_since("0" * 40, "HEAD"), [])
        self.assertEqual(delta_paths_since(None, "HEAD"), [])


class TestEffectiveDelta(unittest.TestCase):
    def test_effective_delta_empty_without_prior(self):
        # A previous head SHA exists, but no prior Claude review → NOT a re-review.
        # Returns early without touching git.
        self.assertEqual(effective_delta("anything", "HEAD", has_prior_review=False), [])

    def test_effective_delta_used_when_prior(self):
        with patch.object(bc, "delta_paths_since", return_value=["a.py"]):
            self.assertEqual(
                effective_delta("prevsha", "HEAD", has_prior_review=True), ["a.py"]
            )


class TestManifestReReviewText(unittest.TestCase):
    def test_manifest_no_rereview_text_when_delta_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_manifest(Path(tmp), entries=[], base="basesha", head="headsha",
                           delta_paths=[])
            text = (Path(tmp) / "MANIFEST.md").read_text(encoding="utf-8")
        self.assertNotIn("RE-REVIEW", text)

    def test_manifest_has_rereview_text_when_delta_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_manifest(Path(tmp), entries=[], base="basesha", head="headsha",
                           delta_paths=["a.py"])
            text = (Path(tmp) / "MANIFEST.md").read_text(encoding="utf-8")
        self.assertIn("RE-REVIEW", text)


if __name__ == "__main__":
    unittest.main()
