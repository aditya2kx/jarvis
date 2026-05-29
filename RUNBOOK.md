# BHAGA Operator Runbook

**Status:** Cloud is the sole primary. The laptop / non-cloud flow is retired (2026-05-29).
This runbook is written so the system is fully operable from a **fresh machine with only
GitHub + GCP access** — no dependency on the old laptop, its Keychain, or its launchd jobs.

- **GCP project:** `jarvis-bhaga-prod`
- **Region:** `us-central1`
- **Store:** `palmetto` (Palmetto Superfoods, Austin) — `AK JUICY BOWLS LLC`
- **Repo:** `git@github.com-personal:aditya2kx/jarvis.git` (branch `main`)

---

## 0. Read this first — the "promote-staging" reality (avoid future confusion)

During the cloud migration, the cloud job was pointed at a set of **staging** sheets while the
laptop nightly kept writing the original prod sheets. On **2026-05-29** we cut over: the cloud
flow is now the **sole** primary, and the sheets it writes were **promoted to production**.

Because of how the cutover was done, there are two facts that look contradictory but are correct:

1. **The job intentionally runs `BHAGA_SHEET_MODE=staging`.** This env var is **routing-only** —
   it tells `core/config_loader.py:resolve_sheet_id()` to use the `BHAGA_STAGING_*_SID` sheet IDs
   instead of the `google_sheets` IDs in `store-profiles/palmetto.json`. It does **NOT** disable,
   skip, or degrade any pipeline step, OTP behavior, or writes. **DO NOT change it to `prod`** —
   doing so would repoint the job at the old, frozen prod sheets.
2. **The sheet internally called "staging" (`18NH71J…`) IS now PRODUCTION.** It has been renamed
   to `BHAGA Model`. The old prod Model (`1Drj9…`) has been renamed
   `[DEPRECATED — superseded by cloud sheet, do not use] BHAGA Model` and is frozen (no longer
   written, no permission/data changes). The same promote/deprecate was applied to the three raw
   sheets.

> **TL;DR:** "staging" is just the env-var name for the active sheet-ID set. The sheets it points
> at are the real production sheets. Leave `BHAGA_SHEET_MODE=staging` on the job.

---

## 1. Architecture overview

```
Scheduler (bhaga-nightly, 21:30 CT)
        │  POST jobs/bhaga-daily-refresh:run  (OIDC)
        ▼
Cloud Run Job  bhaga-daily-refresh  (image: bhaga-orchestrator)
        │  scrapes ADP + Square + ClickUp reviews, builds the model sheet
        │  state in Firestore  runs/<date>;  scrape cache in GCS bhaga-scrape-cache
        │  if a portal needs an OTP → writes Firestore otps/<portal>, then BLOCKS
        ▼
Operator replies OTP / READY in Slack DM  D0B67MW6J02  ("bhaga cloud")
        ▲
        │  Slack Events API  →  POST /slack/events
Cloud Run Service  bhaga-webhook  (image: bhaga-webhook)
        writes the OTP into Firestore otps/<portal>; the job unblocks and resumes.
```

- The webhook **replaces** the old laptop Socket Mode listener. It is **stateless** and the **sole
  OTP/READY path**.
- Deploys are fully automated via **GitHub Actions + Workload Identity Federation** (no static
  service-account keys).

---

## 2. Sheet topology

All sheets live under the **`BHAGA`** Google Drive folder
(`1ko9yx78RPQvp0chaYfKOGNk2xF0vhjfP`) on the **`palmetto`** Google account (`adi@mypalmetto.co`).
The Cloud Run service account is shared on every active sheet (see
`agents/bhaga/scripts/share_sheets_with_sa.py`).

### PRODUCTION (active — written by the cloud job; env-var set `BHAGA_STAGING_*_SID`)

| Role | Title | Spreadsheet ID |
|---|---|---|
| Model (PRIMARY) | `BHAGA Model` | `18NH71JwMOAX6euFugSsSQlJhHPgBghWk09YWnsSuvDk` |
| ADP raw | `BHAGA ADP Raw` | `1sv-zK6Mc_ybPUZrObt0CWmodxIVNYm3ahfZg8WZtLyo` |
| Square raw | `BHAGA Square Raw` | `1X2sCGwJi8YfcM0DAYlDzHBxG3_Du4jLauppfAw_A1rw` |
| Review raw | `BHAGA Review Raw` | `16pkNefCOEcEUlhIU6zH03nEcg5PXmBpJhkHy3aUa-k4` |

**Primary Model URL:** https://docs.google.com/spreadsheets/d/18NH71JwMOAX6euFugSsSQlJhHPgBghWk09YWnsSuvDk/edit

