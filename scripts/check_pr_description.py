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

# Patterns that identify pytest output lines (used to detect pytest-only evidence).
_PYTEST_LINE_PATTERNS = [
    re.compile(r"::test_\w+\s+(PASSED|FAILED|ERROR|SKIPPED)", re.IGNORECASE),
    re.compile(r"^\s*={3,}.*={3,}\s*$"),           # ====...====
    re.compile(r"^\s*-{3,}.*-{3,}\s*$"),           # ----...----
    re.compile(r"\d+\s+passed\b.*\bin\b.*[\d.]+s"),  # "16 passed in 0.04s"
    re.compile(r"\bcollected\b.*\bitem"),            # "collected N items"
    re.compile(r"\bno tests ran\b", re.IGNORECASE),
    re.compile(r"^\s*PASSED\s*$", re.IGNORECASE),
    re.compile(r"^\s*FAILED\s*$", re.IGNORECASE),
    re.compile(r"test session starts", re.IGNORECASE),
    re.compile(r"rootdir:", re.IGNORECASE),
    re.compile(r"cachedir:", re.IGNORECASE),
    re.compile(r"platform\s+\w+\s+--\s+Python", re.IGNORECASE),
    re.compile(r"^\s*short test summary", re.IGNORECASE),
    re.compile(r"^\s*warnings summary", re.IGNORECASE),
]

_EVIDENCE_BLOCK_RE = re.compile(
    r"<details><summary>Evidence</summary>(.*?)</details>",
    re.IGNORECASE | re.DOTALL,
)

# G1: reject local file paths in markdown image references inside §4 evidence.
# Local paths (e.g. /tmp/foo.png, ./shot.png, ~/Desktop/x.png) are not viewable
# by the operator. Screenshots must be uploaded to GitHub or a public https host.
_IMG_REF_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)[^)]*\)", re.IGNORECASE)
_LOCAL_IMG_RE = re.compile(r"^(?:/|\./|\.\./|~|file://|[A-Za-z]:\\|/?tmp/)", re.IGNORECASE)

# Issue #123: PRs must assert which issue they implement via a Closes/Fixes/
# Resolves keyword so GitHub auto-closes the tracking issue on merge, and so
# the merge-lifecycle signal chain's branch-slug fallback has a second source
# of truth. A bare "Refs #N" / "#N" mention does not count — those describe a
# relationship (follow-up, background) without asserting "this implements N".
_CLOSE_KEYWORD_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)


def _is_real_evidence(evidence_block_content: str) -> bool:
    """Return True if evidence contains real tool output beyond just pytest results.

    Fails when the entire evidence block consists only of:
      - pytest test names / PASSED / FAILED lines
      - pytest header/summary lines
      - blank lines
      - $ command invocations (without real output after them)

    A PR whose only evidence is "all tests PASSED" is not proof the tool works
    end-to-end.  Real evidence means actual command output with real data.
    """
    has_pytest_content = False
    has_real_data = False

    for line in evidence_block_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Shell prompt lines (just commands) — not counted as real data output.
        if stripped.startswith("$") or stripped.startswith("```"):
            continue
        # Classify as pytest vs real output.
        if any(p.search(stripped) for p in _PYTEST_LINE_PATTERNS):
            has_pytest_content = True
        else:
            has_real_data = True

    # If we found pytest content but no real data output, flag it.
    if has_pytest_content and not has_real_data:
        return False
    return True


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

    # Evidence must contain real tool output, not just pytest results.
    evidence_match = _EVIDENCE_BLOCK_RE.search(body)
    if evidence_match and not _is_real_evidence(evidence_match.group(1)):
        errors.append(
            "  ✗ §4 End-to-end test: evidence appears to contain only unit test results "
            "(pytest PASSED/FAILED lines). "
            "Unit tests are necessary but are not proof the tool works end-to-end. "
            "Add real output from running the actual command (e.g. "
            "`python3 -m agents.bhaga.scripts.status --store palmetto`) "
            "showing actual data rows / sheet diffs / job logs."
        )

    # G1: screenshots in §4 evidence must be viewable https URLs (not local paths).
    if evidence_match:
        for src in _IMG_REF_RE.findall(evidence_match.group(1)):
            src_stripped = src.strip()
            if _LOCAL_IMG_RE.match(src_stripped) or not src_stripped.lower().startswith("http"):
                errors.append(
                    f"  ✗ §4: screenshot '{src_stripped}' must be a viewable https URL "
                    "(e.g. GitHub releases/user-attachments or grafana.net), not a local path. "
                    "Upload via `agents/bhaga/grafana/capture_screenshot.py` or drag-drop to GitHub."
                )

    # At least 3 checklist items must be checked [x]
    checked = len(re.findall(r"^\s*-\s*\[x\]", body, re.IGNORECASE | re.MULTILINE))
    if checked < 3:
        errors.append(
            f"  ✗ §6 Checklist: only {checked} item(s) checked [x]; "
            "review each item and check all that apply (at least 3 expected)"
        )

    # Issue #123: the PR body must assert which issue it implements via
    # Closes/Fixes/Resolves — a bare "Refs #N" mention doesn't count. This is
    # what lets GitHub auto-close the tracking issue on merge and prevents
    # the class of bug that stranded Issues #101/#108/#112/#113/#118 (merged
    # PRs whose tracking issue was never closed).
    if not _CLOSE_KEYWORD_RE.search(body):
        errors.append(
            "  ✗ PR must link its tracking issue with a "
            "Closes/Fixes/Resolves #N keyword (not just Refs #N or a bare "
            "mention) — this is required so the merge auto-closes the "
            "issue instead of stranding it (Issue #123)."
        )

    return errors


def _fetch_pr_body(pr_number: int) -> str:
    import json as _json
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--json", "body"],
        capture_output=True,
        text=True,
        check=True,
    )
    return _json.loads(result.stdout).get("body", "").strip()


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
