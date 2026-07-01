#!/usr/bin/env python3
"""Doc-freshness checker — keeps docs in lock-step with code.

Maps changed code paths to the docs that are supposed to move with them, and
reports any doc that *should* have been touched but wasn't. Built to be:

  - fast & deterministic (pure git diff + path matching, no network),
  - portable (runs on any machine / cloud agent / CI, no laptop deps),
  - self-maintaining (extend COUPLINGS below when you add a code<->doc link).

This is intentionally a NUDGE, not a gate, by default. It exits 0 and just
prints reminders so it never blocks honest work (test-only changes, refactors,
doc-only commits). Pass --strict to exit non-zero (e.g. for CI) when a coupled
doc is missing from the change set.

Usage:
    # Check uncommitted work (staged + unstaged) — the common pre-commit case
    python3 scripts/check_doc_freshness.py

    # Check a commit range (CI / pre-push): everything since origin/main
    python3 scripts/check_doc_freshness.py --base origin/main

    # Make it fail the build when a coupled doc wasn't updated
    python3 scripts/check_doc_freshness.py --base origin/main --strict
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys

# Each coupling: if any changed path matches a `code` glob, then at least one of
# the `docs` is expected to also be in the change set. `why` is printed to the
# operator/agent so the reminder is actionable. Keep these coarse and obvious;
# add a new entry whenever you create a new code<->doc dependency (the rule in
# .cursor/rules/doc-maintenance.mdc tells agents to do exactly this).
COUPLINGS: list[dict] = [
    {
        "code": ["agents/bhaga/scripts/*.py", "skills/tip_ledger_writer/*.py"],
        "docs": ["agents/bhaga/scripts/README.md"],
        "why": "pipeline/script/data-model change → update the BHAGA scripts code map (incl. 'Extending the model').",
    },
    {
        "code": [
            "agents/bhaga/scripts/ingest_inventory.py",
            "skills/inventory_parse/*.py",
            "core/migrations/027_inventory_closing.sql",
        ],
        "docs": [
            "agents/bhaga/scripts/README.md",
            "RUNBOOK.md",
            ".cursor/rules/bhaga.mdc",
        ],
        "why": "inventory ingest / parser / schema change → update README script map, RUNBOOK, and bhaga.mdc invariants.",
    },
    {
        "code": ["skills/credentials/registry.py"],
        "docs": ["RUNBOOK.md", "AGENTS.md"],
        "why": "credentials registry change (hydrate, new secrets) → update RUNBOOK § local bootstrap + AGENTS.md doc map.",
    },
    {
        "code": [
            "skills/tip_ledger_writer/schema.py",
            "agents/bhaga/scripts/update_model_sheet.py",
            "agents/bhaga/scripts/forecast.py",
            "agents/bhaga/scripts/process_reviews.py",
            "agents/bhaga/scripts/item_operations.py",
            "skills/bhaga_labor/*.py",
            "skills/square_tips/transactions_backend.py",
        ],
        "docs": ["agents/bhaga/knowledge-base/DOMAIN.md"],
        "why": "sheet columns / metrics / domain semantics changed → update the BHAGA domain data dictionary.",
    },
    {
        "code": [
            "cloud/**",
            ".github/workflows/deploy.yml",
            "agents/bhaga/scripts/daily_refresh*.py",
            "agents/bhaga/scripts/otp_gate.py",
        ],
        "docs": ["RUNBOOK.md"],
        "why": "deploy/scheduler/secrets/OTP/orchestration change → update the operator RUNBOOK.",
    },
    {
        "code": [
            "skills/tip_pool_allocation/*.py",
            "agents/bhaga/knowledge-base/store-profiles/*.json",
        ],
        "docs": [".cursor/rules/bhaga.mdc", "RUNBOOK.md"],
        "why": "allocation invariant / sheet source-of-truth change → update BHAGA behavioral spec / RUNBOOK.",
    },
    {
        "code": [
            ".github/workflows/claude-review.yml",
            ".github/claude-review-guidelines.md",
            ".github/pull_request_template.md",
            "scripts/build_claude_review_context.py",
        ],
        "docs": ["CONTRIBUTING.md", "docs/contributing/review-bot.md"],
        "why": "PR process / review bot / template changed → update CONTRIBUTING.md stub + docs/contributing/review-bot.md.",
    },
    {
        "code": ["scripts/pr_triage.py"],
        "docs": ["docs/contributing/review-bot.md", ".cursor/rules/pr-workflow.mdc"],
        "why": "PR triage aggregator changed → update review-bot convergence loop + pr-workflow babysit step.",
    },
    {
        "code": [
            ".github/workflows/ship-emoji-force-merge.yml",
            ".github/workflows/pr-merged-lifecycle.yml",
            "scripts/ship_merge.py",
            "scripts/post_merge_lifecycle.py",
        ],
        "docs": [
            "CONTRIBUTING.md",
            "docs/contributing/enforcement.md",
            "docs/WORKFLOW.md",
        ],
        "why": "ship-emoji / post-merge lifecycle changed → update CONTRIBUTING.md merge section + enforcement.md + WORKFLOW.md.",
    },
    {
        "code": [
            "scripts/cursor_usage.py",
            "scripts/pr_cost_ledger.py",
            "scripts/pr_cost_store.py",
            "scripts/deploy_events.py",
            "scripts/git-hooks/*",
            "scripts/install-git-hooks.sh",
            "grafana/jarvis_dev/**",
            ".github/workflows/pr-cost-gate.yml",
            ".github/workflows/pr-cost-finalize.yml",
            ".github/workflows/grafana-jarvis-dev-sync.yml",
            ".github/workflows/deploy.yml",
        ],
        "docs": ["docs/contributing/cost.md", "RUNBOOK.md"],
        "why": "cost ledger / attribution / BQ store / Grafana / deploy events changed → update docs/contributing/cost.md + RUNBOOK.md.",
    },
    {
        "code": [
            "scripts/new_requirement.py",
            "scripts/start_pr_session.py",
            "scripts/phase_state.py",
            "scripts/lifecycle.py",
            "scripts/verify.py",
            "scripts/requirements_tracker.py",
        ],
        "docs": ["docs/WORKFLOW.md"],
        "why": "lifecycle scripts changed → update docs/WORKFLOW.md (the canonical lifecycle map).",
    },
    {
        "code": [
            ".github/workflows/sandbox-e2e.yml",
            ".github/workflows/sandbox-teardown.yml",
            "agents/bhaga/scripts/sandbox_e2e.py",
            "agents/bhaga/scripts/sandbox_provision.py",
        ],
        "docs": ["RUNBOOK.md", "agents/bhaga/scripts/README.md"],
        "why": "sandbox e2e runner / CI changed → update RUNBOOK §13 + the scripts code map.",
    },
    {
        "code": [
            "skills/_browser_runtime/*.py",
            "skills/bhaga_config/state_adapter.py",
        ],
        "docs": ["RUNBOOK.md", "agents/bhaga/scripts/README.md"],
        "why": "browser-launch runtime (retry/smoke-test) or run-state markers (clear_step recovery) changed → update RUNBOOK retry/recovery + the scripts code map.",
    },
    {
        "code": ["agents/**/*.py", "skills/**/*.py", "cloud/**/*.py", "core/**/*.py"],
        "docs": ["PROGRESS.md"],
        "why": "notable code change → add a dated line to PROGRESS.md (status / decision / blocker).",
    },
    {
        "code": [
            "core/migrations/*.sql",
            "agents/bhaga/grafana/dashboard.json",
        ],
        "docs": ["agents/bhaga/scripts/status.py"],
        "why": (
            "schema migration or Grafana dashboard changed → update the status doctor's "
            "target registry (BQ_TARGETS / GRAFANA_VIEWS / KNOWN_UNCHECKED_GRAFANA_REFS) "
            "and its anti-drift sync tests so the freshness checker stays in sync."
        ),
    },
]

# Files that never *trigger* a doc reminder. Docs (*.md) are excluded because a
# doc change never obligates another doc; tests/build artifacts are noise.
IGNORE_GLOBS = [
    "*.md",       # top-level docs (fnmatch '**/*.md' doesn't match paths without a '/')
    "**/*.md",
    "**/test_*.py",
    "**/*_test.py",
    "**/__pycache__/**",
]


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    ).stdout


def changed_files(base: str | None) -> list[str]:
    if base:
        out = _git(["diff", "--name-only", f"{base}...HEAD"])
        return sorted({line.strip() for line in out.splitlines() if line.strip()})
    # Default (pre-commit case): staged + unstaged + untracked. `git status
    # --porcelain` covers all three; strip the 2-char XY status prefix and any
    # rename arrow ("old -> new" → keep new).
    files: set[str] = set()
    for line in _git(["status", "--porcelain"]).splitlines():
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            files.add(path.strip('"'))
    return sorted(files)


def _matches(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="git ref to diff against (e.g. origin/main). Default: uncommitted work.")
    ap.add_argument("--strict", action="store_true", help="exit non-zero when a coupled doc is missing.")
    args = ap.parse_args()

    all_changed = changed_files(args.base)
    # Two views: docs satisfy a coupling, so the satisfaction check needs the FULL
    # set (incl. *.md). IGNORE_GLOBS only governs what may *trigger* a reminder
    # (a doc change must never obligate another doc).
    changed_set = set(all_changed)
    triggers = [c for c in all_changed if not _matches(c, IGNORE_GLOBS)]
    if not triggers:
        print("doc-freshness: no relevant changed files. ✓")
        return 0

    reminders: list[str] = []

    for rule in COUPLINGS:
        triggering = [c for c in triggers if _matches(c, rule["code"])]
        if not triggering:
            continue
        if any(doc in changed_set for doc in rule["docs"]):
            continue  # at least one coupled doc was updated — satisfied
        reminders.append(
            "  • "
            + rule["why"]
            + "\n    docs: "
            + ", ".join(rule["docs"])
            + "\n    triggered by: "
            + ", ".join(triggering[:5])
            + (" …" if len(triggering) > 5 else "")
        )

    if not reminders:
        print(f"doc-freshness: {len(triggers)} changed file(s); all coupled docs updated. ✓")
        return 0

    print("doc-freshness: some docs may be stale relative to this change:\n")
    print("\n\n".join(reminders))
    print(
        "\nIf the change genuinely needs no doc update, ignore this (it's a nudge)."
        "\nSee .cursor/rules/doc-maintenance.mdc and AGENTS.md § Keeping docs current."
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
