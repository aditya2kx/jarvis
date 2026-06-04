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

# Default Agent model for new-requirement handoffs (matches Cursor usage API slug).
# Sonnet 4.6 medium thinking — CONTRIBUTING § Cost-efficiency playbook default.
DEFAULT_HANDOFF_MODEL = "claude-4.6-sonnet-medium-thinking"

# Model routing guidance (keep in sync with CONTRIBUTING.md § Cost-efficiency playbook).
_ROUTING_REMINDER = """Model routing (CONTRIBUTING § Cost-efficiency playbook):
  • Sonnet 4.6 medium thinking — DEFAULT for new chats (handoff pre-selects this model)
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


def _gh_current_branch() -> str:
    """Current git branch (the provisional session key before a PR exists)."""
    try:
        b = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        return "" if b in ("", "HEAD") else b
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _prior_analysis(pr: int | str) -> str:
    """One-liner from the most recent prior PR's cost analysis.

    For a provisional (non-numeric) session the PR number isn't known yet, so
    "prior" is simply the highest-numbered recorded PR.
    """
    all_prs = L._all_prs()
    prs = [p for p in all_prs if p < pr] if isinstance(pr, int) else all_prs
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


def _is_provisional(key: int | str) -> bool:
    return not (isinstance(key, int) or (isinstance(key, str) and str(key).isdigit()))


def _brief_path(key: int | str) -> Path:
    if _is_provisional(key):
        return L.LEDGER_DIR / f"session-{L._slug(str(key))}-brief.md"
    return L.LEDGER_DIR / f"PR-{int(key)}-brief.md"


def _launch_path(key: int | str) -> Path:
    if _is_provisional(key):
        return L.LEDGER_DIR / f"session-{L._slug(str(key))}-launch.html"
    return L.LEDGER_DIR / f"PR-{int(key)}-launch.html"


def _label(key: int | str) -> str:
    """Human label for a session: a real PR number, or 'this PR' before one exists."""
    return f"PR #{int(key)}" if not _is_provisional(key) else "this PR"


def generate_brief(
    key: int | str,
    *,
    requirement: str | None = None,
    title: str | None = None,
    branch: str | None = None,
) -> str:
    """Write and return the brief Markdown for this session.

    ``key`` is either a real PR number (legacy) or — preferred for a brand-new
    requirement — a provisional **branch name**, because the PR number does not
    exist until ``gh pr create`` runs (and parallel chat spaces compete for it).
    """
    provisional = _is_provisional(key)
    rec = L.load_record(key)

    # Pull from GitHub if not provided (only meaningful once a PR exists).
    if not provisional and not title and not rec.get("title"):
        title = _gh("pr", "view", str(int(key)), "--json", "title", "--jq", ".title") or None
    if not provisional and not branch and not rec.get("branch"):
        branch = _gh("pr", "view", str(int(key)), "--json", "headRefName", "--jq", ".headRefName") or None
    if provisional and not branch:
        branch = str(key)

    # Stamp session start — cost sync attributes usage only after this moment.
    session_started = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    L.set_meta(
        key,
        title=title or rec.get("title"),
        branch=branch or rec.get("branch"),
        requirement=requirement or rec.get("requirement"),
        session_started_at=session_started,
    )
    rec = L.load_record(key)

    label = _label(key)
    req = requirement or rec.get("requirement") or rec.get("title") or f"({label})"
    br = rec.get("branch") or branch or "(unknown branch)"
    prior = _prior_analysis(key)

    heading = f"# {label.capitalize()} session brief" if not provisional \
        else f"# Session brief — `{br}` (PR # assigned at open)"

    if provisional:
        pr_number_block = f"""## PR number
**Not assigned yet — do NOT guess it.** The PR number is allocated by GitHub when
you run `gh pr create`; multiple chat spaces may be opening PRs at the same time.
This session is tracked provisionally by its **branch** (`{br}`).

**Right after you open the PR**, bind the cost ledger to the real number:
```bash
python3 scripts/pr_cost_ledger.py bind-pr --branch {br}   # auto-resolves the new PR #
python3 scripts/pr_cost_ledger.py sync --pr <the-new-number>
git add metrics/pr_cost/ && git commit -m "chore(cost): sync PR #<n> ledger"
```"""
        gate = ("## Cost gate reminder\n"
                "Before your final push, AFTER the PR exists: run `bind-pr` (above) then\n"
                "`pr_cost_ledger.py sync --pr <n>` and commit `metrics/pr_cost/`.")
    else:
        n = int(key)
        pr_number_block = f"""## PR number
