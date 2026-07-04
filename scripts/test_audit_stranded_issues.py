#!/usr/bin/env python3
"""Tests for audit_stranded_issues.py."""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_stranded_issues as A


def _issue(number, title="Some issue", labels=None):
    return {"number": number, "title": title, "labels": [{"name": l} for l in (labels or ["jarvis-work"])], "body": ""}


def _pr(number, head_ref, body="", merged_at="2026-07-01T00:00:00Z"):
    return {"number": number, "title": f"pr {number}", "headRefName": head_ref, "body": body, "mergedAt": merged_at, "url": f"https://x/{number}"}


class TestBranchSlugMatch(unittest.TestCase):
    def test_matches_iNNN_token(self):
        self.assertEqual(A.branch_slug_issue_number("fix/i112-would-love-to-see-a-chart"), 112)

    def test_no_token_returns_none(self):
        self.assertIsNone(A.branch_slug_issue_number("fix/add-weights-in-lbs-per-row-v2"))

    def test_bare_number_in_middle_not_matched_as_prefix(self):
        # "add-weights-in-lbs-per-row-v2" has no i<digits> token at all.
        self.assertIsNone(A.branch_slug_issue_number("fix/pr-base-branch-guard"))


class TestCloseKeywordMatch(unittest.TestCase):
    def test_closes_matches(self):
        self.assertEqual(A.close_keyword_issue_numbers("Closes #101"), {101})

    def test_fixes_and_resolves_case_insensitive(self):
        self.assertEqual(A.close_keyword_issue_numbers("fixes #108\nResolved #109"), {108, 109})

    def test_bare_hash_mention_rejected(self):
        """A bare '#N' mention (e.g. a follow-up issue referencing its spawning PR)
        must NOT be treated as 'this PR implements issue N'."""
        self.assertEqual(A.close_keyword_issue_numbers("See #134 for background."), set())

    def test_refs_keyword_rejected(self):
        self.assertEqual(A.close_keyword_issue_numbers("Refs #137"), set())


class TestFindImplementingPRs(unittest.TestCase):
    def test_matches_by_slug(self):
        prs = [_pr(127, "fix/i112-would-love-to-see-a-chart")]
        hits = A.find_implementing_prs(112, prs)
        self.assertEqual([p["number"] for p in hits], [127])

    def test_matches_by_close_keyword(self):
        prs = [_pr(109, "fix/i108-https", body="Closes #108")]
        hits = A.find_implementing_prs(108, prs)
        self.assertEqual([p["number"] for p in hits], [109])

    def test_no_match_for_loose_mention(self):
        """Reproduces the false-positive risk: PR #139 mentions #134 in its body
        (a follow-up issue it spawned) but does not implement #134."""
        prs = [_pr(139, "fix/i137-dual-date-order-reco", body="Found during #134 investigation.")]
        hits = A.find_implementing_prs(134, prs)
        self.assertEqual(hits, [])

    def test_multiple_implementing_prs(self):
        prs = [
            _pr(116, "fix/i113-https"),
            _pr(117, "feat/i113-order-reco"),
        ]
        hits = A.find_implementing_prs(113, prs)
        self.assertEqual({p["number"] for p in hits}, {116, 117})


class TestAudit(unittest.TestCase):
    def test_flags_stranded_issue(self):
        issues = [_issue(112, "chart request")]
        prs = [_pr(127, "fix/i112-would-love-to-see-a-chart")]
        stranded = A.audit(issues, prs)
        self.assertEqual(len(stranded), 1)
        self.assertEqual(stranded[0].number, 112)
        self.assertEqual(stranded[0].implementing_prs[0]["number"], 127)

    def test_no_false_positive_for_unimplemented_issue(self):
        issues = [_issue(133, "drain grafana logic")]
        prs = [_pr(135, "fix/i126-for-order-assistant-and-in-general", body="Refs #133 as follow-up work.")]
        stranded = A.audit(issues, prs)
        self.assertEqual(stranded, [])

    def test_issue_with_no_merged_pr_not_flagged(self):
        issues = [_issue(140, "comment trigger bug")]
        prs = [_pr(139, "fix/i137-dual-date-order-reco")]
        stranded = A.audit(issues, prs)
        self.assertEqual(stranded, [])

    def test_multiple_issues_mixed(self):
        issues = [_issue(112, "a"), _issue(133, "b")]
        prs = [_pr(127, "fix/i112-would-love-to-see-a-chart"), _pr(135, "fix/i126-x", body="Refs #133")]
        stranded = A.audit(issues, prs)
        self.assertEqual([s.number for s in stranded], [112])


class TestFormatReport(unittest.TestCase):
    def test_empty_report(self):
        self.assertIn("0 stranded", A.format_report([]))

    def test_nonempty_report_includes_issue_and_pr(self):
        stranded = A.audit([_issue(112, "chart")], [_pr(127, "fix/i112-would-love-to-see-a-chart")])
        report = A.format_report(stranded)
        self.assertIn("#112", report)
        self.assertIn("PR #127", report)


class TestReportCLI(unittest.TestCase):
    def test_report_mode_exits_zero_even_with_stranded_issues(self):
        with patch.object(A, "list_open_jarvis_issues", return_value=[_issue(112, "chart")]):
            with patch.object(A, "list_merged_prs", return_value=[_pr(127, "fix/i112-would-love-to-see-a-chart")]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = A.main(["--report"])
        self.assertEqual(rc, 0)
        self.assertIn("#112", buf.getvalue())

    def test_report_mode_zero_stranded(self):
        with patch.object(A, "list_open_jarvis_issues", return_value=[]):
            with patch.object(A, "list_merged_prs", return_value=[]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = A.main(["--report"])
        self.assertEqual(rc, 0)
        self.assertIn("0 stranded", buf.getvalue())


class TestReconcileDryRun(unittest.TestCase):
    def test_reconcile_dry_run_does_not_call_gh_mutating_commands(self):
        stranded = A.audit([_issue(112, "chart")], [_pr(127, "fix/i112-would-love-to-see-a-chart")])
        with patch("audit_stranded_issues.subprocess.run") as mock_run:
            A.reconcile(stranded, dry_run=True)
            mock_run.assert_not_called()

    def test_reconcile_live_comments_and_closes(self):
        stranded = A.audit([_issue(112, "chart")], [_pr(127, "fix/i112-would-love-to-see-a-chart")])
        with patch.object(A, "_already_linked", return_value=False):
            with patch("audit_stranded_issues.subprocess.run") as mock_run:
                A.reconcile(stranded, dry_run=False)
        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertTrue(any("comment" in c for c in calls))
        self.assertTrue(any("close" in c for c in calls))

    def test_reconcile_skips_duplicate_link_comment(self):
        stranded = A.audit([_issue(112, "chart")], [_pr(127, "fix/i112-would-love-to-see-a-chart")])
        with patch.object(A, "_already_linked", return_value=True):
            with patch("audit_stranded_issues.subprocess.run") as mock_run:
                A.reconcile(stranded, dry_run=False)
        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertFalse(any("comment" in c for c in calls))
        self.assertTrue(any("close" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
