#!/usr/bin/env python3
"""Start a new requirement: isolated git worktree + cost session + Cursor handoff.

The single command when the user shares a new requirement (or an agent hears
"new requirement", "let's work on X", etc.). It:

  1. Creates a **sibling git worktree** on a fresh branch off ``origin/main`` so
     concurrent chat spaces never fight over one checkout.
  2. Runs ``start_pr_session.py`` there (branch-keyed cost session + brief +
     launcher — no assumed PR number).
  3. Opens **Cursor** on that worktree in a **new window** and seeds a fresh
     Agent chat via ``cursor://`` deeplink.

The agent in the *current* chat must NOT implement the requirement — hand off.

Usage:
    python3 scripts/new_requirement.py \\
        --requirement "Fix cost report titles and de-contaminate ledgers"

    python3 scripts/new_requirement.py \\
        --requirement "Add zero-shift guard" \\
        --branch fix/zero-shift-guard \\
        --requirement-id 5

    python3 scripts/new_requirement.py --requirement "…" --dry-run
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pr_cost_ledger as L
import start_pr_session as S


def _repo_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit("new_requirement: not inside a git repository") from exc


def _run_git(cwd: Path, *args: str, check: bool = True) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=cwd, text=True, stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError as exc:
        if check:
            raise SystemExit(f"git {' '.join(args)} failed:\n{exc.output}") from exc
        return exc.output or ""


def _slug_branch_part(text: str, *, max_len: int = 48) -> str:
    """Short slug for branch suffix / worktree folder name."""
    words = re.sub(r"[^A-Za-z0-9]+", " ", text.strip()).split()
    if not words:
        return "requirement"
    slug = "-".join(words[:6]).lower()
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:max_len].rstrip("-") or "requirement"


def default_branch(requirement: str, *, prefix: str = "fix") -> str:
    return f"{prefix}/{_slug_branch_part(requirement)}"


def _branch_exists(repo_root: Path, branch: str) -> bool:
    return subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root, capture_output=True,
    ).returncode == 0


def default_worktree_path(repo_root: Path, branch: str) -> Path:
    """Sibling directory: ``../<repo>-wt-<branch-slug>``."""
    repo_name = repo_root.name
    slug = L._slug(branch)
    return repo_root.parent / f"{repo_name}-wt-{slug}"


def create_worktree(
    *,
    repo_root: Path,
    branch: str,
    worktree_path: Path,
    base: str = "origin/main",
    dry_run: bool = False,
) -> None:
    if worktree_path.exists():
        raise SystemExit(
            f"Worktree path already exists: {worktree_path}\n"
            f"  Remove it (`git worktree remove {worktree_path}`) or pass --worktree with a new path."
        )
    if _branch_exists(repo_root, branch):
        raise SystemExit(
            f"Branch '{branch}' already exists locally. Pick a different --branch name."
        )

    print(f"Fetching {base} …")
    if not dry_run:
        _run_git(repo_root, "fetch", "origin", "main")

    cmd = ["worktree", "add", "-b", branch, str(worktree_path), base]
    print(f"git {' '.join(cmd)}")
    if dry_run:
        return
    _run_git(repo_root, *cmd)


def start_session_in_worktree(
    *,
    worktree: Path,
    branch: str,
    requirement: str,
    requirement_id: str | None = None,
    model: str = S.DEFAULT_HANDOFF_MODEL,
    dry_run: bool = False,
) -> tuple[Path, Path, str]:
    """Run start_pr_session in the worktree; return (brief, launch, deeplink)."""
    script = worktree / "scripts" / "start_pr_session.py"
    args = [
        sys.executable, str(script),
        "--branch", branch,
        "--requirement", requirement,
        "--model", model,
    ]
    if requirement_id:
        args += ["--requirement-id", requirement_id]

    print(f"Starting cost session in worktree …")
    if dry_run:
        brief = worktree / "metrics" / "pr_cost" / f"session-{L._slug(branch)}-brief.md"
        launch = worktree / "metrics" / "pr_cost" / f"session-{L._slug(branch)}-launch.html"
        return brief, launch, "(dry-run deeplink)"

    subprocess.run(args, cwd=worktree, check=True)

    brief = S._brief_path(branch)
    # Session files live in the worktree's metrics/pr_cost — resolve via worktree path.
    brief = worktree / "metrics" / "pr_cost" / brief.name
    launch = worktree / "metrics" / "pr_cost" / S._launch_path(branch).name
    rec = json_load(worktree, branch)
    req = requirement or rec.get("requirement") or ""
    brief_rel = f"metrics/pr_cost/{brief.name}"
    seed = S.seed_prompt(branch, brief_rel=brief_rel, requirement=req or None)
    deeplink = S.make_deeplink(seed, model=model)
    return brief, launch, deeplink


def json_load(worktree: Path, branch: str) -> dict:
    import json

    path = worktree / "metrics" / "pr_cost" / f"session-{L._slug(branch)}.json"
    if path.is_file():
        return json.loads(path.read_text())
    return {}


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cli.add_argument(
        "--requirement", required=True,
        help="What to build (passed to the brief + seeded Agent chat)",
    )
    cli.add_argument(
        "--branch",
        help="Git branch name (default: fix/<slug-from-requirement>)",
    )
    cli.add_argument(
        "--worktree",
        type=Path,
        help="Worktree directory (default: ../<repo>-wt-<branch-slug> sibling)",
    )
    cli.add_argument(
        "--base", default="origin/main",
        help="Branch/ref to create the worktree from (default: origin/main)",
    )
    cli.add_argument(
        "--requirement-id",
        help="ID from Playground/REQUIREMENTS.md → mark 🔄 In Progress",
    )
    cli.add_argument(
        "--no-open-cursor", action="store_true",
        help="Skip opening Cursor (print paths + seed text only)",
    )
    cli.add_argument(
        "--cursor-delay", type=float, default=3.5,
        help="Seconds to wait after opening Cursor before the deeplink (default: 3.5)",
    )
    cli.add_argument(
        "--model", default=S.DEFAULT_HANDOFF_MODEL,
        help=f"Agent model for handoff deeplink (default: {S.DEFAULT_HANDOFF_MODEL})",
    )
    cli.add_argument("--dry-run", action="store_true", help="Print plan without creating anything")
    args = cli.parse_args(argv)

    repo_root = _repo_root()
    branch = args.branch or default_branch(args.requirement)
    worktree = args.worktree or default_worktree_path(repo_root, branch)

    print(f"Repo:     {repo_root}")
    print(f"Branch:   {branch}")
    print(f"Worktree: {worktree}")
    print(f"Base:     {args.base}\n")

    create_worktree(
        repo_root=repo_root,
        branch=branch,
        worktree_path=worktree,
        base=args.base,
        dry_run=args.dry_run,
    )

    brief, launch, deeplink = start_session_in_worktree(
        worktree=worktree,
        branch=branch,
        requirement=args.requirement,
        requirement_id=args.requirement_id,
        model=args.model,
        dry_run=args.dry_run,
    )

    print(f"\nBrief   → {brief}")
    print(f"Launcher → {launch}\n")

    if args.dry_run:
        print("(dry-run — no Cursor opened)")
        return 0

    if not args.no_open_cursor:
        S.open_cursor_handoff(
            folder=worktree,
            deeplink=deeplink,
            launch_html=launch,
            delay_sec=args.cursor_delay,
        )

    print("\n─── HANDOFF ───")
    print("Do NOT implement this requirement in the current chat.")
    print(f"Switch to the new Cursor window on: {worktree}")
    print("After `gh pr create` in that worktree:")
    print(f"  python3 scripts/pr_cost_ledger.py bind-pr --branch {branch}")
    print("  python3 scripts/pr_cost_ledger.py sync --pr <n>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
