#!/usr/bin/env python3
"""Single source of truth for dev-flow Cursor chat model slugs.

Every script/doc that pre-selects or recommends a model for a *dev-flow*
Cursor chat (PR handoffs, jam sessions, cost-ledger recommendations, the
routing table in docs/contributing/cost.md) imports the constants below
instead of hardcoding a slug string. To swap the default model repo-wide,
change one constant here and regenerate the doc block:

    python3 scripts/dev_models.py emit-routing-md

Out of scope (deliberately not covered here): the *historical* model
strings recorded in metrics/pr_cost/*.json and the Claude-review Action's
own Anthropic API model id (see pr_cost_ledger.py `_model_tier` / the
`claude-sonnet-4-6` parse fallback, post_claude_review_cost.py). Those are
cost-accounting identifiers for sessions that already ran — renaming them
would corrupt historical cost attribution, not just relabel a default.
"""

from __future__ import annotations

import sys

# ── Canonical Cursor chat slugs (dev-flow routing defaults) ────────────────
DEFAULT_IMPL_MODEL = "claude-sonnet-5-thinking-medium"
DEFAULT_JAM_MODEL = "claude-opus-4-8-thinking-high"
ESCALATION_MODEL = "claude-opus-4-8-thinking-medium"
MECHANICAL_MODEL = "composer-2.5"

# Friendly display name for each slug (used in briefs, deeplink docstrings, tables).
FRIENDLY: dict[str, str] = {
    DEFAULT_IMPL_MODEL: "Sonnet 5 medium thinking",
    DEFAULT_JAM_MODEL: "Opus 4.8 thinking high",
    ESCALATION_MODEL: "Opus 4.8 thinking medium",
    MECHANICAL_MODEL: "Composer 2.5",
}

# (task description, slug) rows — renders both the cost.md table and the
# in-chat routing reminder so the two never drift apart.
ROUTING_TABLE: list[tuple[str, str]] = [
    ("Feature work, refactors, doc edits", DEFAULT_IMPL_MODEL),
    ("Complex logic, architecture decisions", DEFAULT_IMPL_MODEL),
    ("Hard bugs, plan reviews, code review", ESCALATION_MODEL),
    ("Doc-only changes, table of contents", MECHANICAL_MODEL),
]

_ROUTING_MD_BEGIN = "<!-- dev-models:begin -->"
_ROUTING_MD_END = "<!-- dev-models:end -->"


def render_routing_md() -> str:
    """Markdown table for docs/contributing/cost.md, wrapped in sync markers."""
    rows = "\n".join(f"| {task} | {FRIENDLY[slug]} |" for task, slug in ROUTING_TABLE)
    return (
        f"{_ROUTING_MD_BEGIN}\n"
        f"| Task | Model |\n"
        f"|---|---|\n"
        f"{rows}\n"
        f"{_ROUTING_MD_END}"
    )


def render_routing_reminder() -> str:
    """Plain-text routing reminder embedded in start_pr_session.py briefs."""
    lines = [f"  • {FRIENDLY[slug]:<24} — {task}" for task, slug in ROUTING_TABLE]
    return "Model routing (docs/contributing/cost.md):\n" + "\n".join(lines)


def _cmd_emit_routing_md(argv: list[str]) -> int:
    print(render_routing_md())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "emit-routing-md":
        print(__doc__)
        print("\nUsage: python3 scripts/dev_models.py emit-routing-md")
        return 0 if not argv else 2
    return _cmd_emit_routing_md(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
