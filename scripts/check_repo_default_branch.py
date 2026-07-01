#!/usr/bin/env python3
"""
check_repo_default_branch.py — CI/local gate that fails if the GitHub repo's
configured default branch has drifted away from `main`.

Why this exists (incident 2026-07-01): `gh pr create` with no `--base` flag
targets the repo's *configured default branch*, not necessarily `main`. The
jarvis repo's default branch was silently switched to a long-lived feature
branch (`fix/i101-combine-related-tasks-1-retrospective-protocol`), so a PR
opened without an explicit `--base main` auto-merged into that branch instead
of `main` — even though the PR's own branch ancestry looked correct (it really
was forked from `main`'s tip), because `git log` on the feature branch showed
nothing wrong. The only observable symptom was the PR's `baseRefName`.

This is a repo-wide, not-per-branch invariant, so it is cheap to check
unconditionally: one `gh api` call, independent of which branch/PR is active.

Usage:
    python3 scripts/check_repo_default_branch.py
    python3 scripts/check_repo_default_branch.py --repo aditya2kx/jarvis --expect main

Exit 0 = default branch matches --expect (default: main).
Exit 1 = drifted, or the check could not run (e.g. gh not authenticated) —
         fails loud rather than silently skip, since a false negative here is
         exactly the failure mode this gate exists to catch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


def _detect_repo() -> str | None:
    """Best-effort `owner/repo` from `git remote get-url origin`."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except Exception:
        return None
    # Handles both git@github.com:owner/repo.git and https://github.com/owner/repo.git
    if "github.com" not in url:
        return None
    tail = url.split("github.com", 1)[1].lstrip(":/").rstrip("/")
    return tail[:-4] if tail.endswith(".git") else tail


def get_default_branch(repo: str) -> str:
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}", "--jq", ".default_branch"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"could not query repo {repo!r} default branch via `gh api`: "
            f"{result.stderr.strip()[:300]}"
        )
    return result.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=None, help="owner/repo (default: detected from origin remote)")
    ap.add_argument("--expect", default="main", help="expected default branch (default: main)")
    args = ap.parse_args()

    repo = args.repo or _detect_repo()
    if not repo:
        print(
            "check_repo_default_branch: could not detect owner/repo from `git remote "
            "get-url origin`; pass --repo explicitly.",
            file=sys.stderr,
        )
        return 1

    try:
        actual = get_default_branch(repo)
    except RuntimeError as e:
        print(f"check_repo_default_branch: FAIL — {e}", file=sys.stderr)
        return 1

    if actual != args.expect:
        print(
            f"check_repo_default_branch: FAIL — repo {repo!r} default branch is "
            f"{actual!r}, expected {args.expect!r}.\n"
            f"  Fix (owner-only admin op): gh api repos/{repo} -X PATCH -f default_branch={args.expect}\n"
            f"  Until fixed, EVERY `gh pr create` without an explicit --base flag will "
            f"target {actual!r} instead of {args.expect!r} — always pass "
            f"`--base {args.expect}` explicitly as a second line of defense.",
            file=sys.stderr,
        )
        return 1

    print(f"check_repo_default_branch: OK — {repo!r} default branch is {actual!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
