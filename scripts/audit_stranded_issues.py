#!/usr/bin/env python3
"""Audit + reconcile stranded GitHub issues (Issue #123).

A "stranded" issue is an open ``jarvis-work`` issue whose implementing PR has
already been squash-merged to ``main`` — but the merge never closed it,
because the merge -> jarvis-signal -> watch-all chain was dead at the source
(``pr-merged-lifecycle.yml``'s ``Find tracking issue`` step failed on 100% of
merges; see Issue #130). This script is the one-time backlog cleanup plus a
repeatable read-only check.

Matching is intentionally strict — an issue counts as "implemented by PR #N"
only when:
  1. PR #N's head branch encodes the issue number as an ``iNNN`` slug token
     (matches the ``new_requirement.py`` branch-naming convention), OR
  2. PR #N's body contains a ``Closes|Fixes|Resolves #N`` keyword for the
     issue.

A loose ``Refs #N`` or a bare ``#N`` mention does NOT count — those are used
by follow-up issues that merely reference the PR that spawned them (e.g.
Issues #128/#133/#134/#141 reference their spawning PR without being
implemented by it). Treating those as "stranded" would be a false positive.

Usage:
    python3 scripts/audit_stranded_issues.py --report
        Read-only. Print a table of stranded issues found. Exit 0 always
        (this is a diagnostic, not a gate) — safe to run in CI/cron.

    python3 scripts/audit_stranded_issues.py --reconcile
        Mutating. For each stranded issue: post an idempotent link comment
        citing the implementing PR(s), then close the issue. Does not
        attempt to replay `phase_state.py` substep-by-substep for these
        branches — their worktrees are gone and the substeps already
        happened without formal tracking; replaying the gate machinery
        after the fact would fabricate history. The close comment records
        this explicitly for auditability.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any, NamedTuple

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from post_merge_lifecycle import _BRANCH_ISSUE_SLUG_RE  # noqa: E402

# ---------------------------------------------------------------------------
# gh helpers
# ---------------------------------------------------------------------------


def _gh_json(args: list[str]) -> Any:
    try:
        out = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except FileNotFoundError:
        print("error: `gh` CLI not found on PATH.", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        print(f"error: gh {' '.join(args)} failed:\n{e.stderr}", file=sys.stderr)
        sys.exit(2)
    return json.loads(out) if out.strip() else []


def list_open_jarvis_issues() -> list[dict]:
    """Return all open issues, not just ``jarvis-work``-labelled ones.

    Some lifecycle-tracked issues (e.g. Issue #108) carry ``bug``/
    ``enhancement`` labels instead of ``jarvis-work``, so filtering by label
    would silently miss them. Matching against merged PRs is strict
    (slug + Closes/Fixes/Resolves keyword only), so scanning every open
    issue does not introduce false positives.
    """
    return _gh_json([
        "issue", "list", "--state", "open",
        "--limit", "200", "--json", "number,title,labels,body,url",
    ])


def list_merged_prs(limit: int = 100) -> list[dict]:
    return _gh_json([
        "pr", "list", "--state", "merged", "--limit", str(limit),
        "--json", "number,title,headRefName,body,mergedAt,url",
    ])


# ---------------------------------------------------------------------------
# Matching (pure, unit-testable — no network)
# ---------------------------------------------------------------------------

_CLOSE_KEYWORD_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE
)


def branch_slug_issue_number(branch: str) -> int | None:
    """Parse the ``iNNN`` issue-number token out of a branch name, if present."""
    m = _BRANCH_ISSUE_SLUG_RE.search(branch or "")
    return int(m.group(1)) if m else None


def close_keyword_issue_numbers(pr_body: str) -> set[int]:
    """Issue numbers referenced via a Closes/Fixes/Resolves keyword.

    Deliberately excludes bare ``#N`` or ``Refs #N`` mentions — those do not
    assert "this PR implements issue N".
    """
    return {int(n) for n in _CLOSE_KEYWORD_RE.findall(pr_body or "")}


class StrandedIssue(NamedTuple):
    number: int
    title: str
    labels: list[str]
    implementing_prs: list[dict]  # each: {number, title, headRefName, mergedAt, url}


def find_implementing_prs(issue_number: int, prs: list[dict]) -> list[dict]:
    hits = []
    for pr in prs:
        slug_n = branch_slug_issue_number(pr.get("headRefName") or "")
        close_ns = close_keyword_issue_numbers(pr.get("body") or "")
        if slug_n == issue_number or issue_number in close_ns:
            hits.append(pr)
    return hits


def audit(issues: list[dict], prs: list[dict]) -> list[StrandedIssue]:
    """Return the subset of ``issues`` that are stranded (implementing PR merged)."""
    stranded: list[StrandedIssue] = []
    for issue in issues:
        n = issue["number"]
        hits = find_implementing_prs(n, prs)
        if hits:
            labels = [l["name"] for l in issue.get("labels", [])]
            stranded.append(StrandedIssue(n, issue["title"], labels, hits))
    return stranded


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_report(stranded: list[StrandedIssue]) -> str:
    if not stranded:
        return "0 stranded issues found — merge-close chain is healthy."
    lines = [f"{len(stranded)} stranded issue(s) found (open, but implementing PR already merged):", ""]
    for s in stranded:
        lines.append(f"#{s.number} {s.title!r}  [{','.join(s.labels)}]")
        for pr in s.implementing_prs:
            lines.append(f"    <- PR #{pr['number']} ({pr['headRefName']}) merged {pr['mergedAt'][:10]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reconciliation (mutating)
# ---------------------------------------------------------------------------


def _already_linked(issue_number: int, pr_number: int) -> bool:
    comments = _gh_json(["issue", "view", str(issue_number), "--json", "comments", "-q", ".comments"])
    marker = f"PR #{pr_number}"
    return any(marker in (c.get("body") or "") for c in comments)


def reconcile(stranded: list[StrandedIssue], *, dry_run: bool = False) -> None:
    for s in stranded:
        pr_refs = ", ".join(f"#{pr['number']}" for pr in s.implementing_prs)
        body = (
            f"**Reconciled by stranded-issue audit ({pr_refs}).** "
            f"The implementing PR(s) above were already squash-merged to `main`, but the "
            f"merge-lifecycle signal chain failed to close this issue automatically "
            f"(root cause: Issue #123 / #130 — `pr-merged-lifecycle.yml` could not resolve "
            f"the tracking issue). Closing as part of the one-time backlog cleanup; the "
            f"signal chain repair prevents recurrence."
        )
        if dry_run:
            print(f"(dry-run) would comment + close #{s.number}")
            continue
        if not _already_linked(s.number, s.implementing_prs[0]["number"]):
            subprocess.run(["gh", "issue", "comment", str(s.number), "--body", body], check=False)
        subprocess.run(["gh", "issue", "close", str(s.number), "--comment", "Closed by stranded-issue reconciliation."], check=False)
        print(f"reconciled #{s.number}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true", help="Read-only: print stranded issues, exit 0 always.")
    group.add_argument("--reconcile", action="store_true", help="Mutating: link + close all stranded issues.")
    ap.add_argument("--dry-run", action="store_true", help="With --reconcile, print actions without executing them.")
    args = argv if argv is not None else sys.argv[1:]
    parsed = ap.parse_args(args)

    issues = list_open_jarvis_issues()
    prs = list_merged_prs()
    stranded = audit(issues, prs)

    if parsed.report:
        print(format_report(stranded))
        return 0

    print(format_report(stranded))
    reconcile(stranded, dry_run=parsed.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
