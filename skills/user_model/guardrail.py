#!/usr/bin/env python3
"""Generalizability guardrail for user preferences.

Every candidate preference must pass this gate before it can be stored.
Grounded in the 4 Karpathy behavioral-anchor principles (behavioral-anchor.md):
  1. Assume the simplest thing first — avoid over-specification.
  2. Simplify without asking — keep rules terse, not brittle step-by-step procedures.
  3. Surgical diffs — a preference should be narrowly scoped.
  4. Declare intent, verify outcome — the preference must be actionable/decidable.

score_candidate(text) -> GuardrailResult (score/max, per-criterion results).
The caller decides the pass threshold; default for add_preference() is 4/6.

CLI:
    python -m skills.user_model.guardrail score "text here"
    python -m skills.user_model.guardrail score "text" --threshold 3
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CriterionResult:
    name: str
    passed: bool
    reason: str


@dataclass
class GuardrailResult:
    score: int
    max_score: int
    results: list[CriterionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.score >= self.max_score // 2  # >=50% by default; caller overrides

    def failures(self) -> list[str]:
        return [r.name for r in self.results if not r.passed]

    def summary(self) -> str:
        lines = [f"Score: {self.score}/{self.max_score}"]
        for r in self.results:
            mark = "✓" if r.passed else "✗"
            lines.append(f"  {mark} {r.name}: {r.reason}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# One-off / task-specific token patterns
# ---------------------------------------------------------------------------

# Patterns that indicate the text is tied to a specific task instance
_TASK_SPECIFIC_PATTERNS = [
    re.compile(r"\b(PR\s*#?\d+|issue\s*#?\d+)\b", re.I),    # PR #63, issue #70
    re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"),                  # ISO dates
    re.compile(r"\bfix/[\w-]+\b"),                            # branch names
    re.compile(r"\bsession-[\w-]+-phase\.json\b", re.I),      # phase cache files
    re.compile(r"\bcommit\s+[0-9a-f]{6,}\b", re.I),          # commit SHAs
]

# Patterns that indicate a principle / general rule (positive signal)
_PRINCIPLE_PATTERNS = [
    re.compile(r"\b(always|never|every\s+time|in\s+general|by\s+default)\b", re.I),
    re.compile(r"\bshould\b", re.I),
    re.compile(r"\bprefer\b", re.I),
    re.compile(r"\bwhenever\b", re.I),
    re.compile(r"\bany\s+(time|requirement|issue|work|chat)\b", re.I),
]

# Step-by-step / overly-prescriptive patterns
_PRESCRIPTIVE_PATTERNS = [
    re.compile(r"\b(step\s+\d+|first\s+do|then\s+do|next\s+do|step\s+by\s+step)\b", re.I),
    re.compile(r"^\s*\d+\.\s+\w", re.M),   # numbered list items (brittle procedure)
    re.compile(r"\bversion\s+\d+\.\d+\b", re.I),  # pinned versions
    re.compile(r"\bonly\s+on\s+\w+\s+(day|date|monday|tuesday|wednesday)\b", re.I),
]

# Known agent scope names for criterion 3
_KNOWN_AGENTS = {"bhaga", "chitra", "chanakya", "akshaya", "jarvis", "global"}

# Phrases that mean "this is transient / temporary"
_TRANSIENT_PATTERNS = [
    re.compile(r"\b(for\s+now|temporary|temporarily|this\s+time\s+only|just\s+this\s+once)\b", re.I),
    re.compile(r"\buntil\s+(we|I|you)\b", re.I),
    re.compile(r"\bonce\s+we\s+(fix|build|migrate)\b", re.I),
]


# ---------------------------------------------------------------------------
# Criteria (each returns CriterionResult)
# ---------------------------------------------------------------------------

def _c1_generalizable(text: str) -> CriterionResult:
    """Not tied to a specific task instance (no PR#, issue#, dates, branch names)."""
    hits = [p.search(text) for p in _TASK_SPECIFIC_PATTERNS if p.search(text)]
    if hits:
        sample = hits[0].group(0)
        return CriterionResult(
            "generalizable",
            False,
            f"contains task-specific token: {sample!r} — extract the general rule",
        )
    return CriterionResult("generalizable", True, "no task-specific tokens found")


def _c2_not_prescriptive(text: str) -> CriterionResult:
    """Principle-shaped, not a brittle step-by-step procedure."""
    # Count total occurrences across all prescriptive patterns (not just unique pattern types).
    total_presc = sum(len(p.findall(text)) for p in _PRESCRIPTIVE_PATTERNS)
    if total_presc >= 2:
        return CriterionResult(
            "not_prescriptive",
            False,
            "looks like a procedure (numbered steps / pinned versions); prefer a principle",
        )
    # Reward if it has any principle phrasing
    has_principle = any(p.search(text) for p in _PRINCIPLE_PATTERNS)
    if not has_principle and len(text.split()) > 40:
        return CriterionResult(
            "not_prescriptive",
            False,
            "long text without principle phrasing — check for over-specification",
        )
    return CriterionResult("not_prescriptive", True, "principle-shaped or concise")


def _c3_scoped(text: str) -> CriterionResult:
    """Declares a scope (global or a known agent) or is clearly cross-cutting."""
    text_lower = text.lower()
    if any(agent in text_lower for agent in _KNOWN_AGENTS):
        return CriterionResult("scoped", True, "mentions a known agent or global scope")
    # Accept if text is short (< 20 words) and reads as a global principle
    if len(text.split()) < 20 and any(p.search(text) for p in _PRINCIPLE_PATTERNS):
        return CriterionResult("scoped", True, "short global principle — scope inferred as global")
    return CriterionResult(
        "scoped",
        False,
        "unclear scope — add 'for BHAGA', 'globally', etc., so it isn't blindly applied",
    )


def _c4_non_duplicate(text: str) -> CriterionResult:
    """Not already covered by an active preference (checked against live store)."""
    try:
        # Lazy import to avoid circular dependency
        _here = Path(__file__).parent
        sys.path.insert(0, str(_here.parent.parent))
        from skills.user_model.store import list_preferences  # type: ignore
        from skills.user_model.store import _normalize         # type: ignore
        existing = list_preferences()
        norm = _normalize(text)
        for pref in existing:
            for val in pref.fields.values():
                if val and len(val) > 10 and _normalize(val)[:60] == norm[:60]:
                    return CriterionResult(
                        "non_duplicate",
                        False,
                        f"appears to duplicate existing preference: {val[:60]!r}",
                    )
    except Exception:
        pass  # degrade gracefully if store not available
    return CriterionResult("non_duplicate", True, "no obvious duplicate found")


def _c5_actionable(text: str) -> CriterionResult:
    """A future agent can act on it — contains a verb + an observable outcome."""
    # Must have at least one action verb
    action_verbs = re.compile(
        r"\b(use|run|check|verify|prefer|skip|avoid|always|never|require|"
        r"show|ask|build|set|apply|record|store|update|add|remove)\b",
        re.I,
    )
    if not action_verbs.search(text):
        return CriterionResult(
            "actionable",
            False,
            "no action verb found — a preference needs to drive agent behavior",
        )
    return CriterionResult("actionable", True, "contains an action verb")


def _c6_durable(text: str) -> CriterionResult:
    """Not explicitly transient or one-time."""
    hits = [p.search(text) for p in _TRANSIENT_PATTERNS if p.search(text)]
    if hits:
        sample = hits[0].group(0)
        return CriterionResult(
            "durable",
            False,
            f"marked as transient ({sample!r}) — preferences should be standing policy",
        )
    return CriterionResult("durable", True, "no transient markers")


_CRITERIA = [_c1_generalizable, _c2_not_prescriptive, _c3_scoped,
             _c4_non_duplicate, _c5_actionable, _c6_durable]

DEFAULT_THRESHOLD = 4  # 4/6 required to pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_candidate(text: str) -> GuardrailResult:
    """Score a preference candidate against all 6 criteria.

    Criterion 1 (generalizable) is a hard gate: if the text contains
    task-specific tokens (PR numbers, ISO dates, branch names, etc.) the
    result immediately fails regardless of other scores, because a task-
    specific item must never enter the standing preference store.

    Returns a GuardrailResult with per-criterion detail. Does NOT modify any
    file — purely evaluative.
    """
    # Hard gate: run generalizable first; fail fast.
    c1_result = _c1_generalizable(text)
    if not c1_result.passed:
        # Fill remaining criteria as skipped so callers see a complete result.
        skipped = [
            CriterionResult(c.__name__.split("_c")[1][2:].split("_", 1)[-1],
                            False, "skipped (hard gate c1 failed)")
            for c in _CRITERIA[1:]
        ]
        return GuardrailResult(
            score=0,
            max_score=len(_CRITERIA),
            results=[c1_result] + skipped,
        )

    results = [c1_result] + [c(text) for c in _CRITERIA[1:]]
    score = sum(1 for r in results if r.passed)
    return GuardrailResult(score=score, max_score=len(_CRITERIA), results=results)


def passes(text: str, threshold: int = DEFAULT_THRESHOLD) -> bool:
    """Quick boolean check — True when the candidate meets the threshold."""
    return score_candidate(text).score >= threshold


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_score(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Score a preference candidate for generalizability."
    )
    parser.add_argument("text", help="Candidate preference text (quote it)")
    parser.add_argument(
        "--threshold", type=int, default=DEFAULT_THRESHOLD,
        help=f"Minimum passing score (default: {DEFAULT_THRESHOLD}/{len(_CRITERIA)})",
    )
    args = parser.parse_args(argv)
    result = score_candidate(args.text)
    print(result.summary())
    verdict = "PASS" if result.score >= args.threshold else "FAIL"
    print(f"\nVerdict: {verdict} ({result.score}/{result.max_score} >= {args.threshold})")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("Usage: python -m skills.user_model.guardrail score '<text>' [--threshold N]")
        sys.exit(0)
    if argv[0] == "score":
        sys.exit(_cli_score(argv[1:]))
    print(f"Unknown command: {argv[0]}", file=sys.stderr)
    sys.exit(1)
