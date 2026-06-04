#!/usr/bin/env python3
"""
check_pr_description.py — CI gate that fails if the PR description doesn't
satisfy the 5-section template in .github/pull_request_template.md.

Usage:
  # In CI (GitHub Actions):
  python3 scripts/check_pr_description.py --body "$PR_BODY"

  # Locally against a PR:
  python3 scripts/check_pr_description.py --pr 19

Exit 0 = description is complete.
Exit 1 = one or more required sections are missing or unfilled.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import textwrap

# ---------------------------------------------------------------------------
# Required sections — each entry is (section_id, header_regex, min_content_chars)
# The regex matches the markdown heading line.  Content is measured after
# stripping HTML comments (<!-- ... -->) and whitespace.
# ---------------------------------------------------------------------------
REQUIRED_SECTIONS = [
    (
        "what_is_the_change",
        re.compile(r"^##\s*1\.\s*What is the change", re.IGNORECASE | re.MULTILINE),
        60,
        "§1 What is the change",
    ),
    (
        "motivation",
        re.compile(r"^##\s*2\.\s*Motivation", re.IGNORECASE | re.MULTILINE),
        40,
        "§2 Motivation",
    ),
    (
        "e2e_test",
        re.compile(r"^##\s*3\.\s*End.to.end test", re.IGNORECASE | re.MULTILINE),
        40,
        "§3 End-to-end test (with evidence)",
    ),
    (
        "backward_compat",
        re.compile(r"^##\s*4\.\s*Backward compat", re.IGNORECASE | re.MULTILINE),
        40,
        "§4 Backward compatibility — and proof",
    ),
    (
        "checklist",
        re.compile(r"^##\s*5\.\s*Checklist", re.IGNORECASE | re.MULTILINE),
        10,
        "§5 Checklist",
    ),
]

# Placeholder phrases that count as "not filled in"
PLACEHOLDER_PATTERNS = [
    re.compile(r"<paste commands \+ output here>", re.IGNORECASE),
    re.compile(r"^#\s*TODO", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\[to be filled\]", re.IGNORECASE),
    re.compile(r"\[fill in\]", re.IGNORECASE),
    re.compile(r"^\s*TBD\s*$", re.IGNORECASE | re.MULTILINE),
]

# The evidence block must have SOMETHING beyond the template skeleton
EVIDENCE_SKELETON = re.compile(
    r"<details><summary>Evidence</summary>\s*```\s*<paste commands \+ output here>\s*```\s*</details>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _section_content(body: str, header_re: re.Pattern, next_header_re: re.Pattern | None) -> str:
    """Return the text between this section header and the next (or end of body)."""
    m = header_re.search(body)
    if m is None:
        return ""
    start = m.end()
    if next_header_re:
        m2 = next_header_re.search(body, start)
        end = m2.start() if m2 else len(body)
    else:
        end = len(body)
    return body[start:end]


def _check_body(body: str) -> list[str]:
    """Return list of human-readable error strings; empty list means OK."""
    errors: list[str] = []

    # Strip HTML comments before content checks
    stripped = _strip_html_comments(body)

    # Check all 5 sections are present and have content
    for i, (sid, header_re, min_chars, label) in enumerate(REQUIRED_SECTIONS):
        # next section header for boundary detection
        next_re = REQUIRED_SECTIONS[i + 1][1] if i + 1 < len(REQUIRED_SECTIONS) else None

        raw_content = _section_content(body, header_re, next_re)
        if not raw_content:
            # Header itself is missing
            errors.append(f"  ✗ {label}: section heading not found in PR description")
            continue

        content = _strip_html_comments(raw_content).strip()

        if len(content) < min_chars:
            errors.append(
                f"  ✗ {label}: section appears empty or too short "
                f"({len(content)} chars, need ≥ {min_chars})"
            )
            continue

        # Check for unfilled placeholder text
        for pat in PLACEHOLDER_PATTERNS:
            if pat.search(content):
                errors.append(f"  ✗ {label}: contains unfilled placeholder text")
                break

    # Evidence block must not be the raw skeleton
    if EVIDENCE_SKELETON.search(body):
        errors.append(
            "  ✗ §3 End-to-end test: evidence block still contains the template skeleton "
            '("<paste commands + output here>"). Replace with real output.'
        )

    # At least 3 checklist items must be checked [x]
    checked = len(re.findall(r"^\s*-\s*\[x\]", body, re.IGNORECASE | re.MULTILINE))
    if checked < 3:
        errors.append(
            f"  ✗ §5 Checklist: only {checked} item(s) checked [x]; "
            "review each item and check all that apply (at least 3 expected)"
        )

    return errors


def _fetch_pr_body(pr_number: int) -> str:
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--json", "body", "--jq", ".body"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PR description against the 5-section template")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--body", help="PR body text (pass ${{ github.event.pull_request.body }} in CI)")
    group.add_argument("--pr", type=int, help="PR number to fetch via gh CLI")
    args = parser.parse_args()

    body = args.body if args.body else _fetch_pr_body(args.pr)

    if not body or not body.strip():
        print("ERROR: PR description is empty.\n", file=sys.stderr)
        _print_reminder()
        return 1

    errors = _check_body(body)
    if errors:
        print("PR description check FAILED — the following sections are missing or incomplete:\n")
        for e in errors:
            print(e)
        print()
        _print_reminder()
        return 1

    print("PR description check PASSED — all 5 sections are present and filled.")
    return 0


def _print_reminder():
    print(textwrap.dedent("""
        The PR description must follow the 5-section template in
        .github/pull_request_template.md.  The operator reads it to decide
        whether to approve — it must answer, without follow-up questions:

          §1  What is the change  — concrete, 2-5 sentences
          §2  Motivation          — why, linked to ticket/chat/PROGRESS.md
          §3  End-to-end test     — REAL commands + REAL output (not "it worked")
          §4  Backward compat     — explicit yes/no + proof (diff, test output, flag)
          §5  Checklist           — each box checked [x] or explained

        Diagrams (Mermaid, ASCII, screenshots) are welcome and encouraged.

        Quick fix: edit the PR description on GitHub, or run:
          gh pr edit <n> --body "$(cat .github/pull_request_template.md)"
        then fill in every section.
    """).strip())


if __name__ == "__main__":
    sys.exit(main())
