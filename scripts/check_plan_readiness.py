#!/usr/bin/env python3
"""Plan execution-readiness gate.

Scores a plan markdown file against the 10-point checklist in
.cursor/rules/plan-execution-readiness.mdc and exits nonzero when the score is
below the threshold.  Acts as the HARD gate in verify.py --full --plan <path>
that guards the Plan -> Agent transition so a weaker model can execute the plan
without additional operator prompting.

Usage:
    python3 scripts/check_plan_readiness.py --plan path/to/plan.md
    python3 scripts/check_plan_readiness.py --plan path/to/plan.md --threshold 8

Exit 0  = plan meets or exceeds the threshold (default: 9/10).
Exit 1  = plan is below threshold; table shows which items failed.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Checklist items (aligned 1-for-1 with plan-execution-readiness.md)
# ---------------------------------------------------------------------------

def _check_file_line_citations(text: str) -> tuple[bool, str]:
    """Item 1: Every change cites exact file path + line number."""
    # Accept patterns like foo.py:42, path/to/file.md line 3, or markdown links
    # with line fragments: [text](file.py) ... :42
    patterns = [
        r"\b\w[\w/.-]+\.(py|md|sh|json|yml|yaml|txt)\b.*?:\d+",  # file.py:42
        r"line\s+\d+",           # "line 42"
        r"lines?\s+\d+[-–]\d+",  # "lines 14-23"
        r"\[\w[\w\s/.-]*\]\([^)]+\).*?:\d+",  # md link + :N
        r"offset\s*=\s*\d+",     # Read tool offset param (used in plan evidence)
    ]
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True, "file:line citations found"
    return False, "no file:line citations (e.g. foo.py:42, 'line N')"


def _check_inline_stubs(text: str) -> tuple[bool, str]:
    """Item 2: Concrete artifacts inline — stubs, CLI commands, env vars."""
    has_code_fence = bool(re.search(r"```\w*\n", text))
    has_bash_cmd = bool(re.search(r"python3\s+\w|bash\s+\w|git\s+\w|gh\s+\w", text))
    has_function_stub = bool(re.search(
        r"def\s+\w+\(|class\s+\w+|NamedTuple|dataclass|argparse", text))
    if has_code_fence and (has_bash_cmd or has_function_stub):
        return True, "code fences + CLI/stubs found"
    if has_bash_cmd:
        return True, "CLI commands found"
    return False, "no inline stubs/CLI commands in fenced blocks"


def _check_milestones_with_verify(text: str) -> tuple[bool, str]:
    """Item 3: >=3 milestones each with a verifiable test command."""
    # Count milestone headings
    milestone_headings = re.findall(
        r"^#{1,3}\s+Milestone\s+\d+|^#{1,3}\s+M\d+\s+[-—]", text, re.MULTILINE | re.IGNORECASE
    )
    if len(milestone_headings) < 3:
        return False, f"only {len(milestone_headings)} milestone(s) found (need >=3)"
    # Each milestone section should have a Verify block or test command
    verify_blocks = re.findall(
        r"(?i)\*\*verify|verify \(copy|```bash[\s\S]*?pytest|```bash[\s\S]*?python3 scripts/verify",
        text
    )
    if len(verify_blocks) >= 3:
        return True, f"{len(milestone_headings)} milestones, {len(verify_blocks)} verify blocks"
    # Looser check: at least one verify command and 3+ milestones
    has_verify_cmd = bool(re.search(
        r"pytest|python3 scripts/verify|python3 scripts/check_", text, re.IGNORECASE
    ))
    if has_verify_cmd and len(milestone_headings) >= 3:
        return True, f"{len(milestone_headings)} milestones with verify commands"
    return False, f"{len(milestone_headings)} milestones but missing verify commands per milestone"


def _check_per_scenario_evidence(text: str) -> tuple[bool, str]:
    """Item 4: Per-scenario evidence enumerated (happy path + failures/recovery)."""
    evidence_signals = [
        r"happy\s+path|green\s+path",
        r"failure|error|failure.*recover|recovery",
        r"evidence\s+pack|§\s*4|PR\s+§\s*4|\bsection\s+4\b",
        r"dry.?run|--dry-run",
        r"pass\s+criterion|exit\s+criteria|verify.*pass",
    ]
    matches = sum(1 for pat in evidence_signals
                  if re.search(pat, text, re.IGNORECASE))
    if matches >= 3:
        return True, f"{matches} evidence-related signals"
    if matches >= 2:
        return True, f"{matches} evidence-related signals (marginal pass)"
    return False, f"only {matches} evidence signals — enumerate happy path + failure cases"


def _check_sandbox_tier(text: str) -> tuple[bool, str]:
    """Item 5: Sandbox tier stated (Tier-1 e2e / Tier-2 live)."""
    signals = [
        r"sandbox|tier.?\d|e2e|end.to.end",
        r"live.?run|prod.*change|local.*verify|verify.*local",
        r"out.of.scope|in.scope",
    ]
    matches = sum(1 for pat in signals if re.search(pat, text, re.IGNORECASE))
    if matches >= 2:
        return True, "sandbox/scope context present"
    return False, "sandbox tier not stated (add scope/out-of-scope section)"


def _check_invariants(text: str) -> tuple[bool, str]:
    """Item 6: Invariants explicitly preserved."""
    signals = [
        r"invariant|idempotent|upsert",
        r"integer\s+cent|America/Chicago|read.only",
        r"forward.only|no\s+hardcod|backward.compat",
        r"side.effect|never\s+retry|duplicate",
        r"risk\s+guard|preserv|must\s+not\s+break",
    ]
    matches = sum(1 for pat in signals if re.search(pat, text, re.IGNORECASE))
    if matches >= 2:
        return True, f"{matches} invariant/safety signals"
    return False, f"only {matches} invariant signals — name what must not break"


def _check_feature_flag_decision(text: str) -> tuple[bool, str]:
    """Item 7: Feature-flag decision made."""
    signals = [
        r"feature.?flag|FEATURE_FLAG|out.of.scope|flag.*decision",
        r"no\s+(new\s+)?flag|flag.*not\s+needed|flag.*n/a",
        r"additive|backward.compat|no.*existing.*behavior",
        r"scope|in.scope|out.of.scope",
    ]
    matches = sum(1 for pat in signals if re.search(pat, text, re.IGNORECASE))
    if matches >= 2:
        return True, "feature-flag/scope decision present"
    return False, "feature-flag decision missing (add scope or flag rationale)"


def _check_docs_lockstep(text: str) -> tuple[bool, str]:
    """Item 8: Docs lock-step targets listed."""
    doc_signals = [
        r"RUNBOOK\.md|PROGRESS\.md|DOMAIN\.md",
        r"check_doc_freshness|doc.maintenance|lock.step|doc.*update",
        r"CONTRIBUTING\.md|WORKFLOW\.md|AGENTS\.md",
        r"README\.md|scripts/README",
    ]
    matches = sum(1 for pat in doc_signals if re.search(pat, text, re.IGNORECASE))
    if matches >= 2:
        return True, f"{matches} doc lock-step signals"
    return False, f"only {matches} doc signals — list which docs update with code changes"


def _check_branch_pr_mechanics(text: str) -> tuple[bool, str]:
    """Item 9: Branch/PR mechanics noted."""
    signals = [
        r"gh\s+pr\s+create|PR\s+mechanics|branch.*PR",
        r"never\s+self.?merge|operator.*merge|babysit",
        r"pr.workflow|pr-workflow",
        r"--no-verify|bot.?account|GH_TOKEN",
        r"one\s+(branch|PR)\s+per|single\s+branch",
    ]
    matches = sum(1 for pat in signals if re.search(pat, text, re.IGNORECASE))
    if matches >= 2:
        return True, f"{matches} PR mechanics signals"
    return False, f"only {matches} PR mechanics signals — note branch, bot account, never self-merge"


def _check_model_routing(text: str) -> tuple[bool, str]:
    """Item 10: Model routing per milestone stated."""
    signals = [
        r"Sonnet|Opus|Composer|sonnet|opus|composer",
        r"model.?routing|cost.?playbook|model.*per.*milestone",
        r"M\d.*Sonnet|M\d.*Opus|M\d.*Composer",
    ]
    matches = sum(1 for pat in signals if re.search(pat, text, re.IGNORECASE))
    if matches >= 2:
        return True, "model routing present"
    return False, "model routing not stated — specify Sonnet/Opus/Composer per milestone"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

CHECKLIST: list[tuple[str, object]] = [
    ("1. File:line citations", _check_file_line_citations),
    ("2. Inline stubs/CLI commands", _check_inline_stubs),
    ("3. >=3 milestones with verify", _check_milestones_with_verify),
    ("4. Per-scenario evidence", _check_per_scenario_evidence),
    ("5. Sandbox tier / scope", _check_sandbox_tier),
    ("6. Invariants preserved", _check_invariants),
    ("7. Feature-flag decision", _check_feature_flag_decision),
    ("8. Docs lock-step listed", _check_docs_lockstep),
    ("9. Branch/PR mechanics", _check_branch_pr_mechanics),
    ("10. Model routing", _check_model_routing),
]


def score_plan(text: str) -> list[tuple[str, bool, str]]:
    """Score the plan text. Returns list of (label, passed, detail)."""
    results = []
    for label, check_fn in CHECKLIST:
        passed, detail = check_fn(text)
        results.append((label, passed, detail))
    return results


def _resolve_branch(branch: str | None) -> str | None:
    """Return branch arg, or detect current branch via git, or None."""
    if branch:
        return branch
    try:
        import subprocess as _sub
        repo = Path(__file__).parent.parent
        res = _sub.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(repo),
        )
        return res.stdout.strip() or None
    except Exception:
        return None


def _check_phase_gates(branch: str | None) -> tuple[bool, str | list]:
    """Return (ok, message) — verify jam + define-evidence are recorded done.

    Delegates cache path resolution to phase_state so the same METRICS_DIR
    (patchable in tests) is used.
    Loads the phase cache for the given branch (or current branch when None).
    If the cache is absent the branch is untracked — gate skipped (ok=True).
    """
    try:
        import json as _json
        sys.path.insert(0, str(Path(__file__).parent))
        from phase_state import _cache_path  # type: ignore

        branch = _resolve_branch(branch)
        if not branch:
            return True, "could not determine branch — phase precheck skipped"

        cache_path = _cache_path(branch)
        if not cache_path.exists():
            return True, f"branch {branch!r} not lifecycle-tracked — phase precheck skipped"

        data = _json.loads(cache_path.read_text())
        done_set = set(data.get("done", []))
        missing = [g for g in ("jam", "define-evidence") if g not in done_set]
        if missing:
            return False, missing
        return True, "jam and define-evidence are recorded done"
    except Exception as exc:
        return True, f"phase precheck error (non-fatal): {exc}"


def _stamp_plan_ready(branch: str | None, plan_name: str, score: int) -> None:
    """Write plan_ready stamp into the phase cache so OBSERVABLE_FLOOR can detect it."""
    try:
        import json as _json
        import datetime as _dt
        sys.path.insert(0, str(Path(__file__).parent))
        from phase_state import _cache_path  # type: ignore

        branch = _resolve_branch(branch)
        if not branch:
            return

        cache_path = _cache_path(branch)
        if not cache_path.exists():
            return

        data = _json.loads(cache_path.read_text())
        data["plan_ready"] = {
            "plan": plan_name,
            "score": score,
            "at": _dt.datetime.utcnow().isoformat() + "Z",
        }
        cache_path.write_text(_json.dumps(data, indent=2))
    except Exception:
        pass  # stamp is best-effort; never block plan gate on it


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score a plan file against the 10-point execution-readiness checklist."
    )
    parser.add_argument("--plan", required=True, metavar="PATH",
                        help="Path to the plan markdown file")
    parser.add_argument("--threshold", type=int, default=9, metavar="N",
                        help="Minimum passing score out of 10 (default: 9)")
    parser.add_argument("--branch", default=None, metavar="BRANCH",
                        help="Branch to check phase gates for (default: current branch)")
    parser.add_argument("--skip-phase-check", action="store_true",
                        help="Skip the jam/define-evidence phase precheck")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"ERROR: plan file not found: {plan_path}", file=sys.stderr)
        sys.exit(1)

    # Phase precheck: jam + define-evidence must be recorded before plan is ready.
    if not args.skip_phase_check:
        ok, detail = _check_phase_gates(args.branch)
        if not ok:
            missing = detail  # list of missing substeps
            branch = args.branch or "(current)"
            print(
                f"\nPHASE PRECHECK FAILED — branch {branch!r}\n"
                f"The plan gate requires operator gates to be recorded done first.\n"
                f"Missing: {', '.join(missing)}\n",
                file=sys.stderr,
            )
            for substep in missing:
                print(
                    f"  Record it:\n"
                    f"    python3 scripts/phase_state.py advance --branch {branch}"
                    f" --to {substep} --operator-approved [--note '<summary>']",
                    file=sys.stderr,
                )
            print(
                "\nObtain operator approval in chat (jam/define-evidence gates), record via "
                "phase_state.py advance, then re-run check_plan_readiness.py.\n",
                file=sys.stderr,
            )
            sys.exit(1)

    text = plan_path.read_text(encoding="utf-8")
    results = score_plan(text)

    passed_count = sum(1 for _, p, _ in results if p)
    total = len(results)

    print(f"\nPlan readiness: {plan_path.name}")
    print(f"{'─'*60}")
    print(f"{'Item':<38} {'Result':<6} Detail")
    print(f"{'─'*60}")
    for label, passed, detail in results:
        indicator = "✓" if passed else "✗"
        result_str = "PASS" if passed else "FAIL"
        print(f"  {indicator} {label:<36} {result_str:<6} {detail}")
    print(f"{'─'*60}")
    print(f"Score: {passed_count}/{total}")

    if passed_count < args.threshold:
        print(f"\nPlan is NOT execution-ready (score {passed_count} < threshold {args.threshold}).")
        print("Fix the FAIL items above before moving from Plan to Agent mode.")
        sys.exit(1)
    else:
        print(f"\nPlan is execution-ready (score {passed_count}/{total} >= {args.threshold}).")
        _stamp_plan_ready(args.branch, plan_path.name, passed_count)


if __name__ == "__main__":
    main()
