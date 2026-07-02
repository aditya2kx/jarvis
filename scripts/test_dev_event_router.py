#!/usr/bin/env python3
"""Unit tests for dev_event_router.py — no network, no GH API."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dev_event_router as R


def _make_cache(tmp: str, branch: str, extra: dict | None = None) -> Path:
    """Write a minimal phase-cache JSON so the branch is 'tracked'."""
    mdir = Path(tmp) / "metrics" / "pr_cost"
    mdir.mkdir(parents=True, exist_ok=True)
    data = {"branch": branch, "issue": 42, "delivered_signals": [],
            "pending_event_count": 0, **(extra or {})}
    path = mdir / f"session-{R._slug(branch)}-phase.json"
    path.write_text(json.dumps(data))
    return path


def _signal(event: str, branch: str, sid: str = "test-uuid-1", **kw) -> dict:
    return {"id": sid, "event": event, "branch": branch, "ts": "2026-06-30T00:00:00Z", **kw}


class TestRouteSignalUntracked(unittest.TestCase):
    def test_unrouted_when_no_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(R, "METRICS_DIR", Path(tmp) / "metrics" / "pr_cost"):
                result = R.route_signal(_signal("ci_failed", "fix/no-cache"))
        self.assertEqual(result, "unrouted")


class TestRouteSignalDelivered(unittest.TestCase):
    def test_delivers_ci_failed(self):
        branch = "fix/test-ci-failed"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("ci_failed", branch))
                self.assertEqual(result, "delivered")
                # Inbox must have one entry
                inbox = mdir / f"session-{R._slug(branch)}-pending.jsonl"
                self.assertTrue(inbox.exists())
                record = json.loads(inbox.read_text().strip())
                self.assertEqual(record["kind"], "babysit_ci")

    def test_delivers_ci_passed(self):
        branch = "fix/test-ci-passed"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("ci_passed", branch))
        self.assertEqual(result, "delivered")

    def test_delivers_pr_merged(self):
        branch = "fix/test-pr-merged"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("pr_merged", branch))
        self.assertEqual(result, "delivered")

    def test_event_kind_mapping(self):
        """Each event maps to the correct inbox kind."""
        cases = [
            ("ci_failed", "babysit_ci"),
            ("ci_passed", "ci_green"),
            ("ci_other", "ci_status"),
            ("pr_merged", "retrospective"),
            ("intake", "intake"),
        ]
        for event, expected_kind in cases:
            branch = f"fix/kind-{event}"
            with tempfile.TemporaryDirectory() as tmp:
                mdir = Path(tmp) / "metrics" / "pr_cost"
                _make_cache(tmp, branch)
                extra = {"author": "aditya2kx"} if event == "intake" else {}
                with patch.object(R, "METRICS_DIR", mdir):
                    R.route_signal(_signal(event, branch), author="aditya2kx")
                    inbox = mdir / f"session-{R._slug(branch)}-pending.jsonl"
                    record = json.loads(inbox.read_text().strip().splitlines()[-1])
                self.assertEqual(record["kind"], expected_kind, f"wrong kind for {event}")


class TestIdempotency(unittest.TestCase):
    def test_duplicate_signal_skipped(self):
        branch = "fix/test-dedup"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            sig = _signal("ci_failed", branch, sid="dup-uuid")
            with patch.object(R, "METRICS_DIR", mdir):
                r1 = R.route_signal(sig)
                r2 = R.route_signal(sig)  # same id
                inbox = mdir / f"session-{R._slug(branch)}-pending.jsonl"
                count = len(inbox.read_text().strip().splitlines())
            self.assertEqual(r1, "delivered")
            self.assertEqual(r2, "duplicate")
            self.assertEqual(count, 1)


class TestDebounce(unittest.TestCase):
    def test_ci_failed_debounced_within_window(self):
        branch = "fix/test-debounce"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            import time
            now = time.time()
            with patch.object(R, "METRICS_DIR", mdir):
                r1 = R.route_signal(_signal("ci_failed", branch, sid="u1"), now=now)
                # Second ci_failed 10 s later — within 300 s window
                r2 = R.route_signal(_signal("ci_failed", branch, sid="u2"), now=now + 10)
            self.assertEqual(r1, "delivered")
            self.assertEqual(r2, "debounced")

    def test_ci_passed_never_debounced(self):
        branch = "fix/test-pass-dedup"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            import time
            now = time.time()
            with patch.object(R, "METRICS_DIR", mdir):
                r1 = R.route_signal(_signal("ci_passed", branch, sid="p1"), now=now)
                r2 = R.route_signal(_signal("ci_passed", branch, sid="p2"), now=now + 5)
            # Both distinct ids, ci_passed is not debounced
            self.assertEqual(r1, "delivered")
            self.assertEqual(r2, "delivered")


class TestAllowlist(unittest.TestCase):
    def test_intake_from_allowed_author(self):
        branch = "fix/test-intake-ok"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("intake", branch), author="aditya2kx")
        self.assertEqual(result, "delivered")

    def test_intake_from_bot_allowed(self):
        branch = "fix/test-intake-bot"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("intake", branch, sid="u-bot"), author="jarvis-agent-bot328")
        self.assertEqual(result, "delivered")

    def test_intake_unauthorized_author(self):
        branch = "fix/test-intake-bad"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("intake", branch), author="random-outsider")
        self.assertEqual(result, "unauthorized")

    def test_intake_no_author(self):
        branch = "fix/test-intake-noauth"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("intake", branch))
        self.assertEqual(result, "unauthorized")

    def test_ci_failed_no_author_allowed(self):
        """CI signals don't need an author — they come from trusted workflows."""
        branch = "fix/test-ci-noauth"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("ci_failed", branch))
        self.assertEqual(result, "delivered")


