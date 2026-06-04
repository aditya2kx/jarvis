#!/usr/bin/env python3
"""
check_pr_description.py — CI gate that fails if the PR description doesn't
satisfy the 6-section template in .github/pull_request_template.md.

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
import re
import subprocess
import sys
import textwrap

# ---------------------------------------------------------------------------
# Required sections — (section_id, header_regex, min_content_chars, label)
# Content is measured after stripping HTML comments and whitespace.
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
        "design",
        re.compile(r"^##\s*3\.\s*Design", re.IGNORECASE | re.MULTILINE),
        40,
        "§3 Design / Approach",
    ),
    (
        "e2e_test",
        re.compile(r"^##\s*4\.\s*End.to.end test", re.IGNORECASE | re.MULTILINE),
        40,
        "§4 End-to-end test (with evidence)",
    ),
    (
        "backward_compat",
        re.compile(r"^##\s*5\.\s*Backward compat", re.IGNORECASE | re.MULTILINE),
        40,
        "§5 Backward compatibility — and proof",
    ),
    (
        "checklist",
        re.compile(r"^##\s*6\.\s*Checklist", re.IGNORECASE | re.MULTILINE),
        10,
        "§6 Checklist",
    ),
]

# Placeholder phrases that count as "not filled in"
PLACEHOLDER_PATTERNS = [
    re.compile(r"<paste real commands \+ real output here>", re.IGNORECASE),
    re.compile(r"<paste commands \+ output here>", re.IGNORECASE),
    re.compile(r"^\s*#\s*TODO", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\[to be filled\]", re.IGNORECASE),
    re.compile(r"\[fill in\]", re.IGNORECASE),
    re.compile(r"^\s*TBD\s*$", re.IGNORECASE | re.MULTILINE),
]

EVIDENCE_SKELETON = re.compile(
    r"<details><summary>Evidence</summary>\s*```\s*<paste.*?here>\s*```\s*</details>",
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

    for i, (sid, header_re, min_chars, label) in enumerate(REQUIRED_SECTIONS):
        next_re = REQUIRED_SECTIONS[i + 1][1] if i + 1 < len(REQUIRED_SECTIONS) else None

        raw_content = _section_content(body, header_re, next_re)
        if not raw_content:
            errors.append(f"  ✗ {label}: section heading not found in PR description")
            continue

        content = _strip_html_comments(raw_content).strip()

        if len(content) < min_chars:
            errors.append(
                f"  ✗ {label}: section appears empty or too short "
                f"({len(content)} chars, need ≥ {min_chars})"
            )
            continue

        for pat in PLACEHOLDER_PATTERNS:
            if pat.search(content):
                errors.append(f"  ✗ {label}: contains unfilled placeholder text")
                break

    # Evidence block must not be the raw skeleton
    if EVIDENCE_SKELETON.search(body):
        errors.append(
            "  ✗ §4 End-to-end test: evidence block still contains the template skeleton. "
            "Replace with real commands + real output."
        )

    # At least 3 checklist items must be checked [x]
    checked = len(re.findall(r"^\s*-\s*\[x\]", body, re.IGNORECASE | re.MULTILINE))
    if checked < 3:
        errors.append(
            f"  ✗ §6 Checklist: only {checked} item(s) checked [x]; "
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
    parser = argparse.ArgumentParser(description="Validate PR description against the 6-section template")
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

    print("PR description check PASSED — all 6 sections are present and filled.")
    return 0


def _print_reminder():
    print(textwrap.dedent("""
        The PR description must follow the 6-section template in
        .github/pull_request_template.md.  The operator reads it to decide
        whether to approve — it must answer, without follow-up questions:

          §1  What is the change  — concrete, 2-5 sentences
          §2  Motivation          — why, linked to ticket/chat/PROGRESS.md
          §3  Design / Approach   — how it's built; diagrams strongly preferred
          §4  End-to-end test     — REAL commands + REAL output (not "it worked")
          §5  Backward compat     — explicit yes/no + proof (diff, test output, flag)
          §6  Checklist           — each box checked [x] or explained

        Diagrams (Mermaid, ASCII, screenshots) are welcome and encouraged in §3.

        Quick fix: edit the PR description on GitHub to use the template sections,
        or run: gh pr edit <n>  and add all 6 sections.
    """).strip())


if __name__ == "__main__":
    sys.exit(main())