`#{n}`"""
        gate = ("## Cost gate reminder\n"
                f"Before your final push: `python3 scripts/pr_cost_ledger.py sync --pr {n}`\n"
                f"Then: `git add metrics/pr_cost/ && git commit -m \"chore(cost): sync PR #{n} ledger\"`")

    brief = f"""{heading}

## Requirement
{req}

## Branch
`{br}`

{pr_number_block}

## Session started (cost attribution anchor)
`{session_started}`

## Default model
**Sonnet 4.6 medium thinking** (`{DEFAULT_HANDOFF_MODEL}`) — the handoff deeplink
pre-selects this. Stay on Sonnet for feature work; escalate to Opus only when stuck.

Open a **new** Cursor chat for this requirement, then implement. Build cost is
attributed to chat space(s) with AI edits after this timestamp (see
`pr_cost_ledger.py sync`).

## Prior PR cost reference
{prior}

## {_ROUTING_REMINDER}
{gate}
"""
    brief_path = _brief_path(key)
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


def seed_prompt(key: int | str, *, brief_rel: str, requirement: str | None = None) -> str:
    """Short seed text — full brief lives in the markdown file, not the URL."""
    if _is_provisional(key):
        header = _truncate_requirement(requirement) if requirement else f"New requirement (`{key}`)"
    else:
        header = f"PR #{int(key)}"
        if requirement:
            header = f"{header} — {_truncate_requirement(requirement)}"
    return (
        f"{header}\n\n"
        f"Read `{brief_rel}` first (requirement, branch, model-routing, cost gate). "
        f"Acknowledge those rules from the brief, then implement the requirement — "
        f"do not ask what to build; it is already specified in the brief. "
        f"Do NOT assume a PR number; it is assigned only when you run `gh pr create`. "
        f"Use **Sonnet 4.6 medium thinking** for this session (handoff should pre-select it)."
    )


def make_deeplink(
    text: str,
    *,
    mode: str = "agent",
    model: str | None = DEFAULT_HANDOFF_MODEL,
) -> str:
    """cursor:// deeplink that opens a new IDE chat pre-seeded with text.

    ``mode=agent`` and ``model=…`` are Cursor-specific extensions beyond the
    documented ``text`` param; the deeplink handler in Cursor 3.6+ honors them
    for Agent chat handoffs (Sonnet 4.6 medium by default).
    """
    encoded = urllib.parse.quote(text, safe="")
    link = f"cursor://anysphere.cursor-deeplink/prompt?text={encoded}&mode={mode}"
    if model:
        link += f"&model={urllib.parse.quote(model, safe='')}"
    if len(link) > _MAX_DEEPLINK_CHARS:
        raise ValueError(
            f"deeplink too long ({len(link)} chars > {_MAX_DEEPLINK_CHARS}); "
            "use seed_prompt() + brief file instead of embedding the full brief"
        )
    return link


_CURSOR_CANDIDATES = (
    "cursor",
    "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
)


def find_cursor_cli() -> Path | None:
    """Locate the Cursor CLI (``cursor`` on PATH or macOS app bundle)."""
    for candidate in _CURSOR_CANDIDATES:
        p = Path(candidate)
        if p.is_file():
            return p
        try:
            out = subprocess.check_output(
                ["which", candidate], text=True, stderr=subprocess.DEVNULL
            ).strip()
            if out:
                return Path(out)
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None


def open_cursor_handoff(
    *,
    folder: Path,
    deeplink: str,
    launch_html: Path,
    delay_sec: float = 3.5,
) -> None:
    """Open ``folder`` in a new Cursor window, then seed Agent chat + launcher backup.

    Order matters: open the worktree folder **first**, then fire the deeplink so
    the new chat attaches to the correct workspace (not whichever window was focused).
    """
    import time

    cursor = find_cursor_cli()
    folder = folder.resolve()
    launch_html = launch_html.resolve()

    if cursor is None:
        print(
            "⚠️  Cursor CLI not found — open the folder manually, then use the launcher:\n"
            f"    {folder}\n"
            f"    {launch_html}"
        )
        subprocess.run(["open", str(launch_html)], check=False)
        return

    print(f"Opening Cursor → {folder}")
    subprocess.Popen([str(cursor), "-n", str(folder)])
    time.sleep(delay_sec)
    print("Seeding Agent chat (approve Cursor's deeplink dialog if prompted)…")
    subprocess.run(["open", deeplink], check=False)
    subprocess.run(["open", str(launch_html)], check=False)


def write_launch_html(
    key: int | str, deeplink: str, *, brief_path: Path, seed_text: str,
) -> Path:
    """Browser launcher — the reliable click target (chat UI won't open cursor://)."""
    pr = _label(key)  # "PR #N" or "this PR" (provisional)
    out = _launch_path(key)
    brief_uri = brief_path.resolve().as_uri()
    safe_link = html.escape(deeplink, quote=True)
    safe_brief = html.escape(brief_uri, quote=True)
    safe_seed = html.escape(seed_text)
    out.write_text(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{pr} — open new Cursor chat</title>
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
  <h1>{pr} — new Cursor chat</h1>
  <p>Click below to open a <strong>new Agent chat</strong> in Cursor with the brief seeded.
     Cursor may show a confirmation dialog first — approve it.</p>
  <p><a class="btn" href="{safe_link}">Open new chat for {pr}</a></p>
  <h2>First message (if the button fails)</h2>
  <p class="muted">Copy this into Cursor → <strong>New Chat</strong> (Agent mode). Do not use a
     placeholder like &ldquo;Test {pr}&rdquo; — it hides the requirement.</p>
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
    cli.add_argument("--pr", type=int,
                     help="PR number — ONLY if the PR already exists. For a NEW requirement, "
                          "omit this (the number isn't assigned until `gh pr create`) and use --branch.")
    cli.add_argument("--requirement", help="Requirement text (overrides what's in the ledger)")
    cli.add_argument("--requirement-id", type=str, dest="requirement_id",
                     help="ID from Playground/REQUIREMENTS.md to mark as 🔄 In Progress")
    cli.add_argument("--title", help="PR title (optional; fetched from gh if omitted)")
    cli.add_argument("--branch", help="Branch name. Required when --pr is omitted: it is the "
                                      "provisional key that `bind-pr` later maps to the real PR number.")
    cli.add_argument("--open", action="store_true",
                     help="Open the launcher HTML in the default browser (macOS: use the button there)")
    cli.add_argument("--open-cursor", action="store_true",
                     help="Open this folder in a new Cursor window + seed Agent chat (macOS)")
    cli.add_argument("--cursor-delay", type=float, default=3.5,
                     help="Seconds to wait before deeplink when using --open-cursor")
    cli.add_argument("--model", default=DEFAULT_HANDOFF_MODEL,
                     help=f"Agent model slug for the handoff deeplink (default: {DEFAULT_HANDOFF_MODEL})")
    args = cli.parse_args(argv)

    # The PR number is assigned by GitHub at `gh pr create` and parallel chats
    # compete for it — so a brand-new requirement is keyed by its BRANCH until the
    # PR exists, then `pr_cost_ledger.py bind-pr` promotes it to PR-<n>.json.
    if args.pr is not None:
        key: int | str = args.pr
    else:
        branch = args.branch or _gh_current_branch()
        if not branch:
            cli.error("provide --branch (or run inside a git branch) when --pr is omitted — "
                      "the PR number does not exist until you open the PR")
        key = branch

    brief = generate_brief(key, requirement=args.requirement,
                           title=args.title, branch=args.branch)
    brief_path = _brief_path(key)
    brief_rel = f"metrics/pr_cost/{brief_path.name}"
    rec = L.load_record(key)
    req = args.requirement or rec.get("requirement") or rec.get("title") or ""
    seed = seed_prompt(key, brief_rel=brief_rel, requirement=req or None)
    print(f"\nBrief written → {brief_path}\n")

    if args.requirement_id:
        updated = update_requirement_status(
            args.requirement_id, _STATUS_IN_PROGRESS, pr=args.pr
        )
        if updated:
            print(f"Requirements tracker → #{args.requirement_id} marked 🔄 In Progress (PR #{args.pr})")
        else:
            print(f"⚠️  Requirement #{args.requirement_id} not found in REQUIREMENTS.md — update manually")

    deeplink = make_deeplink(seed, model=args.model)
    launch_path = write_launch_html(key, deeplink, brief_path=brief_path, seed_text=seed)
    launch_uri = launch_path.resolve().as_uri()
    print(f"Launcher → {launch_path}")
    print(f"  Open in browser: {launch_uri}\n")
    if args.open:
        subprocess.run(["open", str(launch_path)], check=False)
    if args.open_cursor:
        open_cursor_handoff(
            folder=Path.cwd(),
            deeplink=deeplink,
            launch_html=launch_path,
            delay_sec=args.cursor_delay,
        )
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
