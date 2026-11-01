#!/usr/bin/env python3
"""
check_workflow_yaml.py — gate that fails when a GitHub Actions workflow file is
not valid YAML.

Why this exists (2026-11-01): ship-emoji-force-merge.yml had an inline
`python3 -c` block and a multi-line comment body starting at column 0, which
ends the `run: |` block scalar. GitHub cannot parse such a file, so the
workflow never ran for its own trigger and instead logged a failed run on every
push to main — for four months, unnoticed, because a broken workflow raises no
PR check of its own.

Usage:
    python3 scripts/check_workflow_yaml.py

Exit 0 = every .github/workflows/*.yml / *.yaml parses.
Exit 1 = at least one does not (each failure printed with line/column).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def invalid_workflows(directory: Path = WORKFLOWS) -> list[str]:
    errors = []
    for path in sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]):
        try:
            yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: {' '.join(str(exc).split())}")
    return errors


def main() -> int:
    errors = invalid_workflows()
    for e in errors:
        print(f"BREADCRUMB workflow_yaml invalid {e}")
    if errors:
        print(f"FAIL: {len(errors)} workflow file(s) are not valid YAML — GitHub will never run them.")
        return 1
    print("OK: all workflow files parse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
