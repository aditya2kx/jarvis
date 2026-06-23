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

**Multiple requirements** are consolidated into **one** worktree / PR by default.
Pass ``--split`` to create a separate worktree and PR for each requirement.

Usage:
    # Single requirement
    python3 scripts/new_requirement.py \\
        --requirement "Fix cost report titles and de-contaminate ledgers"

    # Multiple requirements → one consolidated worktree/PR (default)
    python3 scripts/new_requirement.py \\
        --requirement "Add zero-shift guard" \\
        --requirement "Auto-halt on known-bad runs" \\
        --branch feat/zero-shift-and-auto-halt

    # Multiple requirements → one PR each (opt-out of consolidation)
    python3 scripts/new_requirement.py \\
        --requirement "Add zero-shift guard" \\
        --requirement "Auto-halt on known-bad runs" \\
        --split

    # Legacy single form (also still works)
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


def init_phase_tracking(*, branch: str, requirement: str, dry_run: bool) -> str | None:
    """Create the GitHub work-tracking issue for this branch via OUR phase_state.py.

    phase_state is GitHub-global (one issue per branch), so we call THIS repo's
    copy, not the worktree copy. Non-fatal: a tracking failure must not abort the
    handoff. Returns the issue URL when known.
    """
    script = Path(__file__).parent / "phase_state.py"
    args = [
        sys.executable, str(script), "init",
        "--branch", branch, "--requirement", requirement, "--kickoff",
    ]
    if dry_run:
        args.append("--dry-run")
    print("Creating work-tracking issue …")
    proc = subprocess.run(args, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        print(f"⚠️  phase_state init failed (non-fatal): {proc.stderr[:200]}", file=sys.stderr)
        return None
    m = re.search(r"https://github\.com/\S+/issues/\d+", proc.stdout)
    return m.group(0) if m else None


def _consolidated_requirement(requirements: list[str]) -> str:
    """Join multiple requirements into a single numbered-list string."""
    if len(requirements) == 1:
        return requirements[0]
    lines = "\n".join(f"{i + 1}) {r}" for i, r in enumerate(requirements))
    return lines


def _run_one(
    *,
    repo_root: Path,
    branch: str,
    worktree: Path | None,
    base: str,
    requirement: str,
    requirement_id: str | None,
    model: str,
    cursor_delay: float,
    dry_run: bool,
) -> int:
    """Create one worktree for a single (possibly consolidated) requirement."""
    wt = worktree or default_worktree_path(repo_root, branch)

    print(f"Repo:     {repo_root}")
    print(f"Branch:   {branch}")
    print(f"Worktree: {wt}")
    print(f"Base:     {base}\n")

    create_worktree(
        repo_root=repo_root,
        branch=branch,
        worktree_path=wt,
        base=base,
        dry_run=dry_run,
    )

    brief, launch, deeplink = start_session_in_worktree(
        worktree=wt,
        branch=branch,
        requirement=requirement,
        requirement_id=requirement_id,
        model=model,
        dry_run=dry_run,
    )

    print(f"\nBrief   → {brief}")
    print(f"Launcher → {launch}\n")

    issue_url = init_phase_tracking(branch=branch, requirement=requirement, dry_run=dry_run)

    if dry_run:
        print("(dry-run — no Cursor opened)")
        if issue_url:
            print(f"Tracking issue → {issue_url}")
        return 0

    S.open_cursor_handoff(
        folder=wt,
        deeplink=deeplink,
        launch_html=launch,
        delay_sec=cursor_delay,
    )

    print("\n─── HANDOFF ───")
    print("Do NOT implement this requirement in the current chat.")
    print(f"Switch to the new Cursor window on: {wt}")
    if issue_url:
        print(f"  Tracking issue: {issue_url}")
    print("After `gh pr create` in that worktree:")
    print(f"  python3 scripts/pr_cost_ledger.py bind-pr --branch {branch}")
    print("  python3 scripts/pr_cost_ledger.py sync --pr <n>")
    return 0


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cli.add_argument(
        "--requirement", dest="requirements", action="append", required=True,
        metavar="REQUIREMENT",
        help=(
            "What to build. Repeat to add multiple requirements. "
            "Multiple requirements are consolidated into one worktree/PR by default "
            "(use --split to create one PR per requirement instead)."
        ),
    )
    cli.add_argument(
        "--split", action="store_true",
        help=(
            "When multiple --requirement flags are given, create a separate "
            "worktree and PR for each instead of consolidating into one."
        ),
    )
    cli.add_argument(
        "--branch",
        help=(
            "Git branch name. For a single (or consolidated) requirement the "
            "default is fix/<slug>. Ignored when --split is used with multiple requirements."
        ),
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
        help=(
            "ID from Playground/REQUIREMENTS.md → mark 🔄 In Progress. "
            "Applied to the first (or consolidated) requirement."
        ),
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
    requirements: list[str] = args.requirements

    if len(requirements) > 1 and args.split:
        # One worktree per requirement
        print(f"--split: creating {len(requirements)} separate worktrees/PRs.\n")
        rc = 0
        for i, req in enumerate(requirements, 1):
            print(f"\n{'=' * 60}")
            print(f"Requirement {i}/{len(requirements)}: {req[:80]}")
            print('=' * 60)
            branch = default_branch(req)
            rc = rc or _run_one(
                repo_root=repo_root,
                branch=branch,
                worktree=None,
                base=args.base,
                requirement=req,
                requirement_id=args.requirement_id if i == 1 else None,
                model=args.model,
                cursor_delay=args.cursor_delay,
                dry_run=args.dry_run,
            )
        return rc

    # Consolidate all requirements into one worktree/PR (default)
    combined = _consolidated_requirement(requirements)
    branch = args.branch or default_branch(combined if len(requirements) == 1 else requirements[0])

    if len(requirements) > 1:
        print(f"Consolidating {len(requirements)} requirements into one PR (branch: {branch}).")
        print("Pass --split to create one PR per requirement.\n")

    return _run_one(
        repo_root=repo_root,
        branch=branch,
        worktree=args.worktree,
        base=args.base,
        requirement=combined,
        requirement_id=args.requirement_id,
        model=args.model,
        cursor_delay=args.cursor_delay,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
