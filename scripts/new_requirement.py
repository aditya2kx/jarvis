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
import shutil
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


def _current_branch(repo_root: Path) -> str:
    """Return the current branch name, or 'origin/main' as a safe fallback."""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return branch if branch and branch != "HEAD" else "origin/main"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "origin/main"


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

    # Only fetch when base looks like a remote ref; local branches are already present.
    if base.startswith("origin/"):
        print(f"Fetching {base} …")
        if not dry_run:
            remote_branch = base.split("/", 1)[1]
            _run_git(repo_root, "fetch", "origin", remote_branch)
    else:
        print(f"Using local base branch: {base}")

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
    model: str = S.DEFAULT_JAM_HANDOFF_MODEL,
    mode: str = S.DEFAULT_JAM_HANDOFF_MODE,
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
    seed = S.seed_prompt_jam(branch, brief_rel=brief_rel, requirement=req or None)
    deeplink = S.make_deeplink(seed, mode=mode, model=model)
    return brief, launch, deeplink


def json_load(worktree: Path, branch: str) -> dict:
    import json

    path = worktree / "metrics" / "pr_cost" / f"session-{L._slug(branch)}.json"
    if path.is_file():
        return json.loads(path.read_text())
    return {}


def init_phase_tracking(
    *, branch: str, requirement: str, dry_run: bool, existing_issue: int | None = None
) -> str | None:
    """Create (or link) the GitHub work-tracking issue for this branch.

    phase_state is GitHub-global (one issue per branch), so we call THIS repo's
    copy, not the worktree copy. Non-fatal: a tracking failure must not abort the
    handoff. Returns the issue URL when known.

    Pass existing_issue to link a pre-filed issue instead of creating a new one.
    """
    script = Path(__file__).parent / "phase_state.py"
    args = [sys.executable, str(script), "init", "--branch", branch]
    if existing_issue:
        args += ["--issue", str(existing_issue)]
    else:
        args += ["--requirement", requirement, "--kickoff"]
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


def _seed_cache_to_worktree(*, branch: str, worktree: Path, dry_run: bool) -> None:
    """Copy the phase cache from the parent repo into the worktree's metrics/pr_cost/.

    new_requirement.py calls phase_state.py init from the PARENT repo (intentional —
    phase tracking is GitHub-global).  That writes the cache to the parent's
    metrics/pr_cost/*-phase.json.  The worktree is a sibling directory with its own
    metrics/pr_cost/, so without this copy phase_state.py status inside the worktree
    shows Issue: #none (no local cache) even though GitHub has the correct issue.
    """
    import re as _re
    slug = _re.sub(r"[^a-zA-Z0-9_-]", "-", branch)[:60]
    src = Path(__file__).parent.parent / "metrics" / "pr_cost" / f"session-{slug}-phase.json"
    dst_dir = worktree / "metrics" / "pr_cost"
    dst = dst_dir / src.name
    if dry_run:
        print(f"(dry-run) would seed phase cache: {src.name} → worktree/metrics/pr_cost/")
        return
    if not src.exists():
        # init_phase_tracking failed silently; nothing to copy
        return
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        print(f"Phase cache seeded into worktree: {dst}")
    except Exception as exc:
        print(f"⚠️  Could not seed phase cache to worktree (non-fatal): {exc}", file=sys.stderr)


def _run_one(
    *,
    repo_root: Path,
    branch: str,
    worktree: Path | None,
    base: str,
    requirement: str,
    requirement_id: str | None,
    model: str,
    handoff_mode: str,
    cursor_delay: float,
    dry_run: bool,
    no_open: bool = False,
    existing_issue: int | None = None,
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
        mode=handoff_mode,
        dry_run=dry_run,
    )

    print(f"\nBrief   → {brief}")
    print(f"Launcher → {launch}\n")

    issue_url = init_phase_tracking(
        branch=branch, requirement=requirement, dry_run=dry_run,
        existing_issue=existing_issue,
    )

    # Seed the phase cache into the worktree so `phase_state.py status` inside
    # the worktree shows the correct issue number and substep state.  Without
    # this copy the worktree's metrics/pr_cost/ has no *-phase.json and status
    # reports Issue: #none even though GitHub has the correct issue.
    _seed_cache_to_worktree(branch=branch, worktree=wt, dry_run=dry_run)

    if dry_run:
        print("(dry-run — no Cursor opened)")
        if issue_url:
            print(f"Tracking issue → {issue_url}")
        return 0

    if no_open:
        # Agent-driven run (cloud/CI/dogfood): skip the Cursor window.
        print("\n─── WORKTREE READY (--no-open) ───")
        print(f"Worktree: {wt}")
        if issue_url:
            print(f"Tracking issue: {issue_url}")
        print("Pick up the work in this chat; commit and push from within the worktree.")
        return 0

    S.open_cursor_handoff(
        folder=wt,
        deeplink=deeplink,
        launch_html=launch,
        delay_sec=cursor_delay,
        mode=handoff_mode,
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
        "--base", default=None,
        help=(
            "Branch/ref to create the worktree from. "
            "Defaults to the current branch of this repo so that worktrees "
            "inherit the framework changes in flight (e.g. an open PR branch). "
            "Pass 'origin/main' explicitly to branch from clean main."
        ),
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
        "--mode", default=S.DEFAULT_JAM_HANDOFF_MODE,
        choices=("ask", "agent", "plan"),
        help=f"Cursor chat mode for the jam handoff deeplink (default: {S.DEFAULT_JAM_HANDOFF_MODE})",
    )
    cli.add_argument(
        "--model", default=S.DEFAULT_JAM_HANDOFF_MODEL,
        help=f"Model slug for the jam handoff deeplink (default: {S.DEFAULT_JAM_HANDOFF_MODEL})",
    )
    cli.add_argument("--no-open", action="store_true",
                     help="Create worktree + brief + issue without opening a Cursor window "
                          "(agent-driven / cloud / dogfood runs).")
    cli.add_argument("--issue", type=int, default=None,
                     help="Link an already-filed GitHub issue instead of creating a new one.")
    cli.add_argument("--dry-run", action="store_true", help="Print plan without creating anything")
    args = cli.parse_args(argv)

    repo_root = _repo_root()
    requirements: list[str] = args.requirements

    # Resolve base: default to current branch so worktrees inherit in-flight framework changes.
    base = args.base or _current_branch(repo_root)

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
                base=base,
                requirement=req,
                requirement_id=args.requirement_id if i == 1 else None,
                model=args.model,
                handoff_mode=args.mode,
                cursor_delay=args.cursor_delay,
                dry_run=args.dry_run,
                no_open=args.no_open,
                existing_issue=args.issue,
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
        base=base,
        requirement=combined,
        requirement_id=args.requirement_id,
        model=args.model,
        handoff_mode=args.mode,
        cursor_delay=args.cursor_delay,
        dry_run=args.dry_run,
        no_open=args.no_open,
        existing_issue=args.issue,
    )


if __name__ == "__main__":
    raise SystemExit(main())
