# i384 — Operator Console development from anywhere (Cursor mobile, Siri, Grok)

Issue #384 · PR #385 (branch `cursor/console-preview-mode-457d`) · prerequisite PR #386
(`allowed_bots: cursor`, split out per `docs/contributing/review-bot.md` § Bootstrap mode).
Sources: `CONTRIBUTING.md` (dev loop = success criteria, §4 evidence),
`.cursor/rules/bhaga-principles.mdc` (read-only prod, sandbox isolation),
`docs/contributing/gcp-access.md` (no ADC on Cloud Agents, never a JSON key),
`docs/contributing/review-bot.md`, `.cursor/rules/pr-workflow.mdc`.

Evidence tier: unit-only (waiver: no BHAGA pipeline code changes — the live proof is CI-run checkpoints against prod GCP/GitHub listed per milestone, not a sandbox scenario)

## Scope and flag decision

Feature-flag decision: no new runtime flag on prod paths. Every new path is additive and
off-by-default: `CONSOLE_PREVIEW=1` only on separate `operator-console-pr<N>` services
(`operator-console-deploy.yml` untouched); Cloud Agent intake only via the new `/jarvis-cloud-task`
command. **Mac flow must not break:** `new_requirement.py`, `operator-console-deploy.yml` and the
`intake-signal` job are byte-identical to `main`; `detect_surface()` returns `mac_local` on darwin
even with `CURSOR_AGENT=1` (Cursor desktop agent terminals set it).
Can it silently produce wrong numbers? No — previews are read-only and the rest is dev tooling.
Out of scope: OAuth for the gateway (path-token instead), Mac-only `user-playwright` provisioning.

## Invariants preserved

- Read-only prod from previews: `apps/operator-console/middleware.ts` line 16 returns 403 for every
  non-GET/HEAD when `isConsolePreview()`; `app/accounting/page.tsx` skips `dedupePlaidTransactions`.
- Public repo: no plaintext prod data in logs/comments/artifacts — `scripts/prod_read.py` encrypts
  (RSA-OAEP-wrapped AES-256) to the requester's key; screenshots only `/home` + `/inventory`.
- Prod mutations never unreviewed: `prod-ops.yml` guard refuses unless Environment `prod` has a
  required reviewer (GitHub auto-creates referenced environments unprotected).
- Idempotent cost rows: `cloud_agent.record_cost` keys the build row on the agent's `createdAt`
  (cumulative usage → each push replaces the same row, no duplicate cost).
- No hardcoded secrets; identity via WIF in CI only (no JSON keys). Integer cents / America/Chicago
  untouched (no money or date math changes). Never retry side effects: gateway `/task` is one
  issue + one comment, no retry loop.

## M1 — Cloud Agent parity (Sonnet)

`scripts/gcp_access_probe.py:50` `detect_surface(env=None, platform=None) -> str` (`ci` /
`cursor_cloud` / `mac_local` / `other`), reported as `dev_surface=` only; `.cursor/environment.json`; `.cursor/rules/pr-workflow.mdc` § Cursor Cloud Agent surface.

**Verify:** `python3 -m pytest -q scripts/test_gcp_access_probe.py scripts/test_new_requirement.py`
— pass criterion: all green; env build `bld-20261011-6de31ea8` SUCCEEDED.

## M2 — Console preview (Opus)

`lib/config/preview.ts`, middleware gate, Preview `Badge` (shadcn outline, amber, lucide `Eye`,
matches Topbar chips — visual polish is an acceptance bar), `operator-console-ci.yml`,
`operator-console-preview.yml` (deploy / screenshots / teardown / sweep), `scripts/sticky_pr_comment.py`.

**Verify:** `(cd apps/operator-console && npm test && npm run build)`; `python3 -m pytest -q scripts/test_sticky_pr_comment.py`
— pass criterion: preview URL 302s to accounts.google.com unauthenticated; screenshot comment renders.

## M3 — Prod access + cost (Opus)

`scripts/prod_read.py` + `prod-read.yml`; `scripts/prod_ops.py` + `prod-ops.yml`;
`scripts/cloud_agent.py` (`launch`, `record_cost`) + `pr-cost-gate.yml` step + `jarvis-dev-signals.yml`
new `cloud-intake` job (`/jarvis-cloud-task`); `pr_cost_ledger.py` tiers `opus-5-5` / `sonnet-5-5`.

**Verify:** `python3 -m pytest -q scripts/test_prod_read.py scripts/test_prod_ops.py scripts/test_cloud_agent.py scripts/test_pr_cost_ledger.py`
— pass criterion: `prod-read` self-test round-trips; cost gate prints `[OK] PR #385 cost record is present`.

## M4 — Siri / Grok gateway (Sonnet)

`cloud/jarvis_dev_gateway/app.py`: `start_task(text) -> dict`, `task_status(limit=5) -> dict`,
routes `/health`, `/task`, `/status`, `/mcp`, `/mcp/<path_token>`; `jarvis-dev-gateway-deploy.yml`.

**Verify:** `python3 -m pytest -q cloud/jarvis_dev_gateway` — pass criterion: 14 passed; PR
`self-test` job smoke (health, 401 unauth, MCP tools/list + task_status) green.

## Per-scenario evidence (PR §4)

| Scenario | Evidence |
|---|---|
| Happy path: preview | live `operator-console-pr385`, IAP 302, screenshots |
| Happy path: prod-read | self-test status + bq round-trip decrypt |
| Happy path: cost | cost-gate row 27,155,114 tokens |
| Failure: unauth preview / gateway | 302 to Google / 401 |
| Failure: prod-ops without protected env | guard refuses (comment) / warns (PR) |
| Failure: write in preview | 403 middleware test |
| Recovery: preview cleanup | teardown on close + nightly sweep (post-merge) |
| Legacy: Mac | `git diff origin/main --stat -- scripts/new_requirement.py .github/workflows/operator-console-deploy.yml` empty; `intake-signal` job unchanged; `test_local_cursor_agent_terminal_on_mac_is_still_mac`; `test_new_requirement.py` 44 pass; prod_read round-trip skips on LibreSSL |

## Docs lock-step

`RUNBOOK.md` (§7 secrets, §17 preview), `docs/WORKFLOW.md` §9, `AGENTS.md` (cloud workers + map),
`docs/contributing/dev-anywhere.md`, `docs/contributing/gcp-access.md`, `docs/contributing/review-bot.md`
(PR #386), `cloud/jarvis_dev_gateway/README.md`, `apps/operator-console/README.md`, `PROGRESS.md`;
`python3 scripts/check_doc_freshness.py --base origin/main --strict`.

## PR mechanics

One branch per PR: #385 carries everything except the `claude-review.yml` line (#386). Cloud Agents act
as `jarvis-agent-bot328`, like the Mac: `scripts/cloud_git_identity.sh` (environment `start`; bot PAT
from Secret Manager via Cursor OIDC, `provision_cloud_agent_identity.sh` once) for commits/pushes, and `cloud-agent-pr.yml` opens the PR as the bot
(`--base main`); ManagePullRequest only updates it, because its create opens under the operator's
account, which the operator cannot approve (Issue #321). Babysit per `pr-workflow.mdc`;
never self-merge — operator merges #386 first, then #385.

## Model routing

M1 Sonnet, M2 Opus (UI + IAP), M3 Opus (security-sensitive), M4 Sonnet — cost playbook; this chat
ran on Opus 5.5 throughout (operator request), one chat for the PR.
