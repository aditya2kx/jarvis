#!/usr/bin/env python3
"""Parse 'Evidence confidence rating: X%' from a Claude review comment and fail
(exit 1) if X is below --min. Reads the body from --text, --file, or stdin.

Exit codes:
  0 = score >= min, OR no score found (infra hiccup — verdict gate handles that)
  1 = score below min (blocking)
"""
from __future__ import annotations

import argparse
import re
import sys

# Tolerates: "Evidence confidence: 96%", "Evidence confidence rating: **85%**",
# "### Evidence confidence rating: 100 %".
_PATTERN = re.compile(
    r"Evidence confidence(?:\s+rating)?\s*[:*\s]+\*{0,2}(\d+)\s*%",
    re.IGNORECASE,
)


def parse_score(text: str) -> int | None:
    m = _PATTERN.search(text or "")
    return int(m.group(1)) if m else None


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
    if score < args.min:
        print(f"::error::Evidence confidence {score}% is below the required {args.min}%. "
              "Improve the §4 evidence (real execution output covering all changed "
              "paths) and push to trigger a new review.")
        return 1
    print(f"Evidence confidence {score}% >= {args.min}% — gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
