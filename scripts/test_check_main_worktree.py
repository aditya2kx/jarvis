"""check_main_worktree.py — the operator's main checkout must stay a real work tree."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_main_worktree as M  # noqa: E402


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


class InspectTests(unittest.TestCase):
    def _repo(self) -> Path:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.email", "t@example.com")
        _git(root, "config", "user.name", "t")
        (root / "f").write_text("a\n")
        _git(root, "add", "f")
        _git(root, "commit", "-m", "init")
        return root

    def test_missing_directory_is_not_a_problem(self):
        self.assertEqual(M.inspect(Path("/tmp/no-such-jarvis-checkout")), [])

    def test_clean_checkout_is_ok(self):
        self.assertEqual(M.inspect(self._repo()), [])

    def test_bare_flag_on_a_checkout_fails(self):
        repo = self._repo()
        _git(repo, "config", "core.bare", "true")
        problems = M.inspect(repo)
        self.assertEqual(len(problems), 1)
        self.assertIn("core.bare=true", problems[0])

    def test_uncommitted_tracked_change_fails(self):
        repo = self._repo()
        (repo / "f").write_text("b\n")
        problems = M.inspect(repo)
        self.assertTrue(any("uncommitted tracked changes" in p for p in problems))

    def test_untracked_file_does_not_fail(self):
        repo = self._repo()
        (repo / "extra").write_text("x\n")
        self.assertEqual(M.inspect(repo), [])


if __name__ == "__main__":
    unittest.main()
