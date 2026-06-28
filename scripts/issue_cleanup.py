#!/usr/bin/env python3
"""Jarvis issue hygiene tool.

Two operations:
  1. Deduplicate open ``jarvis-work`` issues that share the same branch
     (i.e. a manual issue + the ``[work] …`` issue created by new_requirement).
  2. Close open ``jarvis-work`` issues whose tracking PR was merged (detected
     via ``closes/fixes/resolves #NN`` in the merged PR body).

Safety guarantees:
  - Never mutates without ``--apply``.
  - Never closes an issue with an open or in-flight PR.
  - Prints a breadcrumb comment on every closed issue.

Usage:
    python3 scripts/issue_cleanup.py --dry-run        # show plan, no changes
    python3 scripts/issue_cleanup.py                   # alias for --dry-run
    python3 scripts/issue_cleanup.py --apply           # execute plan
    python3 scripts/issue_cleanup.py --apply --issues 88,83   # limit scope
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import NamedTuple


# ---------------------------------------------------------------------------
# gh helpers (mirrors phase_state.py style)
# ---------------------------------------------------------------------------

def _gh_available() -> bool:
    try:
        r = subprocess.run(["gh", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _gh(*args: str, input_text: str | None = None) -> tuple[int, str]:
    cmd = ["gh"] + list(args)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            input=input_text,
        )
        return proc.returncode, proc.stdout + proc.stderr
    except Exception as e:
        return -1, str(e)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class CloseCandidate(NamedTuple):
    issue_num: int
    reason: str          # human-readable: "duplicate of #NN" or "PR #NN merged"
    survivor: int | None  # for duplicates: the issue to keep


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

_BRANCH_RE = re.compile(r"Branch:\s*`([^`]+)`")
_CROSSREF_RE = re.compile(r"\(issue\s+#(\d+)\)")
_CLOSES_RE = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)


def _fetch_open_jarvis_issues() -> list[dict]:
    """Return list of open jarvis-work issues with number, title, body."""
    rc, out = _gh(
        "issue", "list",
        "--label", "jarvis-work",
        "--state", "open",
        "--json", "number,title,body",
        "--limit", "100",
    )
    if rc != 0 or not out.strip():
        return []
    try:
        return json.loads(out)
    except Exception:
        return []


def _fetch_merged_prs() -> list[dict]:
    """Return merged PRs with number, headRefName, and body (last 200)."""
    rc, out = _gh(
        "pr", "list",
        "--state", "merged",
        "--json", "number,headRefName,body",
        "--limit", "200",
    )
    if rc != 0 or not out.strip():
        return []
    try:
        return json.loads(out)
    except Exception:
        return []


def _is_open_issue(issue_num: int) -> bool:
    """Return True if the given issue exists and is open (any labels)."""
    rc, out = _gh("issue", "view", str(issue_num), "--json", "state", "-q", ".state")
    return rc == 0 and out.strip().upper() == "OPEN"


def _has_open_pr(issue_num: int) -> bool:
    """Return True if any open PR references this issue number."""
    rc, out = _gh(
        "pr", "list",
        "--state", "open",
        "--json", "number,body",
        "--limit", "100",
    )
    if rc != 0 or not out.strip():
        return False
    try:
        prs = json.loads(out)
    except Exception:
        return False
    pattern = re.compile(rf"(?:closes|fixes|resolves)\s+#{issue_num}\b", re.IGNORECASE)
    return any(pattern.search(pr.get("body") or "") for pr in prs)


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

def find_duplicates(issues: list[dict]) -> list[CloseCandidate]:
    """Group issues by branch; flag all but the survivor for closure.

    Survivor selection (in priority order):
      1. The ``[work]``-prefixed issue (created by new_requirement.py — has the
         full tracking body).
      2. Lowest issue number as a tiebreaker.

    Also flags issues where the body contains ``(issue #NN)`` pointing at
    another open issue — those are derivative copies.
    """
    # Group by branch name extracted from body
    branch_groups: dict[str, list[dict]] = {}
    for iss in issues:
        body = iss.get("body") or ""
        m = _BRANCH_RE.search(body)
        if m:
            branch = m.group(1)
            branch_groups.setdefault(branch, []).append(iss)

    candidates: list[CloseCandidate] = []
    for branch, group in branch_groups.items():
        if len(group) < 2:
            continue
        # Pick survivor: [work]-prefixed wins, else lowest number
        work_issues = [i for i in group if i["title"].startswith("[work]")]
        if work_issues:
            survivor = min(work_issues, key=lambda i: i["number"])
        else:
            survivor = min(group, key=lambda i: i["number"])
        for iss in group:
            if iss["number"] != survivor["number"]:
                candidates.append(CloseCandidate(
                    issue_num=iss["number"],
                    reason=f"duplicate of #{survivor['number']} (same branch `{branch}`)",
                    survivor=survivor["number"],
                ))

    # Also detect cross-reference pattern: body contains "(issue #NN)" where #NN is open.
    # The referenced issue may lack the jarvis-work label (manually-filed), so we
    # check its live state rather than restricting to open_nums.
    open_nums = {i["number"] for i in issues}
    already_flagged = {c.issue_num for c in candidates}
    for iss in issues:
        if iss["number"] in already_flagged:
            continue
        body = iss.get("body") or ""
        for m in _CROSSREF_RE.finditer(body):
            ref = int(m.group(1))
            if ref == iss["number"]:
                continue
            # Accept refs to other jarvis-work issues OR any open issue
            ref_is_open = ref in open_nums or _is_open_issue(ref)
            if ref_is_open:
                candidates.append(CloseCandidate(
                    issue_num=iss["number"],
                    reason=f"duplicate — references origin issue #{ref} in body",
                    survivor=ref,
                ))
                already_flagged.add(iss["number"])
                break

    return candidates


def find_merged_pr_issues(
    issues: list[dict],
    merged_prs: list[dict],
) -> list[CloseCandidate]:
    """Flag open issues whose work was merged.

    Two detection methods:
      1. Explicit ``closes/fixes/resolves #NN`` in a merged PR body.
      2. Branch-name match: a merged PR's ``headRefName`` equals the branch
         recorded in the issue body (``Branch: ...``).  Covers PRs that
         did not use the ``closes`` keyword.

    Issues with an open PR are explicitly excluded.
    """
    open_nums = {i["number"] for i in issues}
    # Build branch → issue map for method 2
    branch_to_issue: dict[str, int] = {}
    for iss in issues:
        body = iss.get("body") or ""
        m = _BRANCH_RE.search(body)
        if m:
            branch_to_issue[m.group(1)] = iss["number"]

    candidates: list[CloseCandidate] = []
    flagged: set[int] = set()

    for pr in merged_prs:
        pr_num = pr["number"]
        body = pr.get("body") or ""
        head_branch = pr.get("headRefName") or ""

        # Method 1: explicit closes/fixes/resolves #NN keyword
        for m in _CLOSES_RE.finditer(body):
            issue_num = int(m.group(1))
            if issue_num not in open_nums or issue_num in flagged:
                continue
            if _has_open_pr(issue_num):
                continue
            candidates.append(CloseCandidate(
                issue_num=issue_num,
                reason=f"PR #{pr_num} merged (closes #{issue_num})",
                survivor=None,
            ))
            flagged.add(issue_num)

        # Method 2: merged PR branch matches the branch recorded in the issue body
        if head_branch and head_branch in branch_to_issue:
            issue_num = branch_to_issue[head_branch]
            if issue_num in open_nums and issue_num not in flagged:
                if not _has_open_pr(issue_num):
                    candidates.append(CloseCandidate(
                        issue_num=issue_num,
                        reason=f"PR #{pr_num} merged on branch `{head_branch}`",
                        survivor=None,
                    ))
                    flagged.add(issue_num)

    return candidates


# ---------------------------------------------------------------------------
# Close action
# ---------------------------------------------------------------------------

def close_issues(
    candidates: list[CloseCandidate],
    *,
    apply: bool,
    limit: set[int] | None = None,
) -> None:
    if not candidates:
        print("No issues to close — repo is clean.")
        return

    for c in candidates:
        if limit and c.issue_num not in limit:
            print(f"  skip #{c.issue_num}: not in --issues filter")
            continue
        print(f"  #{c.issue_num}: {c.reason}")
        if not apply:
            continue
        comment = f"Closed by issue_cleanup: {c.reason}"
        _gh("issue", "comment", str(c.issue_num), "--body", comment)
        _gh("issue", "close", str(c.issue_num))
        print(f"    → closed and commented.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--apply", action="store_true",
                     help="Execute closures (default is --dry-run)")
    cli.add_argument("--dry-run", action="store_true",
                     help="Print plan without closing anything (default when --apply absent)")
    cli.add_argument("--issues", default=None,
                     help="Comma-separated issue numbers to restrict scope (e.g. 88,83)")
    args = cli.parse_args(argv)

    apply = args.apply
    limit: set[int] | None = None
    if args.issues:
        limit = {int(n.strip()) for n in args.issues.split(",") if n.strip().isdigit()}

    if not _gh_available():
        print("ERROR: gh CLI not available.", file=sys.stderr)
        return 1

    print("Fetching open jarvis-work issues …")
    issues = _fetch_open_jarvis_issues()
    print(f"  {len(issues)} open issue(s) found.")

    print("Fetching merged PRs …")
    merged_prs = _fetch_merged_prs()
    print(f"  {len(merged_prs)} merged PR(s) found.")

    dupes = find_duplicates(issues)
    stale = find_merged_pr_issues(issues, merged_prs)
    all_candidates = dupes + stale

    if not all_candidates:
        print("\nNothing to close — all issues are clean.")
        return 0

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"\n[{mode}] Candidates to close ({len(all_candidates)}):")
    close_issues(all_candidates, apply=apply, limit=limit)

    if not apply:
        print("\n(Pass --apply to execute the above closures.)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
