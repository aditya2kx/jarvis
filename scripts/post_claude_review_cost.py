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

# USD per million tokens (Anthropic API list, 2026-05): (input, output). Used
# only when the execution file lacks total_cost_usd.
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

# Cache tokens are NOT billed at the base input rate. Anthropic prompt caching:
# a 5-minute cache WRITE costs 1.25× base input; a cache READ costs 0.10× base
# input. Summing cache + input into one "billable input" number and pricing it
# all at the base rate over-states cost 2–3× (cache read dominates volume but is
# the cheapest tier) — that was the old bug.
_CACHE_WRITE_MULT = 1.25
_CACHE_READ_MULT = 0.10


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


def _price_for(model: str) -> tuple[float, float] | None:
    """(input, output) USD/Mtok for a model, or None if pricing is unknown."""
    key = model.lower()
    for name, prices in _PRICE_PER_MTOK.items():
        if name in key or key in name:
            return prices
    return None


def cost_breakdown_usd(model: str, stats: dict[str, Any]) -> dict[str, float] | None:
    """Per-tier USD using list prices, or None if the model isn't priceable.

    Each tier is priced at its own rate (input, output at list; cache write at
    1.25× input; cache read at 0.10× input), so the parts sum to a realistic
    total instead of pricing cached tokens as fresh input.
    """
    prices = _price_for(model)
    if prices is None:
        return None
    in_p, out_p = prices
    input_cost = (int(stats.get("input_tokens") or 0) / 1_000_000) * in_p
    output_cost = (int(stats.get("output_tokens") or 0) / 1_000_000) * out_p
    cache_write_cost = (
        int(stats.get("cache_creation_input_tokens") or 0) / 1_000_000
    ) * in_p * _CACHE_WRITE_MULT
    cache_read_cost = (
        int(stats.get("cache_read_input_tokens") or 0) / 1_000_000
    ) * in_p * _CACHE_READ_MULT
    return {
        "input": input_cost,
        "output": output_cost,
        "cache_write": cache_write_cost,
        "cache_read": cache_read_cost,
        "total": input_cost + output_cost + cache_write_cost + cache_read_cost,
    }


def estimate_cost_usd(model: str, stats: dict[str, Any]) -> float | None:
    """Estimate total USD (cache-tier-aware) when the log lacks total_cost_usd."""
    breakdown = cost_breakdown_usd(model, stats)
    return breakdown["total"] if breakdown is not None else None


def format_comment(
    *,
    pr_number: int,
    stats: dict[str, Any],
    default_model: str,
    workflow_run_url: str | None,
    execution_missing: bool,
    skip_reason: str | None = None,
) -> str:
    if skip_reason == "bootstrap_workflow":
        lines = [
            "### Claude review — API cost",
            "",
            "**Review did not run on this PR.** `anthropics/claude-code-action` only executes when "
            "`.github/workflows/claude-review.yml` is **byte-identical to `main`**. This PR changes "
            "that workflow file (bootstrap) — that is expected, not a broken cost reporter.",
            "",
            "After this PR merges, the next PR gets a real Sonnet review and non-zero token/cost "
            "stats in this comment.",
        ]
        if workflow_run_url:
            lines.append(f"\n[Workflow run]({workflow_run_url})")
        return "\n".join(lines)

    model = stats.get("model") if stats.get("model") != "unknown" else default_model
    # Tokens split by billing tier. "Input (uncached)" is usually tiny because
    # the diff prompt is served from cache — that is expected, not a bug, so we
    # label it explicitly and never collapse the tiers into one "billable" sum.
    lines = [
        "### Claude review — API cost",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Model | `{model}` |",
        f"| Turns | {stats.get('num_turns', '—')} |",
        f"| Input tokens (uncached) | {stats.get('input_tokens', 0):,} |",
        f"| Output tokens | {stats.get('output_tokens', 0):,} |",
    ]
    if stats.get("cache_read_input_tokens"):
        lines.append(f"| Cache read tokens (0.10×) | {stats['cache_read_input_tokens']:,} |")
    if stats.get("cache_creation_input_tokens"):
        lines.append(f"| Cache write tokens (1.25×) | {stats['cache_creation_input_tokens']:,} |")

    cost = stats.get("total_cost_usd")
    breakdown = cost_breakdown_usd(model, stats)
    if cost is not None:
        lines.append(f"| **Reported cost** | **${cost:.4f}** |")
    elif breakdown is not None:
        lines.append(
            f"| **Estimated cost** | **~${breakdown['total']:.4f}** (no `total_cost_usd` in log) |"
        )
    else:
        lines.append("| **Cost** | _(not reported — unknown model pricing)_ |")

    if stats.get("duration_ms") is not None:
        lines.append(f"| Duration | {stats['duration_ms'] / 1000:.1f}s |")
    if stats.get("conclusion"):
        lines.append(f"| Run result | `{stats['conclusion']}` |")

    # Per-tier composition when cache tiers explain the headline cost.
    if breakdown is not None and (
        stats.get("cache_read_input_tokens") or stats.get("cache_creation_input_tokens")
    ):
        lines += [
            "",
            "<details><summary>Cost composition (list prices)</summary>",
            "",
            "| Tier | USD |",
            "| --- | --- |",
            f"| Input (uncached) | ${breakdown['input']:.4f} |",
            f"| Cache write | ${breakdown['cache_write']:.4f} |",
            f"| Cache read | ${breakdown['cache_read']:.4f} |",
            f"| Output | ${breakdown['output']:.4f} |",
            f"| **Sum (list)** | **${breakdown['total']:.4f}** |",
            "",
            "</details>",
        ]

    lines.append("")
    if execution_missing:
        lines.append(
            "_No execution file was produced (review skipped, failed early, or workflow "
            "validation mismatch). No token usage to report._"
        )
    else:
        lines.append(
            "_Source: `claude-code-action` `execution_file`. **Reported cost** (when present) "
            "is authoritative; the composition uses list prices and may differ slightly._"
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
    cli.add_argument(
        "--skip-reason",
        choices=["bootstrap_workflow"],
        default=None,
        help="Why the review did not run (e.g. workflow file differs from main).",
    )
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
        skip_reason=args.skip_reason,
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
