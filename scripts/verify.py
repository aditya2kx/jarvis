#!/usr/bin/env python3
"""Local verify harness — mirrors CI so the development loop stays fast.

Usage:
    python3 scripts/verify.py --fast     # pre-commit speed: secret scan + doc-freshness + pytest
    python3 scripts/verify.py --full     # pre-push: all fast gates + full pytest + PR gates
    python3 scripts/verify.py --full --plan path/to/plan.md   # also check plan readiness
    python3 scripts/verify.py --full --strict                 # promote doc-freshness to hard

"Done" means this script exits 0. Every gate here has a CI counterpart so the
local loop and CI stay in sync. The CI-parity test in test_verify.py enforces this.

Gates are a list of (name, argv, hard, modes) tuples — no logic duplication;
every gate shells out to an existing script.  Escape hatch: VERIFY=0 env var.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Gate registry
# ---------------------------------------------------------------------------

class Gate(NamedTuple):
    name: str
    argv: list  # passed to subprocess; {PR} and {PLAN} are substituted at runtime
    hard: bool  # True => exit nonzero on failure; False => nudge only
    modes: set  # {"fast"} or {"full"} or {"fast", "full"}


# Gates whose argv contains {PR} are skipped when no PR is open.
# Gates whose argv contains {PLAN} are skipped when --plan is not supplied.
GATES: list[Gate] = [
    # secret-scan: scan the git diff (staged for --fast; origin/main delta for --full)
    # so pattern references in source code don't produce false positives.
    Gate(
        name="secret-scan-staged",
        argv=["git", "diff", "--cached", "--unified=0"],
        hard=True,
        modes={"fast"},
    ),
    Gate(
        name="secret-scan-full",
        argv=["git", "diff", "origin/main", "--unified=0"],
        hard=True,
        modes={"full"},
    ),
    Gate(
        name="doc-freshness",
        # hard flag overridden at runtime when --strict is passed
        argv=["python3", "scripts/check_doc_freshness.py"],
        hard=False,
        modes={"fast"},
    ),
    Gate(
        name="doc-freshness-base",
        argv=["python3", "scripts/check_doc_freshness.py", "--base", "origin/main"],
        hard=False,  # promoted to hard when --strict
        modes={"full"},
    ),
    Gate(
        name="pytest-changed",
        # In fast mode run only test files that correspond to changed scripts;
        # computed dynamically at runtime via _changed_test_files().
        # argv is a sentinel: the actual test files are injected in run().
        argv=["python3", "-m", "pytest", "-q", "--tb=short", "{CHANGED_TESTS}"],
        hard=True,
        modes={"fast"},
    ),
    Gate(
        name="pytest-full",
        argv=["python3", "-m", "pytest", "-q", "--tb=short",
              "agents/bhaga/scripts/", "skills/", "core/", "cloud/", "scripts/"],
        hard=True,
        modes={"full"},
    ),
    Gate(
        name="plan-readiness",
        argv=["python3", "scripts/check_plan_readiness.py", "--plan", "{PLAN}"],
        hard=True,
        modes={"full"},
    ),
    Gate(
        name="pr-description",
        argv=["python3", "scripts/check_pr_description.py", "--pr", "{PR}"],
        hard=True,
        modes={"full"},
    ),
    Gate(
        name="evidence-readiness",
        argv=["python3", "scripts/check_evidence_readiness.py", "--pr", "{PR}"],
        hard=True,
        modes={"full"},
    ),
    Gate(
        name="pr-review-replies",
        argv=["python3", "scripts/check_pr_review_replies.py", "--pr", "{PR}"],
        hard=True,
        modes={"full"},
    ),
    Gate(
        name="phase-gate",
        argv=["python3", "scripts/phase_state.py", "gate"],
        hard=True,
        modes={"full"},
    ),
    Gate(
        name="repo-default-branch",
        argv=["python3", "scripts/check_repo_default_branch.py"],
        hard=True,
        modes={"full"},
    ),
]

# Names of CI scripts this local harness must cover (used by test_ci_parity).
# Update this list when a new CI workflow adds a python3 scripts/* invocation.
CI_SCRIPT_NAMES: frozenset[str] = frozenset([
    "check_doc_freshness.py",
    "check_pr_description.py",
    "pr_cost_ledger.py",   # cost gate — handled outside verify.py (pr-workflow.mdc)
])

# Secret pattern from CONTRIBUTING.md § "Pushing & opening PRs"
SECRET_PATTERN = r"AIza|sk-[A-Za-z0-9]{20}|-----BEGIN|password\s*[:=]|api[_-]?key"


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------

def _changed_test_files() -> list[str]:
    """Return test_*.py paths in scripts/ that correspond to changed files.

    Strategy: get changed .py files from git (staged + unstaged), map each
    script/foo.py -> scripts/test_foo.py, and collect those that exist.
    Falls back to all scripts/test_*.py if git is unavailable.
    """
    try:
        staged = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            capture_output=True, text=True, timeout=5,
        ).stdout.splitlines()
        unstaged = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, timeout=5,
        ).stdout.splitlines()
        changed = set(staged) | set(unstaged)
    except Exception:
        changed = set()

    test_files: list[str] = []
    for path in changed:
        if not path.startswith("scripts/"):
            continue
        name = os.path.basename(path)
        if name.startswith("test_"):
            # Changed file is itself a test
            if os.path.exists(path):
                test_files.append(path)
        else:
            # Try to find the corresponding test file
            base = name.replace(".py", "")
            candidate = f"scripts/test_{base}.py"
            if os.path.exists(candidate):
                test_files.append(candidate)

    if not test_files:
        # No specific tests found — run only scripts/test_verify.py and any new tests
        fallback = [p for p in [
            "scripts/test_verify.py",
            "scripts/test_check_plan_readiness.py",
            "scripts/test_verify_lifecycle.py",
            "scripts/test_phase_state.py",
            "scripts/test_lifecycle.py",
        ] if os.path.exists(p)]
        return fallback if fallback else []

    return sorted(set(test_files))


def _get_pr_number() -> str | None:
    """Return the PR number for the current branch, or None."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number", "-q", ".number"],
            capture_output=True, text=True, timeout=10,
        )
        num = result.stdout.strip()
        return num if num else None
    except Exception:
        return None


