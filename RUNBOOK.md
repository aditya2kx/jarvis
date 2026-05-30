# BHAGA Operator Runbook

**Status:** Cloud is the sole primary. The laptop / non-cloud flow is retired (2026-05-29).
This runbook is written so the system is fully operable from a **fresh machine with only
GitHub + GCP access** — no dependency on the old laptop, its Keychain, or its launchd jobs.

- **GCP project:** `jarvis-bhaga-prod`
- **Region:** `us-central1`
- **Store:** `palmetto` (Palmetto Superfoods, Austin) — `AK JUICY BOWLS LLC`
- **Repo:** `git@github.com-personal:aditya2kx/jarvis.git` (branch `main`)

---

## 0. Read this first — sheet source of truth

During the cloud migration the job temporarily ran `BHAGA_SHEET_MODE=staging` to route writes to a
parallel set of "staging" sheets while the laptop nightly kept writing the original prod sheets. On
**2026-05-29** the cloud flow became the **sole** primary; on **2026-05-30** the cutover was
**finalized**: the promoted sheet IDs were folded directly into the `google_sheets` (prod) block of
`store-profiles/palmetto.json`, and the staging-mode plumbing was **retired**.

What this means now (the simple, non-confusing reality):

1. **The profile's `google_sheets` block is the single source of truth.** `resolve_sheet_id()`
   returns those IDs in plain prod mode. There is **no** `BHAGA_SHEET_MODE` env var on the job, **no**
   `BHAGA_STAGING_*_SID` env vars, and **no** `google_sheets_staging` block in the profile anymore.
2. **The active production sheets** are `18NH71J…` (Model) plus the three raw sheets listed in §2.
   The old laptop-era sheets (`1Drj9…` etc.) are renamed `[DEPRECATED …]`, frozen, and **no longer
   referenced anywhere in code or config** (git history preserves the old IDs if ever needed).

> **TL;DR:** prod mode, prod IDs, one source of truth. `config.yaml` is not in the cloud image —
> the store-profile JSON is what the job reads.

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

### PRODUCTION (active — written by the cloud job; `google_sheets` block in `store-profiles/palmetto.json`)

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

> The deprecated IDs are **no longer referenced** in `store-profiles/palmetto.json` or anywhere in
> code (they were removed when the promoted IDs were folded into the `google_sheets` prod block on
> 2026-05-30). They are listed here only as a record of the frozen, renamed Drive files. The
> `_assert_not_production_sheet()` guard in `config_loader.py` is now inert (it only fired in
> `BHAGA_SHEET_MODE=staging`, which is retired); the function is left in place harmlessly.

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

### `bhaga-daily-refresh` environment (no secret values shown)

| Env | Value |
|---|---|
| `BHAGA_SECRETS_BACKEND` | `gcp` (use Secret Manager / ADC, not local Keychain) |
| `BHAGA_STATE_BACKEND` | `firestore` |
| `GCP_PROJECT` | `jarvis-bhaga-prod` |
| `STORE` | `palmetto` |
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

---

## 12. Operating rules (how to change BHAGA safely)

1. **Commit → push → deploy. Never run prod against local or unpushed code.** The deployed artifact
   is a container image built by `.github/workflows/deploy.yml` on push to `main` (§9). A local edit
   has **zero** effect on the nightly job until it's pushed and the image redeploys. Verify the deploy
   landed (`gh run watch`) before expecting new behavior in prod.
2. **Tests before push:** `python3 -m pytest agents/bhaga/scripts/ skills/tip_ledger_writer/ core/ cloud/`.
3. **No PII / secrets in git.** Credentials live in Secret Manager (§7). Sheet IDs / emails live in
   the store profile and docs — see the git-hook note below.
