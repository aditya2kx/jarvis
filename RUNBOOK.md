# BHAGA Operator Runbook

**Status:** Cloud is the sole primary. The laptop / non-cloud flow is retired (2026-05-29).
This runbook is written so the system is fully operable from a **fresh machine with only
GitHub + GCP access** — no dependency on the old laptop, its Keychain, or its launchd jobs.

- **GCP project:** `jarvis-bhaga-prod`
- **Region:** `us-central1`
- **Store:** `palmetto` (Palmetto Superfoods, Austin) — `AK JUICY BOWLS LLC`
- **Repo:** `https://github.com/aditya2kx/jarvis` (branch `main`; agent pushes via `jarvis-agent-bot328` HTTPS, PAT in Keychain)

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
- **BigQuery** (`jarvis-bhaga-prod.bhaga`) is the **single source of truth** for ALL data: scraped raw
  data (Square, ADP, KDS, Reviews), ADP earnings, and operator-editable tunables (`bhaga.store_config`).
  Sheets are a **read-only projection** — written by the pipeline, never authoritative. See §14 for
  BQ + Grafana operations and §15 for the BQ SoT details.
- **GCS** (`bhaga-scrape-cache`) retains **only** trusted-device browser sessions (`_session/`) and
  failure evidence/logs (`<date>/evidence/`). Raw data files are **no longer written to GCS** — BQ
  is the persistent store. The old scrape-file cache (`download_cached_files`) is retired.

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

Model tabs: `config`, `daily`, `labor_daily`, `labor_weekly`, `labor_period`, `tip_alloc_daily`,
`tip_alloc_period`, `period_summary`, `review_bonus_period`, `labor_daily_forecast`,
`item_operations`.

Square raw tabs include `item_lines` (per-item lines; upserted nightly with Item Sales CSV).

### Item-level operations backfill (no extra OTP)

Item Sales CSVs are already cached in GCS `bhaga-scrape-cache` from the nightly Square session. To
populate history without a new scrape, replay the **cloud cache** (not laptop files — see §13):

```bash
# 1. Replay cached items-*.csv from GCS → raw item_lines (default = GCS only)
python3 -m agents.bhaga.scripts.backfill_item_lines_from_cache --store palmetto

# 2. Upsert model item_operations for all dates present in raw
python3 -m agents.bhaga.scripts.update_model_sheet --store palmetto \
  --item-operations-only --all-item-operations
```

The script logs `first_date_covered` / `last_date_covered`. Earliest rows may be later than
`config.data_window_start` if no `items-*.csv` exists in GCS for those dates.

> The script **defaults to GCS only** and does not read laptop `extracted/downloads/`.
> `--local-only` exists for offline tests only — never for prod. See §13 for *where* to run it.

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
| `BHAGA_SESSION_PERSIST` | `1` — persist/restore the Square `storage_state` (trusted device) to/from `gs://bhaga-scrape-cache/_session/square-palmetto.json` so prod stops re-prompting magic link / 2FA every night. Codified in `deploy.yml` so it survives a job recreate. See §13 "Login escalation". |
| `BHAGA_BROWSER_LAUNCH_RETRIES` | _(optional, default `3`)_ headless browser launch attempts on transient crash — see §13 Browser-launch resilience |
| `BHAGA_BROWSER_LAUNCH_BACKOFF_MS` | _(optional, default `1000`)_ base backoff between launch retries (exponential) |

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
- [ ] `jarvis-agent-bot328` PAT is in Keychain (`security find-generic-password -s github-bot-pat -a jarvis-agent-bot328 -w`). This bot is a Write collaborator on the repo and is used for all agent pushes and PRs.
- [ ] You (as `aditya2kx`) have GitHub access to approve and merge PRs — use `gh-adi` alias in terminal. Bot pushes; you approve + merge.
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

1. **Branch → PR → review → merge → deploy. Never push to `main` directly.** Every change lands via a
   PR that gets the automated Claude Opus review + CI; merge to `main` builds the container image
   (`.github/workflows/deploy.yml`, §9). A local edit has **zero** effect on the nightly job until it's
   merged and the image redeploys. Verify the deploy landed (`gh run watch`) before expecting new
   behavior in prod. Full process + PR template + review rubric: `CONTRIBUTING.md`.
2. **Tests before push:** `python3 -m pytest agents/bhaga/scripts/ skills/tip_ledger_writer/ core/ cloud/`.
3. **No PII / secrets in git.** Credentials live in Secret Manager (§7). Sheet IDs / emails live in
   the store profile and docs — see the git-hook note below.
