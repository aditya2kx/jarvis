#!/usr/bin/env python3
"""Pure helpers for the post-merge lifecycle workflow.

Called by pr-merged-lifecycle.yml to:
  1. Locate the tracking issue for a merged branch.
  2. Parse the "Post-merge verification" block from the PR description §4.

All functions are stdlib-only and unit-testable without network access.
Network calls (gh CLI) are handled in the workflow YAML, not here.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Tracking issue resolution
# ---------------------------------------------------------------------------

def _slug(branch: str) -> str:
    """Match the slugifier in phase_state.py / pr_cost_ledger.py."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", branch)[:60]


def _metrics_dir() -> str:
    """Return the metrics/pr_cost directory relative to the repo root."""
    # When running inside a CI checkout the repo root is the CWD.
    return os.path.join(os.environ.get("GITHUB_WORKSPACE", "."), "metrics", "pr_cost")


def find_tracking_issue_from_cache(branch: str) -> int | None:
    """Read the issue number from the phase-cache JSON for ``branch``.

    Returns None if the cache file does not exist or lacks an issue number.
    """
    slug = _slug(branch)
    cache_path = os.path.join(_metrics_dir(), f"session-{slug}-phase.json")
    if not os.path.exists(cache_path):
        return None
    try:
        data = json.loads(open(cache_path).read())
        issue = data.get("issue")
        return int(issue) if issue else None
    except Exception:
        return None


def find_tracking_issue_from_gh(branch: str) -> int | None:
    """Scan open (and recently-closed) jarvis-work issues for one that
    mentions the branch in its body.

    Uses the ``gh`` CLI — only works in environments with gh auth configured.
    Returns None if gh is unavailable or no match is found.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "list",
             "--label", "jarvis-work",
             "--state", "all",
             "--limit", "100",
             "--json", "number,body"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        issues: list[dict] = json.loads(result.stdout or "[]")
        for iss in issues:
            body = iss.get("body") or ""
            if f"`{branch}`" in body or branch in body:
                return int(iss["number"])
    except Exception:
        pass
    return None


def find_tracking_issue(branch: str) -> int | None:
    """Resolve the tracking issue for ``branch``.

    Strategy: phase-cache first (fast, no network), then gh scan (network).
    """
    n = find_tracking_issue_from_cache(branch)
    if n:
        return n
    return find_tracking_issue_from_gh(branch)


# ---------------------------------------------------------------------------
# Post-merge verification block parser
# ---------------------------------------------------------------------------

# Deny-list: command prefixes / keywords that indicate a side-effecting operation.
# Commands matching any of these are classified as "agent follow-up" (not auto-run).
_SIDE_EFFECTING_PATTERNS: list[re.Pattern] = [
    re.compile(r"\botp\b", re.IGNORECASE),
    re.compile(r"\bscrape\b", re.IGNORECASE),
    re.compile(r"\bdaily.refresh\b", re.IGNORECASE),
    re.compile(r"\bgcloud\s+run\s+jobs\s+execute\b", re.IGNORECASE),
    re.compile(r"\bdeploy\b", re.IGNORECASE),
    re.compile(r"\bgh\s+pr\s+merge\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b", re.IGNORECASE),
    re.compile(r"\bsend\b.*\bslack\b", re.IGNORECASE),
    re.compile(r"\bnotify\b", re.IGNORECASE),
]


class PostMergeCommand(NamedTuple):
    raw: str          # the command string as written in the PR body
    readonly: bool    # True = safe to auto-run in CI; False = agent follow-up


def _is_side_effecting(cmd: str) -> bool:
    return any(p.search(cmd) for p in _SIDE_EFFECTING_PATTERNS)


def parse_post_merge_block(pr_body: str) -> list[PostMergeCommand]:
    """Parse the "Post-merge verification" subsection from PR §4.

    The section is expected under §4 in the format:

        ### Post-merge verification
        ```
        some-command --with args
        another-command
        ```

    or multi-block:

        ### Post-merge verification
        ```bash
        cmd1
        ```
        ```
        cmd2
        ```

    Returns a list of PostMergeCommand objects (may be empty).
    Non-empty, non-comment lines inside fenced blocks are extracted as commands.
    Lines starting with ``#`` are comments and are skipped.

    Implementation uses a line-by-line state machine so that ``# comment``
    lines inside fenced blocks are never mistaken for Markdown headings.
    """
    if not pr_body:
        return []

    section_heading_re = re.compile(
        r"^(#{1,4})\s+Post.merge\s+[Vv]erification\s*$",
        re.IGNORECASE,
    )

    lines = pr_body.splitlines()
    section_start_idx: int | None = None
    section_level: int = 0

    for i, line in enumerate(lines):
        m = section_heading_re.match(line)
        if m:
            section_start_idx = i + 1
            section_level = len(m.group(1))
            break

    if section_start_idx is None:
        return []

    # Collect lines until we hit a heading of equal or higher level (fewer #s),
    # being careful to ignore heading-like lines inside fenced blocks.
    in_fence = False
    section_lines: list[str] = []
    heading_re = re.compile(r"^(#{1,6})\s")

    for line in lines[section_start_idx:]:
        # Toggle fence state on opening/closing ``` markers.
        stripped_for_fence = line.strip()
        if stripped_for_fence.startswith("```"):
            in_fence = not in_fence
            section_lines.append(line)
            continue

        if not in_fence:
            m = heading_re.match(line)
            if m and len(m.group(1)) <= section_level:
                break  # end of our section

        section_lines.append(line)

    # Now extract commands from fenced blocks in section_lines.
    commands: list[PostMergeCommand] = []
    collecting = False
    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            collecting = not collecting
            continue
        if collecting:
            if not stripped or stripped.startswith("#"):
                continue
            commands.append(PostMergeCommand(
                raw=stripped,
                readonly=not _is_side_effecting(stripped),
            ))

    return commands


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_find_issue(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", required=True)
    args = ap.parse_args(argv)
    n = find_tracking_issue(args.branch)
    if n:
        print(str(n))
        return 0
    print("none")
    return 1


def _cmd_parse_post_merge(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--body-file", default=None,
                    help="Path to a file containing the PR body. Reads stdin if omitted.")
    args = ap.parse_args(argv)
    if args.body_file:
        body = open(args.body_file, encoding="utf-8").read()
    else:
        body = sys.stdin.read()
    cmds = parse_post_merge_block(body)
    print(json.dumps([{"raw": c.raw, "readonly": c.readonly} for c in cmds]))
    return 0


_COMMANDS = {
    "find-issue": _cmd_find_issue,
    "parse-post-merge": _cmd_parse_post_merge,
}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in _COMMANDS:
        print(f"Usage: post_merge_lifecycle.py <{'|'.join(_COMMANDS)}> [opts]", file=sys.stderr)
        return 2
    return _COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
