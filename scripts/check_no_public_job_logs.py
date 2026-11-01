#!/usr/bin/env python3
"""CI/local gate: never dump Cloud Run job logs into public Actions stdout (Issue #467).

This repo is public. Job logs carry employee names and wages. `/prod-ops` and workflow
steps must not call ``gcloud logging read`` (or equivalent) and print the result — use
encrypted `/prod-read logs` instead. Caught live in #459 (run 41270855462 deleted).
"""

from __future__ import annotations

import pathlib
import re
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SELF = pathlib.Path(__file__).resolve()
_SELF_TEST = _SELF.with_name("test_check_no_public_job_logs.py")

# Paths that run (or plan) in public Actions and must never pull job log bodies.
_SCAN = (
    _REPO / "scripts" / "prod_ops.py",
    *sorted((_REPO / ".github" / "workflows").glob("*.yml")),
)

# Anything that fetches log *bodies* for printing. Metric filters / log-based metrics
# live outside these paths and are not scanned.
# Shell form (`gcloud logging read`) and argv-list form (`"gcloud", "logging", "read"`).
FORBIDDEN = re.compile(
    r"gcloud[\s'\",]+logging[\s'\",]+read|\blogging\s+read\b|textPayload",
    re.IGNORECASE,
)


def violations(path: pathlib.Path, text: str) -> list[str]:
    out = []
    for m in FORBIDDEN.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        out.append(
            f"{path}:{line}: public Actions must not dump job logs ({m.group(0)!r}) — "
            f"use `/prod-read logs` (Issue #467 / #459)"
        )
    return out


def main() -> int:
    errors: list[str] = []
    for p in _SCAN:
        if not p.is_file():
            continue
        if p.resolve() in (_SELF, _SELF_TEST):
            continue
        rel = p.relative_to(_REPO)
        errors += violations(rel, p.read_text(errors="ignore"))
    if errors:
        print("public job-log dump found (Issue #467 — encrypted /prod-read only):")
        for e in errors:
            print(f"  {e}")
        return 1
    print("public job-log dump OK (prod_ops + workflows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
