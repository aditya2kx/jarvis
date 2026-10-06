#!/usr/bin/env python3
"""Fail when new_requirement would create a worktree path over 200 bytes.

Exercises the naming helper, including a synthetic linked worktree whose
folder name is already stacked. Does not scan worktrees already on disk.

Usage:
    python3 scripts/check_worktree_path_length.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import new_requirement as N


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def evaluate() -> list[str]:
    """Return greppable failure strings. Empty means the naming contract holds."""
    errors: list[str] = []
    repo = _repo_root()
    branch = "fix/" + ("long-worktree-name-" * 6)
    base = N.base_repo_name(repo)
    try:
        path = N.default_worktree_path(repo, branch)
    except SystemExit as exc:
        errors.append(f"current checkout rejected: {exc}")
    else:
        name_bytes = len(path.name.encode("utf-8"))
        if name_bytes > N.MAX_WORKTREE_NAME_BYTES:
            errors.append(
                f"current checkout name is {name_bytes} bytes "
                f"(limit {N.MAX_WORKTREE_NAME_BYTES}): {path.name}"
            )
        nbytes = N._worktree_path_nbytes(path)
        if nbytes > N.MAX_WORKTREE_PATH_BYTES:
            errors.append(
                f"current checkout path is {nbytes} bytes "
                f"(limit {N.MAX_WORKTREE_PATH_BYTES}): {path}"
            )
        prefix = f"{base}-wt-"
        if not path.name.startswith(prefix):
            errors.append(f"expected prefix {prefix}, got {path.name}")
        if repo.name != base and path.name.startswith(repo.name):
            errors.append(f"path stacked the current folder name: {path.name}")

    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        tmp_path = Path(tmp)
        main = tmp_path / "jarvis"
        gitdir = main / ".git" / "worktrees" / "old"
        gitdir.mkdir(parents=True)
        stacked_name = "jarvis" + ("-wt-fix-stacked" * 12)
        stacked = tmp_path / stacked_name
        stacked.mkdir()
        (stacked / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
        try:
            path = N.default_worktree_path(stacked, "fix/" + ("z" * 100))
        except SystemExit as exc:
            errors.append(f"synthetic linked worktree rejected: {exc}")
        else:
            if not path.name.startswith("jarvis-wt-"):
                errors.append(f"expected jarvis-wt- prefix, got {path.name}")
            if path.name.startswith(stacked_name) or "-wt-fix-stacked" in path.name:
                errors.append(f"stacked suffix leaked into {path.name}")
            name_bytes = len(path.name.encode("utf-8"))
            if name_bytes > N.MAX_WORKTREE_NAME_BYTES:
                errors.append(f"synthetic name is {name_bytes} bytes: {path.name}")
            nbytes = N._worktree_path_nbytes(path)
            if nbytes > N.MAX_WORKTREE_PATH_BYTES:
                errors.append(f"synthetic path is {nbytes} bytes: {path}")
    return errors


def main() -> int:
    errors = evaluate()
    if errors:
        for err in errors:
            print(f"check_worktree_path_length: {err}", file=sys.stderr)
        return 1
    print("check_worktree_path_length: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
