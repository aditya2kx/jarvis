#!/usr/bin/env python3
"""Tests for build_claude_review_context (pure helpers)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_claude_review_context import expand_paths, paired_test_candidates


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


if __name__ == "__main__":
    unittest.main()
