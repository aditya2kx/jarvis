#!/usr/bin/env python3
"""Start a fresh cost-tracked Cursor session for a PR or new requirement.

The single thing to run before opening a new Cursor chat for a requirement:
  1. Creates/updates the cost ledger meta for the PR.
  2. Writes a brief Markdown scaffold (metrics/pr_cost/PR-<n>-brief.md) with
     requirement, branch, model-routing reminder, context discipline, and a
     link to the prior PR's post-merge analysis.
  3. Prints a cursor:// deeplink — click it to open a new IDE chat pre-seeded
     with the brief (one user click, no auto-submit).
  4. Prints the brief text for manual copy-paste if you prefer.

Why one-chat-per-PR: each Cursor turn re-reads the entire conversation history
as cache-read tokens ($0.50/M on Opus). A fresh chat resets this counter;
reusing a merged PR's thread drags its full history into every turn.

Usage:
    python3 scripts/start_pr_session.py --pr 15
    python3 scripts/start_pr_session.py --requirement "Add zero-shift guard"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pr_cost_ledger as L

# Model routing guidance (keep in sync with CONTRIBUTING.md § Cost-efficiency playbook).
_ROUTING_REMINDER = """Model routing (CONTRIBUTING § Cost-efficiency playbook):
  • Sonnet 4.6     — DEFAULT for feature code, refactors, most edits
  • Opus 4.8 med   — Hard multi-file reasoning, subtle bugs, architecture decisions
  • Opus 4.8 high  — Only when genuinely stuck; adds ~30% output tokens vs medium
  • Composer 2.5   — Mechanical: renames, test scaffolding, doc edits, log reading
  Rates (verified 2026-06-03): Opus cache-read $0.50/M · Sonnet $0.30/M · Composer $0.20/M

Context discipline:
  • One chat per PR — do NOT continue the previous PR's thread (cache-read bloat)
  • /clear or new chat between unrelated sub-tasks within the same PR
  • Prefer Plan mode + targeted file reads over open-ended exploration
  • Run `pr_cost_ledger.py sync --pr <n>` before your final push to commit build+review cost
"""


def _gh(*args: str) -> str:
    try:
        return subprocess.check_output(["gh", *args], text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _prior_analysis(pr: int) -> str:
    """One-liner from the most recent prior merged PR's analysis."""
    prs = [p for p in L._all_prs() if p < pr]
    if not prs:
        return "(no prior PR ledger found)"
    prior = max(prs)
    rec = L.load_record(prior)
    t = rec.get("totals", {})
    b = rec.get("build", {})
    r = rec.get("review", {})
    return (
        f"PR #{prior} '{rec.get('title', '?')[:60]}': "
        f"${t.get('cost_usd', 0):.2f} total "
        f"(build ${b.get('cost_usd_total', 0):.2f} / review ${r.get('cost_usd_total', 0):.2f}, "
        f"{r.get('run_count', 0)} review runs)"
    )


def generate_brief(
    pr: int,
    *,
    requirement: str | None = None,
    title: str | None = None,
    branch: str | None = None,
) -> str:
    """Write and return the brief Markdown for this PR session."""
    rec = L.load_record(pr)

    # Pull from GitHub if not provided
    if not title and not rec.get("title"):
        title = _gh("pr", "view", str(pr), "--json", "title", "--jq", ".title") or None
    if not branch and not rec.get("branch"):
        branch = _gh("pr", "view", str(pr), "--json", "headRefName", "--jq", ".headRefName") or None

    # Update meta
    if title or branch:
        L.set_meta(pr, title=title or rec.get("title"), branch=branch or rec.get("branch"))
        rec = L.load_record(pr)

    req = requirement or rec.get("requirement") or rec.get("title") or f"(PR #{pr})"
    br = rec.get("branch") or branch or "(unknown branch)"
    prior = _prior_analysis(pr)

    brief = f"""# PR #{pr} session brief

## Requirement
{req}

## Branch
`{br}`

## Prior PR cost reference
{prior}

## {_ROUTING_REMINDER}
## Cost gate reminder
Before your final push: `python3 scripts/pr_cost_ledger.py sync --pr {pr}`
Then: `git add metrics/pr_cost/ && git commit -m "chore(cost): sync PR #{pr} ledger"`
"""
    brief_path = L.LEDGER_DIR / f"PR-{pr}-brief.md"
    brief_path.write_text(brief, encoding="utf-8")
    return brief


def make_deeplink(text: str) -> str:
    """cursor:// deeplink that opens a new IDE chat pre-seeded with text."""
    encoded = urllib.parse.quote(text, safe="")
    return f"cursor://anysphere.cursor-deeplink/prompt?text={encoded}&mode=agent"


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--pr", type=int, required=True,
                     help="PR number (creates the ledger record if it doesn't exist yet)")
    cli.add_argument("--requirement", help="Requirement text (overrides what's in the ledger)")
    cli.add_argument("--title", help="PR title (optional; fetched from gh if omitted)")
    cli.add_argument("--branch", help="Branch name (optional; fetched from gh if omitted)")
    args = cli.parse_args(argv)

    brief = generate_brief(args.pr, requirement=args.requirement,
                           title=args.title, branch=args.branch)
    brief_path = L.LEDGER_DIR / f"PR-{args.pr}-brief.md"
    print(f"\nBrief written → {brief_path}\n")

    deeplink = make_deeplink(
        f"Starting work on PR #{args.pr}. Brief:\n\n{brief}\n\n"
        "Acknowledge the model routing and context discipline rules, then ask me what to build."
    )
    print("─── cursor:// deeplink (click to open a new seeded chat) ─────────────────")
    print(deeplink)
    print("──────────────────────────────────────────────────────────────────────────\n")
    print("Or paste this brief into a New Chat manually:\n")
    print(brief)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
