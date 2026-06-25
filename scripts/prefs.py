#!/usr/bin/env python3
"""Quick lookup for operator preferences.

Usage:
    python3 scripts/prefs.py list                   # all preferences
    python3 scripts/prefs.py list --category style  # one category
    python3 scripts/prefs.py search diagnosis        # keyword match
    python3 scripts/prefs.py score "text here"       # run guardrail
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from skills.user_model.store import list_preferences           # noqa: E402
from skills.user_model.guardrail import score_candidate        # noqa: E402


def cmd_list(category: str | None) -> int:
    prefs = list_preferences(category=category)
    if not prefs:
        print("(no preferences stored)")
        return 0
    current_cat = None
    for p in prefs:
        if p.category != current_cat:
            current_cat = p.category
            print(f"\n── {current_cat.upper()} ──")
        content_key = list(p.fields.keys())[1] if len(p.fields) > 1 else list(p.fields.keys())[0]
        text = p.fields.get(content_key, "")
        source = p.fields.get("Source", "")
        print(f"  • {text}")
        if source:
            print(f"    [{source}]")
    return 0


def cmd_search(keyword: str) -> int:
    prefs = list_preferences()
    kw = keyword.lower()
    hits = [
        p for p in prefs
        if any(kw in str(v).lower() for v in p.fields.values())
    ]
    if not hits:
        print(f"No preferences matching {keyword!r}")
        return 0
    print(f"{len(hits)} match(es) for {keyword!r}:")
    for p in hits:
        content_key = list(p.fields.keys())[1] if len(p.fields) > 1 else list(p.fields.keys())[0]
        text = p.fields.get(content_key, "")
        print(f"  [{p.category}] {text}")
    return 0


def cmd_score(text: str, threshold: int) -> int:
    result = score_candidate(text)
    print(result.summary())
    verdict = "PASS" if result.score >= threshold else "FAIL"
    print(f"\nVerdict: {verdict} ({result.score}/{result.max_score} >= {threshold})")
    return 0 if verdict == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Operator preferences lookup")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="List all preferences")
    p_list.add_argument("--category", default=None,
                        choices=["style", "principle", "domain", "decision"])

    p_search = sub.add_parser("search", help="Keyword search across preferences")
    p_search.add_argument("keyword", help="Keyword to search for")

    p_score = sub.add_parser("score", help="Run the guardrail on a candidate text")
    p_score.add_argument("text", help="Candidate preference text (quoted)")
    p_score.add_argument("--threshold", type=int, default=4,
                         help="Pass threshold out of 6 (default: 4)")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 0

    if args.cmd == "list":
        return cmd_list(args.category)
    if args.cmd == "search":
        return cmd_search(args.keyword)
    if args.cmd == "score":
        return cmd_score(args.text, args.threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
