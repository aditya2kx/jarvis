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


# ---------------------------------------------------------------------------
# Intake handling in catch_up
# ---------------------------------------------------------------------------

def _make_intake_signal_comment(requirement: str, sid: str, author: str = "aditya2kx") -> dict:
    from post_merge_lifecycle import format_signal
    sig_block = format_signal("intake", "", signal_id=sid, issue=101, requirement=requirement)
    return {
        "body": f"/jarvis-new-task {requirement}\n\n{sig_block}",
        "createdAt": "2026-06-30T10:00:00Z",
        "author": {"login": author},
    }


class TestIntakeCatchUp(unittest.TestCase):
    def test_intake_dispatches_new_requirement(self):
        """Intake signal from allowlisted author triggers _dispatch (which runs new_requirement.py)."""
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            mdir.mkdir(parents=True)
            intake_seen = mdir / "listener-intake-seen.json"
            comment = _make_intake_signal_comment("add dark mode", "intake-uuid-1")
            with patch.object(L, "METRICS_DIR", mdir), \
                 patch.object(R, "METRICS_DIR", mdir), \
                 patch.object(L, "_INTAKE_SEEN_FILE", intake_seen), \
                 patch.object(L, "_gh_issue_comments", return_value=[comment]), \
                 patch.object(L, "_dispatch", return_value="dispatched") as mock_dispatch:
                n = L.catch_up(101)
        self.assertEqual(n, 1)
        mock_dispatch.assert_called_once()
        call_args = mock_dispatch.call_args
        self.assertEqual(call_args[0][0], "")  # branch is empty for intake
        self.assertEqual(call_args[0][1].get("requirement"), "add dark mode")

    def test_intake_dedup_via_seen_file(self):
        """Second catch_up with same intake signal id is a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            mdir.mkdir(parents=True)
            intake_seen = mdir / "listener-intake-seen.json"
            comment = _make_intake_signal_comment("add dark mode", "intake-uuid-dup")
            with patch.object(L, "METRICS_DIR", mdir), \
                 patch.object(R, "METRICS_DIR", mdir), \
                 patch.object(L, "_INTAKE_SEEN_FILE", intake_seen), \
                 patch.object(L, "_gh_issue_comments", return_value=[comment]), \
                 patch.object(L, "_dispatch", return_value="dispatched") as mock_dispatch:
                # First call — should dispatch
                n1 = L.catch_up(101)
                # Second call with same comment — should be deduped
                n2 = L.catch_up(101)
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0)  # deduped
        self.assertEqual(mock_dispatch.call_count, 1)

    def test_intake_unauthorized_author_skipped(self):
        """Intake signal from non-allowlisted author is ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            mdir.mkdir(parents=True)
            intake_seen = mdir / "listener-intake-seen.json"
            comment = _make_intake_signal_comment("bad actor", "intake-uuid-unauth",
                                                   author="random-outsider")
            with patch.object(L, "METRICS_DIR", mdir), \
                 patch.object(R, "METRICS_DIR", mdir), \
                 patch.object(L, "_INTAKE_SEEN_FILE", intake_seen), \
                 patch.object(L, "_gh_issue_comments", return_value=[comment]), \
                 patch.object(L, "_dispatch") as mock_dispatch:
                n = L.catch_up(101)
        self.assertEqual(n, 0)
        mock_dispatch.assert_not_called()

    def test_intake_no_requirement_still_dispatches(self):
        """Intake signal without a requirement text still dispatches (empty string)."""
        from post_merge_lifecycle import format_signal
        sig_block = format_signal("intake", "", signal_id="intake-uuid-noreq", issue=101)
        comment = {
            "body": f"/jarvis-new-task\n\n{sig_block}",
            "createdAt": "2026-06-30T10:00:00Z",
            "author": {"login": "aditya2kx"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            mdir.mkdir(parents=True)
            intake_seen = mdir / "listener-intake-seen.json"
            with patch.object(L, "METRICS_DIR", mdir), \
                 patch.object(R, "METRICS_DIR", mdir), \
                 patch.object(L, "_INTAKE_SEEN_FILE", intake_seen), \
                 patch.object(L, "_gh_issue_comments", return_value=[comment]), \
                 patch.object(L, "_dispatch", return_value="dispatched") as mock_dispatch:
                n = L.catch_up(101)
        self.assertEqual(n, 1)
        mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# watch-all enumeration
# ---------------------------------------------------------------------------

class TestWatchAll(unittest.TestCase):
    def test_watch_all_calls_catch_up_for_each_target(self):
        """watch-all enumerates issues + PRs and calls catch_up for each unique number."""
        call_log: list[int] = []

        def fake_catch_up(n, **kwargs):
            call_log.append(n)
            return 0

        with patch.object(L, "_gh_open_jarvis_issue_numbers", return_value=[101, 102]), \
             patch.object(L, "_gh_open_pr_numbers", return_value=[115, 102]), \
             patch.object(L, "catch_up", side_effect=fake_catch_up), \
             patch("time.sleep", side_effect=KeyboardInterrupt):
            try:
                L.watch_all(interval=1)
            except KeyboardInterrupt:
                pass

        # Should have called catch_up for union: {101, 102, 115}
        self.assertEqual(sorted(call_log), [101, 102, 115])

    def test_watch_all_deduplicates_issue_and_pr_numbers(self):
        """PR #102 is in both lists — catch_up called once per unique number."""
        call_log: list[int] = []

        def fake_catch_up(n, **kwargs):
            call_log.append(n)
            return 0

        with patch.object(L, "_gh_open_jarvis_issue_numbers", return_value=[101]), \
             patch.object(L, "_gh_open_pr_numbers", return_value=[101, 115]), \
             patch.object(L, "catch_up", side_effect=fake_catch_up), \
             patch("time.sleep", side_effect=KeyboardInterrupt):
            try:
                L.watch_all(interval=1)
            except KeyboardInterrupt:
                pass

        self.assertEqual(sorted(call_log), [101, 115])


# ---------------------------------------------------------------------------
# ensure-daemon idempotency
# ---------------------------------------------------------------------------

class TestEnsureDaemon(unittest.TestCase):
    def test_already_running_is_noop(self):
        """If launchctl list succeeds, ensure_daemon returns already_running."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = L.ensure_daemon()
        self.assertEqual(result, "already_running")
        # launchctl load should NOT have been called
        load_calls = [c for c in mock_run.call_args_list
                      if "load" in str(c)]
        self.assertEqual(len(load_calls), 0)

    def test_not_macos_returns_not_macos(self):
        """On non-macOS platforms, ensure_daemon returns not_macos."""
        with patch.object(L.sys, "platform", "linux"):
            result = L.ensure_daemon()
        self.assertEqual(result, "not_macos")

    def test_installs_when_not_loaded(self):
        """If launchctl list fails (not loaded), plist is written and load is called."""
        with tempfile.TemporaryDirectory() as tmp:
            # Place the fake plist inside tmp (parent already exists)
            fake_dir = Path(tmp) / "LaunchAgents"
            fake_dir.mkdir()
            fake_plist = fake_dir / "com.jarvis.devsignals.plist"
            with patch.object(L, "_LAUNCHD_PLIST", fake_plist), \
                 patch("subprocess.run") as mock_run:
                # First call (launchctl list) returns non-zero → not loaded
                # Second call (launchctl load) returns 0 → success
                mock_run.side_effect = [
                    MagicMock(returncode=1),   # launchctl list → not loaded
                    MagicMock(returncode=0),   # launchctl load → success
                ]
                result = L.ensure_daemon()
            # Assertions inside the with block while temp dir still exists
            self.assertEqual(result, "installed")
            self.assertTrue(fake_plist.exists())
            plist_text = fake_plist.read_text()
            self.assertIn("watch-all", plist_text)
            self.assertIn("com.jarvis.devsignals", plist_text)


if __name__ == "__main__":
    unittest.main()
