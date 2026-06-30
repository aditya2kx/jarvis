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


class TestWorkflowYamlValid(unittest.TestCase):
    """Smoke-check that jarvis-dev-signals.yml is valid YAML when it exists."""

    def test_workflow_yaml_parses(self):
        import yaml  # type: ignore
        wf = Path(__file__).parent.parent / ".github" / "workflows" / "jarvis-dev-signals.yml"
        if not wf.exists():
            self.skipTest("jarvis-dev-signals.yml not yet written (M3)")
        yaml.safe_load(wf.read_text())


if __name__ == "__main__":
    unittest.main()
