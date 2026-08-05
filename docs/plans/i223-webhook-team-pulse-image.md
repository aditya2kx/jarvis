# Fix webhook team-pulse image (Issue #223)

Evidence tier: sandbox-e2e  
scenario: dry-run POST /team-pulse on prod `bhaga-webhook` after deploy

## Context

PR #222 shipped `POST /team-pulse` in `cloud/webhook/handler.py` (~1870) calling
`agents.bhaga.scripts.team_pulse.run_team_pulse`. Prod returned
`{"error":"No module named 'agents'"}` because `cloud/webhook/Dockerfile` only
`COPY`ed `handler.py` + `skills/plaid_api`.

## Milestone 1 — Image contents (Composer)

| Path | Change |
|---|---|
| `cloud/webhook/Dockerfile` lines 9–20 | After plaid_api: `COPY agents/bhaga/scripts`, `skills/clickup_chat`, `core` |

```dockerfile
COPY agents/bhaga/scripts ./agents/bhaga/scripts
COPY skills/clickup_chat ./skills/clickup_chat
COPY core ./core
```

Verify: `docker build -f cloud/webhook/Dockerfile .` includes `/app/agents/bhaga/scripts/team_pulse.py`.

## Milestone 2 — Deploy secrets/env (Composer)

| Path | Change |
|---|---|
| `.github/workflows/deploy.yml` ~91–99 (`Deploy webhook`) | Add `BHAGA_DATASTORE=bigquery`; `--update-secrets …,CLICKUP_PAT=jarvis-clickup-palmetto-pat:latest` |

```bash
gcloud run services update bhaga-webhook \
  --update-env-vars PLAID_ENV=production,PLAID_DEFAULT_STORE=palmetto,BHAGA_DATASTORE=bigquery \
  --update-secrets PLAID_CLIENT_ID=plaid_client_id:latest,PLAID_SECRET=plaid_secret:latest,CLICKUP_PAT=jarvis-clickup-palmetto-pat:latest
```

Verify: describe service shows `CLICKUP_PAT` secret ref + `BHAGA_DATASTORE`.

## Milestone 3 — Docs + live dry-run (Sonnet)

| Path | Change |
|---|---|
| `RUNBOOK.md` § Team pulse | Note image + secret requirements |
| `PROGRESS.md` | Dated #223 entry |

```bash
TOKEN=$(gcloud secrets versions access latest --secret=sandbox-trigger-token --project=jarvis-bhaga-prod)
curl -sS -X POST https://bhaga-webhook-4yl5izovxq-uc.a.run.app/team-pulse \
  -H "X-Team-Pulse-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"dry_run":true}'
# expect: status dry_run / composed markdown — NOT "No module named 'agents'"
```

## Per-scenario evidence

| # | Scenario | Pass |
|---|---|---|
| E1 | Happy dry-run | curl returns dry_run body with leaderboard |
| E2 | Auth fail | wrong token → 401/403 |
| E3 | Legacy routes | Slack/Plaid paths still healthy (handler tests) |
| E4 | Docs | RUNBOOK + PROGRESS updated |

## Invariants

- Idempotent posts unchanged (`automation_posts` natural key)
- Integer cents / America/Chicago in team_pulse compose
- Sandbox isolation: DM-first default untouched
- Additive image COPY only — no webhook route signature change

Feature flag: **none** — deploy fix; cannot silently wrong-number payroll.

Docs lock-step: `RUNBOOK.md`, `PROGRESS.md`, `docs/plans/i223-webhook-team-pulse-image.md`.

Branch/PR: `fix/fix-216-webhook-team-pulse-image` → PR → bot push → never self-merge; `--base main`.

Model routing: M1–M2 Composer; M3 Sonnet. One chat per PR.