Model tabs: `config`, `daily`, `tip_alloc_daily`, `tip_alloc_period`, `period_summary`,
`review_bonus_period`.

### DEPRECATED / FROZEN (old laptop prod — do not use, not deleted, data preserved)

| Role | Title | Spreadsheet ID |
|---|---|---|
| Model | `[DEPRECATED — superseded by cloud sheet, do not use] BHAGA Model` | `1Drj9nplWcdeRChWQ9fk0dfZQPkQweIuPVL5yqNIDOd0` |
| ADP raw | `[DEPRECATED …] BHAGA ADP Raw` | `1-08EIN6EO72t-ImCKRCf4gbIaVN5cJ1FRVlekccvg6w` |
| Square raw | `[DEPRECATED …] BHAGA Square Raw` | `1q_uP14ZvbxPBLy8HcgK0EmwaQMmIPP1jwTV3xmd6kZU` |
| Review raw | `[DEPRECATED …] BHAGA Review Raw` | `1FRtLNy5Ae-m7TK-Q0-alA62A-F7l0cwRZLj1sUMBfmM` |

> The deprecated IDs are still listed under `google_sheets` in `store-profiles/palmetto.json` and are
> used by `config_loader._load_production_sheet_ids()` as the **block-list** for the
> `_assert_not_production_sheet()` guard — that guard prevents the staging-mode job from ever
> touching them. Keep them in the profile.

---

## 3. Cloud Run units

| Unit | Type | Image (Artifact Registry) | Source |
|---|---|---|---|
| `bhaga-daily-refresh` | Cloud Run **Job** | `…/jarvis-images/bhaga-orchestrator:<git-sha>` (+ `:latest`) | repo root `Dockerfile` |
| `bhaga-webhook` | Cloud Run **Service** | `…/jarvis-images/bhaga-webhook:<git-sha>` (+ `:latest`) | `cloud/webhook/Dockerfile` |

- Registry base: `us-central1-docker.pkg.dev/jarvis-bhaga-prod/jarvis-images`
- **Webhook URL:** https://bhaga-webhook-4yl5izovxq-uc.a.run.app
  - Routes: `POST /slack/events` (Events API), `POST /slack/commands` (`/bhaga refresh <date>`,
    `/bhaga status`), `GET /health`.

### `bhaga-daily-refresh` environment (routing-only, no secret values shown)

| Env | Value |
|---|---|
| `BHAGA_SECRETS_BACKEND` | `gcp` (use Secret Manager / ADC, not local Keychain) |
| `BHAGA_STATE_BACKEND` | `firestore` |
| `BHAGA_SHEET_MODE` | `staging` ← **leave as-is** (routing-only; see §0) |
| `GCP_PROJECT` | `jarvis-bhaga-prod` |
| `STORE` | `palmetto` |
| `BHAGA_STAGING_BHAGA_MODEL_SID` | `18NH71JwMOAX6euFugSsSQlJhHPgBghWk09YWnsSuvDk` |
| `BHAGA_STAGING_BHAGA_ADP_RAW_SID` | `1sv-zK6Mc_ybPUZrObt0CWmodxIVNYm3ahfZg8WZtLyo` |
| `BHAGA_STAGING_BHAGA_SQUARE_RAW_SID` | `1X2sCGwJi8YfcM0DAYlDzHBxG3_Du4jLauppfAw_A1rw` |
| `BHAGA_STAGING_BHAGA_REVIEW_RAW_SID` | `16pkNefCOEcEUlhIU6zH03nEcg5PXmBpJhkHy3aUa-k4` |
| `BHAGA_DM_CHANNEL` | `D0B67MW6J02` |
| `BHAGA_HEADLESS` | `1` |
| `SLACK_BOT_TOKEN` | secret → `slack-bot-token` |
| `CLICKUP_PAT` | secret → `jarvis-clickup-palmetto-pat` |

### `bhaga-webhook` environment

| Env | Value |
|---|---|
| `GCP_PROJECT` | `jarvis-bhaga-prod` |
| `FIRESTORE_DB` | `(default)` |
| `CLOUD_RUN_JOB_NAME` | `projects/jarvis-bhaga-prod/locations/us-central1/jobs/bhaga-daily-refresh` |
| `AGENT_CONFIG_JSON` | see §6 |
| `SLACK_SIGNING_SECRET` | secret → `slack-signing-secret` |
| `SLACK_BOT_TOKEN` | secret → `slack-bot-token` |

---

## 4. Scheduler