4. **Corporate pre-push hook (`--no-verify`).** A machine-global DoorDash pre-push hook scans pushes
   for "potential data leaks" (sheet IDs, email addresses) and can block a push to this **personal**
   repo (`aditya2kx/jarvis`, pushed with personal `aditya.2ky@gmail.com` creds — not DoorDash). It is
   a generic security control, **not** a credential problem. When a push is blocked solely by this
   hook and the diff contains only non-secret config (sheet IDs, the operator's own email),
   `git push --no-verify` is the sanctioned bypass. Do **not** `--no-verify` to push actual secrets.
5. **Keep docs in lock-step (the reason this repo stays portable).** Any behavior change updates the
   matching doc in the **same** change. Targets: pipeline/sheets/secrets/scheduler → this file; a
   script or extension recipe → `agents/bhaga/scripts/README.md`; an invariant → `.cursor/rules/bhaga.md`;
   anything notable → a dated line in `PROGRESS.md`. See `AGENTS.md` § Keeping docs current. A doc
   that lags the code is a bug.

---

## 13. Common tasks

### Force a step to re-run (clear the idempotency marker)

A re-run skips steps already marked done in Firestore `runs/<YYYY-MM-DD>`
(`skills/bhaga_config/state_adapter.py`). To force one step (e.g. re-run reviews after a fix), clear
its marker, then execute the job for that date:

```bash
# View the run doc to see which step markers are set
gcloud firestore documents get \
  "projects/jarvis-bhaga-prod/databases/(default)/documents/runs/YYYY-MM-DD"

# Clear a single step's marker (delete the field) or delete the whole run doc to force everything,
# via the Firestore console (collection `runs`, doc `YYYY-MM-DD`). Then:
gcloud run jobs execute bhaga-daily-refresh \
  --region=us-central1 --update-env-vars REFRESH_DATE=YYYY-MM-DD
```

> Deleting the entire `runs/YYYY-MM-DD` doc forces a full re-run for that date. Writes stay idempotent
> (upsert by natural key), so re-running is safe — it overwrites, never duplicates.

### Rebuild model/history from scratch

Reset `data_window_end` in the Model `config` tab back to `data_window_start`, then run the job for
the target date — the gap window recomputes the full span and re-derives every Model tab from the raw
sheets. (Raw sheets are the source; the model is always reproducible from them.)

### Review pipeline mechanics (debugging "did we miss a review?")

- `process_reviews.py` pulls reviews from ClickUp, then **rebuilds `review_bonus_period`
  unconditionally** every run (the old gate that skipped the rebuild when no local `Earnings*.xlsx`
  was present was removed 2026-05-29, commit `4059604` — that file is never downloaded in the cloud).
- Dedupe / "held-back" logic keys off review identity + a high-water timestamp, so reruns don't
  double-count and late-arriving reviews still land on the next run.
- **First debugging step for a "missing review":** confirm you're reading the **primary** sheet IDs
  from `palmetto.json` (`google_sheets` block). The classic false alarm was a local verification
  script reading the **old deprecated** sheets while the cloud job wrote the **promoted** ones. The
  raw review tab (`bhaga_review_raw`) is the ground truth — check it before concluding anything is
  missing.

### Add a column or a new derived tab

See `agents/bhaga/scripts/README.md` § Extending the model (Recipe A: add a column; Recipe B: new
tab from raw). Schema-backed tabs auto-migrate **additive** header changes; reordering/removing does
not.

### Run the per-PR sandbox e2e (prod-like, zero-OTP)

`agents/bhaga/scripts/sandbox_e2e.py` is the prod-like end-to-end that proves a change without
touching the production workbooks and **without ever calling Square / ADP / Google Reviews or
triggering an OTP**. It provisions four ephemeral sandbox sheets, replays the GCS scrape cache
(read-only), backfills the sandbox raw sheets, builds the sandbox model, asserts the tabs are
populated, prints evidence, then tears the sandbox down.

It runs automatically on every PR via `.github/workflows/sandbox-e2e.yml` (and
`sandbox-teardown.yml` cleans up on PR close). Run it manually with ADC:

```bash
# auto-select the most recent cached window (preferred — always cache-backed):
python3 -m agents.bhaga.scripts.sandbox_e2e --pr-number 0 --auto-window --max-days 2

# or pin an explicit window that exists in the GCS cache:
python3 -m agents.bhaga.scripts.sandbox_e2e --pr-number 0 --start 2026-05-01 --end 2026-05-02

# keep the sandbox sheets for inspection instead of tearing down:
python3 -m agents.bhaga.scripts.sandbox_e2e --pr-number 0 --auto-window --keep
```

**Enabling it in CI (one-time, operator):**
1. Set the repo **variable** `SANDBOX_E2E_ENABLED=true` (the workflows no-op until then, so they never
   red-X a PR before you opt in).
2. The workflows reuse `deploy.yml`'s `WIF_PROVIDER` / `WIF_SERVICE_ACCOUNT` secrets. The e2e service
   account additionally needs **Drive create/delete** (it makes + deletes the `BHAGA-sandbox` sheets)
   and **GCS read** on `bhaga-scrape-cache`. If the deploy SA's Drive quota is tight, point
   `SANDBOX_E2E_SERVICE_ACCOUNT` at a dedicated SA (optionally one that writes into a Shared Drive).
3. Optional: `SANDBOX_E2E_MAX_DAYS` (default `2`) bounds the replay window for cost.

> Reviews (ClickUp) are intentionally **out of scope** for the per-PR e2e (they need a live call). The
> e2e proves the sales / labor / tip / model core. Item-level operations are picked up automatically if
> `backfill_item_lines_from_cache` lands on main.
