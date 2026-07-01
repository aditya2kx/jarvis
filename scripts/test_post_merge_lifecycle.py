#!/usr/bin/env python3
"""Tests for post_merge_lifecycle.py."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import post_merge_lifecycle as PML


class TestFindTrackingIssueFromCache(unittest.TestCase):
    def test_reads_issue_from_cache(self):
        branch = "fix/test-pmv-cache-branch"
        slug = PML._slug(branch)
        with tempfile.TemporaryDirectory() as tmp:
            mdir = os.path.join(tmp, "metrics", "pr_cost")
            os.makedirs(mdir)
            cache = os.path.join(mdir, f"session-{slug}-phase.json")
            open(cache, "w").write(json.dumps({"issue": 42, "done": []}))
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": tmp}):
                n = PML.find_tracking_issue_from_cache(branch)
            self.assertEqual(n, 42)

    def test_returns_none_when_cache_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": tmp}):
                n = PML.find_tracking_issue_from_cache("fix/nonexistent-branch-xyz")
        self.assertIsNone(n)

    def test_returns_none_when_issue_absent(self):
        branch = "fix/no-issue-in-cache"
        slug = PML._slug(branch)
        with tempfile.TemporaryDirectory() as tmp:
            mdir = os.path.join(tmp, "metrics", "pr_cost")
            os.makedirs(mdir)
            cache = os.path.join(mdir, f"session-{slug}-phase.json")
            open(cache, "w").write(json.dumps({"done": []}))
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": tmp}):
                n = PML.find_tracking_issue_from_cache(branch)
        self.assertIsNone(n)


class TestParsePostMergeBlock(unittest.TestCase):
    def test_empty_body(self):
        self.assertEqual(PML.parse_post_merge_block(""), [])

    def test_no_section(self):
        body = "## 4. End-to-end test\nsome evidence\n## 5. Backward compat\nyes\n"
        self.assertEqual(PML.parse_post_merge_block(body), [])

    def test_basic_read_only_commands(self):
        body = """## 4. End-to-end test (with evidence)

some evidence here

### Post-merge verification
```
python3 -m agents.bhaga.scripts.status --store palmetto
gh pr view 77 --json state
```

## 5. Backward compatibility
"""
        cmds = PML.parse_post_merge_block(body)
        self.assertEqual(len(cmds), 2)
        self.assertTrue(cmds[0].readonly)
        self.assertTrue(cmds[1].readonly)
        self.assertIn("status", cmds[0].raw)

    def test_side_effecting_commands_flagged(self):
        body = """## 4. End-to-end test

### Post-merge verification
```bash
# Read-only
python3 scripts/verify.py --fast
# Side-effecting — agent should run this manually
gcloud run jobs execute bhaga-daily-refresh --region us-central1
python3 scripts/trigger_dated_refresh.py  # triggers a scrape
```
"""
        cmds = PML.parse_post_merge_block(body)
        readonly_cmds = [c for c in cmds if c.readonly]
        side_cmds = [c for c in cmds if not c.readonly]
        self.assertTrue(any("verify" in c.raw for c in readonly_cmds))
        self.assertTrue(any("gcloud" in c.raw for c in side_cmds))

    def test_comments_skipped(self):
        body = """### Post-merge verification
```
# this is a comment
python3 -m pytest scripts/ -q
```
"""
        cmds = PML.parse_post_merge_block(body)
        self.assertEqual(len(cmds), 1)
        self.assertNotIn("#", cmds[0].raw.split()[0])

    def test_multiple_fenced_blocks(self):
        body = """### Post-merge verification
```bash
cmd1
```
Some prose in between.
```
cmd2
```
"""
        cmds = PML.parse_post_merge_block(body)
        self.assertEqual(len(cmds), 2)
        self.assertEqual(cmds[0].raw, "cmd1")
        self.assertEqual(cmds[1].raw, "cmd2")

    def test_section_ends_at_next_heading(self):
        body = """### Post-merge verification
```
good-cmd
```
## 5. Backward compatibility
```
should-not-appear
```
"""
        cmds = PML.parse_post_merge_block(body)
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0].raw, "good-cmd")

    def test_otp_is_side_effecting(self):
        body = """### Post-merge verification
```
trigger-otp-flow.sh
```
"""
        cmds = PML.parse_post_merge_block(body)
        self.assertFalse(cmds[0].readonly)

    def test_deploy_is_side_effecting(self):
        body = """### Post-merge verification
```
bash scripts/deploy.sh prod
```
"""
        cmds = PML.parse_post_merge_block(body)
        self.assertFalse(cmds[0].readonly)

    def test_empty_section(self):
        body = """### Post-merge verification