| Field | Value |
|---|---|
| Name | `bhaga-nightly` (`--location=us-central1`) |
| Schedule | `30 21 * * *` |
| Time zone | `America/Chicago` (21:30 CT, ~60 min after the 21:00 store close) |
| Target | `POST https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/jarvis-bhaga-prod/jobs/bhaga-daily-refresh:run` (OIDC) |

```bash
# Inspect / pause / resume
gcloud scheduler jobs describe bhaga-nightly --location=us-central1
gcloud scheduler jobs pause   bhaga-nightly --location=us-central1
gcloud scheduler jobs resume  bhaga-nightly --location=us-central1
```

> The `bhaga-daily-diff` scheduler + Cloud Run job have been **retired** (deleted 2026-05-29) and
> the diff build/deploy steps were removed from `.github/workflows/deploy.yml`. `cloud/diff/` source
> remains in the repo but is no longer built or deployed.

---

## 5. State, cache, registry

- **Firestore** (database `(default)`):
  - `runs/<YYYY-MM-DD>` — one document per refresh date; per-step completion + checkpoint state
    (`skills/bhaga_config/state_adapter.py`, collection `runs`).
  - `otps/<portal>` — pending/answered OTP records written by the job (request) and the webhook
    (operator reply). Portals e.g. `adp`, `square`.
- **GCS** bucket `bhaga-scrape-cache` — raw scrape artifacts (XLSX/CSV downloads) keyed by date,
  so a re-run can reuse a scrape instead of re-driving the portal.
- **Artifact Registry** `jarvis-images` (DOCKER, `us-central1`) — orchestrator + webhook images.

---

## 6. AGENT_CONFIG_JSON (webhook routing)

The webhook is a standalone deploy unit and cannot import `core`/`skills`; it learns the
agent→DM-channel mapping purely from the `AGENT_CONFIG_JSON` env var. **Current value:**

```json
{"chitra":{"dm_channel":"D0AP8SKH0HZ"},"chanakya":{"dm_channel":"D0ASA4KEL9K"},"bhaga":{"dm_channel":"D0ATWHSA14J","cloud_dm_channel":"D0B67MW6J02"}}
```

`bhaga.cloud_dm_channel` = `D0B67MW6J02` is the DM where the operator replies OTP/READY for the
**cloud** flow.

Regenerate + reinject (the source of truth is `slack.agents` in `config.yaml`; the script projects
it down to routing-only keys, dropping all tokens):

```bash
gcloud run services update bhaga-webhook \
  --region us-central1 --project jarvis-bhaga-prod \
  --update-env-vars AGENT_CONFIG_JSON="$(python3 scripts/build_agent_config.py)"
```

> Note: `deploy.yml` only updates the **image**, so manually-set env vars (incl. `AGENT_CONFIG_JSON`)
> persist across image redeploys. They are only changed by an explicit `gcloud run … update-env-vars`.

---

## 7. Secret inventory (Secret Manager — NAMES only, never values)

| Secret name | Used by | Purpose |
|---|---|---|
| `slack-bot-token` | refresh job, webhook | Slack bot token — post OTP prompts / notifications, verify Events API |
| `slack-signing-secret` | webhook | Verify Slack request signatures on `/slack/events` + `/slack/commands` |
| `slack_bhaga_bot` | (bhaga bot) | BHAGA Slack bot token |
| `slack_bhaga_app` | (bhaga app) | BHAGA Slack app-level token (Socket Mode era; kept for reference) |
| `slack_bhaga_cloud_bot` | cloud bhaga | Slack bot token for the cloud bhaga identity |
| `adp_palmetto_login` | refresh job (ADP scrape) | ADP RUN username + password (timecards / earnings) |
| `square_palmetto_login` | refresh job (Square scrape) | Square dashboard login (transactions report) |
| `google_palmetto` | refresh job (Sheets/Drive) | Google OAuth creds for the `palmetto` account |
| `jarvis-clickup-palmetto-pat` | refresh job (`CLICKUP_PAT`) | ClickUp PAT — read review channel |
| `clickup_palmetto_pat` | (legacy ClickUp PAT) | ClickUp PAT (older handle) |
| `clickup` | (legacy ClickUp) | Legacy ClickUp credential |

Manual rotation (add a new version; the job/service read `:latest`):

```bash
gcloud secrets versions add <name> --data-file=- --project jarvis-bhaga-prod
# (paste the new secret value on stdin, then Ctrl-D)
```

> **CRITICAL — credential custody:** Secret Manager holds the *deployed copy*, but the **underlying
> ADP / Square / Google / ClickUp / Slack credentials must be retained in a password manager that is
> INDEPENDENT of this laptop's macOS Keychain.** Once the laptop is decommissioned, the Keychain is
> gone; without an independent copy you cannot rotate a leaked/expired secret or recover a portal
> login. Verify each of the above is recorded in the password manager BEFORE decommissioning.