class TestDrain(unittest.TestCase):
    def test_drain_fifo_order(self):
        branch = "fix/test-drain"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                # Deliver two events
                R.route_signal(_signal("ci_failed", branch, sid="first"))
                R.route_signal(_signal("pr_merged", branch, sid="second"))
                # Drain should return oldest first
                e1 = R.drain(branch)
                e2 = R.drain(branch)
                e3 = R.drain(branch)  # empty
            self.assertIsNotNone(e1)
            self.assertEqual(e1["id"], "first")
            self.assertIsNotNone(e2)
            self.assertEqual(e2["id"], "second")
            self.assertIsNone(e3)

    def test_drain_moves_to_processed(self):
        branch = "fix/test-drain-processed"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                R.route_signal(_signal("ci_passed", branch, sid="proc-1"))
                R.drain(branch)
                processed = mdir / f"session-{R._slug(branch)}-processed.jsonl"
                self.assertTrue(processed.exists())
                record = json.loads(processed.read_text().strip())
                self.assertEqual(record["id"], "proc-1")

    def test_drain_decrements_pending_count(self):
        branch = "fix/test-drain-count"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                R.route_signal(_signal("ci_failed", branch, sid="cnt-1"))
                cache_before = json.loads((mdir / f"session-{R._slug(branch)}-phase.json").read_text())
                self.assertEqual(cache_before["pending_event_count"], 1)
                R.drain(branch)
                cache_after = json.loads((mdir / f"session-{R._slug(branch)}-phase.json").read_text())
                self.assertEqual(cache_after["pending_event_count"], 0)

    def test_drain_empty_inbox_returns_none(self):
        branch = "fix/test-drain-empty"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.drain(branch)
        self.assertIsNone(result)