## 5. Backward compat
"""
        cmds = PML.parse_post_merge_block(body)
        self.assertEqual(cmds, [])


class TestFormatSignal(unittest.TestCase):
    def test_round_trip(self):
        sig = PML.format_signal("ci_failed", "fix/my-branch", pr=42, issue=101, signal_id="test-uuid")
        self.assertIn("jarvis-signal:", sig)
        parsed = PML.parse_signal(sig)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["event"], "ci_failed")
        self.assertEqual(parsed["branch"], "fix/my-branch")
        self.assertEqual(parsed["pr"], 42)
        self.assertEqual(parsed["issue"], 101)
        self.assertEqual(parsed["id"], "test-uuid")

    def test_auto_uuid_generated(self):
        sig1 = PML.format_signal("pr_merged", "fix/branch-a")
        sig2 = PML.format_signal("pr_merged", "fix/branch-a")
        p1 = PML.parse_signal(sig1)
        p2 = PML.parse_signal(sig2)
        self.assertIsNotNone(p1)
        self.assertIsNotNone(p2)
        self.assertNotEqual(p1["id"], p2["id"])

    def test_extra_kwargs_included(self):
        sig = PML.format_signal("ci_other", "fix/b", conclusion="cancelled")
        parsed = PML.parse_signal(sig)
        self.assertEqual(parsed["conclusion"], "cancelled")

    def test_parse_signal_malformed_json(self):
        self.assertIsNone(PML.parse_signal("<!-- jarvis-signal:{bad json} -->"))

    def test_parse_signal_no_block(self):
        self.assertIsNone(PML.parse_signal("Just a plain comment with no signal."))

    def test_parse_signal_none_body(self):
        self.assertIsNone(PML.parse_signal(None))

    def test_parse_signal_empty(self):
        self.assertIsNone(PML.parse_signal(""))


class TestEmitSignalCLI(unittest.TestCase):
    def test_emit_signal_outputs_parseable_block(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = PML.main(["emit-signal", "--event", "pr_merged", "--branch", "fix/test-branch",
                           "--pr", "99", "--issue", "101", "--signal-id", "fixed-uuid"])
        self.assertEqual(rc, 0)
        out = buf.getvalue().strip()
        parsed = PML.parse_signal(out)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["event"], "pr_merged")
        self.assertEqual(parsed["id"], "fixed-uuid")

    def test_emit_signal_comment_url_round_trips(self):
        """--comment-url must appear in parsed payload as comment_url."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = PML.main([
                "emit-signal", "--event", "comment",
                "--branch", "fix/test-branch",
                "--issue", "115",
                "--signal-id", "fixed-uuid-2",
                "--comment-url", "https://github.com/foo/bar/issues/115#issuecomment-999",
            ])
        self.assertEqual(rc, 0)
        out = buf.getvalue().strip()
        parsed = PML.parse_signal(out)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["event"], "comment")
        self.assertEqual(parsed["comment_url"],
                         "https://github.com/foo/bar/issues/115#issuecomment-999")


class TestEmitSignalRequirement(unittest.TestCase):
    def test_emit_signal_requirement_round_trips(self):
        """--requirement must appear in parsed payload as requirement field."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = PML.main([
                "emit-signal", "--event", "intake",
                "--branch", "",
                "--issue", "101",
                "--signal-id", "req-uuid-test",
                "--requirement", "add dark mode toggle",
            ])
        self.assertEqual(rc, 0)
        parsed = PML.parse_signal(buf.getvalue().strip())
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["event"], "intake")
        self.assertEqual(parsed["requirement"], "add dark mode toggle")

    def test_emit_signal_no_requirement_omits_field(self):
        """Without --requirement, the field must not appear in the payload."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            PML.main([
                "emit-signal", "--event", "intake",
                "--branch", "",
                "--issue", "101",
                "--signal-id", "req-uuid-none",
            ])
        parsed = PML.parse_signal(buf.getvalue().strip())
        self.assertIsNotNone(parsed)
        self.assertNotIn("requirement", parsed)


class TestFindBranchForIssue(unittest.TestCase):
    def test_finds_branch_from_seeded_cache(self):
        branch = "fix/test-find-branch"
        with tempfile.TemporaryDirectory() as tmp:
            mdir = os.path.join(tmp, "metrics", "pr_cost")
            os.makedirs(mdir)
            slug = PML._slug(branch)
            cache = os.path.join(mdir, f"session-{slug}-phase.json")
            open(cache, "w").write(json.dumps({"branch": branch, "issue": 77}))
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": tmp}):
                result = PML.find_branch_for_issue(77)
        self.assertEqual(result, branch)

    def test_returns_none_when_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": tmp}):
                result = PML.find_branch_for_issue(999)
        self.assertIsNone(result)

    def test_find_branch_cli_prints_branch(self):
        branch = "fix/test-find-branch-cli"
        with tempfile.TemporaryDirectory() as tmp:
            import io
            from contextlib import redirect_stdout
            mdir = os.path.join(tmp, "metrics", "pr_cost")
            os.makedirs(mdir)
            slug = PML._slug(branch)
            cache = os.path.join(mdir, f"session-{slug}-phase.json")
            open(cache, "w").write(json.dumps({"branch": branch, "issue": 88}))
            buf = io.StringIO()
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": tmp}):
                with redirect_stdout(buf):
                    rc = PML.main(["find-branch", "--issue", "88"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), branch)

    def test_find_branch_cli_none_on_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": tmp}):
                with redirect_stdout(buf):
                    rc = PML.main(["find-branch", "--issue", "9999"])
        self.assertEqual(rc, 1)
        self.assertEqual(buf.getvalue().strip(), "none")


if __name__ == "__main__":
    unittest.main()
