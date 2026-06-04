#!/usr/bin/env python3
"""Start a fresh cost-tracked Cursor session for a PR or new requirement.

Also updates Playground/REQUIREMENTS.md — marks the linked requirement as
🔄 In Progress when --requirement-id is supplied.

The single thing to run before opening a new Cursor chat for a requirement:
  1. Creates/updates the cost ledger meta for the PR.
  2. Writes a brief Markdown scaffold (metrics/pr_cost/PR-<n>-brief.md) with
     requirement, branch, model-routing reminder, context discipline, and a
     link to the prior PR's post-merge analysis.
  3. Writes metrics/pr_cost/PR-<n>-launch.html — open in a browser and click the
     button (chat markdown links do NOT invoke cursor://; full deeplinks also exceed
     macOS URL length limits if the brief is embedded).
  4. Prints a short cursor:// deeplink + the brief for manual copy-paste.
  5. Optional: --open launches the HTML launcher in your default browser.

Why one-chat-per-PR: each Cursor turn re-reads the entire conversation history
as cache-read tokens ($0.50/M on Opus). A fresh chat resets this counter;
reusing a merged PR's thread drags its full history into every turn.

Usage:
    python3 scripts/start_pr_session.py --pr 15
    python3 scripts/start_pr_session.py --requirement "Add zero-shift guard"
"""

from __future__ import annotations

import argparse
import datetime
import html
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pr_cost_ledger as L

_REQUIREMENTS_MD = Path(__file__).parent.parent / "Playground" / "REQUIREMENTS.md"

# Status emoji used in the requirements table
_STATUS_PENDING     = "🔲 Pending"
_STATUS_IN_PROGRESS = "🔄 In Progress"
_STATUS_DONE        = "✅ Done"
_STATUS_P0          = "🔴 P0"


def _req_status_line_pattern(req_id: int | str) -> re.Pattern:
    """Match a table row for the given requirement ID."""
    return re.compile(
        r"^(\| *(?:✅ Done|🔄 In Progress|🔲 Pending|🔴 P0) *\| *"
        + re.escape(str(req_id))
        + r" *\|.*)",
        re.MULTILINE,
    )


def update_requirement_status(
    req_id: int | str,
    new_status: str,
    pr: int | None = None,
) -> bool:
    """Update a requirement row's status in REQUIREMENTS.md.

    Returns True if a row was found and updated, False if not found.
    """
    if not _REQUIREMENTS_MD.exists():
        return False
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8")
    pattern = _req_status_line_pattern(req_id)
    match = pattern.search(text)
    if not match:
        return False
    old_row = match.group(1)
    # Replace just the status cell (first pipe-delimited column after leading |)
    new_row = re.sub(
        r"^(\| *)(?:✅ Done|🔄 In Progress|🔲 Pending|🔴 P0)( *\|)",
        rf"\g<1>{new_status}\g<2>",
        old_row,
    )
    # If a PR number is provided, append it to the PR(s) column if not already there
    if pr is not None:
        # PR(s) is the 4th column (index 3 in split by |)
        cols = new_row.split("|")
        if len(cols) > 4:
            pr_cell = cols[4].strip()
            pr_ref = f"#{pr}"
            if pr_ref not in pr_cell:
                if pr_cell in ("", "—"):
                    cols[4] = f" {pr_ref} "
                else:
                    cols[4] = f" {pr_cell}, {pr_ref} "
                new_row = "|".join(cols)
    text = text[: match.start()] + new_row + text[match.end():]
    _REQUIREMENTS_MD.write_text(text, encoding="utf-8")
    return True

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
        f"PR #{prior} '{(rec.get('title') or '?')[:60]}': "
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

    # Stamp session start — cost sync attributes usage only after this moment.
    session_started = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    L.set_meta(
        pr,
        title=title or rec.get("title"),
        branch=branch or rec.get("branch"),
        requirement=requirement or rec.get("requirement"),
        session_started_at=session_started,
    )
    rec = L.load_record(pr)

    req = requirement or rec.get("requirement") or rec.get("title") or f"(PR #{pr})"
    br = rec.get("branch") or branch or "(unknown branch)"
    prior = _prior_analysis(pr)

    brief = f"""# PR #{pr} session brief

## Requirement
{req}

## Branch
`{br}`

## Session started (cost attribution anchor)
`{session_started}`

Open a **new** Cursor chat for this PR, then implement. Build cost is attributed to
chat space(s) with AI edits after this timestamp (see `pr_cost_ledger.py sync`).

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


# macOS / browser URL handlers often fail silently above ~2 KB.
_MAX_DEEPLINK_CHARS = 1800

# Requirement one-liner in the deeplink — keeps the chat title meaningful without
# embedding the full brief (that lives in PR-<n>-brief.md).
_SEED_REQUIREMENT_MAX = 140


def _truncate_requirement(text: str, *, max_len: int = _SEED_REQUIREMENT_MAX) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 1].rstrip() + "…"


def seed_prompt(pr: int, *, brief_rel: str, requirement: str | None = None) -> str:
    """Short seed text — full brief lives in the markdown file, not the URL."""
    header = f"PR #{pr}"
    if requirement:
        header = f"{header} — {_truncate_requirement(requirement)}"
    return (
        f"{header}\n\n"
        f"Read `{brief_rel}` first (requirement, branch, model-routing, cost gate). "
        f"Acknowledge those rules from the brief, then implement the requirement — "
        f"do not ask what to build; it is already specified in the brief."
    )


def make_deeplink(text: str) -> str:
    """cursor:// deeplink that opens a new IDE chat pre-seeded with text."""
    encoded = urllib.parse.quote(text, safe="")
    link = f"cursor://anysphere.cursor-deeplink/prompt?text={encoded}&mode=agent"
    if len(link) > _MAX_DEEPLINK_CHARS:
        raise ValueError(
            f"deeplink too long ({len(link)} chars > {_MAX_DEEPLINK_CHARS}); "
            "use seed_prompt() + brief file instead of embedding the full brief"
        )
    return link


