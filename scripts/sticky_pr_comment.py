#!/usr/bin/env python3
"""Create-or-update one PR comment identified by an HTML marker.

Used by CI jobs that report on every push (preview URL, screenshots) so the
PR carries a single current comment per job instead of one per push.

    python3 scripts/sticky_pr_comment.py --repo owner/name --pr 123 \
        --marker operator-console-preview --body-file body.md

Auth: whatever `gh` is logged in as (GH_TOKEN / GITHUB_TOKEN in Actions).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def marker_tag(marker: str) -> str:
    return f"<!-- sticky:{marker} -->"


def find_comment_id(comments: list[dict], marker: str) -> int | None:
    tag = marker_tag(marker)
    for c in comments:
        if tag in (c.get("body") or ""):
            return int(c["id"])
    return None


def _gh(args: list[str], stdin: str | None = None) -> str:
    out = subprocess.run(["gh", "api", *args], input=stdin, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"sticky_pr_comment: gh api {args[0]} failed: {out.stderr.strip()}")
    return out.stdout


def upsert(repo: str, pr: int, marker: str, body: str) -> str:
    full = f"{marker_tag(marker)}\n{body}"
    pages = _gh([f"repos/{repo}/issues/{pr}/comments", "--paginate", "--slurp"])
    comments = [c for page in json.loads(pages or "[]") for c in page]
    cid = find_comment_id(comments, marker)
    payload = json.dumps({"body": full})
    if cid is None:
        _gh([f"repos/{repo}/issues/{pr}/comments", "-X", "POST", "--input", "-"], payload)
        return "created"
    _gh([f"repos/{repo}/issues/comments/{cid}", "-X", "PATCH", "--input", "-"], payload)
    return "updated"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--marker", required=True)
    ap.add_argument("--body-file", required=True, help="Markdown body ('-' for stdin)")
    a = ap.parse_args(argv)
    body = sys.stdin.read() if a.body_file == "-" else open(a.body_file).read()
    print(f"sticky_pr_comment: {upsert(a.repo, a.pr, a.marker, body)} marker={a.marker} pr={a.pr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