4. **Corporate pre-push hook (`--no-verify`).** A machine-global DoorDash pre-push hook scans pushes
   for "potential data leaks" (sheet IDs, email addresses) and can block a push to this repo
   (pushed as `jarvis-agent-bot328` via HTTPS — not DoorDash). It is a generic security control,
   **not** a credential problem. When a push is blocked solely by this hook and the diff contains
   only non-secret config (sheet IDs, the operator's own email), `git push --no-verify` is the
   sanctioned bypass. Do **not** `--no-verify` to push actual secrets.
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

### Browser-launch resilience (all portals)

Every portal scrape launches Chromium through `skills/_browser_runtime/runtime.py::launch_persistent`.
In the container this can hit a transient `TargetClosedError` — the browser dies on startup (the
classic cause is the tiny 64 MB `/dev/shm`). The runtime now:

- **Retries the launch _setup_** (start driver → launch → context → page) up to
  `BHAGA_BROWSER_LAUNCH_RETRIES` (default 3) with exponential backoff
  (`BHAGA_BROWSER_LAUNCH_BACKOFF_MS`, default 1000 ms), restarting the whole driver each attempt. It
  **never** retries the scrape/OTP body and **never** retries an auth/2FA error (those can't be a launch
  crash and a retry could re-fire an OTP).
- Adds container-stability flags **only headless** (`--disable-dev-shm-usage`, `--no-sandbox`,
  `--disable-gpu`); the laptop headed/real-Chrome anti-bot path is unchanged.
- Leaves a greppable breadcrumb per failed attempt and on recovery: `grep '[runtime] .* chromium launch'`
  in the Cloud Run logs.
- Exposes `browser_healthcheck()` — a pre-flight smoke test (launch + `about:blank`) the orchestrator
  runs before spending an OTP (headless only) so a transient crash heals before the operator's SMS is
  spent. Non-fatal; the real launch has its own retry.

### Model semantic verification + the pipeline halt circuit breaker

After the rebuild, `daily_refresh` verifies the Model **mechanically** (`assert_model_tabs_populated`)
and then **semantically** (`model_semantics.assert_model_semantics`, the same pure checks the per-PR
sandbox e2e runs): per-day tip-pool conservation, the **latest closed period's `adp_paid` is populated**
when a covering ADP Earnings export exists in the GCS cache, and **credited review bonuses survived**
the rebuild. (Mechanical guards alone missed commit 6f87f9c, which left `adp_paid` permanently `N/A`.)

A **semantic** failure is treated as a known-bad regression (it will repeat every night), so it:
1. records the failure + DMs the operator (`verify_model_sheet`),
2. clears the `update_model_sheet` marker so a rerun **rebuilds** (not just re-verifies), and
3. **trips the pipeline halt circuit breaker.**

**Exit codes** (so monitoring can tell the stop reasons apart): `0` = success **or** a clean
OTP-pending wait; `1` = a step/verification failure (the wrapper retries); `EXIT_HALTED` (`3`) = the
breaker is tripped and the run **refused to start** so it can't repeat known-bad output.

The breaker is a GLOBAL flag (Firestore `<collection>/_pipeline_state`, local
`~/.bhaga/state/pipeline_state.json`), NOT keyed by date. While tripped, fresh scheduled runs refuse
and exit `EXIT_HALTED`; an in-flight OTP READY resume passes through (it's completing a handshake, not
a fresh attempt). **To recover:** fix + deploy the regression, then re-run with `--ignore-halt` (or set
`BHAGA_IGNORE_HALT=1`) — **a fully-healthy verified run auto-clears the breaker.** To clear it manually
without a run, use the sanctioned path `state_adapter.clear_pipeline_halt()` (never hand-edit
Firestore). Inspect the current state with `state_adapter.get_pipeline_halt()` (returns the `reason` /
`since` / `refresh_date` that tripped it, or `None` when healthy).

### OTP-portal recovery (auto-invalidate stale downstream markers)

When a previously-failed OTP portal (Square/ADP) succeeds on a later run **while** the downstream
markers (`load_raw_bigquery` / `update_model_sheet` / `process_reviews`) are already `done` from the
prior partial run, those steps would short-circuit and the fresh data would never reach the Model
sheet (`data_window_end` stuck — the 2026-05-31 incident). `daily_refresh` now **always** detects this
and clears those markers (via `state_adapter.clear_step`, the sanctioned path — never a shell `rm`) so
they recompute on the fresh data; the post-condition guard then verifies `data_window_end` advanced.

This is **not** behind a feature flag — it's safe by construction: the trigger is precisely "a portal
produced fresh data *and* a downstream marker is already done" (a prior partial run; on a normal first
run the markers don't exist yet, so nothing is cleared), and the downstream re-run only upserts by
natural key, so it can never duplicate or corrupt rows. The worst case — a forced full re-scrape of an
already-complete date — merely recomputes idempotently.

### Recover a partial-failure date (e.g. the 2026-05-31 Square-launch crash)

Concrete runbook for "an OTP portal crashed on launch, downstream ran on stale data, `data_window_end`
is stuck and bonuses are held back":

1. **Confirm the state.** Read Firestore `runs/2026-05-31`: `square_transactions` will be **absent**
   (it failed) while `load_raw_bigquery` / `update_model_sheet` / `process_reviews` are **present** (they
   ran on stale data). Read the Model `config` tab — `data_window_end` will be stuck at `2026-05-30`.
2. **Announce the OTP.** Square will re-scrape and fire **one SMS** to the operator. Post in the BHAGA
   DM that the rerun is about to fire an OTP **before** triggering it (Operating rule / HL#8).
3. **Re-run the date as a Cloud Run job** (never a laptop). ADP skips its browser/OTP via the GCS
   cache; Square re-scrapes (the one OTP). The recovery is automatic — when Square succeeds, the stale
   `load_raw_bigquery`/`update_model_sheet`/`process_reviews` markers are invalidated so they recompute:
   ```bash
   gcloud run jobs execute bhaga-daily-refresh \
     --region=us-central1 --update-env-vars REFRESH_DATE=2026-05-31
   ```
4. **Verify** (the rerun isn't done until verified): `data_window_end` advanced to `2026-05-31`, the
   master CSV gained the 5/31 Square rows, and the held-back review bonuses (24 on 5/31) released into
   `review_bonus_period`. Re-read the sheets and diff expected vs actual.

> This prod rerun happens **after this PR merges + the image redeploys** (the flag/marker behavior must
> be in the deployed image), with operator involvement for the OTP. It is not part of the PR's CI.

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

### Run a one-off backfill / maintenance script against prod

This is the canonical way to run something that isn't the nightly `daily_refresh` (a backfill, a
re-derive, a data fix) against **prod sheets** — without a laptop and without touching laptop files.

**Golden rule: cloud reads from the cloud.** Prod data = GCS `bhaga-scrape-cache`; secrets = Secret
Manager. Never populate a prod sheet from `extracted/downloads/` or laptop Keychain.
`backfill_item_lines_from_cache.py` defaults to GCS-only; use `--local-only` only in tests.

**Option A — run it as a Cloud Run job (preferred; fully in-cloud).** The image already contains all
code, the prod service account, GCS access, and Secret Manager wiring. Override the container command
on the existing job, execute, then revert the command:

```bash
JOB=bhaga-daily-refresh; REGION=us-central1
# Point the job at the one-off script (entrypoint default is daily_refresh)
gcloud run jobs update "$JOB" --region="$REGION" \
  --command=python3 \
  --args="-m,agents.bhaga.scripts.backfill_item_lines_from_cache,--store,palmetto"
gcloud run jobs execute "$JOB" --region="$REGION"   # watch logs (see §10)
# IMPORTANT: revert so the nightly schedule runs daily_refresh again
gcloud run jobs update "$JOB" --region="$REGION" \
  --command=python3 --args="-m,agents.bhaga.scripts.daily_refresh,--store,palmetto"
```

(The script must already be on `main` and deployed into the image — commit → push → deploy first, §9.)

**Option B — run from an ADC-authenticated shell (Cloud Shell or any machine with `gcloud`).** No
laptop Keychain, no laptop downloads; secrets resolve from Secret Manager:

```bash
export BHAGA_SECRETS_BACKEND=gcp                  # resolve creds from Secret Manager, not Keychain
gcloud auth application-default login             # one-time per environment
python3 -m agents.bhaga.scripts.backfill_item_lines_from_cache --store palmetto
python3 -m agents.bhaga.scripts.update_model_sheet --store palmetto --item-operations-only --all-item-operations
```

**Then verify (don't assume).** Re-read the affected sheet/tab and the script's
`first_date_covered`/`last_date_covered` (or row counts) against what you expected. For model tabs,
spot-check a known date against the raw sheet. A backfill isn't done until it's verified.

### Add a column or a new derived tab

See `agents/bhaga/scripts/README.md` § Extending the model (Recipe A: add a column; Recipe B: new
tab from raw; **Recipe C**: capture a new field source→raw; **Recipe D**: a high-volume tab that
upserts incrementally instead of clear-and-write, like `item_operations`). Schema-backed tabs
auto-migrate **additive** header changes; reordering/removing does not.

### Exempt an employee/shift from the tip pool (training shifts)

Tip-pool exclusions drop a `(employee, date)`'s hours from that day's **tip** denominator only
(labor% unaffected), so the pool redistributes to everyone else. All three sources funnel through the
single `_is_excluded` chokepoint in `update_model_sheet.py` — **no code change is needed to add an
exemption**, only a sheet edit, then a model rebuild. See `README.md` § Extending the model **Recipe
E** for the full table. Quick reference:

- **Permanent** (manager/owner): `excluded_from_tip_pool_and_labor_pct` in `palmetto.json`.
- **Through a date** (bulk "all shifts were training up to X"): add a `config`-tab row
  `training_excluded:Last, First = YYYY-MM-DD` (inclusive).
- **One specific shift**: add a row to the **`training_shifts`** tab (`employee_name | date | note`).
  This tab is **human-owned** (Lindsay/operator keep it current); the pipeline only reads it. Seed/edit
  it programmatically via `tip_ledger_writer.write_training_shifts` (idempotent `(employee,date)`
  upsert; preserves other operator rows) or by hand.

After editing, rebuild: `python3 -m agents.bhaga.scripts.update_model_sheet --store palmetto` (or let
the nightly do it), then confirm `tip_alloc_period` shows $0 for the exempted shift and the pool total
is conserved.

### Run the per-PR sandbox e2e (prod-like, zero-OTP)

`agents/bhaga/scripts/sandbox_e2e.py` is the prod-like end-to-end that proves a change without
touching the production workbooks and **without ever calling Square / ADP / Google Reviews or
triggering an OTP**. It **leases a slot** from a pre-created sheet pool (see below), clears +
re-seeds it, seeds the sandbox raw sheets, builds the sandbox model, asserts the model tabs are
populated, prints evidence, then releases the slot.

**Two seeding sources (`--source`):**

- `prod-raw` (**the per-PR default in CI**): reads the **PROD** raw Square+ADP sheets directly
  (read-prod is sanctioned; reads use the prod sid, never the staging override) and writes the
  windowed rows into the **sandbox** raw sheets (writes are staging-resolved, so the production-sheet
  guard makes a prod write impossible). Pair with `--period last-closed` to cover the most-recent
  **closed** pay period (the boundaries come from the store profile anchor —
  `most_recent_closed_period`, identical to `discover_periods`). A closed period is always complete,
  so the verify is **stricter**: the period-grain tabs (`labor_period`, `period_summary`,
  `tip_alloc_period`) MUST populate and the **tip pool is checked for per-day conservation**
  (`assert_tip_pool_conserved` — allocations sum to that day's pool, cent-exact). It also
  **mirrors the human-owned prod `training_shifts` overlay** into the sandbox model
  (`seed_sandbox_training_shifts_from_prod`, read-prod/write-sandbox) and **verifies the
  exemptions actually bite** (`assert_exemptions_applied`): every worked training shift is
  dropped from `tip_alloc_daily`, the day's pool redistributes to the remaining staff, a
  whole-period-exempt employee earns $0 over the period while a partially-exempt employee keeps
  their non-exempt earnings (with the exempt-day hours removed from the denominator), and the
  period total conserves. So a future PR that breaks the overlay fails the gate, not just one
  that breaks conservation.
- `gcs-replay` (local/legacy): replays the GCS scrape cache (read-only), re-parses it via
  `backfill_from_downloads`, and uses the lenient small-window verify. Use with `--auto-window` for a
  fast local smoke.

**Why a pool (not create-per-PR):** the Cloud Run service account can *edit* sheets shared with it
but cannot *create* Drive files on a consumer Google account. The operator creates the pool once as
a real user; CI only clears/writes/releases.

**One-time pool setup (operator, user creds — not the SA):**

```bash
# Creates 3 slots × 4 workbooks in Drive, shares each with the SA, writes sandbox_pool.json
python3 -m agents.bhaga.scripts.sandbox_provision --store palmetto --action create-pool --slots 3
git add agents/bhaga/scripts/sandbox_pool.json && git commit -m "chore: register sandbox sheet pool"
```

Slot selection in CI: Firestore transaction leases a free slot (up to 3 concurrent PRs). Locally,
`pr_number % num_slots` is used (single-caller).

It runs automatically on every PR via `.github/workflows/sandbox-e2e.yml` (and
`sandbox-teardown.yml` releases the lease on PR close). The workflow also has a **fast no-op on
`push` to `main`** so the job name **Sandbox e2e** appears in GitHub branch-protection settings
(full e2e still runs only on `pull_request` when `SANDBOX_E2E_ENABLED=true`). See
`CONTRIBUTING.md` § Enabling enforcement.

**Cost ledger rides in your own commits:** the per-PR cost ledger (`metrics/pr_cost/`) is kept current
by the `pre-commit` hook (`bash scripts/install-git-hooks.sh` once per clone), which `sync`s and
auto-stages it into your commits. CI does **not** push a cost commit — `pr-cost-gate.yml` is a pure
validator. So every CI run is on real code and there are no automatic `chore(cost):` commits to skip.

Run it manually with ADC + palmetto OAuth:

```bash
gcloud auth application-default login   # aditya.2ky@gmail.com — prod read + GCS read
# what CI runs: read prod raw for the last closed pay period, verify in sandbox:
python3 -m agents.bhaga.scripts.sandbox_e2e --pr-number 0 --source prod-raw --period last-closed

# legacy GCS-replay smoke (auto-select the most recent cached window):
python3 -m agents.bhaga.scripts.sandbox_e2e --pr-number 0 --auto-window --max-days 2

# keep the leased slot uncleared after the run (debugging):
python3 -m agents.bhaga.scripts.sandbox_e2e --pr-number 0 --auto-window --keep
```

**Enabling it in CI (one-time, operator):**
1. Set the repo **variable** `SANDBOX_E2E_ENABLED=true`.
2. Enable **Google Drive API** on project `jarvis-bhaga-prod` (Sheets API alone is not enough for
   pool folder lookup). The SA needs **Sheets read/write** on the pre-shared pool sheets and **GCS
   read** on `bhaga-scrape-cache` — not Drive create/delete.
3. Workflows reuse `WIF_PROVIDER` / `WIF_SERVICE_ACCOUNT`. CI sets `BHAGA_STATE_BACKEND=firestore`
   for slot leasing.
4. Optional: `SANDBOX_E2E_MAX_DAYS` (default `2`) bounds the replay window for cost.

> The PR that *introduces* this infrastructure can't run the live e2e on itself (the workflow is dormant
> until `SANDBOX_E2E_ENABLED=true`). The **first feature PR that lands after you flip the flag** is the
> real live-validation of the sandbox harness — link its evidence comment back to the bootstrap PR.

> Reviews (ClickUp) are intentionally **out of scope** for the per-PR e2e (they need a live call). The
> e2e proves the sales / labor / tip / model core. Item-level operations are picked up automatically if
> `backfill_item_lines_from_cache` lands on main.

### Run a LIVE sandbox run (real scrape + OTP, unmerged PR code)

The replay e2e above can't exercise a live browser, so it can't reproduce or prove a fix for
**selector drift** (e.g. the 2026-05-31 item-sales "date picker not found" incident). For that, use
`agents/bhaga/scripts/sandbox_live_run.py` via **`.github/workflows/sandbox-live-run.yml`**
(`workflow_dispatch`). It builds the current ref's orchestrator image, deploys it to the
**`bhaga-sandbox-refresh`** Cloud Run job, and runs the **real** Square/ADP pipeline for a chosen
`REFRESH_DATE`.

**Sandbox isolation is enforced (read prod, never write prod).** The job runs with
`BHAGA_SHEET_MODE=staging` (leased pool slot), `BHAGA_GCS_CACHE_WRITE_BUCKET=bhaga-scrape-cache-sandbox`
(reads still come from the prod cache), `BHAGA_BQ_DATASET=bhaga_sandbox` (BQ writes divert to the
isolated dataset — never prod `bhaga`), and `BHAGA_FIRESTORE_COLLECTION=sandbox_runs`. The script runs
an **isolation pre-flight** (`assert_sandbox_isolation`) that fails *before* any deploy/execute if an
override is missing or points at a prod source — backstopped by the runtime guards in
`config_loader`, `gcs_cache`, `datastore` (BQ), and `state_adapter`.

**Scrape-from-source backfill (`--fresh-scrape`).** A windowed backfill that must populate
`bhaga_sandbox` from the **actual upstream portals** (not prod GCS cache, not Sheets) passes
`--fresh-scrape`, which also points the cache READ bucket at the empty sandbox bucket so no cached
scrape file can shortcut the browser. Create the dataset + tables first with
`BHAGA_DATASTORE=bigquery BHAGA_BQ_DATASET=bhaga_sandbox python3 -c "from core import datastore; datastore.ensure_schema()"`.

**OTP routing.** The run uses the **same prod BHAGA cloud Slack bot**, but the prompt is **labeled**
`:test_tube: *[SANDBOX · PR#… …]*` (via `BHAGA_RUN_ENV`/`BHAGA_RUN_LABEL`) and its pending-OTP
checkpoint (in `sandbox_runs`) carries routing metadata (`env`, `run_label`, `target_job`). The
webhook scans `sandbox_runs` **first** (sandbox precedence), so the operator's READY/code reply
resumes the **sandbox** job — never the nightly — even if a prod run is awaiting OTP at the same time.

Runs are organized as a **named scenario suite** (`agents/bhaga/scripts/sandbox_scenarios.py`,
e.g. `item-sales-live`, `full-live`) so you control **what** runs and **when**, with three selectors:

```bash
# 1. Committed config (works PRE-MERGE): list scenarios in .github/sandbox-live.yml
#    and add the `sandbox-live` label to the PR. Each runs the live pipeline and
#    posts evidence as a PR comment. Remove the label/scenarios (and delete the
#    file before merge) to turn it off.
#
# 2. PR comment (works once this workflow is on main): control a one-shot run —
#    /sandbox run item-sales-live date=2026-05-31
#
# 3. Manual dispatch (post-merge):
gh workflow run sandbox-live-run.yml \
  -f scenario=item-sales-live -f date=2026-05-31 -f pr_number=<PR#>
```

Forks are refused (secrets never exposed) and comment commands require an
OWNER/COLLABORATOR/MEMBER author. Add a scenario by extending
`sandbox_scenarios.SCENARIOS`. A scenario may declare **`skip`** (pipeline steps to omit so the run is
focused — e.g. `item-sales-live` skips `adp,reviews,model` to exercise **only** the Square download that
broke; threaded as `--skip` → `BHAGA_SKIP_<STEP>` env, read by `daily_refresh.main`) and **`verify`** (a
post-run gate; `item_sales` asserts `<date>/square/items-*.csv` exists with >0 data rows, so a "green"
run truly means the deliverable landed — it fails the run even if the job exited 0 because login broke
before item-sales). The verdict is shown in the auto-posted PR evidence comment.

**Login escalation — magic link & trusted device.** Square may escalate an **unrecognized device** to an
email **magic link** ("Magic link sent. Use this device to sign in.") instead of an SMS code; the
code-entry flow cannot satisfy it (the link only works in the **requesting** browser). Two layers handle
this:
- *Trusted device (1st line):* the 2FA flow ticks "trust this device for 30 days" and, with
  `BHAGA_SESSION_PERSIST=1`, persists the Square `storage_state` to `gs://<bucket>/_session/square-<store>.json`
  and restores it next run — so Square recognizes the device and stops escalating. Sandbox keeps its OWN
  session in the sandbox bucket (isolation preserved).
- *Magic-link relay (fallback):* when the magic-link page is detected, BHAGA DMs the operator to **paste
  the magic-link URL** — ⚠️ do **NOT** tap "Sign in" on your phone (that signs in the phone, not the
  container); copy the `https://squareup.com/login?rml=1&…` URL from the email and paste it in the DM.
  The container then navigates to it to finish sign-in.

**Step-by-step screenshot trace.** Sandbox runs set `BHAGA_TRACE_SCREENSHOTS=1`, so `runtime.trace_step`
captures the **full browser after every login + item-sales action** and uploads each frame to
`gs://<sandbox-bucket>/<date>/trace/NN-<label>.png` (`landing`, `email-filled`, `password-screen`,
`otp-code-screen`/`otp-code-filled`/`after-otp-submit`, `magic-link-sent-page`/`magic-link-navigated`/
`magic-link-result`, `item-sales-page`/`item-sales-date-range-set`/`item-sales-exported`). Pull the whole
sequence with `gcloud storage cp -r gs://<sandbox-bucket>/<date>/trace .` to scrub the flow frame-by-frame
without a rerun. Off by default for the prod nightly (cost/overhead); best-effort and never breaks a scrape.

**One-time setup (operator).** By least privilege the run SA has GCS read + object write but not
project bucket-create, so create the sandbox cache bucket once and grant the SA object access:

```bash
gcloud storage buckets create gs://bhaga-scrape-cache-sandbox \
  --location=us-central1 --uniform-bucket-level-access --project=jarvis-bhaga-prod
gcloud storage buckets add-iam-policy-binding gs://bhaga-scrape-cache-sandbox \
  --member=serviceAccount:<run-sa> --role=roles/storage.objectAdmin
```

The `bhaga-sandbox-refresh` job **self-wires** on first create — `sandbox_live_run` inherits the prod
job's secret bindings + service account + **resources/timeout** (cpu/mem/`task-timeout`/`max-retries`;
a default job is 512Mi/600s and would OOM/timeout a Chromium scrape) **and the prod job's plain env
vars** (`BHAGA_SECRETS_BACKEND=gcp`, `GCP_PROJECT`, `BHAGA_DM_CHANNEL`, … — without these the config
loader falls back to a non-existent `config.yaml`). The describe-JSON parsers are schema-robust (handle
both the v2 and KRM/v1 shapes). Same creds/sizing; only the isolation overlay differs. The webhook's
`SANDBOX_RUNS_COLLECTION` **defaults to `""` (sandbox OTP scan OFF — the prod READY path is byte-for-byte
unchanged)**; set `SANDBOX_RUNS_COLLECTION=sandbox_runs` on the `bhaga-webhook` service to enable sandbox
OTP routing. Supervised live runs don't need it — they wait for the code inline via
`BHAGA_OTP_ASSUME_READY=1`, so the OTP round-trip works even before the new webhook deploys. No scheduler
is ever pointed at the sandbox job — execute-on-demand only.

**While iterating on a live incident, pause the nightly** so a 21:30 CT run doesn't race your fix or
compete for the OTP, then resume it after the prod rerun:

```bash
gcloud scheduler jobs pause  bhaga-nightly --location=us-central1   # before the fix loop
# … reproduce in sandbox → fix → prove green → merge → rerun prod 5/31 + 6/1 …
gcloud scheduler jobs resume bhaga-nightly --location=us-central1   # after prod is caught up
```

---

## 14. BigQuery + Grafana Cloud (added PR #16, 2026-06-03)

### Status doctor — check freshness first

Before hand-investigating whether a run landed, use the read-only doctor CLI:

```bash
BHAGA_SECRETS_BACKEND=gcp \
BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \
python3 -m agents.bhaga.scripts.status --store palmetto
# or for a specific date:
python3 -m agents.bhaga.scripts.status --store palmetto --date 2026-06-03
# machine-readable:
python3 -m agents.bhaga.scripts.status --store palmetto --json
# verify registry columns exist in live BQ (catches schema drift):
python3 -m agents.bhaga.scripts.status --store palmetto --check-schema
```

It prints a compact freshness table across all three layers (Sheets → BQ → Grafana BI
views) and exits nonzero if any layer is missing the date — so it is usable in scripts
and alerts.  Don't hand-investigate; run this first.

**Anti-drift contract:** `status.py` keeps a declarative registry (`BQ_TARGETS`,
`GRAFANA_VIEWS`) that must track `core/migrations/*.sql` and
`agents/bhaga/grafana/dashboard.json`.  Sync is enforced by:
- Static tests in `agents/bhaga/scripts/test_status.py` (parse migration SQL + dashboard
  JSON and assert the registry covers them — fails CI if a new table or panel appears
  without an update to the registry).
- `scripts/check_doc_freshness.py --strict` coupling (CI hard-fails a migration or
  dashboard PR that doesn't also update `status.py`).

### Architecture

- **BQ dataset:** `jarvis-bhaga-prod.bhaga`
- **Raw tables:** `square_transactions`, `adp_shifts`, `adp_punches`, `adp_wage_rates`, `square_daily_rollup`
- **Curated views:** `vw_daily_sales`, `vw_tips_by_hour`, `vw_labor_daily`, `vw_labor_weekly`, `vw_sales_labor_daily`, `vw_employee_hours_summary`
- **Model tables:** `model_daily`, `model_labor_daily`, `model_labor_weekly`, `model_labor_period`, `model_tip_alloc_period`, `model_tip_alloc_daily`, `model_period_summary`
- **Model views (Grafana BI contract):** `vw_model_labor_daily`, `vw_model_period_summary`
- **Grafana org:** `steadyangelfish2985`
- **Dashboard URL:** `https://steadyangelfish2985.grafana.net/d/bhaga-analytics-v1/bhaga-analytics`
- **Read-only SA:** `grafana-bq-reader@jarvis-bhaga-prod.iam.gserviceaccount.com` (DataViewer + JobUser)
- **SA key:** stored in Secret Manager secret `grafana-bq-reader-key`
- **API token:** stored in macOS Keychain (`security find-generic-password -s grafana-cloud-api-token -a steadyangelfish2985 -w`)
- **GitHub secrets required:** `GRAFANA_API_TOKEN`, `GRAFANA_ORG_SLUG` (= `steadyangelfish2985`)

### Orchestrator SA IAM (BigQuery) — required, easy to miss

The nightly job runs as `bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com`. For the BQ
mirror steps to work it **must** hold both:

| Role | Why | Grant |
|---|---|---|
| `roles/bigquery.jobUser` | `bigquery.jobs.create` — run any read/write query | `gcloud projects add-iam-policy-binding jarvis-bhaga-prod --member=serviceAccount:bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com --role=roles/bigquery.jobUser` |
| `roles/bigquery.dataEditor` | create/MERGE into the `bhaga` dataset tables | `…--role=roles/bigquery.dataEditor` |

> **Incident (2026-06-03, PR #23).** The SA was missing **both** roles, so every BQ job returned
> `403 Access Denied: …does not have bigquery.jobs.create`. Because `load_bigquery` /
> `materialize_model_bq` are **non-fatal**, the nightly stayed green while the BQ mirror silently
> stopped advancing (Sheets ran ahead by a day). The read path (`core.datastore.read_query`) also
> **swallowed** the 403 into `[]`, so `materialize_model_bq` failed with a misleading
> `max() iterable argument is empty` instead of the real cause. Fixes: granted the two roles;
> `read_query` now **re-raises** access errors (only genuinely-empty / not-found degrade to `[]`);
> `materialize_model_bq` raises a precise breadcrumb when raw is empty. **Symptom to grep for:**
> `bigquery.jobs.create` or `BigQuery access denied` in the Cloud Run job logs.

Verify the roles any time:

```bash
gcloud projects get-iam-policy jarvis-bhaga-prod \
  --flatten="bindings[].members" \
  --filter="bindings.members:bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com" \
  --format="value(bindings.role)" | grep bigquery
```

### Daily cron integration

The nightly pipeline is now **BQ-primary for raw data** (hard cutover, no feature flag):

1. **`load_raw_bigquery` step:** `backfill_from_downloads` (with `BHAGA_DATASTORE=bigquery`) loads scrape
   files directly into BQ raw tables. BQ is the system of record.
2. **`render_raw_sheets` step (non-fatal):** `render_raw_sheet_from_bq` renders raw Sheets as projections
   from BQ (Square/ADP tabs, windowed by `--since gap_start`).
3. **Reviews:** `process_reviews` writes `google_reviews` to BQ; then `render_raw_sheet_from_bq --tabs reviews`
   renders the reviews Sheet tab from BQ (non-fatal).

The model compute follows one of two paths:
- **Legacy path (default, `BHAGA_SHEET_FROM_BQ` unset):** `update_model_sheet` computes the model from Sheet raw and writes the Sheet; `materialize_model_bq` then mirrors to BQ (non-fatal).
- **BQ-canonical path (`BHAGA_SHEET_FROM_BQ=1`):** `materialize_model_bq` runs first (BQ is canonical); `render_model_sheet_from_bq` **incrementally upserts** (by natural key, `--since` window) the Sheet tabs from BQ. This eliminates dual-compute drift. See `docs/FEATURE_FLAGS.md` for flip criteria.

After model writes, `reconcile_model.py` (non-fatal) compares each Sheet model tab against its BQ table and alerts on drift. See `agents/bhaga/scripts/reconcile_model.py`.

**Flip procedure (`BHAGA_SHEET_FROM_BQ`):**
1. Confirm `reconcile_model` has been green in prod for ≥ 2 consecutive nights.
2. Set `BHAGA_SHEET_FROM_BQ=1` in the Cloud Run job env (Cloud Console → Jobs → `bhaga-daily-refresh` → Edit → Environment variables).
3. Run a manual job and confirm the Sheet renders from BQ correctly.
4. Record the flip in `docs/FEATURE_FLAGS.md`.

### Re-deploying the dashboard

```bash
# from repo root, after activating venv:
python3 agents/bhaga/grafana/deploy.py --org-slug steadyangelfish2985

# or CI: push a change to agents/bhaga/grafana/** → GitHub Action auto-deploys
```

### Running SQL migrations

```bash
python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
```

Migrations live in `core/migrations/001_initial_schema.sql` … `005_raw_parity.sql`. They are idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE OR REPLACE VIEW`). Migration 005 adds: `square_item_lines`, `square_kds_daily`, `square_kds_tickets`, `adp_earnings`, `google_reviews` raw tables; and `vw_order_quality_daily`, `vw_kds_item_investigation`, `vw_staff_on_shift`, extended `vw_model_labor_daily/weekly`, extended `vw_model_payroll_period` (with ADP actuals + diffs).

### BQ backfill (one-shot)

Backfills all 11 tables (existing + new raw-parity tables from migration 005):
```bash
BHAGA_DATASTORE=bigquery BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \
  python3 -m agents.bhaga.scripts.backfill_bigquery --store palmetto
```

To backfill specific tables only:
```bash
python3 -m agents.bhaga.scripts.backfill_bigquery --store palmetto --tables square_kds_daily,square_kds_tickets,adp_earnings,google_reviews
```

To load scrape files directly to BQ (primary path, no Sheet write):
```bash
BHAGA_DATASTORE=bigquery BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \
  python3 -m agents.bhaga.scripts.backfill_from_downloads --store palmetto
```

To render raw Sheets from BQ (projections, non-fatal):
```bash
BHAGA_DATASTORE=bigquery BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \
  python3 -m agents.bhaga.scripts.render_raw_sheet_from_bq --store palmetto --since 2026-01-01
```

### Materialize model into BQ (one-shot)

```bash
BHAGA_DATASTORE=bigquery BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \
  python3 -m agents.bhaga.scripts.materialize_model_bq --store palmetto
```

---

## 15. BQ as single source of truth (added PR #33, 2026-06-05)

### Principle

BigQuery is the **single source of truth** for all BHAGA data:
- **Scraped raw data** — `square_transactions`, `adp_shifts`, `square_kds_daily`, `square_kds_tickets`,
  `square_item_lines`, `google_reviews`, `adp_earnings`.
- **ADP earnings** — `bhaga.adp_earnings`; the model reads actuals from here (not from GCS XLSX).
- **Operator tunables (config)** — `bhaga.store_config`; replaces the Sheet config tab as the
  authoritative read source (Sheet config tab is now a read-only projection).

**GCS retains only:** browser sessions (`_session/`) and failure evidence (`<date>/evidence/`).
No pipeline code reads data files from GCS.

### Coverage-aware gap resolver

The nightly and windowed backfill use `agents/bhaga/scripts/bq_coverage.py` to determine which
business days are already in BQ and scrape upstream **only for missing days**:

```python
from agents.bhaga.scripts.bq_coverage import SOURCE_COVERAGE, missing_ranges
sq_table, sq_col = SOURCE_COVERAGE["square_transactions"]
gaps = missing_ranges(sq_table, sq_col, data_start, refresh_date)
# gaps = [(gap_start, gap_end), ...] or [] if fully covered
```

If BQ is unavailable (e.g. `BHAGA_DATASTORE` unset), falls back to the legacy
sheet-based `_read_data_window_end_from_sheet` / `compute_gap_window` path.

### Retry-skips-rescrape guarantee

If `load_raw_bigquery` fails (BQ upsert error), `daily_refresh` immediately clears the `square.done`
and `adp.done` Firestore markers so the next retry re-scrapes from upstream with fresh data (rather
than failing with no local files in the ephemeral Cloud Run container).

### Operator tunables — edit via Slack

```
/bhaga-cloud config get <key>          — read a tunable from bhaga.store_config
/bhaga-cloud config set <key> <value>  — update a tunable (audit: updated_by + updated_at)
```

Pipeline reads `store_config` via `core.store_config.get_config(store, key)` — BQ first, Sheet as
fallback while the BQ table is being seeded. After seeding, Sheet config becomes display-only.

**Seed the BQ config from the current Sheet values (one-time, idempotent):**
```bash
BHAGA_DATASTORE=bigquery python3 - <<'EOF'
from core.store_config import set_config
from agents.bhaga.scripts.update_model_sheet import REVIEW_TUNABLE_KEYS, LABOR_TUNABLE_KEYS, _read_config_value
from core.config_loader import resolve_sheet_id
# read current Sheet values and upsert to BQ
store = "palmetto"
sid = resolve_sheet_id(store, "bhaga_model")
all_keys = list(REVIEW_TUNABLE_KEYS) + list(LABOR_TUNABLE_KEYS)
for k in all_keys:
    v = _read_config_value(spreadsheet_id=sid, store=store, key=k)
    if v is not None:
        set_config(store, k, v, updated_by="seed-from-sheet")
        print(f"  seeded {k} = {v}")
EOF
```

### Apply migration 007 (store_config table)

```bash
BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
```

Expected output: `['007_store_config']` (first run) or `[]` (already applied).
