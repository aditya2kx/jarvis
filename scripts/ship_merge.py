#!/usr/bin/env python3
"""Pure helpers for the ship-emoji force-merge workflow.

All functions are dependency-free (stdlib only) and fully unit-testable.
The GitHub Actions workflow (ship-emoji-force-merge.yml) calls these via
``python3 scripts/ship_merge.py <subcommand>`` for the logic that is hard
to express cleanly in bash.

Authorized logins are read from the env var SHIP_MERGE_AUTHORIZED_LOGINS
(comma-separated; default "aditya2kx").  The GitHub author_association must
also be "OWNER" — this is a defense-in-depth check so that even if the
allowlist were extended, non-owners can never trigger the force-merge.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Emoji detection
# ---------------------------------------------------------------------------

# The literal Unicode code points for rocket (🚀) and ship (🚢).
_SHIP_GLYPHS: frozenset[str] = frozenset(["\U0001F680", "\U0001F6A2"])

# Negation tokens: if any appear in the comment body the intent is NOT to ship.
_NEGATION_TOKENS: frozenset[str] = frozenset(["not", "don't", "dont", "wait", "hold", "nope", "no"])

# Accepted standalone phrases (lowercased, stripped, emoji substituted to glyph)
# The body is normalised before comparison: strip outer whitespace, collapse
# internal runs, lower-case.  We then check if it matches one of these patterns.
_SHIP_PHRASES_RE = re.compile(
    r"^(?:[\U0001F680\U0001F6A2]|ship\s+it|[\U0001F680\U0001F6A2]\s+ship\s+it|ship\s+it\s+[\U0001F680\U0001F6A2])$",
    re.UNICODE,
)


def is_ship_intent(body: str) -> bool:
    """Return True when the comment unambiguously expresses intent to ship.

    Rules:
    - Body (stripped + normalised whitespace) must match one of:
        🚀  |  🚢  |  🚀 ship it  |  🚢 ship it  |  ship it 🚀  |  ship it 🚢
    - If ANY negation token appears anywhere in the body, return False.
    """
    if not body:
        return False
    normalised = re.sub(r"\s+", " ", body.strip()).lower()
    # Negation guard: scan lowercased body for negation words as whole words.
    for token in _NEGATION_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", normalised):
            return False
    return bool(_SHIP_PHRASES_RE.match(normalised))


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def _authorized_logins() -> frozenset[str]:
    raw = os.environ.get("SHIP_MERGE_AUTHORIZED_LOGINS", "aditya2kx")
    return frozenset(l.strip() for l in raw.split(",") if l.strip())


def is_authorized(login: str, assoc: str, allowed: frozenset[str] | None = None) -> bool:
    """Return True iff login is in the allowlist AND author_association is OWNER.

    Defense-in-depth: requiring both prevents a collaborator whose name somehow
    ends up in the allowlist from triggering the force-merge.
    """
    if allowed is None:
        allowed = _authorized_logins()
    return login in allowed and assoc == "OWNER"


# ---------------------------------------------------------------------------
# Check-status predicate
# ---------------------------------------------------------------------------

class MergeBlockReason(NamedTuple):
    blocked: bool
    reason: str  # human-readable; empty string when not blocked


def only_evidence_confidence_blocking(
    checks_json: str,
    verdict_header: str,
    confidence: int | None,
) -> MergeBlockReason:
    """Return (blocked=False, "") when the ONLY red check is evidence-confidence.

    Parameters
    ----------
    checks_json:
        JSON string from ``gh pr checks --json name,state,conclusion`` — a list
        of objects with at least ``name`` and ``state``/``conclusion`` keys.
    verdict_header:
        First 3 lines of the latest claude[bot] comment, uppercased.  Used to
        detect REQUEST CHANGES.
    confidence:
        Evidence confidence score parsed from the claude[bot] comment (0-100),
        or None if not found.

    Logic:
    - If verdict_header contains "REQUEST CHANGES" → blocked (reviewer has
      substantive issues that emoji cannot override).
    - If any check OTHER THAN the Claude-review family has state/conclusion
      other than success/neutral/skipped → blocked (real CI failure).
    - If confidence is None or >= 95 → nothing to bypass → not blocked (no-op,
      caller should skip the merge).
    - Otherwise → not blocked; ship-emoji may proceed.
    """
    # REQUEST CHANGES verdict is always blocking.
    if "REQUEST CHANGES" in verdict_header.upper():
        return MergeBlockReason(True, "Claude review verdict is REQUEST CHANGES — resolve blocking issues first.")

    # Unreplied inline threads are caught by the claude-review CI step;
    # we don't re-detect them here — they show up as a failing check.

    # Parse checks JSON.
    try:
        checks: list[dict] = json.loads(checks_json) if checks_json.strip() else []
    except json.JSONDecodeError:
        return MergeBlockReason(True, "Could not parse pr checks JSON.")

    # Names of the Claude-review check family (the ones ship-emoji may bypass).
    _CLAUDE_CHECK_NAMES = {
        "Claude review",
        "Claude PR Review",
        "Evidence confidence gate (fail if < 95%)",
        "Gate on Claude verdict (fail if REQUEST CHANGES)",
        "Check all inline review threads are replied to",
    }

    _TERMINAL_FAILURE = {"failure", "timed_out", "cancelled", "action_required"}
    _SUCCESS_OR_NEUTRAL = {"success", "neutral", "skipped", "", None}

    real_failures: list[str] = []
    for check in checks:
        name = check.get("name", "")
        # Normalise: GitHub returns either `state` or `conclusion` depending on
        # whether the check is in-progress or complete.
        state = (check.get("conclusion") or check.get("state") or "").lower()
        if name in _CLAUDE_CHECK_NAMES:
            continue  # this is exactly what ship-emoji is allowed to bypass
        if state in _TERMINAL_FAILURE:
            real_failures.append(f"{name} ({state})")

    if real_failures:
        return MergeBlockReason(
            True,
            "The following non-Claude checks are still failing:\n"
            + "\n".join(f"  • {f}" for f in real_failures),
        )

    # Confidence gate: if confidence is already >= 95, auto-merge would have
    # handled it.  Ship-emoji only fires when confidence < 95 (or not found
    # but claude review is red).  If confidence is None and no checks failed,
    # the reviewer may not have run — treat as not-applicable (caller logs this).
    if confidence is not None and confidence >= 95:
        return MergeBlockReason(
            True,
            f"Evidence confidence is already {confidence}% (>= 95%) — "
            "auto-merge should handle this; ship-emoji is not needed.",
        )

    return MergeBlockReason(False, "")


# ---------------------------------------------------------------------------
# CLI entry points (called by the workflow via bash)
# ---------------------------------------------------------------------------

def _cmd_is_ship_intent(argv: list[str]) -> int:
    """Exit 0 if the comment body (from --body or stdin) is ship intent."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--body", default=None)
    args = ap.parse_args(argv)
    body = args.body if args.body is not None else sys.stdin.read()
    result = is_ship_intent(body)
    print("ship_intent=true" if result else "ship_intent=false")
    return 0 if result else 1


def _cmd_is_authorized(argv: list[str]) -> int:
    """Exit 0 if --login / --assoc are authorized."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", required=True)
    ap.add_argument("--assoc", required=True)
    args = ap.parse_args(argv)
    result = is_authorized(args.login, args.assoc)
    print("authorized=true" if result else "authorized=false")
    return 0 if result else 1


def _cmd_check_blocking(argv: list[str]) -> int:
    """Exit 0 (not blocked) or 1 (blocked) based on pr-checks + claude verdict."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks-json", default="[]")
    ap.add_argument("--verdict-header", default="")
    ap.add_argument("--confidence", type=int, default=None)
    args = ap.parse_args(argv)
    result = only_evidence_confidence_blocking(
        args.checks_json, args.verdict_header, args.confidence
    )
    if result.blocked:
        print(f"blocked=true\nreason={result.reason}")
        return 1
    print("blocked=false")
    return 0


_COMMANDS = {
    "is-ship-intent": _cmd_is_ship_intent,
    "is-authorized": _cmd_is_authorized,
    "check-blocking": _cmd_check_blocking,
}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in _COMMANDS:
        print(f"Usage: ship_merge.py <{'|'.join(_COMMANDS)}> [options]", file=sys.stderr)
        return 2
    return _COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
