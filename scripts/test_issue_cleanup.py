#!/usr/bin/env python3
"""Tests for issue_cleanup.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import issue_cleanup as C


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _issue(num: int, title: str, body: str = "") -> dict:
    return {"number": num, "title": title, "body": body}


def _pr(num: int, body: str, head: str = "") -> dict:
    return {"number": num, "body": body, "headRefName": head}


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------

class TestFindDuplicates(unittest.TestCase):
    def test_branch_keyed_duplicate(self):
        """Two issues sharing the same Branch: `...` → non-[work] is flagged."""
        issues = [
            _issue(87, "Use Git Issues for New Requirements Intake",
                   "manually filed\nBranch: `fix/use-git-issues`"),
            _issue(88, "[work] Use Git Issues for New Requirements Intake",
                   "Work item: **...**\nBranch: `fix/use-git-issues`\n<!-- phase-state -->"),
        ]
        candidates = C.find_duplicates(issues)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        # [work]-prefixed issue #88 is the survivor; #87 is the duplicate
        self.assertEqual(c.issue_num, 87)
        self.assertEqual(c.survivor, 88)
        self.assertIn("duplicate", c.reason)

    def test_lowest_number_wins_when_no_work_prefix(self):
        """Without [work] prefix, lowest number wins."""
        issues = [
            _issue(20, "thing A", "Branch: `fix/thing`"),
            _issue(15, "thing B", "Branch: `fix/thing`"),
        ]
        candidates = C.find_duplicates(issues)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].issue_num, 20)
        self.assertEqual(candidates[0].survivor, 15)

    def test_single_issue_per_branch_not_flagged(self):
        """Only one issue per branch — nothing to flag."""
        issues = [
            _issue(10, "[work] solo", "Branch: `fix/solo`"),
        ]
        self.assertEqual(C.find_duplicates(issues), [])

    def test_no_branch_in_body_not_flagged(self):
        """Issue without Branch: in body is ignored by branch-key dedup."""
        issues = [
            _issue(1, "no branch", "just some content"),
            _issue(2, "no branch either", "also no branch"),
        ]
        self.assertEqual(C.find_duplicates(issues), [])

    def test_crossref_pattern_flagged_same_list(self):
        """Issue body containing (issue #NN) cross-ref is flagged when ref is in open list."""
        issues = [
            _issue(87, "origin", "some original issue"),
            _issue(88, "[work] derived",
                   "Use Git Issues (issue #87)\nBranch: `fix/use-git-issues`"),
        ]
        # #88 has "(issue #87)" in body; #87 is in open list → #88 is duplicate
        candidates = C.find_duplicates(issues)
        flagged = [c for c in candidates if c.issue_num == 88]
        self.assertTrue(flagged, "#88 should be flagged as referencing #87")
        self.assertEqual(flagged[0].survivor, 87)

    def test_crossref_to_external_issue_flagged(self):
        """Cross-ref to a non-jarvis-work open issue is also flagged (live check)."""
        issues = [
            _issue(88, "[work] derived",
                   "Use Git Issues (issue #87)\nBranch: `fix/use-git-issues-for-new-requirements`"),
        ]
        # #87 is NOT in the issues list (manually-filed, no jarvis-work label),
        # but _is_open_issue(87) returns True → #88 should still be flagged.
        with patch.object(C, "_is_open_issue", return_value=True):
            candidates = C.find_duplicates(issues)
        flagged = [c for c in candidates if c.issue_num == 88]
        self.assertTrue(flagged, "#88 should be flagged even when #87 is not in the jarvis-work list")
        self.assertEqual(flagged[0].survivor, 87)

    def test_three_issues_same_branch_two_flagged(self):
        """Three issues on same branch → two flagged, one [work] survivor."""
        issues = [
            _issue(10, "manual A", "Branch: `fix/shared`"),
            _issue(11, "manual B", "Branch: `fix/shared`"),
            _issue(12, "[work] tracked", "Branch: `fix/shared`"),
        ]
        candidates = C.find_duplicates(issues)
        flagged_nums = {c.issue_num for c in candidates}
        self.assertIn(10, flagged_nums)
        self.assertIn(11, flagged_nums)
        self.assertNotIn(12, flagged_nums)


# ---------------------------------------------------------------------------
# find_merged_pr_issues
# ---------------------------------------------------------------------------

class TestFindMergedPrIssues(unittest.TestCase):
    def test_merged_pr_closes_syntax(self):
        """Issue referenced by 'closes #NN' in a merged PR body is flagged."""
        issues = [_issue(83, "[work] ship-emoji", "Branch: `fix/ship-emoji`")]
        prs = [_pr(85, "feat(lifecycle): …\n\ncloses #83", "fix/ship-emoji")]
        with patch.object(C, "_has_open_pr", return_value=False):
            candidates = C.find_merged_pr_issues(issues, prs)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].issue_num, 83)
        self.assertIn("PR #85", candidates[0].reason)

    def test_branch_name_match_without_closes_keyword(self):
        """PR merged on the same branch as the issue → issue is flagged (no closes keyword)."""
        issues = [_issue(83, "[work] ship-emoji",
                         "Branch: `fix/add-ship-emoji-comment-force-merge`")]
        prs = [_pr(85, "Some PR description, no closes keyword",
                   head="fix/add-ship-emoji-comment-force-merge")]
        with patch.object(C, "_has_open_pr", return_value=False):
            candidates = C.find_merged_pr_issues(issues, prs)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].issue_num, 83)
        self.assertIn("branch", candidates[0].reason)

    def test_fixes_and_resolves_keywords(self):
        """'fixes #NN' and 'resolves #NN' are both recognized."""
        issues = [
            _issue(10, "[work] A", ""),
            _issue(11, "[work] B", ""),
        ]
        prs = [
            _pr(20, "fixes #10"),
            _pr(21, "resolves #11"),
        ]
        with patch.object(C, "_has_open_pr", return_value=False):
            candidates = C.find_merged_pr_issues(issues, prs)
        flagged = {c.issue_num for c in candidates}
        self.assertIn(10, flagged)
        self.assertIn(11, flagged)

    def test_open_pr_excluded(self):
        """Issue with an open PR is NOT flagged, even if a merged PR also closes it."""
        issues = [_issue(79, "[work] investigate", "")]
        prs = [_pr(80, "Fixes #79")]
        with patch.object(C, "_has_open_pr", return_value=True):
            candidates = C.find_merged_pr_issues(issues, prs)
        self.assertEqual(candidates, [])

    def test_unreferenced_issue_not_flagged(self):
        """An issue not mentioned in any merged PR body is left alone."""
        issues = [_issue(91, "[work] this branch", "Branch: `fix/this`")]
        prs = [_pr(100, "fixes #99")]  # #91 not mentioned
        with patch.object(C, "_has_open_pr", return_value=False):
            candidates = C.find_merged_pr_issues(issues, prs)
        self.assertEqual(candidates, [])

    def test_closed_issue_not_in_open_list(self):
        """Issues absent from the open-issues list are never flagged."""
        issues = []  # empty — no open issues
        prs = [_pr(50, "closes #42")]
        with patch.object(C, "_has_open_pr", return_value=False):
            candidates = C.find_merged_pr_issues(issues, prs)
        self.assertEqual(candidates, [])