def _print_phase_state() -> None:
    """Best-effort: print current phase if phase_state.py exists."""
    if not os.path.exists("scripts/phase_state.py"):
        return
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        result = subprocess.run(
            ["python3", "scripts/phase_state.py", "status", "--branch", branch],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"[phase] {result.stdout.strip()}")
    except Exception:
        pass


def run(mode: str, plan_path: str | None, strict: bool) -> int:
    """Execute all gates for the given mode. Returns exit code (0 = all pass)."""
    if os.environ.get("VERIFY", "1") == "0":
        print("VERIFY=0 — skipping all gates.")
        return 0

    _print_phase_state()

    pr = _get_pr_number() if mode == "full" else None
    changed_tests = _changed_test_files()

    selected: list[tuple[Gate, list]] = []
    for gate in GATES:
        if mode not in gate.modes:
            continue
        argv = list(gate.argv)

        # Substitute {PR}
        if any("{PR}" in a for a in argv):
            if pr is None:
                selected.append((gate, None))  # None = SKIP (no PR)
                continue
            argv = [a.replace("{PR}", pr) for a in argv]

        # Substitute {PLAN}
        if any("{PLAN}" in a for a in argv):
            if plan_path is None:
                selected.append((gate, None))  # None = SKIP (no plan)
                continue
            argv = [a.replace("{PLAN}", plan_path) for a in argv]

        # Substitute {CHANGED_TESTS}
        if any("{CHANGED_TESTS}" in a for a in argv):
            if not changed_tests:
                selected.append((gate, None))  # Nothing to test
                continue
            # Replace the sentinel token with the actual file list
            new_argv: list[str] = []
            for a in argv:
                if "{CHANGED_TESTS}" in a:
                    new_argv.extend(changed_tests)
                else:
                    new_argv.append(a)
            argv = new_argv

        selected.append((gate, argv))

    results: list[tuple[str, str, bool]] = []  # (name, status, is_hard)
    any_hard_fail = False

    for gate, argv in selected:
        is_hard = gate.hard
        if gate.name.startswith("doc-freshness") and strict:
            is_hard = True

        if argv is None:
            results.append((gate.name, "SKIP", is_hard))
            continue

        # secret-scan-* gates: get the diff, then pipe through rg pattern check
        if gate.name.startswith("secret-scan"):
            diff_proc = subprocess.run(argv, capture_output=True, text=True)
            diff_text = diff_proc.stdout
            if not diff_text.strip():
                results.append((gate.name, "PASS", is_hard))
                continue
            import re as _re
            hits = [line for line in diff_text.splitlines()
                    if line.startswith("+") and not line.startswith("+++")
                    and _re.search(SECRET_PATTERN, line, _re.IGNORECASE)]
            if hits:
                print(f"\n[{gate.name}] Potential secrets in diff:")
                for h in hits[:10]:
                    print(f"  {h}")
                results.append((gate.name, "FAIL", is_hard))
                if is_hard:
                    any_hard_fail = True
            else:
                results.append((gate.name, "PASS", is_hard))
            continue

        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            if proc.stdout:
                print(proc.stdout)
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)
            results.append((gate.name, "FAIL", is_hard))
            if is_hard:
                any_hard_fail = True
        else:
            results.append((gate.name, "PASS", is_hard))

    # Print summary table
    print(f"\n{'─'*52}")
    print(f"{'Gate':<28} {'Status':<8} {'Hard?'}")
    print(f"{'─'*52}")
    for name, status, is_hard in results:
        hard_label = "HARD" if is_hard else "nudge"
        indicator = "✓" if status == "PASS" else ("·" if status == "SKIP" else "✗")
        print(f"  {indicator} {name:<26} {status:<8} {hard_label}")
    print(f"{'─'*52}")

    if any_hard_fail:
        print("\nVerify FAILED — fix the HARD gate(s) above before pushing.")
        return 1
    print("\nVerify PASSED.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local CI mirror — run before pushing to catch failures early."
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--fast", action="store_true",
                            help="Pre-commit speed: secret scan + doc-freshness + pytest")
    mode_group.add_argument("--full", action="store_true",
                            help="Pre-push: all fast gates + full pytest + PR gates")
    parser.add_argument("--plan", metavar="PATH",
                        help="Path to plan file for check_plan_readiness (--full only)")
    parser.add_argument("--strict", action="store_true",
                        help="Promote doc-freshness from nudge to HARD gate")
    args = parser.parse_args()

    mode = "fast" if args.fast else "full"
    sys.exit(run(mode, args.plan, args.strict))


if __name__ == "__main__":
    main()
