"""Unit tests for scripts/sticky_pr_comment.py — no network."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sticky_pr_comment import find_comment_id, marker_tag  # noqa: E402


class FindCommentIdTests(unittest.TestCase):
    def test_finds_the_marked_comment(self):
        comments = [
            {"id": 1, "body": "unrelated"},
            {"id": 2, "body": f"{marker_tag('preview')}\nURL: x"},
        ]
        self.assertEqual(find_comment_id(comments, "preview"), 2)

    def test_other_markers_do_not_match(self):
        comments = [{"id": 3, "body": f"{marker_tag('preview-shots')}\n…"}]
        self.assertIsNone(find_comment_id(comments, "preview"))

    def test_null_body_is_skipped(self):
        self.assertIsNone(find_comment_id([{"id": 4, "body": None}], "preview"))


if __name__ == "__main__":
    unittest.main()
