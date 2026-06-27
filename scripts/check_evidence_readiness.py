#!/usr/bin/env python3
"""Local predictor: mirrors Claude rubric D2a so the agent iterates before pushing.

Checks whether PR §4 evidence is likely to reach the 95% confidence bar,
or whether a valid operator waiver is present.

Usage:
    python3 scripts/check_evidence_readiness.py --pr 82
    python3 scripts/check_evidence_readiness.py --file path/to/pr-body.md

Exit codes:
  0 = evidence looks ≥95%, OR a valid unit-only waiver is present
  1 = evidence is pytest-only with no waiver → predict <95%, push first

The predictor is conservative: it only blocks when it is *confident* the
Claude gate will fail.  When uncertain it passes (false-negatives are fine;
false-positives that block a correct push are not).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


# ---------------------------------------------------------------------------
# Markers that indicate real execution output (not just pytest output)
# ---------------------------------------------------------------------------

_REAL_EXEC_PATTERNS = [
    r"HELD-BACK",
    r"bq\s+query",
    r"gs://",
    r"Cloud Run",
    r"sandbox",
    r"sheet[\s-]diff",
    r"live",
    r"PASSED.*real",
]

# Markers that indicate evidence is likely pytest-only
_PYTEST_ONLY_PATTERNS = [
    r"\d+\s+passed",
    r"pytest",
    r"PASSED",
    r"OK\s+\d+",
]

# Waiver pattern (matches plan's "Evidence tier: unit-only (waiver: ...)")
_WAIVER_PATTERN = re.compile(
    r"Evidence\s+tier:\s*unit-only\b[^\n]*waiver\s*:\s*\S",
    re.IGNORECASE,
)

# Scenario pattern for sandbox-live (matches plan's "Evidence tier: sandbox-live")
_SANDBOX_LIVE_PATTERN = re.compile(
    r"Evidence\s+tier:\s*sandbox-live",
    re.IGNORECASE,
)

_SANDBOX_E2E_PATTERN = re.compile(
    r"Evidence\s+tier:\s*sandbox-e2e",
    re.IGNORECASE,
)


def _fetch_pr_body(pr_number: str) -> str:
    """Fetch PR body via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/:owner/:repo/pulls/{pr_number}",
             "--jq", ".body"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"WARNING: could not fetch PR #{pr_number}: {result.stderr.strip()}")
            return ""
        return result.stdout.strip()
    except Exception as exc:
        print(f"WARNING: gh CLI error: {exc}")
        return ""


def _extract_section4(body: str) -> str:
    """Extract §4 / section 4 from a PR body (heuristic)."""
    # Look for "§4", "## 4.", "Section 4", or "Evidence" heading
    m = re.search(
        r"(?:§\s*4|##\s+4\b|##\s+Evidence|##\s+\d+\.\s+Evidence)",
        body, re.IGNORECASE,
    )
    if not m:
        return body  # no section found — check full body
    return body[m.start():]


def _diff_touches(prefix: str) -> bool:
    """Return True when the current branch diff (vs origin/main) touches any file
    under the given path prefix.  Used for path-aware evidence requirements."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "origin/main"],
            capture_output=True, text=True, timeout=10,
        )
        return any(line.startswith(prefix) for line in result.stdout.splitlines())
    except Exception:
        return False


_GRAFANA_SCREENSHOT_RE = re.compile(
    r"https?://[^\s)\"']+\.(?:png|jpg|jpeg|gif|webp)\b", re.IGNORECASE
)
_VERIFY_PANELS_RE = re.compile(r"verify_panels|/api/ds/query", re.IGNORECASE)


def predict(body: str) -> tuple[bool, str]:
    """Return (ok, reason).

    ok=True  → evidence likely passes, or waiver present
    ok=False → predict <95%; name the gap
    """
    # G3: Grafana dashboard changes require a viewable screenshot URL + verify_panels
    # output in §4, even when a unit-only waiver is present.  The visual proof is
    # the only meaningful evidence for a dashboard edit.
    # Only activate when the body contains a §4 / Evidence section (avoids false
    # positives on short bodies in tests and on PRs still being drafted).
    if _diff_touches("agents/bhaga/grafana/") and _extract_section4(body) != body:
        section4 = _extract_section4(body)
        has_screenshot = bool(_GRAFANA_SCREENSHOT_RE.search(section4))
        # Match "verify_panels" as a standalone word/token, not embedded in e.g.
        # prose like "No verify_panels output" — require numeric result or run marker.
        has_verify_panels = bool(
            re.search(r"verify_panels\.py|OK=\d+|verify_panels.*output", section4, re.IGNORECASE)
        )
        if not has_screenshot or not has_verify_panels:
            missing = []
            if not has_screenshot:
                missing.append("a viewable https screenshot URL (e.g. GitHub releases PNG)")
            if not has_verify_panels:
                missing.append("verify_panels.py output (OK=N)")
            return False, (
                "grafana change → §4 must show: "
                + " AND ".join(missing)
                + ". Run: python3 agents/bhaga/grafana/capture_screenshot.py --panel <id> "
                "--label <label> && python3 agents/bhaga/grafana/verify_panels.py"
            )

    # Waiver or explicit sandbox tier → pass immediately
    if _WAIVER_PATTERN.search(body):
        return True, "unit-only waiver present — confidence floor lowered to 80%"
    if _SANDBOX_LIVE_PATTERN.search(body):
        return True, "Evidence tier: sandbox-live declared — real execution expected"
    if _SANDBOX_E2E_PATTERN.search(body):
        return True, "Evidence tier: sandbox-e2e declared — e2e execution expected"

    section4 = _extract_section4(body)

    has_real = any(re.search(p, section4, re.IGNORECASE) for p in _REAL_EXEC_PATTERNS)
    has_pytest_only = any(re.search(p, section4) for p in _PYTEST_ONLY_PATTERNS)

    if has_real:
        return True, "§4 contains real-execution markers — evidence looks ≥95%"

    if has_pytest_only and not has_real:
        return False, (
            "§4 appears to contain only pytest output (no real-execution markers: "
            "HELD-BACK, bq query, gs://, Cloud Run, sandbox, live). "
            "Predict <95% confidence. Options:\n"
            "  A) Run sandbox-live / sandbox-e2e and paste output into §4, or\n"
            "  B) Add 'Evidence tier: unit-only (waiver: <reason>)' to the plan."
        )

    # Empty or unclear §4 — don't block
    return True, "§4 evidence unclear — skipping (predictor only blocks on confident pytest-only)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--pr", metavar="N", help="PR number to fetch body from gh")
    src.add_argument("--file", metavar="PATH", help="Read PR body from file")
    ap.parse_args(argv)  # validate args; values used below
    args = ap.parse_args(argv)

    if args.pr:
        body = _fetch_pr_body(args.pr)
    elif args.file:
        with open(args.file, encoding="utf-8") as fh:
            body = fh.read()
    else:
        body = sys.stdin.read()

    ok, reason = predict(body)
    status = "PASS" if ok else "FAIL"
    print(f"[evidence-readiness] {status}: {reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
