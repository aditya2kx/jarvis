#!/usr/bin/env python3
"""Tests for check_workflow_yaml.py (ship-emoji-force-merge.yml regression guard)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import check_workflow_yaml as m

_VALID = "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n          echo hi\n"
# A column-0 line inside a `run: |` block ends the scalar — the original bug.
_COLUMN_ZERO = _VALID + 'python3 -c "\nimport re\n"\n'


class TestInvalidWorkflows(unittest.TestCase):
    def _dir(self, files: dict[str, str]) -> Path:
        d = Path(tempfile.mkdtemp())
        for name, text in files.items():
            (d / name).write_text(text)
        return d

    def test_all_valid(self):
        self.assertEqual(m.invalid_workflows(self._dir({"a.yml": _VALID, "b.yaml": _VALID})), [])

    def test_column_zero_line_in_run_block_is_reported(self):
        errors = m.invalid_workflows(self._dir({"ok.yml": _VALID, "bad.yml": _COLUMN_ZERO}))
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("bad.yml: "))

    def test_repo_workflows_parse(self):
        self.assertEqual(m.invalid_workflows(), [])


if __name__ == "__main__":
    unittest.main()
