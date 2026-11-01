"""Unit tests for scripts/check_fq_no_double_backticks.py."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_fq_no_double_backticks as gate  # noqa: E402


class FqDoubleBacktickGateTests(unittest.TestCase):
    def test_clean_fq_interpolation_is_ok(self):
        self.assertEqual(gate.violations(Path("x.py"), 'f"SELECT * FROM {fq(\'t\')}"'), [])

    def test_double_wrap_is_flagged(self):
        bad = 'f"SELECT * FROM `{fq(\'adp_hire_dates\')}` WHERE 1=1"'
        hits = gate.violations(Path("pay.py"), bad)
        self.assertEqual(len(hits), 1)
        self.assertIn("double-wrapped fq()", hits[0])

    def test_assert_helper_raises(self):
        with self.assertRaises(AssertionError):
            gate.assert_no_double_wrapped_fq("FROM `{fq('t')}`", where="q")

    def test_assert_helper_passes_on_single_wrap(self):
        gate.assert_no_double_wrapped_fq("FROM {fq('t')} WHERE store=@store")

    def test_main_passes_on_clean_tree(self):
        self.assertEqual(gate.main(), 0)

    def test_main_fails_when_a_scanned_file_is_bad(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "skills").mkdir()
            bad = root / "skills" / "bad.py"
            bad.write_text("sql = f\"SELECT * FROM `{fq('t')}`\"\n")
            with mock.patch.object(gate, "_REPO", root), \
                    mock.patch.object(gate, "_SELF", root / "scripts" / "check.py"), \
                    mock.patch.object(gate, "_SELF_TEST", root / "scripts" / "test.py"):
                self.assertEqual(gate.main(), 1)


if __name__ == "__main__":
    unittest.main()
