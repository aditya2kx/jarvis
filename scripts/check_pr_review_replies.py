#!/usr/bin/env python3
"""Merge-readiness gate: every review comment must have an agent reply.

Checks two categories:
1. Inline review-comment threads (diff view) — every root thread must have a reply.
2. Operator issue-level comments — every top-level comment from a human (non-bot)
   user must have a subsequent reply from an agent account.

CONTRIBUTING.md requires the agent to address *every* review comment (bot or
human) and reply — not batch them into one summary.

Usage:
    python3 scripts/check_pr_review_replies.py [--pr N] [--repo owner/name]

Exits 0 when every thread/comment is addressed, 1 when any is unaddressed, 2 on
a usage/tooling error. Requires ``gh``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

# Logins that count as "agent" — a reply from any of these satisfies an
# operator comment. Extend if new bot accounts are added.
_AGENT_LOGINS = {"jarvis-agent-bot328"}

# Logins whose comments never need a reply (CI bots, cost reporters, etc.)
_IGNORED_LOGINS = {"github-actions[bot]"}


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


def _is_bot(login: str) -> bool:
    return login.endswith("[bot]") or login in _AGENT_LOGINS or login in _IGNORED_LOGINS


def _check_inline_threads(repo: str, pr: int) -> list[str]:
    """Return error strings for unaddressed inline review-comment threads."""
    comments = _gh_json(["api", "--paginate", f"repos/{repo}/pulls/{pr}/comments"])
    roots = {c["id"]: c for c in comments if not c.get("in_reply_to_id")}
    replied_roots = {c["in_reply_to_id"] for c in comments if c.get("in_reply_to_id")}
    errors = []
    for cid, c in roots.items():
        if cid not in replied_roots:
            loc = f"{c.get('path')}:{c.get('line') or c.get('original_line')}"
            snippet = " ".join((c.get("body") or "").split())[:100]
            errors.append(
                f"  [inline] id={cid} {loc}\n"
                f"      {snippet}\n"
                f"      Reply: gh api repos/{repo}/pulls/{pr}/comments/{cid}/replies -f body='...'"
            )
    return errors


def _check_issue_comments(repo: str, pr: int) -> list[str]:
    """Return error strings for operator issue-level comments with no agent reply.

    An operator comment (from any non-bot user) is ADDRESSED when any comment
    from an _AGENT_LOGIN appears after it in the thread (by list position,
    which GitHub returns in ascending created_at order).
    """
    all_comments = _gh_json(["api", "--paginate", f"repos/{repo}/issues/{pr}/comments"])

    errors = []
    # Collect operator comments that still need a subsequent agent reply.
    pending_operator: list[dict] = []

    for c in all_comments:
        login = c.get("user", {}).get("login", "")
        if login in _IGNORED_LOGINS:
            continue
        if login in _AGENT_LOGINS:
            # This agent comment addresses all pending operator comments before it.
            pending_operator.clear()
        elif not _is_bot(login):
            # Human/operator comment — needs a subsequent agent reply.
            pending_operator.append(c)

    for c in pending_operator:
        snippet = " ".join((c.get("body") or "").split())[:120]
        errors.append(
            f"  [issue comment] id={c['id']} by {c['user']['login']}\n"
            f"      {snippet}\n"
            f"      Reply: gh api repos/{repo}/issues/{pr}/comments -f body='...'"
        )
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, default=None)
    ap.add_argument("--repo", default=None)
    args = ap.parse_args()

    repo = args.repo or _repo()
    pr = args.pr or _current_pr()
    if not pr:
        print("error: no PR found for the current branch (pass --pr N).", file=sys.stderr)
        return 2

    inline_errors = _check_inline_threads(repo, pr)
    issue_errors = _check_issue_comments(repo, pr)
    all_errors = inline_errors + issue_errors

    if not all_errors:
        print(f"pr-review-replies: PR #{pr} — all threads and operator comments addressed. ✓")
        return 0

    print(
        f"pr-review-replies: PR #{pr} — {len(all_errors)} unaddressed comment(s):",
        file=sys.stderr,
    )
    for e in all_errors:
        print(e, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
