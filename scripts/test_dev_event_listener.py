#!/usr/bin/env python3
"""Unit tests for dev_event_listener.py — gh CLI + cursor/osascript mocked."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dev_event_listener as L
import dev_event_router as R


def _make_phase(tmp: str, branch: str, extra: dict | None = None) -> Path:
    mdir = Path(tmp) / "metrics" / "pr_cost"
    mdir.mkdir(parents=True, exist_ok=True)
    data = {"branch": branch, "issue": 99, "delivered_signals": [],
            "pending_event_count": 0, "last_signal_cursor": None, **(extra or {})}
    path = mdir / f"session-{R._slug(branch)}-phase.json"
    path.write_text(json.dumps(data))
    return mdir


def _make_signal_comment(event: str, branch: str, sid: str) -> dict:
    from post_merge_lifecycle import format_signal
    sig_block = format_signal(event, branch, signal_id=sid, pr=42)
    return {
        "body": f"CI event on {branch}\n\n{sig_block}",
        "createdAt": "2026-06-30T10:00:00Z",
        "author": {"login": "aditya2kx"},
    }


# ---------------------------------------------------------------------------
# Catch-up tests
# ---------------------------------------------------------------------------

class TestCatchUp(unittest.TestCase):
    def test_delivers_new_signal(self):
        branch = "fix/test-listener-delivery"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _make_phase(tmp, branch)
            comment = _make_signal_comment("ci_failed", branch, "listen-uuid-1")
            with patch.object(L, "METRICS_DIR", mdir), \
                 patch.object(R, "METRICS_DIR", mdir), \
                 patch.object(L, "_gh_issue_comments", return_value=[comment]), \
                 patch.object(L, "_dispatch", return_value="dispatched"):
                n = L.catch_up(99, branch=branch)
        self.assertEqual(n, 1)

    def test_skips_comment_without_signal(self):
        branch = "fix/test-listener-no-sig"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _make_phase(tmp, branch)
            comment = {"body": "Just a plain comment", "createdAt": "2026-06-30T10:00:00Z",
                       "author": {"login": "aditya2kx"}}
            with patch.object(L, "METRICS_DIR", mdir), \
                 patch.object(R, "METRICS_DIR", mdir), \
                 patch.object(L, "_gh_issue_comments", return_value=[comment]):
                n = L.catch_up(99, branch=branch)
        self.assertEqual(n, 0)

    def test_filters_by_since_cursor(self):
        branch = "fix/test-listener-since"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _make_phase(tmp, branch, extra={"last_signal_cursor": "2026-06-30T12:00:00Z"})
            # This comment is BEFORE the cursor — should be skipped
            comment = _make_signal_comment("ci_failed", branch, "old-uuid")
            comment["createdAt"] = "2026-06-30T11:00:00Z"
            with patch.object(L, "METRICS_DIR", mdir), \
                 patch.object(R, "METRICS_DIR", mdir), \
                 patch.object(L, "_gh_issue_comments", return_value=[comment]):
                n = L.catch_up(99, branch=branch)
        self.assertEqual(n, 0)

    def test_dry_run_does_not_write_inbox(self):
        branch = "fix/test-listener-dryrun"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _make_phase(tmp, branch)
            comment = _make_signal_comment("pr_merged", branch, "dry-uuid")
            with patch.object(L, "METRICS_DIR", mdir), \
                 patch.object(R, "METRICS_DIR", mdir), \
                 patch.object(L, "_gh_issue_comments", return_value=[comment]):
                n = L.catch_up(99, branch=branch, dry_run=True)
            inbox = mdir / f"session-{R._slug(branch)}-pending.jsonl"
        self.assertEqual(n, 1)
        self.assertFalse(inbox.exists(), "dry-run must not write to inbox")

    def test_skips_signal_for_different_branch(self):
        branch = "fix/test-listener-branch-filter"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = _make_phase(tmp, branch)
            # Signal is for a DIFFERENT branch
            comment = _make_signal_comment("ci_failed", "fix/other-branch", "other-uuid")
            with patch.object(L, "METRICS_DIR", mdir), \
                 patch.object(R, "METRICS_DIR", mdir), \
                 patch.object(L, "_gh_issue_comments", return_value=[comment]):
                n = L.catch_up(99, branch=branch)
        self.assertEqual(n, 0)


# ---------------------------------------------------------------------------
# Auto-open / focus tests
# ---------------------------------------------------------------------------

class TestOpenOrFocusWorktree(unittest.TestCase):
    def test_calls_cursor_open_when_path_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "my-worktree"
            wt.mkdir()
            with patch.object(L, "_cursor_open") as mock_open:
                L._open_or_focus_worktree(wt)
            mock_open.assert_called_once_with(wt)

    def test_no_open_when_path_missing_and_no_requirement(self):
        wt = Path("/nonexistent/worktree-abc")
        with patch.object(L, "_cursor_open") as mock_open, \
             patch("subprocess.run") as mock_run:
            L._open_or_focus_worktree(wt, create_if_missing=False)
        mock_open.assert_not_called()

    def test_creates_worktree_for_intake_when_missing(self):
        wt = Path("/nonexistent/new-worktree-intake")
        with patch("subprocess.run") as mock_run:
            L._open_or_focus_worktree(wt, requirement="add feature X", create_if_missing=True)
        # new_requirement.py should be called
        calls = [str(c) for c in mock_run.call_args_list]
        self.assertTrue(any("new_requirement" in c for c in calls))


class TestCursorOpen(unittest.TestCase):
    def test_uses_cursor_cli_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            with patch("shutil.which", return_value="/usr/local/bin/cursor"), \
                 patch("subprocess.run") as mock_run:
                L._cursor_open(wt)
            first_call_cmd = mock_run.call_args_list[0][0][0]
            self.assertIn("cursor", first_call_cmd[0])

    def test_falls_back_to_open_a_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            with patch("shutil.which", return_value=None), \
                 patch("subprocess.run") as mock_run:
                L._cursor_open(wt)
            first_call_cmd = mock_run.call_args_list[0][0][0]
            self.assertEqual(first_call_cmd[:3], ["open", "-a", "Cursor"])


# ---------------------------------------------------------------------------
# Busy detection + dispatch tests
# ---------------------------------------------------------------------------

class TestWorktreeBusy(unittest.TestCase):
    def _write_status(self, tmp: str, branch: str, state: str, age_sec: int = 0) -> Path:
        mdir = Path(tmp) / "metrics" / "pr_cost"
        mdir.mkdir(parents=True, exist_ok=True)
        import datetime
        ts = datetime.datetime.utcnow() - datetime.timedelta(seconds=age_sec)
        p = mdir / f"session-{R._slug(branch)}-status.json"
        p.write_text(json.dumps({"state": state, "heartbeat": ts.isoformat() + "Z"}))
        return mdir

    def test_busy_when_state_busy_and_fresh(self):
        branch = "fix/test-busy"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = self._write_status(tmp, branch, "busy", age_sec=30)
            with patch.object(L, "METRICS_DIR", mdir):
                result = L._worktree_busy(branch)
        self.assertTrue(result)

    def test_idle_when_state_idle(self):
        branch = "fix/test-idle"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = self._write_status(tmp, branch, "idle")
            with patch.object(L, "METRICS_DIR", mdir):
                result = L._worktree_busy(branch)
        self.assertFalse(result)

    def test_idle_when_stale_lock(self):
        branch = "fix/test-stale"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = self._write_status(tmp, branch, "busy", age_sec=700)  # > STALE_LOCK_SEC=600
            with patch.object(L, "METRICS_DIR", mdir):
                result = L._worktree_busy(branch)
        self.assertFalse(result)

    def test_idle_when_no_status_file(self):
        branch = "fix/test-no-status"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            with patch.object(L, "METRICS_DIR", mdir):
                result = L._worktree_busy(branch)
        self.assertFalse(result)


class TestDispatch(unittest.TestCase):
    def test_queued_when_busy(self):
        branch = "fix/test-dispatch-busy"
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "wt"
            wt.mkdir()
            with patch.object(L, "_auto_open_enabled", return_value=True), \
                 patch.object(L, "_auto_dispatch_enabled", return_value=True), \
                 patch.object(L, "_worktree_busy", return_value=True), \
                 patch.object(L, "_worktree_path_for", return_value=wt), \
                 patch.object(L, "_open_or_focus_worktree"), \
                 patch.object(L, "_notify") as mock_notify:
                result = L._dispatch(branch, {"event": "ci_failed"})
        self.assertEqual(result, "queued")
        mock_notify.assert_called_once()

    def test_dispatched_when_idle(self):
        branch = "fix/test-dispatch-idle"
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "wt"
            wt.mkdir()
            with patch.object(L, "_auto_open_enabled", return_value=True), \
                 patch.object(L, "_auto_dispatch_enabled", return_value=True), \
                 patch.object(L, "_worktree_busy", return_value=False), \
                 patch.object(L, "_worktree_path_for", return_value=wt), \
                 patch.object(L, "_open_or_focus_worktree"), \
                 patch.object(L, "_seed_drain_prompt") as mock_seed:
                result = L._dispatch(branch, {"event": "ci_failed"})
        self.assertEqual(result, "dispatched")
        mock_seed.assert_called_once()

    def test_notify_only_when_auto_dispatch_off(self):
        branch = "fix/test-dispatch-notify"
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "wt"
            wt.mkdir()
            with patch.object(L, "_auto_open_enabled", return_value=True), \
                 patch.object(L, "_auto_dispatch_enabled", return_value=False), \
                 patch.object(L, "_worktree_path_for", return_value=wt), \
                 patch.object(L, "_open_or_focus_worktree"), \
                 patch.object(L, "_notify") as mock_notify:
                result = L._dispatch(branch, {"event": "ci_failed"})
        self.assertEqual(result, "notify_only")
        mock_notify.assert_called_once()

    def test_no_worktree_returns_no_worktree(self):
        branch = "fix/test-dispatch-no-wt"
        with patch.object(L, "_auto_open_enabled", return_value=True), \
             patch.object(L, "_worktree_path_for", return_value=None), \
             patch.object(L, "_open_or_focus_worktree"):
            result = L._dispatch(branch, {"event": "ci_other"})
        self.assertEqual(result, "no_worktree")

    def test_disabled_when_auto_open_off(self):
        with patch.object(L, "_auto_open_enabled", return_value=False):
            result = L._dispatch("fix/any", {"event": "ci_failed"})
        self.assertEqual(result, "disabled")


# ---------------------------------------------------------------------------
# Drain integration (via router)
# ---------------------------------------------------------------------------

class TestDrainViaRouter(unittest.TestCase):
    def test_drain_returns_none_when_empty(self):
        branch = "fix/test-drain-empty"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            mdir.mkdir(parents=True)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.drain(branch)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
