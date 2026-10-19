#!/usr/bin/env python3
"""Fail when the operator's main checkout cannot be updated, or has drifted.

The post-merge pull is `git -C ~/Documents/build-workspace/jarvis pull`. On
2026-10-19 that checkout had `core.bare=true`, so pull and status both refused,
and its files were a stale snapshot of an older commit. CI and Cloud Agents
have no such checkout: a missing directory is a skip, not a failure.

    python3 scripts/check_main_worktree.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MAIN = Path.home() / "Documents" / "build-workspace" / "jarvis"


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def inspect(path: Path) -> list[str]:
    """Problems with this checkout. Empty when it is missing or clean and current."""
    if not path.is_dir():
        return []
    bare = _git(path, "config", "--get", "core.bare")
    if bare.stdout.strip() == "true":
        return [
            f"core.bare=true on {path} — git pull and git status refuse to run. "
            f"`git -C {path} config core.bare false`"
        ]
    status = _git(path, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        return [f"{path} is not a work tree: {status.stderr.strip()}"]
    problems: list[str] = []
    if status.stdout.strip():
        problems.append(f"{path} has uncommitted tracked changes:\n{status.stdout.strip()}")
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if branch.stdout.strip() == "main":
        behind = _git(path, "rev-list", "--count", "HEAD..origin/main")
        if behind.returncode == 0 and behind.stdout.strip() not in ("", "0"):
            problems.append(f"{path} is {behind.stdout.strip()} commit(s) behind origin/main")
    return problems


def main() -> int:
    problems = inspect(MAIN)
    if not problems:
        print(f"main checkout ok ({MAIN})" if MAIN.is_dir() else f"no main checkout at {MAIN} — skipped")
        return 0
    for problem in problems:
        print(f"main checkout drift: {problem}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
