#!/usr/bin/env python3
"""Single source of truth for Jarvis lifecycle stages and substeps.

All other scripts (phase_state.py, start_pr_session.py, verify_lifecycle.py)
import STAGES from here so the ladder cannot drift.

5 tracking stages, 12 substeps total.  Each substep has a driver
("operator" or "agent") and an exit criterion.  Operator substeps are the
4 reserved gates where the agent must pause for human input.
"""

from __future__ import annotations

from typing import NamedTuple


class Substep(NamedTuple):
    name: str
    driver: str          # "operator" or "agent"
    exit_criterion: str


class Stage(NamedTuple):
    name: str
    substeps: list       # list[Substep]


STAGES: list[Stage] = [
    Stage("align", [
        Substep("specify",         "operator", "Requirement statement exists"),
        Substep("setup",           "agent",    "Worktree + branch + brief with stage ladder exist"),
        Substep("jam",             "operator", "Requirements restated and agreed in Ask mode"),
        Substep("define-evidence", "operator", "Acceptance evidence approved → PR §4 contract"),
    ]),
    Stage("plan", [
        Substep("plan",            "agent",    "check_plan_readiness.py passes (HARD gate)"),
    ]),
    Stage("build", [
        Substep("implement",       "agent",    "Code written; tests exist"),
        Substep("verify",          "agent",    "scripts/verify.py --full green"),
    ]),
    Stage("ship", [
        Substep("pr-evidence",     "agent",    "PR §4 evidence assembled and description complete"),
        Substep("babysit",         "agent",    "CI green; every review comment replied to"),
        Substep("merge",           "operator", "Operator squash-merges to main"),
    ]),
    Stage("verify-learn", [
        Substep("post-merge-verify", "agent",  "Production state verified (sheets/Firestore match expected)"),
        Substep("retrospective",     "agent",  "PROGRESS.md entry + requirements_tracker updated"),
    ]),
]

# Operator-driver substeps — advance to these requires explicit approval.
OPERATOR_SUBSTEPS: frozenset[str] = frozenset(
    s.name for stage in STAGES for s in stage.substeps if s.driver == "operator"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def all_substeps() -> list[Substep]:
    """Return all substeps in order."""
    return [s for stage in STAGES for s in stage.substeps]


def substep_index(name: str) -> int:
    """Return the 0-based index of a substep in all_substeps()."""
    for i, s in enumerate(all_substeps()):
        if s.name == name:
            return i
    raise ValueError(f"Unknown substep: {name!r}")


def next_substep(name: str) -> Substep | None:
    """Return the substep after name, or None if it's the last."""
    steps = all_substeps()
    idx = substep_index(name)
    return steps[idx + 1] if idx + 1 < len(steps) else None


def stage_of(substep_name: str) -> Stage:
    """Return the Stage containing the named substep."""
    for stage in STAGES:
        for s in stage.substeps:
            if s.name == substep_name:
                return stage
    raise ValueError(f"Unknown substep: {substep_name!r}")


def overall_pct(done_set: set[str]) -> int:
    """Return overall progress percentage (0-100)."""
    total = len(all_substeps())
    if not total:
        return 0
    return int(len(done_set & {s.name for s in all_substeps()}) / total * 100)


def stage_pct(stage_name: str, done_set: set[str]) -> int:
    """Return progress percentage for a single stage (0-100)."""
    for stage in STAGES:
        if stage.name == stage_name:
            total = len(stage.substeps)
            if not total:
                return 0
            done = sum(1 for s in stage.substeps if s.name in done_set)
            return int(done / total * 100)
    raise ValueError(f"Unknown stage: {stage_name!r}")


def current_substep(done_set: set[str]) -> Substep | None:
    """Return the first incomplete substep (the 'current' one)."""
    for s in all_substeps():
        if s.name not in done_set:
            return s
    return None  # all done


def brief_ladder_text() -> str:
    """Render the 5-stage ladder as a Markdown section for session briefs."""
    lines = ["## Phase ladder\n"]
    lines.append("Agent self-advances through agent-driver substeps; pauses at operator-reserved gates.\n")
    for stage in STAGES:
        lines.append(f"\n### Stage: {stage.name.upper()}")
        for sub in stage.substeps:
            gate_note = " ← **OPERATOR-RESERVED GATE** (await approval)" if sub.driver == "operator" else ""
            lines.append(f"- **{sub.name}** [{sub.driver}]{gate_note}")
            lines.append(f"  Exit: {sub.exit_criterion}")
    lines.append(
        "\n> Self-drive rule: after each agent substep's exit criterion is met, call "
        "`python3 scripts/phase_state.py advance --branch <branch> --to <next-substep>`. "
        "Never advance past an operator-reserved gate without `approved:<substep>` label on the issue."
    )
    return "\n".join(lines)
