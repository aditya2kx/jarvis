#!/usr/bin/env python3
"""Tests for scripts/pr_triage.py."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.pr_triage import (
    _author_class,
    _collect_claude_verdict,
    _collect_failing_checks,
    _collect_merge_status,
    _collect_unresolved_threads,
    _has_work,
    collect,
    main,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ROOT_CLAUDE = {
    "id": 1,
    "in_reply_to_id": None,
    "user": {"login": "claude[bot]"},
    "path": "scripts/foo.py",
    "line": 10,
    "original_line": 10,
    "body": "BLOCKING: this is wrong",
}
_ROOT_BUGBOT = {
    "id": 2,
    "in_reply_to_id": None,
    "user": {"login": "bugbot"},
    "path": "scripts/bar.py",
    "line": 20,
    "original_line": 20,
    "body": "Potential bug here",
}
_ROOT_HUMAN = {
    "id": 3,
    "in_reply_to_id": None,
    "user": {"login": "aditya2kx"},
    "path": "scripts/baz.py",
    "line": 30,
    "original_line": 30,
    "body": "Please address this",
}
_REPLY_TO_1 = {
    "id": 10,
    "in_reply_to_id": 1,
    "user": {"login": "jarvis-agent-bot328"},
    "path": "scripts/foo.py",
    "line": 10,
    "body": "fixed in abc1234",
}


# ---------------------------------------------------------------------------
# Author classification
# ---------------------------------------------------------------------------

class TestAuthorClass(unittest.TestCase):
    def test_claude_bot(self):
        self.assertEqual(_author_class("claude[bot]"), "claude-bot")
        self.assertEqual(_author_class("claude-opus"), "claude-bot")

    def test_bugbot(self):
        self.assertEqual(_author_class("bugbot"), "bugbot")
        self.assertEqual(_author_class("cursor-bot"), "bugbot")

    def test_human(self):
        self.assertEqual(_author_class("aditya2kx"), "human")
        self.assertEqual(_author_class("jarvis-agent-bot328"), "human")

    def test_empty(self):
        self.assertEqual(_author_class(""), "human")


# ---------------------------------------------------------------------------
# Unresolved threads
# ---------------------------------------------------------------------------

class TestUnresolvedThreads(unittest.TestCase):
    def _run(self, comments):
        with patch("scripts.pr_triage._gh_json", return_value=comments):
            return _collect_unresolved_threads(pr=1, repo="owner/repo")

    def test_all_unresolved(self):
        threads = self._run([_ROOT_CLAUDE, _ROOT_BUGBOT, _ROOT_HUMAN])
        self.assertEqual(len(threads), 3)
        classes = {t["author_class"] for t in threads}
        self.assertEqual(classes, {"claude-bot", "bugbot", "human"})

    def test_replied_thread_excluded(self):
        # ROOT_CLAUDE (id=1) has a reply (id=10 in_reply_to_id=1) → excluded
        threads = self._run([_ROOT_CLAUDE, _REPLY_TO_1, _ROOT_BUGBOT])
        ids = [t["id"] for t in threads]
        self.assertNotIn(1, ids)
        self.assertIn(2, ids)

    def test_all_replied_returns_empty(self):
        threads = self._run([_ROOT_CLAUDE, _REPLY_TO_1])
        self.assertEqual(threads, [])

    def test_no_comments_returns_empty(self):
        threads = self._run([])
        self.assertEqual(threads, [])

    def test_author_class_attached(self):
        threads = self._run([_ROOT_CLAUDE])
        self.assertEqual(threads[0]["author_class"], "claude-bot")

    def test_body_snippet_truncated(self):
        long_body = "word " * 50
        root = {**_ROOT_HUMAN, "body": long_body}
        threads = self._run([root])
        self.assertLessEqual(len(threads[0]["body_snippet"]), 130)


# ---------------------------------------------------------------------------
# Failing checks
# ---------------------------------------------------------------------------

class TestFailingChecks(unittest.TestCase):
    _ALL_CHECKS = [
        {"name": "pytest", "state": "SUCCESS", "link": "https://ci/1"},
        {"name": "Claude review", "state": "FAILURE", "link": "https://ci/2"},
        {"name": "doc-freshness", "state": "PENDING", "link": "https://ci/3"},
        {"name": "deploy", "state": "ERROR", "link": "https://ci/4"},
        {"name": "sandbox", "state": "CANCELLED", "link": "https://ci/5"},
    ]

    def _run(self, checks):
        with patch("scripts.pr_triage._gh_json", return_value=checks):
            return _collect_failing_checks(pr=1)

    def test_only_failing_states_surface(self):
        results = self._run(self._ALL_CHECKS)
        names = {r["name"] for r in results}
        self.assertEqual(names, {"Claude review", "deploy", "sandbox"})
        self.assertNotIn("pytest", names)
        self.assertNotIn("doc-freshness", names)

    def test_all_passing_returns_empty(self):
        passing = [{"name": "pytest", "state": "SUCCESS", "link": ""}]
        self.assertEqual(self._run(passing), [])

    def test_empty_returns_empty(self):
        self.assertEqual(self._run([]), [])

    def test_non_list_response_returns_empty(self):
        self.assertEqual(self._run({}), [])

    def test_link_preserved(self):
        failing = [{"name": "Claude review", "state": "FAILURE", "link": "https://ci/2"}]
        results = self._run(failing)
        self.assertEqual(results[0]["link"], "https://ci/2")


# ---------------------------------------------------------------------------
# Merge status
# ---------------------------------------------------------------------------

class TestMergeStatus(unittest.TestCase):
    def _run(self, data):
        with patch("scripts.pr_triage._gh_json", return_value=data):
            return _collect_merge_status(pr=1, repo="owner/repo")

    def test_behind(self):
        ms = self._run({"mergeable": "MERGEABLE", "mergeStateStatus": "BEHIND"})
        self.assertTrue(ms["behind"])
        self.assertFalse(ms["conflict"])

    def test_dirty_conflict(self):
        ms = self._run({"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"})
        self.assertTrue(ms["conflict"])

    def test_clean(self):
        ms = self._run({"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})
        self.assertFalse(ms["behind"])
        self.assertFalse(ms["conflict"])

    def test_non_dict_response(self):
        ms = self._run([])
        self.assertFalse(ms["behind"])
        self.assertFalse(ms["conflict"])


# ---------------------------------------------------------------------------
# Claude verdict
# ---------------------------------------------------------------------------

_APPROVE_COMMENT = {
    "id": 99,
    "user": {"login": "claude[bot]"},
    "html_url": "https://github.com/owner/repo/issues/1#issuecomment-99",
    "body": (
        "APPROVE\n\n"
        "## Evidence confidence rating: 97%\n"
        "Proves: sandbox ran.\n"
    ),
}
_REQUEST_CHANGES_COMMENT = {
    "id": 100,
    "user": {"login": "claude[bot]"},
    "html_url": "https://github.com/owner/repo/issues/1#issuecomment-100",
    "body": (
        "REQUEST CHANGES\n\n"
        "## Evidence confidence rating: 82%\n"
        "Evidence gaps (suggested additional collection):\n"
        "- Run sandbox e2e to cover the new path.\n"
    ),
}


class TestClaudeVerdict(unittest.TestCase):
    def _run(self, comments):
        with patch("scripts.pr_triage._gh_json", return_value=comments):
            return _collect_claude_verdict(pr=1, repo="owner/repo")

    def test_approve_parsed(self):
        cv = self._run([_APPROVE_COMMENT])
        self.assertEqual(cv["verdict"], "APPROVE")
        self.assertEqual(cv["confidence"], 97)

    def test_request_changes_parsed(self):
        cv = self._run([_REQUEST_CHANGES_COMMENT])
        self.assertEqual(cv["verdict"], "REQUEST_CHANGES")
        self.assertEqual(cv["confidence"], 82)

    def test_evidence_gaps_extracted(self):
        cv = self._run([_REQUEST_CHANGES_COMMENT])
        self.assertIn("sandbox e2e", cv["evidence_gaps"])

    def test_no_comments_returns_empty(self):
        cv = self._run([])
        self.assertEqual(cv, {})

    def test_non_claude_comments_ignored(self):
        human_comment = {**_APPROVE_COMMENT, "user": {"login": "aditya2kx"}}
        cv = self._run([human_comment])
        self.assertEqual(cv, {})

    def test_latest_comment_wins(self):
        # Second comment (REQUEST_CHANGES) should override APPROVE
        cv = self._run([_APPROVE_COMMENT, _REQUEST_CHANGES_COMMENT])
        self.assertEqual(cv["verdict"], "REQUEST_CHANGES")

    def test_unknown_verdict(self):
        comment = {**_APPROVE_COMMENT, "body": "COMMENT\nSome notes."}
        cv = self._run([comment])
        self.assertEqual(cv["verdict"], "UNKNOWN")


# ---------------------------------------------------------------------------
# has_work
# ---------------------------------------------------------------------------

class TestHasWork(unittest.TestCase):
    def _clean_triage(self):
        return {
            "pr": 1,
            "repo": "owner/repo",
            "unresolved_threads": [],
            "failing_checks": [],
            "merge_status": {"behind": False, "conflict": False, "raw": "CLEAN"},
            "claude_verdict": {"verdict": "APPROVE", "confidence": 97, "evidence_gaps": ""},
        }

    def test_clean_is_false(self):
        self.assertFalse(_has_work(self._clean_triage()))

    def test_unresolved_thread_triggers(self):
        t = self._clean_triage()
        t["unresolved_threads"] = [{"id": 1}]
        self.assertTrue(_has_work(t))

    def test_failing_check_triggers(self):
        t = self._clean_triage()
        t["failing_checks"] = [{"name": "deploy", "state": "FAILURE"}]
        self.assertTrue(_has_work(t))

    def test_behind_triggers(self):
        t = self._clean_triage()
        t["merge_status"]["behind"] = True
        self.assertTrue(_has_work(t))

    def test_conflict_triggers(self):
        t = self._clean_triage()
        t["merge_status"]["conflict"] = True
        self.assertTrue(_has_work(t))

    def test_request_changes_triggers(self):
        t = self._clean_triage()
        t["claude_verdict"]["verdict"] = "REQUEST_CHANGES"
        self.assertTrue(_has_work(t))

    def test_low_confidence_triggers(self):
        t = self._clean_triage()
        t["claude_verdict"]["confidence"] = 82
        self.assertTrue(_has_work(t))

    def test_exactly_95_confidence_is_clean(self):
        t = self._clean_triage()
        t["claude_verdict"]["confidence"] = 95
        self.assertFalse(_has_work(t))

    def test_no_verdict_is_clean(self):
        t = self._clean_triage()
        t["claude_verdict"] = {}
        self.assertFalse(_has_work(t))


# ---------------------------------------------------------------------------
# Exit codes via main()
# ---------------------------------------------------------------------------

class TestExitCodes(unittest.TestCase):
    def _mock_clean_collect(self, pr, repo):
        return {
            "pr": pr,
            "repo": repo,
            "unresolved_threads": [],
            "failing_checks": [],
            "merge_status": {"behind": False, "conflict": False, "raw": "CLEAN"},
            "claude_verdict": {"verdict": "APPROVE", "confidence": 98, "evidence_gaps": ""},
        }

    def _mock_work_collect(self, pr, repo):
        t = self._mock_clean_collect(pr, repo)
        t["failing_checks"] = [{"name": "deploy", "state": "FAILURE", "link": ""}]
        return t

    def test_exit_0_when_clean(self):
        with patch("scripts.pr_triage.collect", side_effect=self._mock_clean_collect), \
             patch("scripts.pr_triage._current_pr", return_value=1), \
             patch("scripts.pr_triage._repo", return_value="owner/repo"):
            code = main(argv=[])
        self.assertEqual(code, 0)

    def test_exit_1_when_work_remaining(self):
        with patch("scripts.pr_triage.collect", side_effect=self._mock_work_collect), \
             patch("scripts.pr_triage._current_pr", return_value=1), \
             patch("scripts.pr_triage._repo", return_value="owner/repo"):
            code = main(argv=[])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