class TestCommentSignal(unittest.TestCase):
    """Comment events → address_comment kind; allowlist enforced."""

    def test_comment_from_allowed_author_delivered(self):
        branch = "fix/test-comment-ok"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(
                    _signal("comment", branch, comment_url="https://github.com/test/1"),
                    author="aditya2kx",
                )
                self.assertEqual(result, "delivered")
                inbox = mdir / f"session-{R._slug(branch)}-pending.jsonl"
                record = json.loads(inbox.read_text().strip())
                self.assertEqual(record["kind"], "address_comment")

    def test_comment_unauthorized_author(self):
        branch = "fix/test-comment-bad"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(
                    _signal("comment", branch),
                    author="random-outsider",
                )
        self.assertEqual(result, "unauthorized")

    def test_comment_no_author_passes_through(self):
        """Comment signals with no author pass through — workflow is the primary gate."""
        branch = "fix/test-comment-noauth"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("comment", branch), author=None)
        self.assertEqual(result, "delivered")

    def test_ci_failed_still_works_without_author(self):
        """CI signals are NOT gated by author even after the comment change."""
        branch = "fix/test-ci-no-author-post-comment"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("ci_failed", branch))
        self.assertEqual(result, "delivered")


class TestWorkflowYamlValid(unittest.TestCase):
    """Smoke-check that jarvis-dev-signals.yml is valid YAML and has expected jobs."""

    def test_workflow_yaml_parses(self):
        import yaml  # type: ignore
        wf = Path(__file__).parent.parent / ".github" / "workflows" / "jarvis-dev-signals.yml"
        if not wf.exists():
            self.skipTest("jarvis-dev-signals.yml not yet written (M3)")
        yaml.safe_load(wf.read_text())

    def test_comment_signal_job_exists(self):
        import yaml  # type: ignore
        wf = Path(__file__).parent.parent / ".github" / "workflows" / "jarvis-dev-signals.yml"
        if not wf.exists():
            self.skipTest("jarvis-dev-signals.yml not yet written")
        data = yaml.safe_load(wf.read_text())
        jobs = data.get("jobs", {})
        self.assertIn("comment-signal", jobs, "comment-signal job must exist in jarvis-dev-signals.yml")

    def test_comment_signal_loop_guard(self):
        """The comment-signal if: must exclude comments that already contain 'jarvis-signal'."""
        import yaml  # type: ignore
        wf = Path(__file__).parent.parent / ".github" / "workflows" / "jarvis-dev-signals.yml"
        if not wf.exists():
            self.skipTest("jarvis-dev-signals.yml not yet written")
        data = yaml.safe_load(wf.read_text())
        job_if = data["jobs"]["comment-signal"].get("if", "")
        self.assertIn("jarvis-signal", job_if,
                      "loop guard (!contains(... 'jarvis-signal')) must be in comment-signal if:")

    def test_pr_issue_link_job_exists_and_triggers_on_opened(self):
        """obs 3: a pr-issue-link job must fire on pull_request.opened."""
        import yaml  # type: ignore
        wf = Path(__file__).parent.parent / ".github" / "workflows" / "jarvis-dev-signals.yml"
        if not wf.exists():
            self.skipTest("jarvis-dev-signals.yml not yet written")
        data = yaml.safe_load(wf.read_text())
        jobs = data.get("jobs", {})
        self.assertIn("pr-issue-link", jobs, "pr-issue-link job must exist")
        # `on.pull_request.types` must include `opened`.
        # PyYAML parses the bare key `on:` as boolean True, so accept either.
        on = data.get("on", data.get(True, {}))
        pr_types = on["pull_request"]["types"]
        self.assertIn("opened", pr_types, "pull_request trigger must include 'opened'")
        job_if = jobs["pr-issue-link"].get("if", "")
        self.assertIn("opened", job_if, "pr-issue-link if: must gate on action == 'opened'")

    def test_pr_issue_link_is_idempotent_and_refs_issue(self):
        """The link step must be idempotent and append Refs #<issue> to the PR body."""
        import yaml  # type: ignore
        wf = Path(__file__).parent.parent / ".github" / "workflows" / "jarvis-dev-signals.yml"
        if not wf.exists():
            self.skipTest("jarvis-dev-signals.yml not yet written")
        data = yaml.safe_load(wf.read_text())
        steps = data["jobs"]["pr-issue-link"]["steps"]
        run_text = "\n".join(s.get("run", "") for s in steps)
        self.assertIn("Refs #", run_text, "must add Refs #<issue> to the PR body")
        self.assertIn("already refs", run_text, "must guard against double-appending Refs")
        self.assertIn("already links", run_text, "must guard against double-commenting the link")


