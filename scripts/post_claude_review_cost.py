#!/usr/bin/env python3
"""Parse claude-code-action execution output and post API cost stats on a PR.

Reads the JSON array written to RUNNER_TEMP/claude-execution-output.json (exposed
as the action output ``execution_file``). Posts a short PR comment with model,
turns, token counts, and reported/estimated USD cost.

Usage (CI):
    python3 scripts/post_claude_review_cost.py \\
        --pr-number "$PR" \\
        --execution-file "$EXECUTION_FILE" \\
        --default-model claude-sonnet-4-6 \\
        --workflow-run-url "$RUN_URL"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# USD per million tokens (Anthropic API list, 2026-05). Used only when the
# execution file lacks total_cost_usd.
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


def _usage_block(msg: dict[str, Any]) -> dict[str, int]:
    """Extract a usage dict from an SDK or Turn-shaped message."""
    usage = msg.get("usage")
    if not usage and msg.get("message"):
        usage = (msg["message"] or {}).get("usage")
    if not isinstance(usage, dict):
        return {}
    return {
        k: int(usage.get(k) or 0)
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    }


def parse_execution(messages: list[Any]) -> dict[str, Any]:
    """Aggregate stats from claude-code-action execution JSON."""
    model = "unknown"
    num_turns: int | None = None
    total_cost_usd: float | None = None
    duration_ms: int | None = None
    conclusion: str | None = None

    input_tokens = 0
    output_tokens = 0
    cache_creation = 0
    cache_read = 0
    assistant_turns = 0

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        mtype = msg.get("type")
        if mtype == "system" and msg.get("subtype") == "init":
            model = str(msg.get("model") or model)
        elif mtype == "assistant":
            assistant_turns += 1
            u = _usage_block(msg)
            input_tokens += u.get("input_tokens", 0)
            output_tokens += u.get("output_tokens", 0)
            cache_creation += u.get("cache_creation_input_tokens", 0)
            cache_read += u.get("cache_read_input_tokens", 0)
        elif mtype == "result":
            num_turns = msg.get("num_turns", num_turns)
            if msg.get("total_cost_usd") is not None:
                total_cost_usd = float(msg["total_cost_usd"])
            if msg.get("cost_usd") is not None and total_cost_usd is None:
                total_cost_usd = float(msg["cost_usd"])
            duration_ms = msg.get("duration_ms", duration_ms)
            conclusion = str(msg.get("subtype") or conclusion)
            u = _usage_block(msg)
            if u.get("input_tokens") or u.get("output_tokens"):
                input_tokens = u.get("input_tokens", input_tokens)
                output_tokens = u.get("output_tokens", output_tokens)
                cache_creation = u.get("cache_creation_input_tokens", cache_creation)
                cache_read = u.get("cache_read_input_tokens", cache_read)

    if num_turns is None:
        num_turns = assistant_turns

    billable_input = input_tokens + cache_creation + cache_read
    return {
        "model": model,
        "num_turns": num_turns,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "billable_input_tokens": billable_input,
        "total_cost_usd": total_cost_usd,
        "duration_ms": duration_ms,
        "conclusion": conclusion,
    }


def estimate_cost_usd(model: str, billable_input: int, output_tokens: int) -> float | None:
    """Estimate USD when execution file has no total_cost_usd."""
    key = model.lower()
    for name, (in_p, out_p) in _PRICE_PER_MTOK.items():
        if name in key or key in name:
            return (billable_input / 1_000_000) * in_p + (output_tokens / 1_000_000) * out_p
    return None


def format_comment(
    *,
    pr_number: int,
    stats: dict[str, Any],
    default_model: str,
    workflow_run_url: str | None,
    execution_missing: bool,
) -> str:
    model = stats.get("model") if stats.get("model") != "unknown" else default_model
    lines = [
        "### Claude review — API cost",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Model | `{model}` |",
        f"| Turns | {stats.get('num_turns', '—')} |",
        f"| Input tokens | {stats.get('input_tokens', 0):,} |",
        f"| Output tokens | {stats.get('output_tokens', 0):,} |",
    ]
    if stats.get("cache_read_input_tokens"):
        lines.append(f"| Cache read tokens | {stats['cache_read_input_tokens']:,} |")
    if stats.get("cache_creation_input_tokens"):
        lines.append(f"| Cache write tokens | {stats['cache_creation_input_tokens']:,} |")
    if stats.get("billable_input_tokens") != stats.get("input_tokens"):
        lines.append(f"| Billable input (incl. cache) | {stats['billable_input_tokens']:,} |")

    cost = stats.get("total_cost_usd")
    est = None
    if cost is not None:
        lines.append(f"| **Reported cost** | **${cost:.4f}** |")
    else:
        est = estimate_cost_usd(
            model,
            int(stats.get("billable_input_tokens") or 0),
            int(stats.get("output_tokens") or 0),
        )
        if est is not None:
            lines.append(f"| **Estimated cost** | **~${est:.4f}** (no `total_cost_usd` in log) |")
        else:
            lines.append("| **Cost** | _(not reported — unknown model pricing)_ |")

    if stats.get("duration_ms") is not None:
        lines.append(f"| Duration | {stats['duration_ms'] / 1000:.1f}s |")
    if stats.get("conclusion"):
        lines.append(f"| Run result | `{stats['conclusion']}` |")

    lines.append("")
    if execution_missing:
        lines.append(
            "_No execution file was produced (review skipped, failed early, or workflow "
            "validation mismatch). No token usage to report._"
        )
    else:
        lines.append(
            "_Source: `claude-code-action` `execution_file` (`total_cost_usd` when present). "
            "Output-token counts in session logs can under-report; trust **Reported cost**._"
        )
    if workflow_run_url:
        lines.append(f"\n[Workflow run]({workflow_run_url})")
    return "\n".join(lines)


def post_pr_comment(repo: str, pr_number: int, body: str) -> None:
    payload = json.dumps({"body": body}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {_github_token()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        raise RuntimeError(f"GitHub comment failed HTTP {e.code}: {err}") from None


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    out = subprocess.check_output(
        ["gh", "auth", "token"],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()
    return out


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pr-number", type=int, required=True)
    cli.add_argument("--execution-file", default="")
    cli.add_argument("--default-model", default="claude-sonnet-4-6")
    cli.add_argument("--workflow-run-url", default="")
    cli.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args(argv)

    execution_missing = not args.execution_file or not Path(args.execution_file).is_file()
    stats: dict[str, Any] = {
        "model": "unknown",
        "num_turns": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "billable_input_tokens": 0,
        "total_cost_usd": None,
    }
    if not execution_missing:
        raw = json.loads(Path(args.execution_file).read_text())
        if not isinstance(raw, list):
            raise SystemExit("execution file must be a JSON array")
        stats = parse_execution(raw)

    body = format_comment(
        pr_number=args.pr_number,
        stats=stats,
        default_model=args.default_model,
        workflow_run_url=args.workflow_run_url or None,
        execution_missing=execution_missing,
    )
    print(body)
    if args.dry_run:
        return 0
    if not args.repo:
        raise SystemExit("--repo or GITHUB_REPOSITORY required to post comment")
    post_pr_comment(args.repo, args.pr_number, body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
