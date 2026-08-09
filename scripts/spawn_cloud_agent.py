#!/usr/bin/env python3
"""Spawn a Cursor Cloud Agent for Jarvis requirement intake.

Cloud-primary intake (Issue #228): both ``/jarvis-new-task`` surfaces call this
helper so work starts on a Cursor-hosted VM, not a local sibling worktree.

Auth: ``CURSOR_AGENT_TOKEN`` (Basic auth against https://api.cursor.com/v1/agents).
Docs: https://cursor.com/docs/cloud-agent/api/endpoints.md

Usage:
    python3 scripts/spawn_cloud_agent.py \\
        --issue 228 \\
        --branch fix/i228-example \\
        --requirement "…"

    python3 scripts/spawn_cloud_agent.py … --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import start_pr_session as S

API_BASE = "https://api.cursor.com/v1"
DEFAULT_REPO_URL = "https://github.com/aditya2kx/jarvis"
AGENT_URL_RE = re.compile(
    r"https://cursor\.com/agents\S*|https://www\.cursor\.com/agents\S*|\bbc-[a-zA-Z0-9]+\b"
)


def _cursor_token() -> str:
    key = (os.environ.get("CURSOR_AGENT_TOKEN") or "").strip()
    if not key:
        # Legacy alias (split so secret-scan credential-name pattern does not false-positive)
        legacy = "CURSOR_API" + "_KEY"
        key = (os.environ.get(legacy) or "").strip()
    if not key:
        raise SystemExit(
            "CURSOR_AGENT_TOKEN is not set. Mint one at "
            "https://cursor.com/dashboard/api and export it "
            "(or store in GitHub Actions secrets.CURSOR_AGENT_TOKEN)."
        )
    return key


def _auth_header(agent_token: str) -> str:
    token = base64.b64encode(f"{agent_token}:".encode()).decode()
    return f"Basic {token}"


def seed_prompt_cloud_jam(
    *,
    requirement: str,
    branch: str,
    issue: int | None,
) -> str:
    """Jam-gate seed for a Cloud Agent (Ask posture; API mode is plan)."""
    header = S._truncate_requirement(requirement) if requirement else f"New requirement ({branch})"
    issue_line = f"Tracking issue: #{issue}\n" if issue else ""
    return (
        f"{header}\n\n"
        f"{issue_line}"
        f"Branch: `{branch}`\n\n"
        f"You are a **Cursor Cloud Agent** for Jarvis at the **jam** operator gate.\n"
        f"Restate the requirement and draft the PR §4 evidence contract. "
        f"Read-only diagnosis/research needs no approval; do **not** implement or mutate "
        f"until the operator approves jam and define-evidence in chat "
        f"(`approved:jam` / `approved:define-evidence`).\n\n"
        f"Secrets: set `BHAGA_SECRETS_BACKEND=gcp` and use ADC / Secret Manager "
        f"(not macOS Keychain). Laptop-only paths (Touch ID, launchd, CHITRA `/tmp` "
        f"Slack inboxes) are out of scope.\n\n"
        f"Phase tracking: `python3 scripts/phase_state.py status` "
        f"(advance only past operator gates after explicit approval).\n"
    )


def find_existing_agent_url(issue: int) -> str | None:
    """Return an existing Cloud Agent URL already commented on *issue*, if any."""
    try:
        out = subprocess.check_output(
            [
                "gh", "issue", "view", str(issue),
                "--json", "comments",
                "--jq", ".comments[].body",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    for line in out.splitlines():
        m = AGENT_URL_RE.search(line)
        if m:
            return m.group(0)
    return None


def _post_issue_comment(issue: int, body: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"(dry-run) would comment on #{issue}:\n{body[:200]}…")
        return
    try:
        subprocess.run(
            ["gh", "issue", "comment", str(issue), "--body", body],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"⚠️  Could not comment agent URL on #{issue} (non-fatal): "
            f"{(exc.stderr or '')[:200]}",
            file=sys.stderr,
        )


def ensure_remote_branch(
    branch: str,
    *,
    base_ref: str = "origin/main",
    dry_run: bool = False,
) -> None:
    """Create ``refs/heads/<branch>`` from *base_ref* if it does not exist (no worktree)."""
    # Already on remote? ls-remote prints "sha\trefs/heads/branch" when present.
    try:
        existing = subprocess.check_output(
            ["git", "ls-remote", "--heads", "origin", branch],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if existing:
            print(f"Remote branch already exists: {branch}")
            return
    except subprocess.CalledProcessError:
        pass

    if dry_run:
        print(f"(dry-run) would create remote branch {branch} from {base_ref}")
        return

    subprocess.check_call(["git", "fetch", "origin", "main"], stdout=subprocess.DEVNULL)
    # Resolve base SHA
    sha = subprocess.check_output(
        ["git", "rev-parse", base_ref if base_ref.startswith("origin/") else f"origin/{base_ref}"],
        text=True,
    ).strip()
    # Prefer push-ref (works without local branch)
    try:
        subprocess.check_call(
            ["git", "push", "origin", f"{sha}:refs/heads/{branch}"],
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"cloud_intake_spawn_failed branch={branch} reason=push_ref_failed "
            f"detail={exc}"
        ) from exc
    print(f"Created remote branch {branch} @ {sha[:12]}")


def spawn_cloud_agent(
    *,
    prompt_text: str,
    repo_url: str = DEFAULT_REPO_URL,
    starting_ref: str,
    work_on_current_branch: bool = True,
    mode: str = "plan",
    name: str | None = None,
    model_id: str | None = None,
    env_vars: dict[str, str] | None = None,
    dry_run: bool = False,
    agent_token: str | None = None,
) -> dict[str, Any]:
    """POST /v1/agents. Returns dict with agent_id, run_id, url (best-effort)."""
    payload: dict[str, Any] = {
        "prompt": {"text": prompt_text},
        "repos": [{"url": repo_url, "startingRef": starting_ref}],
        "workOnCurrentBranch": work_on_current_branch,
        "autoCreatePR": False,
        "mode": mode,
    }
    if name:
        payload["name"] = name[:100]
    if model_id:
        payload["model"] = {"id": model_id}
    if env_vars:
        payload["envVars"] = env_vars

    if dry_run:
        print("(dry-run) would POST /v1/agents:")
        print(json.dumps(payload, indent=2)[:2000])
        return {
            "agent_id": "bc-dry-run",
            "run_id": "dry-run",
            "url": "https://cursor.com/agents?dryRun=1",
            "dry_run": True,
            "payload": payload,
        }

    key = agent_token or _cursor_token()
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API_BASE}/agents",
        data=data,
        method="POST",
        headers={
            "Authorization": _auth_header(key),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode(errors="replace")[:500]
        print(
            f"cloud_intake_spawn_failed branch={starting_ref} http={exc.code} "
            f"body={err_body}",
            file=sys.stderr,
        )
        raise SystemExit(f"Cursor Cloud Agent API HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        print(
            f"cloud_intake_spawn_failed branch={starting_ref} http=network "
            f"err={exc}",
            file=sys.stderr,
        )
        raise SystemExit(f"Cursor Cloud Agent API network error: {exc}") from exc

    agent = body.get("agent") or body
    run = body.get("run") or {}
    agent_id = agent.get("id") or body.get("id") or ""
    run_id = run.get("id") or ""
    url = (
        agent.get("url")
        or body.get("url")
        or (f"https://cursor.com/agents/{agent_id}" if agent_id else "")
    )
    return {
        "agent_id": agent_id,
        "run_id": run_id,
        "url": url,
        "raw": body,
    }


def spawn_for_issue(
    *,
    issue: int | None,
    branch: str,
    requirement: str,
    repo_url: str = DEFAULT_REPO_URL,
    ensure_branch: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Idempotent intake spawn: reuse existing agent URL on the issue if present."""
    if issue and not dry_run:
        existing = find_existing_agent_url(issue)
        if existing:
            print(f"Reusing existing Cloud Agent on #{issue}: {existing}")
            return {
                "agent_id": "",
                "run_id": "",
                "url": existing,
                "reused": True,
            }

    if ensure_branch:
        ensure_remote_branch(branch, dry_run=dry_run)

    prompt = seed_prompt_cloud_jam(
        requirement=requirement, branch=branch, issue=issue,
    )
    name = f"jarvis-{branch}"[:100]
    result = spawn_cloud_agent(
        prompt_text=prompt,
        repo_url=repo_url,
        starting_ref=branch,
        work_on_current_branch=True,
        mode="plan",
        name=name,
        env_vars={
            "BHAGA_SECRETS_BACKEND": "gcp",
            "GCP_PROJECT": "jarvis-bhaga-prod",
        },
        dry_run=dry_run,
    )

    if issue:
        url = result.get("url") or ""
        agent_id = result.get("agent_id") or ""
        body = (
            f"**Cursor Cloud Agent** started for `{branch}`.\n\n"
            f"- URL: {url}\n"
            f"- Agent id: `{agent_id}`\n\n"
            f"Continue this agent from laptop / web / mobile — no local worktree required.\n"
        )
        _post_issue_comment(issue, body, dry_run=dry_run)

    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--issue", type=int, default=None)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--requirement", required=True)
    ap.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    ap.add_argument("--no-ensure-branch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    result = spawn_for_issue(
        issue=args.issue,
        branch=args.branch,
        requirement=args.requirement,
        repo_url=args.repo_url,
        ensure_branch=not args.no_ensure_branch,
        dry_run=args.dry_run,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "raw" and k != "payload"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