# ---------------------------------------------------------------------------
# close_issues (dry-run vs apply)
# ---------------------------------------------------------------------------

class TestCloseIssues(unittest.TestCase):
    def test_dry_run_does_not_call_gh(self):
        """--dry-run must not invoke gh at all."""
        gh_calls = []
        candidates = [C.CloseCandidate(88, "duplicate of #87", 87)]
        with patch.object(C, "_gh", side_effect=lambda *a, **k: gh_calls.append(a) or (0, "")):
            C.close_issues(candidates, apply=False)
        self.assertEqual(gh_calls, [])

    def test_apply_posts_comment_and_closes(self):
        """--apply posts breadcrumb comment then closes the issue."""
        gh_calls = []
        candidates = [C.CloseCandidate(88, "duplicate of #87", 87)]
        with patch.object(C, "_gh", side_effect=lambda *a, **k: gh_calls.append(a) or (0, "")):
            C.close_issues(candidates, apply=True)
        all_str = str(gh_calls)
        self.assertIn("comment", all_str)
        self.assertIn("close", all_str)
        self.assertIn("88", all_str)

    def test_limit_filter_respected(self):
        """When --issues filter is set, only those numbers are processed."""
        gh_calls = []
        candidates = [
            C.CloseCandidate(88, "duplicate of #87", 87),
            C.CloseCandidate(83, "PR #85 merged", None),
        ]
        with patch.object(C, "_gh", side_effect=lambda *a, **k: gh_calls.append(a) or (0, "")):
            C.close_issues(candidates, apply=True, limit={88})
        all_str = str(gh_calls)
        self.assertIn("88", all_str)
        self.assertNotIn("83", all_str)

    def test_empty_candidates_no_calls(self):
        """Empty candidate list produces no gh calls."""
        gh_calls = []
        with patch.object(C, "_gh", side_effect=lambda *a, **k: gh_calls.append(a) or (0, "")):
            C.close_issues([], apply=True)
        self.assertEqual(gh_calls, [])


# ---------------------------------------------------------------------------
# main (integration-ish, mocked gh)
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):
    def _mock_setup(self, issues, merged_prs, has_open_pr=False):
        def fake_gh(*args, **kwargs):
            if "issue" in args and "list" in args:
                return 0, __import__("json").dumps(issues)
            if "pr" in args and "list" in args and "merged" in args:
                return 0, __import__("json").dumps(merged_prs)
            if "pr" in args and "list" in args and "open" in args:
                return 0, "[]"
            return 0, ""
        return fake_gh

    def test_dry_run_default(self):
        """main() with no --apply defaults to dry-run (exits 0, no mutations)."""
        issues = [_issue(88, "[work] Use Git Issues", "Branch: `fix/use-git-issues`")]
        merged_prs = [_pr(85, "closes #83")]
        fake_gh = self._mock_setup(issues, merged_prs)
        with patch.object(C, "_gh_available", return_value=True), \
             patch.object(C, "_gh", side_effect=fake_gh), \
             patch.object(C, "_has_open_pr", return_value=False):
            rc = C.main(["--dry-run"])
        self.assertEqual(rc, 0)

    def test_no_gh_exits_nonzero(self):
        with patch.object(C, "_gh_available", return_value=False):
            rc = C.main(["--dry-run"])
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
