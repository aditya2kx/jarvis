#!/usr/bin/env python3
"""Parse 'Evidence confidence rating: X%' from a Claude review comment and fail
(exit 1) if X is below --min. Reads the body from --text, --file, or stdin.

Exit codes:
  0 = score >= min, OR no score found (infra hiccup — verdict gate handles that)
  0 = score below min BUT a valid unit-only waiver is present (floor lowered to 80)
  1 = score below min (blocking)

Waiver path (no changes to claude-review.yml):
  If the PR body contains 'Evidence tier: unit-only (waiver: ...)' or the PR
  carries the 'evidence-waiver' label, the effective minimum is lowered to 80.
  PR body is fetched via GH_TOKEN + PR_NUMBER env vars (already set by
  claude-review.yml steps 290-291).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

# Tolerates: "Evidence confidence: 96%", "Evidence confidence rating: **85%**",
# "### Evidence confidence rating: 100 %".
_PATTERN = re.compile(
    r"Evidence confidence(?:\s+rating)?\s*[:*\s]+\*{0,2}(\d+)\s*%",
    re.IGNORECASE,
)

_WAIVER_PATTERN = re.compile(
    r"Evidence\s+tier:\s*unit-only\b[^\n]*waiver\s*:\s*\S",
    re.IGNORECASE,
)

_WAIVER_FLOOR = 80


def parse_score(text: str) -> int | None:
    m = _PATTERN.search(text or "")
    return int(m.group(1)) if m else None


def _has_waiver() -> bool:
    """Return True if the current PR carries a unit-only evidence waiver.

    Checks two sources (either is sufficient):
    1. PR body via GH_TOKEN + PR_NUMBER env vars (set by claude-review.yml).
    2. 'evidence-waiver' label on the PR.

    Returns False if the env vars are absent (local run without PR context).
    """
    pr_number = os.environ.get("PR_NUMBER", "")
    gh_token = os.environ.get("GH_TOKEN", "")
    if not pr_number or not gh_token:
        return False

    try:
        env = {**os.environ, "GH_TOKEN": gh_token}
        result = subprocess.run(
            ["gh", "api", f"repos/:owner/:repo/pulls/{pr_number}",
             "--jq", "{body: .body, labels: [.labels[].name]}"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode != 0:
            return False
        import json
        data = json.loads(result.stdout)
        body = data.get("body") or ""
        labels = data.get("labels") or []
        if "evidence-waiver" in labels:
            return True
        return bool(_WAIVER_PATTERN.search(body))
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--text")
    src.add_argument("--file")
    ap.add_argument("--min", type=int, default=95)
    args = ap.parse_args(argv)

    if args.text is not None:
        body = args.text
    elif args.file:
        with open(args.file, encoding="utf-8") as fh:
            body = fh.read()
    else:
        body = sys.stdin.read()

    score = parse_score(body)
    if score is None:
        print("Evidence confidence score not found — skipping check.")
        return 0
    print(f"Evidence confidence score: {score}%")

    effective_min = args.min
    if score < args.min and _has_waiver():
        effective_min = _WAIVER_FLOOR
        print(
            f"Unit-only evidence waiver detected — lowering floor from "
            f"{args.min}% to {_WAIVER_FLOOR}%."
        )

    if score < effective_min:
        print(f"::error::Evidence confidence {score}% is below the required {effective_min}%. "
              "Improve the §4 evidence (real execution output covering all changed "
              "paths) and push to trigger a new review.")
        return 1
    print(f"Evidence confidence {score}% >= {effective_min}% — gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
