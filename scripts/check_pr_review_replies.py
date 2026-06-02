#!/usr/bin/env python3
"""Merge-readiness gate: every inline review comment must have an author reply.

CONTRIBUTING.md requires the agent to address *every* review comment (bot or
human) and **reply on each inline thread** — not batch them into one summary.
That policy kept getting skipped because nothing mechanically checked it. This
script is the gate (run it like ``check_doc_freshness.py`` before declaring a PR
merge-ready): it fails if any inline review-comment thread has no reply.

A "thread" is a root review comment (``in_reply_to_id is null``). It's considered
ADDRESSED when at least one later comment replies into it (``in_reply_to_id`` ==
root id) — typically the agent's "fixed in <sha> / won't-fix because …" note —
OR the thread is marked resolved on GitHub.

Usage:
    python3 scripts/check_pr_review_replies.py [--pr N] [--repo owner/name]

Exits 0 when every thread is addressed, 1 when any is unaddressed (printing the
file:line + body snippet of each), 2 on a usage/tooling error. Requires ``gh``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


def _gh_json(args: list[str]):
    try:
        out = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True
        ).stdout
    except FileNotFoundError:
        print("error: `gh` CLI not found on PATH.", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        print(f"error: gh {' '.join(args)} failed:\n{e.stderr}", file=sys.stderr)
        sys.exit(2)
    return json.loads(out) if out.strip() else []


def _current_pr() -> int | None:
    data = _gh_json(["pr", "view", "--json", "number"])
    return data.get("number") if isinstance(data, dict) else None


def _repo() -> str:
    data = _gh_json(["repo", "view", "--json", "nameWithOwner"])
    return data["nameWithOwner"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, default=None, help="PR number (default: current branch's PR)")
    ap.add_argument("--repo", default=None, help="owner/name (default: current repo)")
    args = ap.parse_args()

    repo = args.repo or _repo()
    pr = args.pr or _current_pr()
    if not pr:
        print("error: no PR found for the current branch (pass --pr N).", file=sys.stderr)
        return 2

    # All inline review comments (paginated).
    comments = _gh_json(["api", "--paginate", f"repos/{repo}/pulls/{pr}/comments"])

    roots = {c["id"]: c for c in comments if not c.get("in_reply_to_id")}
    replied_roots = {c["in_reply_to_id"] for c in comments if c.get("in_reply_to_id")}

    unaddressed = [c for cid, c in roots.items() if cid not in replied_roots]

    if not unaddressed:
        print(f"pr-review-replies: PR #{pr} — all {len(roots)} inline thread(s) have a reply. ✓")
        return 0

    print(
        f"pr-review-replies: PR #{pr} — {len(unaddressed)} of {len(roots)} inline "
        f"thread(s) have NO reply (CONTRIBUTING requires a reply on each):",
        file=sys.stderr,
    )
    for c in unaddressed:
        loc = f"{c.get('path')}:{c.get('line') or c.get('original_line')}"
        snippet = " ".join((c.get("body") or "").split())[:100]
        print(f"  - id={c['id']} {loc}\n      {snippet}", file=sys.stderr)
    print(
        "\nReply on each with:\n"
        f"  gh api repos/{repo}/pulls/{pr}/comments/<id>/replies -f body='…what you did / why not…'",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
