#!/usr/bin/env python3
"""
Cursor beforeSubmitPrompt hook — new-requirement intake gate.

Reads the Cursor hook JSON payload from stdin, then:
  1. Appends the user prompt to the corpus (always, fail-open).
  2. Checks whether the prompt signals a NEW requirement.
  3. If yes (and no //inline override), hard-blocks the turn and instructs
     the operator to run new_requirement.py instead of implementing inline.

Output (stdout): {"continue": bool, "user_message": str | None}

Design constraints (verified from Cursor hook docs):
  - beforeSubmitPrompt supports ONLY {"continue", "user_message"} — no ask,
    no agent_message, no context injection.
  - Must fail-open (continue=true) on any non-detection error so a crashed
    hook never silently swallows the user's prompt.
  - //inline prefix is the operator's escape hatch for false positives.
"""
from __future__ import annotations

import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Intake detection — deterministic phrase heuristic
# ---------------------------------------------------------------------------

# Lower-cased phrase fragments. A prompt matches if it contains ANY of these
# as a whole-word / phrase match (not substring of another word).
_INTAKE_PHRASES: list[str] = [
    "new requirement",
    "i want to work on",
    "i'd like to work on",
    "let's also",
    "lets also",
    "can you also",
    "can we also",
    "add a requirement",
    "add another requirement",
    "separate requirement",
    "different requirement",
    "new feature",
    "separate pr",
    "another pr",
    "spin up a worktree",
]

_OVERRIDE_PREFIX = "//inline"

_BLOCK_MESSAGE = """\
New-requirement intake gate fired.

This prompt looks like a new, separate requirement. Implementing it inline \
would mix concerns in this branch.

Run this command to spin up an isolated worktree, branch, tracking issue, \
and brief:

    python3 scripts/new_requirement.py --requirement "{prompt_excerpt}"

Then continue in the new Cursor window that opens.

If this is actually a continuation of the current work (false positive), \
re-send your message prefixed with:

    //inline <your original message>

The hook will pass it through unchanged (the prefix stays visible to the agent).\
"""


def _is_new_requirement(prompt: str) -> bool:
    """Return True if the prompt phrase-matches a new-requirement signal."""
    lower = prompt.lower().strip()
    for phrase in _INTAKE_PHRASES:
        # Use word-boundary-aware match: phrase must not be mid-word.
        pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
        if re.search(pattern, lower):
            return True
    return False


def _has_inline_override(prompt: str) -> bool:
    """Return True if the prompt starts with the //inline escape prefix."""
    return prompt.strip().startswith(_OVERRIDE_PREFIX)


# ---------------------------------------------------------------------------
# Corpus append — side-effect, always fail-open
# ---------------------------------------------------------------------------

def _append_corpus(prompt: str, workspace_root: str | None) -> None:
    """Best-effort append to the user-model corpus. Never raises."""
    try:
        if workspace_root:
            sys.path.insert(0, workspace_root)
        from skills.user_model.store import append_to_corpus  # type: ignore[import]
        append_to_corpus(prompt, agent=None, source="cursor-hook")
    except Exception:
        pass  # corpus append is best-effort; never block the turn


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Unparseable input — fail open
        print(json.dumps({"continue": True}))
        return 0

    prompt: str = payload.get("prompt") or ""

    # Workspace root: prefer env var, fall back to workspace_roots in payload
    workspace_root: str | None = (
        os.environ.get("CURSOR_PROJECT_DIR")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or (payload.get("workspace_roots") or [None])[0]
    )

    # 1. Always append to corpus (fail-open)
    _append_corpus(prompt, workspace_root)

    # 2. //inline override — let the turn through
    if _has_inline_override(prompt):
        print(json.dumps({"continue": True}))
        return 0

    # 3. No workspace root → not a Jarvis workspace, pass through
    if not workspace_root:
        print(json.dumps({"continue": True}))
        return 0

    # 4. Hook script not present in this workspace → pass through
    hook_script = os.path.join(workspace_root, ".cursor", "hooks", "enforce.sh")
    if not os.path.exists(hook_script):
        print(json.dumps({"continue": True}))
        return 0

    # 5. Intake detection
    if not _is_new_requirement(prompt):
        print(json.dumps({"continue": True}))
        return 0

    # 6. Block and instruct
    excerpt = prompt[:80].replace('"', "'")
    if len(prompt) > 80:
        excerpt += "…"
    msg = _BLOCK_MESSAGE.format(prompt_excerpt=excerpt)
    print(json.dumps({"continue": False, "user_message": msg}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
