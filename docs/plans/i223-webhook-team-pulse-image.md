# Fix webhook team-pulse image (Issue #223)

Evidence tier: sandbox-e2e
scenario: dry-run POST /team-pulse on prod bhaga-webhook after deploy

## Change
- `cloud/webhook/Dockerfile`: COPY `agents/bhaga/scripts`, `skills/clickup_chat`, `core`
- `.github/workflows/deploy.yml`: mount `CLICKUP_PAT` + `BHAGA_DATASTORE=bigquery`
- RUNBOOK + PROGRESS lock-step

## Evidence
1. Image build includes team_pulse path
2. `curl POST /team-pulse` dry_run returns composed markdown (not `No module named 'agents'`)
3. Console already on #222 SHA with CLICKUP_PAT (IAM granted)

Feature flag: none
Model: Composer/Sonnet