def write_launch_html(
    pr: int, deeplink: str, *, brief_path: Path, seed_text: str,
) -> Path:
    """Browser launcher — the reliable click target (chat UI won't open cursor://)."""
    out = L.LEDGER_DIR / f"PR-{pr}-launch.html"
    brief_uri = brief_path.resolve().as_uri()
    safe_link = html.escape(deeplink, quote=True)
    safe_brief = html.escape(brief_uri, quote=True)
    safe_seed = html.escape(seed_text)
    out.write_text(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>PR #{pr} — open new Cursor chat</title>
<style>
  body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 42rem; margin: 3rem auto; padding: 0 1rem; }}
  a.btn {{ display: inline-block; background: #0969da; color: #fff; padding: 12px 20px;
    border-radius: 8px; text-decoration: none; font-weight: 600; }}
  a.btn:hover {{ background: #0550ae; }}
  .muted {{ color: #57606a; font-size: 14px; margin-top: 1.5rem; }}
  code {{ background: #f6f8fa; padding: 2px 6px; border-radius: 4px; }}
  pre.seed {{ background: #f6f8fa; padding: 12px; border-radius: 8px; white-space: pre-wrap;
    font-size: 14px; line-height: 1.45; border: 1px solid #d0d7de; }}
</style></head><body>
  <h1>PR #{pr} — new Cursor chat</h1>
  <p>Click below to open a <strong>new Agent chat</strong> in Cursor with the PR brief seeded.
     Cursor may show a confirmation dialog first — approve it.</p>
  <p><a class="btn" href="{safe_link}">Open new chat for PR #{pr}</a></p>
  <h2>First message (if the button fails)</h2>
  <p class="muted">Copy this into Cursor → <strong>New Chat</strong> (Agent mode). Do not use a
     placeholder like &ldquo;Test PR {pr}&rdquo; — it hides the requirement.</p>
  <pre class="seed">{safe_seed}</pre>
  <p class="muted">Full brief:
    <a href="{safe_brief}"><code>{html.escape(brief_path.name)}</code></a></p>
  <p class="muted">Fallback terminal: <code>open '{html.escape(deeplink)}'</code></p>
</body></html>
""",
        encoding="utf-8",
    )
    return out


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--pr", type=int, required=True,
                     help="PR number (creates the ledger record if it doesn't exist yet)")
    cli.add_argument("--requirement", help="Requirement text (overrides what's in the ledger)")
    cli.add_argument("--requirement-id", type=str, dest="requirement_id",
                     help="ID from Playground/REQUIREMENTS.md to mark as 🔄 In Progress")
    cli.add_argument("--title", help="PR title (optional; fetched from gh if omitted)")
    cli.add_argument("--branch", help="Branch name (optional; fetched from gh if omitted)")
    cli.add_argument("--open", action="store_true",
                     help="Open PR-<n>-launch.html in the default browser (macOS: use the button there)")
    args = cli.parse_args(argv)

    brief = generate_brief(args.pr, requirement=args.requirement,
                           title=args.title, branch=args.branch)
    brief_path = L.LEDGER_DIR / f"PR-{args.pr}-brief.md"
    brief_rel = f"metrics/pr_cost/PR-{args.pr}-brief.md"
    rec = L.load_record(args.pr)
    req = args.requirement or rec.get("requirement") or rec.get("title") or ""
    seed = seed_prompt(args.pr, brief_rel=brief_rel, requirement=req or None)
    print(f"\nBrief written → {brief_path}\n")

    if args.requirement_id:
        updated = update_requirement_status(
            args.requirement_id, _STATUS_IN_PROGRESS, pr=args.pr
        )
        if updated:
            print(f"Requirements tracker → #{args.requirement_id} marked 🔄 In Progress (PR #{args.pr})")
        else:
            print(f"⚠️  Requirement #{args.requirement_id} not found in REQUIREMENTS.md — update manually")

    deeplink = make_deeplink(seed)
    launch_path = write_launch_html(args.pr, deeplink, brief_path=brief_path, seed_text=seed)
    launch_uri = launch_path.resolve().as_uri()
    print(f"Launcher → {launch_path}")
    print(f"  Open in browser: {launch_uri}\n")
    if args.open:
        subprocess.run(["open", str(launch_path)], check=False)
    print("─── FIRST MESSAGE — paste into New Chat if you skip the launcher button ───")
    print("(Do NOT use a placeholder like 'Test PR N'; use this text verbatim.)\n")
    print(seed)
    print("\n─── cursor:// deeplink (browser address bar if needed) ───")
    print(deeplink)
    print(f"  ({len(deeplink)} chars)\n")
    print("─── full brief (fallback) ───\n")
    print(brief)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
