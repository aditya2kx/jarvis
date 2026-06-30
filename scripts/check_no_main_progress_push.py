#!/usr/bin/env python3
"""Mechanical guard: block direct pushes of PROGRESS.md to main (#89).

Two modes:

  pre-push hook mode (default, reads stdin):
      git hook stdin lines: "<local_sha> <local_ref> <remote_sha> <remote_ref>"
      Exits 1 (blocking) when a push targets refs/heads/main AND
      PROGRESS.md appears in the changed files.
      Exits 0 for all other pushes (feature branches, tags, deletes).

  verify-gate mode (--gate):
      No stdin; falls back to checking whether HEAD==main AND
      PROGRESS.md is in `git diff --name-only origin/main...HEAD`.
      Exits 1 on a feature branch ONLY when HEAD is actually main (rare).
      On any feature branch, always exits 0 — PROGRESS via PR is the
      sanctioned path and this PR itself edits PROGRESS.md.

Install:
    Add to scripts/install-git-hooks.sh OR wire as a verify.py Gate.
    This PR wires it as a verify.py Gate only (simpler; no git-hook change).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _changed_files_between(old_sha: str, new_sha: str) -> list[str]:
    """Return files changed between old_sha and new_sha (git diff --name-only)."""
    zero = "0" * 40
    if old_sha.strip("0") == "":
        # New branch — diff from empty tree
        old_sha = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", old_sha, new_sha],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return [l.strip() for l in out.splitlines() if l.strip()]
    except subprocess.CalledProcessError:
        return []


def violates(refs: list[tuple[str, str, str, str]]) -> bool:
    """Return True when a push targets refs/heads/main AND PROGRESS.md is in the diff.

    refs: list of (local_sha, local_ref, remote_sha, remote_ref) tuples from
    pre-push stdin.
    """
    for local_sha, _local_ref, remote_sha, remote_ref in refs:
        if remote_ref != "refs/heads/main":
            continue
        changed = _changed_files_between(remote_sha, local_sha)
        if "PROGRESS.md" in changed:
            return True
    return False


def _parse_stdin_refs() -> list[tuple[str, str, str, str]]:
    """Parse pre-push hook stdin: '<local_sha> <local_ref> <remote_sha> <remote_ref>'."""
    refs = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) >= 4:
            refs.append((parts[0], parts[1], parts[2], parts[3]))
    return refs


def _gate_mode() -> int:
    """--gate mode: check HEAD branch vs origin/main diff.

    On a feature branch (HEAD != main) always returns 0 — PROGRESS via PR is
    the sanctioned path; this check only blocks direct pushes to main.
    """
    try:
        head_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return 0

    if head_branch != "main":
        # On a feature branch: exit 0 (sanctioned path)
        return 0

    # We ARE on main — check whether PROGRESS.md is in the diff vs origin/main
    try:
        diff = subprocess.check_output(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return 0

    if "PROGRESS.md" in diff.splitlines():
        print(
            "ERROR: PROGRESS.md must not be pushed directly to main.\n"
            "       Make changes on a feature branch and land them via PR.\n"
            "       (check_no_main_progress_push.py --gate)",
            file=sys.stderr,
        )
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--gate" in args:
        return _gate_mode()

    # Pre-push hook mode
    refs = _parse_stdin_refs()
    if not refs:
        return 0

    if violates(refs):
        print(
            "ERROR: refusing push — PROGRESS.md must not land directly on main.\n"
            "       Land it via a PR instead.\n"
            "       (check_no_main_progress_push.py)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
