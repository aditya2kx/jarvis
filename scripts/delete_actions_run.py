#!/usr/bin/env python3
"""Delete a GitHub Actions run (and its public logs) as jarvis-agent-bot328.

The Cloud Agent's injected ``gh`` is read-only (cursor app token). Deleting a
run that leaked PII into a public Actions log needs the bot PAT's ``workflow``
scope — the same token used for pushes.

Usage::

    python3 scripts/delete_actions_run.py 41270855462
    python3 scripts/delete_actions_run.py https://github.com/aditya2kx/jarvis/actions/runs/41270855462

Token source: ``GH_TOKEN`` / Keychain on a Mac; Secret Manager ``github-bot-pat``
on a Cursor Cloud Agent (via ``cloud_agent_gcp.py``).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error
import urllib.request

REPO = "aditya2kx/jarvis"
_RUN_RE = re.compile(r"(?:actions/runs/)?(\d+)/?$")


def _bot_token() -> str:
    tok = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if tok and not tok.startswith("ghs_"):  # ghs_ is the read-only Cursor app token
        return tok
    import subprocess
    from pathlib import Path

    # Cloud Agent / any host with Cursor OIDC → SM github-bot-pat (workflow scope).
    try:
        return subprocess.check_output(
            [sys.executable, str(Path(__file__).resolve().parent / "cloud_agent_gcp.py"),
             "secret", "github-bot-pat"],
            text=True,
        ).strip()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "no bot PAT: set GH_TOKEN to jarvis-agent-bot328's classic PAT "
            f"(workflow scope), or provide SM github-bot-pat ({type(exc).__name__}: {exc})"
        ) from exc


def run_id(arg: str) -> int:
    m = _RUN_RE.search(arg.strip())
    if not m:
        raise SystemExit(f"not a run id or Actions URL: {arg!r}")
    return int(m.group(1))


def delete_run(rid: int, *, token: str, repo: str = REPO) -> int:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/runs/{rid}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "jarvis-delete-actions-run",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"already gone: run {rid}", file=sys.stderr)
            return 404
        body = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"DELETE run {rid} failed: HTTP {e.code} {body}") from e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run", help="numeric run id or Actions run URL")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args(argv)
    rid = run_id(args.run)
    status = delete_run(rid, token=_bot_token(), repo=args.repo)
    print(f"deleted run {rid} (HTTP {status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
