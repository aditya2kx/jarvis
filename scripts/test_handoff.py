#!/usr/bin/env python3
"""Unit tests for handoff.py — temp git repos, gh mocked."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handoff as H

ISSUE_BODY = (
    "Work item: **x**\n<!-- phase-state -->\nCurrent stage: build (3/5)\n"
    "Done: specify, setup, jam\nRemaining: plan\n<!-- /phase-state -->"
)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp: str) -> Path:
    main = Path(tmp) / "jarvis"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    return main


class ParseTests(unittest.TestCase):
    def test_tracking_issue_from_closes_line(self):
        self.assertEqual(H.tracking_issue("Body\n\nCloses #384. Supersedes #234"), 384)
        self.assertIsNone(H.tracking_issue("no link"))

    def test_done_from_issue_status_block(self):
        self.assertEqual(H.done_from_issue(ISSUE_BODY), ["specify", "setup", "jam"])
        self.assertEqual(H.done_from_issue(ISSUE_BODY.replace("specify, setup, jam", "none")), [])
        self.assertEqual(H.done_from_issue("Done: jam but no block"), [])

    def test_latest_note_is_the_last_marked_comment(self):
        comments = [{"body": f"{H.MARKER} to=cloud from=mac sha=a -->\nfirst"}, {"body": "lgtm"},
                    {"body": f"{H.MARKER} to=mac from=cloud sha=b -->\nsecond"}]
        self.assertTrue(H.latest_note(comments).endswith("second"))
        self.assertIsNone(H.latest_note([{"body": "lgtm"}]))

    def test_note_comment_carries_marker_and_next_step(self):
        body = H.note_comment("Done: X. Next: Y.", to="mac", frm="cloud", sha="0123456789", branch="cursor/a",
                              done=["specify"])
        self.assertTrue(body.startswith(f"{H.MARKER} to=mac from=cloud sha=0123456789 -->"))
        self.assertIn("**Handoff cloud → mac** at `0123456`", body)
        self.assertIn("handoff.py in --pr", body)


class CacheTests(unittest.TestCase):
    def test_writes_worktree_and_main_checkout_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = _repo(tmp)
            wt = Path(tmp) / "jarvis-wt-fix-a"
            _git(main, "worktree", "add", "-q", "-b", "fix/a", str(wt))
            H._update_caches("fix/a", wt, handed_off_to="cloud", pr=7)
            for root in (wt, main):
                data = json.loads((root / "metrics/pr_cost/session-fix-a-phase.json").read_text())
                self.assertEqual((data["handed_off_to"], data["pr"], data["branch"]), ("cloud", 7, "fix/a"))

    def test_keeps_existing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = _repo(tmp)
            path = main / "metrics/pr_cost/session-fix-b-phase.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"branch": "fix/b", "done": ["specify"], "delivered_signals": ["s1"]}))
            H._update_caches("fix/b", main, handed_off_to=None)
            data = json.loads(path.read_text())
            self.assertEqual((data["done"], data["delivered_signals"]), (["specify"], ["s1"]))


class OutTests(unittest.TestCase):
    def _run_in(self, cwd):
        real = H._run
        return lambda *a, **kw: real(*a, **{"cwd": cwd, **kw}) if a[0] == "git" else ""

    def test_refuses_uncommitted_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = _repo(tmp)
            (main / "wip.txt").write_text("x")
            with mock.patch.object(H, "surface", return_value="mac"), \
                 mock.patch.object(H, "_run", self._run_in(main)):
                with self.assertRaisesRegex(H.HandoffError, "uncommitted"):
                    H.cmd_out(1, "cloud", "note")

    def test_refuses_handoff_to_the_current_surface(self):
        with mock.patch.object(H, "surface", return_value="cloud"):
            with self.assertRaisesRegex(H.HandoffError, "already on cloud"):
                H.cmd_out(1, "cloud", "note")

    def test_cli_reports_refusal(self):
        with mock.patch.object(H, "cmd_out", side_effect=H.HandoffError("unpushed commits")), \
             tempfile.NamedTemporaryFile("w", suffix=".md") as f, \
             mock.patch("sys.stderr") as err:
            self.assertEqual(H.main(["out", "--pr", "1", "--to", "mac", "--note-file", f.name]), 1)
        self.assertIn("unpushed commits", "".join(c.args[0] for c in err.write.call_args_list))


class InTests(unittest.TestCase):
    def _cmd_in(self, wt: Path, issue_body: str):
        pr = {"headRefName": "fix/c", "body": "Closes #42", "url": "u",
              "comments": [{"body": f"{H.MARKER} to=cloud from=mac sha=abc -->\nNext: wire the badge."}]}
        gh = {("pr", "view"): pr, ("issue", "view"): {"body": issue_body}}
        with mock.patch.object(H, "_checkout", return_value=wt), \
             mock.patch.object(H, "_gh_json", side_effect=lambda *a: gh[a[:2]]), \
             mock.patch.object(H, "surface", return_value="cloud"):
            return H.cmd_in(388)

    def test_restores_phase_tracking_and_prints_the_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = _repo(tmp)
            out = self._cmd_in(main, ISSUE_BODY)
            data = json.loads((main / "metrics/pr_cost/session-fix-c-phase.json").read_text())
        self.assertEqual((data["issue"], data["pr"], data["done"]), (42, 388, ["specify", "setup", "jam"]))
        self.assertIsNone(data["handed_off_to"])
        self.assertIn("phase done: specify, setup, jam", out)
        self.assertIn("Next: wire the badge.", out)

    def test_issue_without_phase_block_keeps_local_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = _repo(tmp)
            path = main / "metrics/pr_cost/session-fix-c-phase.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"branch": "fix/c", "done": ["specify", "setup"], "handed_off_to": "cloud"}))
            self._cmd_in(main, "plain issue body")
            data = json.loads(path.read_text())
        self.assertEqual((data["done"], data["handed_off_to"]), (["specify", "setup"], None))


if __name__ == "__main__":
    unittest.main()
