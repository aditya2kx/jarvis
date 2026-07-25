"""Tests for check_operator_console_actions.py."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    path = _REPO / "scripts" / "check_operator_console_actions.py"
    spec = importlib.util.spec_from_file_location("check_operator_console_actions", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCheckOperatorConsoleActions(unittest.TestCase):
    def test_repo_passes(self):
        mod = _load()
        errs = mod.check()
        self.assertEqual(errs, [], msg="\n".join(errs))


if __name__ == "__main__":
    unittest.main()
