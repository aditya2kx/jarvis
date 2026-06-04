#!/usr/bin/env python3
"""Create an isolated git worktree + branch-keyed PR cost session in one step.

Parallel Cursor chat spaces must not share one checkout (branch switches and
`git checkout` races contaminate builds and cost attribution). This codifies the
manual worktree-per-chat flow from Hard Lesson #19.

Usage:
    python3 scripts/new_worktree.py --branch fix/my-feature \\
        --requirement "Short requirement text"

    # Optional: parent repo path (defaults to cwd if already a git repo)
    python3 scripts/new_worktree.py --branch fix/foo --repo /path/to/jarvis

The worktree is created at ../jarvis-wt-<slug> next to the main repo by default.
Stay in that directory for all git work; do not checkout other branches in the
shared tree while parallel chats are open.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

def _slug(branch: str) -> str:
    out = re.sub(r"[^A-Za-z0-9]+", "-", branch.strip()).strip("-").lower()
    return out[:48] or "work"


def _git_root(start: Path) -> Path:
    out = subprocess.check_output(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        text=True,
    ).strip()
    return Path(out)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--branch", required=True, help="Feature branch name (created if missing)")
    cli.add_argument("--requirement", required=True, help="Requirement text for the cost brief")
    cli.add_argument("--repo", type=Path, help="Path to the main jarvis git checkout")
    cli.add_argument("--worktree-dir", type=Path,
                     help="Explicit worktree path (default: sibling jarvis-wt-<slug>)")
    cli.add_argument("--open", action="store_true", help="Open session launch.html in the browser")
    args = cli.parse_args(argv)

    repo = _git_root(args.repo or Path.cwd())
    slug = _slug(args.branch)
    wt_path = args.worktree_dir or (repo.parent / f"jarvis-wt-{slug}")

    subprocess.run(
        ["git", "-C", str(repo), "fetch", "origin", "main"],
        check=False,
    )
    if wt_path.exists():
        print(f"Worktree already exists: {wt_path}")
    else:
        subprocess.check_call(
            ["git", "-C", str(repo), "worktree", "add", "-B", args.branch,
             str(wt_path), "origin/main"],
        )
        print(f"Created worktree → {wt_path} (branch {args.branch})")

    session_argv = [
        str(Path(__file__).parent / "start_pr_session.py"),
        "--branch", args.branch,
        "--requirement", args.requirement,
    ]
    if args.open:
        session_argv.append("--open")
    subprocess.check_call([sys.executable, *session_argv], cwd=wt_path)

    print(f"\nWork in: {wt_path}")
    print("Do NOT run git checkout in the shared repo while this chat is active.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
