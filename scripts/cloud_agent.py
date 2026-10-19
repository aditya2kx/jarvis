#!/usr/bin/env python3
"""Cursor Cloud Agents API: launch an agent for a requirement, record its cost.

    # /jarvis-cloud-task <requirement> on an issue (jarvis-dev-signals.yml cloud-intake):
    python3 scripts/cloud_agent.py launch --issue 384 --requirement-file req.txt [--model ID]
    # … on a PR (e.g. `handoff.py out --to cloud`): continue that PR on its own branch
    python3 scripts/cloud_agent.py launch --pr 388 --requirement-file req.txt
    # what cloud-intake runs: launch + (PR) cost line in the body + agent link comment
    python3 scripts/cloud_agent.py intake --number 388 --is-pr --requirement-file req.txt

    # pr-cost-gate.yml, for PRs whose body carries `Cursor-Agent: bc-… model=… [prs=385,386]`:
    python3 scripts/cloud_agent.py record-cost --pr 385 --pr-body-file body.md \
        --title "…" --branch cursor/…

Auth: CURSOR_AGENT_TOKEN (Basic, https://cursor.com/dashboard/api).
API: https://cursor.com/docs/cloud-agent/api/endpoints.md
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

sys.path.insert(0, str(Path(__file__).parent))

API_BASE = "https://api.cursor.com/v1"
REPO_URL = "https://github.com/aditya2kx/jarvis"
# No model in the marker: price as Opus 5.5 so an unlabeled PR is over- not under-counted.
FALLBACK_MODEL = "claude-opus-5-5"
# Cursor's usage API returns 0 tokens for a self-hosted worker (PR #413, bc-a19cf89f).
# The cost gate rejects $0, so record the median build cost of the 20 most recent
# opus PRs with cost_usd > 0 in jarvis_dev, as of 2026-10-19 (middle pair $9.04 and
# $11.41). The row is flagged approximate; a later non-zero reading replaces it.
ZERO_USAGE_BENCHMARK_TOKENS = 23_000_000
ZERO_USAGE_BENCHMARK_COST = 10.22
_MARKER = re.compile(
    r"Cursor-Agent:\s*(bc-[0-9a-fA-F-]{8,})(?:\s+model=([\w.\-]+))?(?:\s+prs=([\d,]+))?"
)
GH_API = "https://api.github.com/repos/aditya2kx/jarvis"


class ApiError(RuntimeError):
    pass


def _token() -> str:
    tok = (os.environ.get("CURSOR_AGENT_TOKEN") or "").strip()
    if not tok:
        raise ApiError("CURSOR_AGENT_TOKEN is not set")
    return tok


def _api(method: str, path: str, body: dict | None = None) -> dict:
    auth = base64.b64encode(f"{_token()}:".encode()).decode()
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise ApiError(f"{method} {path} → HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None


def parse_markers(text: str) -> list[tuple[str, str, list[int]]]:
    return [(m.group(1), m.group(2) or FALLBACK_MODEL, [int(p) for p in (m.group(3) or "").split(",") if p])
            for m in _MARKER.finditer(text or "")]


def parse_marker(text: str) -> tuple[str, str, list[int]] | None:
    found = parse_markers(text)
    return found[0] if found else None


def _gh_pr(pr: int) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GH_TOKEN']}"
    req = urllib.request.Request(f"{GH_API}/pulls/{pr}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, ValueError):
        return {}


def split_shares(sizes: dict[int, int]) -> dict[int, float]:
    """Diff-size-weighted shares; equal split when no size is known."""
    total = sum(sizes.values())
    if total <= 0:
        return {pr: 1 / len(sizes) for pr in sizes}
    return {pr: n / total for pr, n in sizes.items()}


def seed_prompt(requirement: str, issue: int | None) -> str:
    issue_line = f"Tracking issue: #{issue} (put `Closes #{issue}` in the PR body).\n" if issue else ""
    return (
        f"{requirement.strip()}\n\n{issue_line}"
        "You are a Jarvis Cursor Cloud Agent started by `/jarvis-cloud-task`. Follow AGENTS.md, "
        "CONTRIBUTING.md and .cursor/rules (pr-workflow.mdc § Cursor Cloud Agent surface): "
        "jam with the operator before building, push your branch early (`cloud-agent-pr.yml` opens the draft PR as jarvis-agent-bot328 — set its title/body with ManagePullRequest update_pr, never create_pr), put "
        "`Cursor-Agent: <your bc- id> model=<your model id>` in the PR body, "
        "use the Operator Console preview + screenshot comments as UI evidence, "
        "use `/prod-read` PR comments for prod data, and subscribe to the PR's review and CI "
        "events so you keep iterating until it is green.\n"
    )


def continue_prompt(requirement: str, pr: int) -> str:
    return (
        f"{requirement.strip()}\n\n"
        f"You are a Jarvis Cursor Cloud Agent continuing PR #{pr}, started by `/jarvis-cloud-task` on the PR "
        "(usually a handoff from the operator's Mac). You are on the PR's own branch: push there, never a new one. "
        f"First run `bash scripts/cloud_git_identity.sh`, then `python3 scripts/handoff.py in --pr {pr}`: it restores "
        "phase tracking and prints the latest handoff note — that note, the PR body and its review threads are your "
        "context (the earlier chat does not travel). Your `Cursor-Agent:` cost line is already in the PR body. "
        "Follow AGENTS.md and pr-workflow.mdc § Cursor Cloud Agent surface, subscribe to the PR's review and CI "
        "events, and keep iterating until it is green. To hand back: `python3 scripts/handoff.py out --to mac`.\n"
    )


def launch(requirement: str, issue: int | None, model: str | None = None, ref: str = "main",
           pr: int | None = None) -> dict:
    """New work starts from `ref` in plan mode (jam first); `pr` continues that PR on its own branch."""
    repo = {"url": REPO_URL, "prUrl": f"{REPO_URL}/pull/{pr}"} if pr else {"url": REPO_URL, "startingRef": ref}
    title = (requirement.strip().splitlines() or ["(no requirement text)"])[0][:80]
    payload: dict = {
        "prompt": {"text": continue_prompt(requirement, pr) if pr else seed_prompt(requirement, issue)},
        "repos": [repo],
        "autoCreatePR": False,
        "mode": "agent" if pr else "plan",
        "name": (f"PR #{pr} " if pr else f"#{issue} " if issue else "") + title,
    }
    if pr:
        payload["workOnCurrentBranch"] = True
    if model:
        payload["model"] = {"id": model}
    body = _api("POST", "/agents", payload)
    agent = body.get("agent") or {}
    return {"id": agent.get("id", ""), "url": agent.get("url", ""), "run_id": (body.get("run") or {}).get("id", "")}


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def intake(number: int, is_pr: bool, requirement: str, model: str | None, repo: str) -> dict:
    """`/jarvis-cloud-task` comment (jarvis-dev-signals.yml cloud-intake): on an issue start new
    work from main; on a PR continue that PR's branch and add the agent's cost line to its body
    (a Mac-opened PR body is not agent-editable)."""
    out = launch(requirement, None if is_pr else number, model, pr=number if is_pr else None)
    if is_pr:
        body = _gh("pr", "view", str(number), "--repo", repo, "--json", "body", "-q", ".body").rstrip("\n")
        line = f"Cursor-Agent: {out['id']}" + (f" model={model}" if model else "")
        _gh("pr", "edit", str(number), "--repo", repo, "--body", f"{body}\n\n{line}\n")
    else:
        subprocess.run(["gh", "issue", "edit", str(number), "--repo", repo, "--add-label", "jarvis-work"],
                       capture_output=True)
    _gh("issue", "comment", str(number), "--repo", repo, "--body",
        f"**Cursor Cloud Agent** started: {out['url']}\n\n"
        f"Open it from Cursor mobile, web or desktop. <!-- cursor-agent: {out['id']} -->")
    return out


def cost_usd(usage: dict, model: str) -> float:
    import pr_cost_ledger as L

    r = L._MODEL_RATES[L._model_tier(model)]
    return (
        int(usage.get("inputTokens") or 0) * r["input"]
        + int(usage.get("outputTokens") or 0) * r["output"]
        + int(usage.get("cacheReadTokens") or 0) * r["cache_read"]
        + int(usage.get("cacheWriteTokens") or 0) * r["cache_write"]
    ) / 1_000_000


def record_cost(pr: int, pr_body: str, title: str, branch: str) -> str:
    """Record each marked agent's cumulative usage (one line per agent, e.g. after a PR is
    re-opened by a later agent); with `prs=a,b` split it across those PRs by diff size."""
    found = parse_markers(pr_body)
    if not found:
        return "cloud-agent cost: no Cursor-Agent marker in PR body — skipped"
    return "\n".join(_record_agent(pr, agent_id, model, prs, title, branch) for agent_id, model, prs in found)


def _record_agent(pr: int, agent_id: str, model: str, prs: list[int], title: str, branch: str) -> str:
    import pr_cost_ledger as L

    prs = prs if pr in prs else [pr, *prs]
    agent = _api("GET", f"/agents/{agent_id}")
    usage = _api("GET", f"/agents/{agent_id}/usage").get("totalUsage") or {}
    cost = cost_usd(usage, model)
    tokens = int(usage.get("totalTokens") or 0)
    approximate = tokens <= 0 or cost <= 0
    if approximate:
        tokens, cost = ZERO_USAGE_BENCHMARK_TOKENS, ZERO_USAGE_BENCHMARK_COST
    meta = {p: _gh_pr(p) if len(prs) > 1 else {} for p in prs}
    shares = split_shares({p: int(m.get("additions") or 0) + int(m.get("deletions") or 0) for p, m in meta.items()})
    # ts = agent creation time: usage is cumulative, so each push replaces the same row.
    ts = agent.get("createdAt") or agent_id
    parts = []
    for p in prs:
        if p == pr:
            L.set_meta(p, title=title, branch=branch)
        else:
            L.set_meta(p, title=meta[p].get("title"), branch=(meta[p].get("head") or {}).get("ref"))
        L.record_build_session(
            p, ts=ts, tokens=round(tokens * shares[p]), cost_usd=cost * shares[p],
            model=model,
            source="benchmark_estimate" if approximate else "cursor_cloud_agent_api",
            note=(f"{agent_id} APPROXIMATE: usage API returned 0; "
                  f"median opus PR build cost as of 2026-10-19") if approximate else agent_id,
            mark_approximate=approximate,
        )
        parts.append(f"PR #{p} {shares[p]:.0%} ${cost * shares[p]:.2f}")
    tag = " approximate" if approximate else ""
    return (f"cloud-agent cost: {agent_id} model={model} tokens={tokens} "
            f"cost=${cost:.2f}{tag} → " + ", ".join(parts))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    la = sub.add_parser("launch")
    la.add_argument("--issue", type=int)
    la.add_argument("--requirement-file", required=True)
    la.add_argument("--model", help="GET /v1/models id; omit for the account default")
    la.add_argument("--pr", type=int, help="continue this PR on its own branch instead of starting from main")
    it = sub.add_parser("intake", help="/jarvis-cloud-task comment: launch, add cost line (PR), comment the link")
    it.add_argument("--number", type=int, required=True, help="issue or PR number the comment is on")
    it.add_argument("--is-pr", action="store_true")
    it.add_argument("--requirement-file", required=True)
    it.add_argument("--model")
    it.add_argument("--repo", default="aditya2kx/jarvis")
    rc = sub.add_parser("record-cost")
    rc.add_argument("--pr", type=int, required=True)
    rc.add_argument("--pr-body-file", required=True)
    rc.add_argument("--title", required=True)
    rc.add_argument("--branch", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "launch":
            req = Path(a.requirement_file).read_text()
            print(json.dumps(launch(req, a.issue, a.model or None, pr=a.pr)))
        elif a.cmd == "intake":
            out = intake(a.number, a.is_pr, Path(a.requirement_file).read_text(), a.model or None, a.repo)
            print(f"cloud intake: agent {out['id']} for {'PR' if a.is_pr else 'issue'} #{a.number}")
        else:
            print(record_cost(a.pr, Path(a.pr_body_file).read_text(), a.title, a.branch))
    except (ApiError, subprocess.CalledProcessError) as e:
        print(f"cloud_agent: {a.cmd} failed — {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
