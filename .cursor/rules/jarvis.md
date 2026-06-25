---
description: Jarvis routing card — always-on architecture map, deployment surfaces, routing rules, and cross-agent conventions. Hard Lessons are in jarvis-hard-lessons.md (on-demand).
alwaysApply: true
---

# Jarvis — routing card

## Architecture
```
Jarvis (this workspace)
├── agents/  Domain agents — BHAGA (tip/payroll), CHITRA (tax), CHANAKYA (strategy), AKSHAYA (inventory)
├── skills/  Shared capabilities (slack, sheets, drive, browser, credentials, square, adp, tip-alloc)
└── core/    Shared config/auth
```

## Deployment surfaces (read before any operational step)
- **BHAGA: cloud-primary** (GCP Cloud Run; laptop retired 2026-05-29). OTP/READY via Firestore+webhook.
  Operate via **[`RUNBOOK.md`](../../RUNBOOK.md)**. No local Slack-listener or `/tmp` protocol for BHAGA.
- **CHITRA / CHANAKYA / AKSHAYA: local laptop** — Slack-listener + `/tmp/jarvis-*.json` protocol applies.
  See `jarvis-hard-lessons.md` § Session Continuity.

## Routing
1. Tax → CHITRA (`chitra.md`, `chitra-workflows.md`, `chitra-playbook.md`)
2. Product/strategy → CHANAKYA (`chanakya.md`)
3. Inventory/ordering → AKSHAYA (`akshaya.md`)
4. Tip allocation/payroll → BHAGA (`bhaga.md`, `RUNBOOK.md`)
5. Skill request → `skills/<name>/`
6. Cross-agent → coordinate; skills are shared infrastructure

## Consult-first (cite what you used)
Before planning or designing, read the relevant docs and derive proposals from them:
- **[`CONTRIBUTING.md`](../../CONTRIBUTING.md)** — dev loop as success criteria; evidence-definition step
- **[`docs/WORKFLOW.md`](../../docs/WORKFLOW.md)** — end-to-end lifecycle, agent hierarchy, autonomy ladder
- Agent card (glob-scoped Tier-2) + domain spec + RUNBOOK for operational changes

## Core conventions
- **Never push `main` directly** — every change lands via PR (branch → PR → CI → operator merge).
- **Skills ≠ agents.** Skills are generic (HOW); agents are glue (WHAT, WHEN). Reusable logic → `skills/`.
- **Config-driven; no hardcoding.** Sheet IDs, store profiles, pay schedule → `config.yaml` / store-profiles JSON.
- **Use `user-playwright` MCP, not `cursor-ide-browser`.** `cursor-ide-browser` is never a fallback.
- **No PII / secrets in git.** Tokens + personal data → Secret Manager (cloud) or gitignored `config.yaml`.
- **Never reflexively retry when a side effect can fire** (OTP/SMS/email/DM/payment). Check process state first.
- **Leave a breadcrumb on every failure** — greppable one-liner + state sufficient to diagnose on another machine.
- **All GitHub ops as `jarvis-agent-bot328`**, never as `aditya2kx` (except owner-only admin ops).
- **Browser automation → Playwright (`user-playwright`); plan API migration.** Ownership ladder: API → MCP → IaC → Playwright.
- **Prod/runtime on hosted infra** (Cloud Run, GH Actions, BQ). Laptop = build + one-time provisioning only.

## Adding an agent or skill
- New agent: `agents/<name>/` + `knowledge-base/` + `scripts/` + `.cursor/rules/<name>.md` (glob-scoped)
  + Slack identity (`skills/slack_app_provisioning/`) + update AGENTS.md routing table + PROGRESS.md.
- New skill: `skills/<name>/` + direct API calls (no heavy deps) + `README.md` + `config.template.yaml` secrets.

## Hard Lessons
Recurring patterns documented from actual mistakes → `jarvis-hard-lessons.md`.
Read before any multi-step task, debugging session, or when a pattern recurs.
