#!/usr/bin/env python3
"""Tests for scripts/check_worktree_path_length.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_worktree_path_length as C


class TestWorktreePathLengthGate(unittest.TestCase):
    def test_evaluate_passes_on_this_checkout(self):
        errors = C.evaluate()
        self.assertEqual(errors, [], msg="; ".join(errors))


if __name__ == "__main__":
    unittest.main()
