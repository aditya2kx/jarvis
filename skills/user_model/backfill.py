#!/usr/bin/env python3
"""One-shot backfill of operator preferences from existing principle docs.

Scans the corpus of principle docs (bhaga-principles.md, CONTRIBUTING.md,
jarvis.md Hard Lessons) plus the 5 answers from the Issue #70 jam transcript,
runs each candidate through the guardrail, and adds passing candidates to the
preferences store. Idempotent — safe to re-run; add_preference deduplicates.

Usage:
    python -m skills.user_model.backfill
    python -m skills.user_model.backfill --dry-run     # score only, no writes
    python -m skills.user_model.backfill --verbose
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_REPO))

from skills.user_model.guardrail import score_candidate, DEFAULT_THRESHOLD  # noqa: E402
from skills.user_model.store import add_preference  # noqa: E402


# ---------------------------------------------------------------------------
# Hard-coded candidate list
# Each entry: (category, fields_dict, scope_hint)
# "scope_hint" is prepended to the text when scoring so the guardrail criterion
# #3 (scoped) has context.
# ---------------------------------------------------------------------------

# Category "principle" -> Design Principles section
# Category "style"     -> Communication Style section
# "domain" and "decision" rows bypass the guardrail (skip_guardrail=True).

CANDIDATES: list[tuple[str, dict, str]] = [
    # ── From bhaga-principles.md ─────────────────────────────────────────
    (
        "principle",
        {
            "#": "B1",
            "Principle": (
                "For BHAGA: prefer sandbox (staging) e2e runs over touching prod sheets "
                "when proving changes pre-merge — prod sheets are integration, not a test env."
            ),
            "Source": "bhaga-principles.md backfill",
        },
        "bhaga",
    ),
    (
        "principle",
        {
            "#": "B2",
            "Principle": (
                "For BHAGA: run `python3 -m agents.bhaga.scripts.status --store palmetto` "
                "before any manual investigation to get a compact freshness table — "
                "never hand-write queries to answer 'did last night land?'"
            ),
            "Source": "bhaga-principles.md backfill",
        },
        "bhaga",
    ),
    (
        "principle",
        {
            "#": "B3",
            "Principle": (
                "For BHAGA: always leave a greppable one-line breadcrumb on every failure "
                "with enough state (refresh_date, attempt N/M, evidence path) to diagnose "
                "from Cloud Run logs + Firestore alone on any machine."
            ),
            "Source": "bhaga-principles.md backfill",
        },
        "bhaga",
    ),
    # ── From Issue #70 jam transcript (5 standing answers) ───────────────
    (
        "principle",
        {
            "#": "J1",
            "Principle": (
                "For BHAGA: read-only diagnosis (BQ queries, Firestore reads, Cloud Run logs) "
                "is always pre-approved in jam mode — never ask for approval before running "
                "non-mutating diagnosis."
            ),
            "Source": "Issue #70 jam transcript backfill",
        },
        "bhaga",
    ),
    (
        "principle",
        {
            "#": "J2",
            "Principle": (
                "For BHAGA: acceptance evidence must always cover both sandbox e2e "
                "and prod ADP/Square live verification — sandbox alone is never sufficient."
            ),
            "Source": "Issue #70 jam transcript backfill",
        },
        "bhaga",
    ),
    (
        "principle",
        {
            "#": "J3",
            "Principle": (
                "For BHAGA: post-merge, always re-run all pending/failed dates together "
                "rather than rerunnning only the most recent — backfill the full gap."
            ),
            "Source": "Issue #70 jam transcript backfill",
        },
        "bhaga",
    ),
    (
        "principle",
        {
            "#": "J4",
            "Principle": (
                "For BHAGA: 'last night' should be derived automatically from prod state "
                "(most-recent failed/pending nightly in Firestore) — never ask the operator "
                "which date to fix when it can be derived."
            ),
            "Source": "Issue #70 jam transcript backfill",
        },
        "bhaga",
    ),
    (
        "principle",
        {
            "#": "J5",
            "Principle": (
                "For BHAGA sandbox evidence: prefer reusing an existing scenario "
                "(e.g. full-live) over adding a new dedicated scenario when the existing "
                "one already exercises the changed code path."
            ),
            "Source": "Issue #70 jam transcript backfill",
        },
        "bhaga",
    ),
    # ── Global style from behavioral-anchor / user-preferences patterns ──
    (
        "style",
        {
            "#": "G1",
            "Pattern": (
                "Globally: when the operator gives a general direction, always pick the "
                "finer implementation detail yourself — never ask micro-decision questions "
                "the operator already delegated by giving a broad answer."
            ),
            "Source": "Issue #70 + pref-store design session backfill",
        },
        "global",
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(dry_run: bool = False, verbose: bool = False,
        threshold: int = DEFAULT_THRESHOLD) -> dict:
    results = {"added": 0, "duplicate": 0, "rejected": 0, "total": len(CANDIDATES)}

    for category, fields, scope_hint in CANDIDATES:
        # Build the text that goes to the guardrail (main content column)
        headers_map = {"principle": "Principle", "style": "Pattern"}
        content_key = headers_map.get(category, "Principle")
        text = fields.get(content_key, "")

        result = score_candidate(text)
        passed = result.score >= threshold

        if verbose:
            verdict = "PASS" if passed else "FAIL"
            print(f"[{verdict} {result.score}/{result.max_score}] {text[:70]!r}")
            if not passed:
                for r in result.results:
                    if not r.passed:
                        print(f"    ✗ {r.name}: {r.reason}")

        if not passed:
            results["rejected"] += 1
            continue

        if dry_run:
            results["added"] += 1  # count as would-add in dry run
            continue

        status, _ = add_preference(category, fields, skip_guardrail=True)
        results[status] += 1
        if verbose:
            print(f"    -> {status}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill operator preferences from principle docs."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Score only; do not write to the preferences file.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-candidate guardrail results.")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help=f"Guardrail pass threshold (default: {DEFAULT_THRESHOLD}/6)")
    args = parser.parse_args()

    results = run(dry_run=args.dry_run, verbose=args.verbose, threshold=args.threshold)

    mode = "(dry-run)" if args.dry_run else ""
    print(
        f"\nBackfill {mode}: {results['added']} added, "
        f"{results['duplicate']} already present, "
        f"{results['rejected']} rejected by guardrail "
        f"(out of {results['total']} candidates)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