---

## 8. OTP / 2FA path (cloud is the sole path)

1. The nightly job hits a portal (ADP/Square) that challenges for an OTP.
2. The job writes `otps/<portal>` in Firestore and **blocks** (checkpoint-and-resume).
3. The Slack bot DMs the operator in **`D0B67MW6J02`** ("bhaga cloud") asking for the code (or a
   `READY` handshake before login).
4. The operator replies in that DM. Slack delivers the event to `POST /slack/events` on the webhook.
5. The webhook validates the signature, writes the code into `otps/<portal>`, and the job unblocks.

There is **no laptop listener** anymore. If OTPs are not being delivered, debug the **webhook**
(logs below) and the Slack app's Events API subscription, not any local process.

---

## 9. Deploy (GitHub Actions + WIF)

- Workflow: `.github/workflows/deploy.yml`, triggered on **push to `main`** (and manual
  `workflow_dispatch` with an optional `rollback_sha`).
- Auth: Workload Identity Federation via repo secrets `WIF_PROVIDER` and `WIF_SERVICE_ACCOUNT`
  (no static keys). 
- Steps: build orchestrator + webhook images → push (`:<git-sha>` and `:latest`) → `gcloud run jobs
  update bhaga-daily-refresh` + `gcloud run services update bhaga-webhook` to the new SHA.
- **Rollback:** `gh workflow run deploy.yml -f rollback_sha=<good-sha>` (re-points both units to a
  prior image SHA; skips the normal deploy steps).

```bash
# Watch the latest deploy
gh run list --workflow=deploy.yml --limit 5
gh run watch <run-id>
```

---

## 10. Run / debug recipes

```bash
# Manual one-off refresh for a specific date (CT). The job reads REFRESH_DATE.
gcloud run jobs execute bhaga-daily-refresh \
  --region=us-central1 --update-env-vars REFRESH_DATE=YYYY-MM-DD

# Tail the most recent job execution logs
gcloud run jobs executions list --job=bhaga-daily-refresh --region=us-central1 --limit=5
gcloud logging read \
  'resource.type=cloud_run_job AND resource.labels.job_name=bhaga-daily-refresh' \
  --project=jarvis-bhaga-prod --limit=200 --freshness=1d

# Webhook (OTP path) logs
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name=bhaga-webhook' \
  --project=jarvis-bhaga-prod --limit=200 --freshness=1d

# Inspect run state + pending OTP in Firestore
gcloud firestore documents get \
  "projects/jarvis-bhaga-prod/databases/(default)/documents/runs/YYYY-MM-DD"
# (or use the Firestore console — collections: runs, otps)

# Inspect the scrape cache for a date
gcloud storage ls gs://bhaga-scrape-cache/

# Health check the webhook
curl -fsS https://bhaga-webhook-4yl5izovxq-uc.a.run.app/health
```

---

## 11. Laptop decommission checklist

The laptop / non-cloud flow has been retired (2026-05-29):
- launchd jobs `com.aditya.jarvis.slack-listener` and `com.aditya.bhaga.poll-commands` booted out.
- All three plists moved to `~/Library/LaunchAgents/disabled-bhaga-<YYYYMMDD>/` (reversible).
- The manual BHAGA Slack listener process (`listener.py --agent bhaga`) was SIGTERM'd.

Before wiping the laptop, confirm ALL of the following are captured online / off-laptop:

- [ ] **Credentials in an independent password manager** (ADP, Square, Google `palmetto`, ClickUp,
      all Slack tokens) — NOT just the macOS Keychain. See §7.
- [ ] You can authenticate `gcloud` from a fresh machine and see project `jarvis-bhaga-prod`.
- [ ] You have GitHub access to `aditya2kx/jarvis` (push to `main` triggers deploy).
- [ ] WIF secrets (`WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`) are configured in the repo (they are; no
      laptop dependency).
- [ ] `bhaga-nightly` scheduler is `ENABLED` and the webhook `/health` returns OK.
- [ ] One successful nightly (or manual `gcloud run jobs execute`) has written the primary Model
      sheet end-to-end with the OTP round-trip exercised via DM `D0B67MW6J02`.
- [ ] (Optional) The CHITRA/other-agent laptop listeners — note that the shared
      `com.aditya.jarvis.slack-listener` supervisor was retired, so non-BHAGA agents that relied on
      the laptop listener will no longer auto-start. Migrate or accept as needed.

Only after every box is checked is the laptop safe to decommission.