class TestWorktreeInboxRouting(unittest.TestCase):
    """obs 4b: the daemon must write the inbox to the child worktree's metrics
    dir (from cache['worktree_path']) so the child drain.sh actually sees it."""

    def test_inbox_written_to_worktree_when_cache_has_path(self):
        branch = "fix/test-wt-route"
        with tempfile.TemporaryDirectory() as daemon, tempfile.TemporaryDirectory() as wt:
            daemon_mdir = Path(daemon) / "metrics" / "pr_cost"
            # Child worktree must exist on disk for the router to honor it.
            (Path(wt) / "metrics" / "pr_cost").mkdir(parents=True, exist_ok=True)
            _make_cache(daemon, branch, extra={"worktree_path": str(wt)})
            with patch.object(R, "METRICS_DIR", daemon_mdir):
                result = R.route_signal(_signal("ci_failed", branch))
            self.assertEqual(result, "delivered")
            child_inbox = Path(wt) / "metrics" / "pr_cost" / f"session-{R._slug(branch)}-pending.jsonl"
            self.assertTrue(child_inbox.exists(), "inbox must land in the child worktree")
            # And NOT in the daemon dir.
            daemon_inbox = daemon_mdir / f"session-{R._slug(branch)}-pending.jsonl"
            self.assertFalse(daemon_inbox.exists(), "inbox must NOT stay in the daemon dir")
            # Dedup/phase cache stays daemon-side.
            self.assertTrue((daemon_mdir / f"session-{R._slug(branch)}-phase.json").exists())

    def test_fallback_to_module_dir_when_no_worktree_path(self):
        branch = "fix/test-wt-fallback"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch)  # no worktree_path
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("ci_failed", branch))
            self.assertEqual(result, "delivered")
            self.assertTrue((mdir / f"session-{R._slug(branch)}-pending.jsonl").exists())

    def test_fallback_when_worktree_path_missing_on_disk(self):
        branch = "fix/test-wt-gone"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = Path(tmp) / "metrics" / "pr_cost"
            _make_cache(tmp, branch, extra={"worktree_path": "/nonexistent/gone-wt"})
            with patch.object(R, "METRICS_DIR", mdir):
                result = R.route_signal(_signal("ci_failed", branch))
            self.assertEqual(result, "delivered")
            self.assertTrue((mdir / f"session-{R._slug(branch)}-pending.jsonl").exists())

    def test_route_then_drain_roundtrip_via_worktree(self):
        """End-to-end: route writes to worktree, drain (reading same cache) pops it."""
        branch = "fix/test-wt-roundtrip"
        with tempfile.TemporaryDirectory() as daemon, tempfile.TemporaryDirectory() as wt:
            daemon_mdir = Path(daemon) / "metrics" / "pr_cost"
            (Path(wt) / "metrics" / "pr_cost").mkdir(parents=True, exist_ok=True)
            _make_cache(daemon, branch, extra={"worktree_path": str(wt)})
            with patch.object(R, "METRICS_DIR", daemon_mdir):
                R.route_signal(_signal("pr_merged", branch, sid="rt-1"))
                popped = R.drain(branch)
            self.assertIsNotNone(popped)
            self.assertEqual(popped["id"], "rt-1")
            processed = Path(wt) / "metrics" / "pr_cost" / f"session-{R._slug(branch)}-processed.jsonl"
            self.assertTrue(processed.exists(), "processed log must also live in the worktree")


if __name__ == "__main__":
    unittest.main()
