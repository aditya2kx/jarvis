"""
Behavioral unit tests for .cursor/hooks/prompt_gate.py

Covers:
- Block on each intake phrase
- Word-boundary / near-miss negatives (should NOT block)
- //inline bypass
- No-workspace pass-through
- Missing enforce.sh pass-through
- Malformed JSON fail-open
- Corpus append side-effect
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_prompt_gate():
    """Import prompt_gate without it being on sys.path."""
    spec = importlib.util.spec_from_file_location(
        "prompt_gate",
        Path(__file__).parent.parent / ".cursor" / "hooks" / "prompt_gate.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pg = _load_prompt_gate()


class TestIntakePhraseDetection(unittest.TestCase):
    """_is_new_requirement should fire on each listed phrase."""

    def test_new_requirement(self):
        self.assertTrue(pg._is_new_requirement("I have a new requirement: add X"))

    def test_i_want_to_work_on(self):
        self.assertTrue(pg._is_new_requirement("I want to work on a different feature"))

    def test_id_like_to_work_on(self):
        self.assertTrue(pg._is_new_requirement("I'd like to work on this separately"))

    def test_lets_also(self):
        self.assertTrue(pg._is_new_requirement("Let's also add a search endpoint"))

    def test_lets_also_noapostrophe(self):
        self.assertTrue(pg._is_new_requirement("lets also fix the other bug"))

    def test_can_you_also(self):
        self.assertTrue(pg._is_new_requirement("can you also create a report"))

    def test_can_we_also(self):
        self.assertTrue(pg._is_new_requirement("can we also update the schema"))

    def test_add_a_requirement(self):
        self.assertTrue(pg._is_new_requirement("add a requirement for dark mode"))

    def test_add_another_requirement(self):
        self.assertTrue(pg._is_new_requirement("add another requirement: localization"))

    def test_separate_requirement(self):
        self.assertTrue(pg._is_new_requirement("this is a separate requirement"))

    def test_different_requirement(self):
        self.assertTrue(pg._is_new_requirement("I have a different requirement"))

    def test_new_feature(self):
        self.assertTrue(pg._is_new_requirement("I want a new feature: notifications"))

    def test_separate_pr(self):
        self.assertTrue(pg._is_new_requirement("let's do this in a separate pr"))

    def test_another_pr(self):
        self.assertTrue(pg._is_new_requirement("should be another pr"))

    def test_spin_up_a_worktree(self):
        self.assertTrue(pg._is_new_requirement("spin up a worktree for this"))


class TestNearMissNegatives(unittest.TestCase):
    """Phrases that look similar but must NOT fire the gate."""

    def test_not_renewal(self):
        self.assertFalse(pg._is_new_requirement("renewal of the lease agreement"))

    def test_not_requirement_reference(self):
        # "requirement" alone without the intake phrase context
        self.assertFalse(pg._is_new_requirement("as per the requirement on line 5"))

    def test_not_normal_question(self):
        self.assertFalse(pg._is_new_requirement("what does this function do?"))

    def test_not_bugfix(self):
        self.assertFalse(pg._is_new_requirement("fix the null pointer bug in parser"))

    def test_empty_prompt(self):
        self.assertFalse(pg._is_new_requirement(""))

    def test_continuation(self):
        self.assertFalse(pg._is_new_requirement("ok, continue with the approach above"))

    def test_word_boundary_no_new(self):
        # "renewal" contains "new" but not as a whole word matching the phrase "new requirement"
        self.assertFalse(pg._is_new_requirement("renewal of the existing requirement"))


class TestInlineOverride(unittest.TestCase):
    def test_inline_bypasses_intake(self):
        self.assertTrue(pg._has_inline_override("//inline keep going"))

    def test_inline_with_new_req(self):
        self.assertTrue(pg._has_inline_override("//inline I want to work on a new requirement"))

    def test_not_inline_without_prefix(self):
        self.assertFalse(pg._has_inline_override("I want to work on a new requirement"))

    def test_not_inline_mid_sentence(self):
        self.assertFalse(pg._has_inline_override("some text //inline more text"))


class TestMainFunction(unittest.TestCase):
    """End-to-end tests via main() reading from stdin."""

    def _run(self, payload: dict, workspace_root: str | None = None) -> dict:
        import io
        stdin_data = json.dumps(payload)
        with patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_out, \
             patch.object(pg, "_append_corpus", return_value=None):  # suppress real corpus writes
            if workspace_root is not None:
                with patch.dict("os.environ", {"CURSOR_PROJECT_DIR": workspace_root}):
                    pg.main()
            else:
                with patch.dict("os.environ", {}, clear=True):
                    pg.main()
        return json.loads(mock_out.getvalue())

    def test_block_on_intake_phrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            # create enforce.sh so the gate fires
            hooks = Path(tmp) / ".cursor" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "enforce.sh").write_text("#!/bin/bash\n")
            result = self._run(
                {"prompt": "I want to work on a new requirement: X"},
                workspace_root=tmp,
            )
        self.assertFalse(result["continue"])
        self.assertIn("new_requirement.py", result.get("user_message", ""))

    def test_passthrough_normal_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = Path(tmp) / ".cursor" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "enforce.sh").write_text("#!/bin/bash\n")
            result = self._run(
                {"prompt": "what does the fetchData function return?"},
                workspace_root=tmp,
            )
        self.assertTrue(result["continue"])

    def test_inline_bypass_blocks_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = Path(tmp) / ".cursor" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "enforce.sh").write_text("#!/bin/bash\n")
            result = self._run(
                {"prompt": "//inline I want to work on a new requirement: X"},
                workspace_root=tmp,
            )
        self.assertTrue(result["continue"])

    def test_no_workspace_passthrough(self):
        result = self._run({"prompt": "I want to work on a new requirement: X"})
        self.assertTrue(result["continue"])

    def test_missing_enforce_sh_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp:
            # enforce.sh deliberately absent
            result = self._run(
                {"prompt": "I want to work on a new requirement: X"},
                workspace_root=tmp,
            )
        self.assertTrue(result["continue"])

    def test_malformed_json_failopen(self):
        import io
        with patch("sys.stdin", io.StringIO("not valid json")), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            pg.main()
        result = json.loads(mock_out.getvalue())
        self.assertTrue(result["continue"])

    def test_corpus_append_called(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = Path(tmp) / ".cursor" / "hooks"
            hooks.mkdir(parents=True)
            (hooks / "enforce.sh").write_text("#!/bin/bash\n")
            import io
            with patch("sys.stdin", io.StringIO(json.dumps({"prompt": "hello world"}))), \
                 patch("sys.stdout", new_callable=io.StringIO), \
                 patch.dict("os.environ", {"CURSOR_PROJECT_DIR": tmp}), \
                 patch.object(pg, "_append_corpus") as mock_append:
                pg.main()
            mock_append.assert_called_once()
            args = mock_append.call_args
            self.assertEqual(args[0][0], "hello world")


if __name__ == "__main__":
    unittest.main()
