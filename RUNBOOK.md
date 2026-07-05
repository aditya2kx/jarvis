# BHAGA Operator Runbook

**Status:** Cloud is the sole primary. The laptop / non-cloud flow is retired (2026-05-29).
This runbook is written so the system is fully operable from a **fresh machine with only
GitHub + GCP access** — no dependency on the old laptop, its Keychain, or its launchd jobs.

- **GCP project:** `jarvis-bhaga-prod`
- **Region:** `us-central1`
- **Store:** `palmetto` (Palmetto Superfoods, Austin) — `AK JUICY BOWLS LLC`
- **Repo:** `https://github.com/aditya2kx/jarvis` (branch `main`; agent pushes via `jarvis-agent-bot328` HTTPS, PAT in Keychain — see §7 bot-PAT auth model)

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
`tip_alloc_period`, `period_summary`, `review_bonus_period`, `item_operations`.

> **Note (2026-06-09):** The `labor_daily_forecast` Sheet tab was retired. Daily order/item forecasts
> are now BQ-authoritative (`model_forecast_daily`); see §15 — Labor Forecast. The sheet
> verification dicts (`MODEL_VERIFY_MIN_ROWS` in `daily_refresh.py` and `PROD_RAW_VERIFY_MIN_ROWS` /
> `SANDBOX_E2E_VERIFY_MIN_ROWS` in `sandbox_e2e.py`) no longer include `labor_daily_forecast`.

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
- **Job resources:** `bhaga-daily-refresh` runs at **2 vCPU / 4Gi** memory, `maxRetries: 0`,
  1h timeout. The 4Gi was added 2026-06-11 when Square used a browser (Chromium). Square now
  uses the REST API (no browser), but 4Gi stays in place for ADP's Chromium instance. 4Gi at
  2 vCPU stays under half the Cloud Run jobs free tier. `--memory 4Gi` is codified in
  `deploy.yml`'s `gcloud run jobs update` step so it survives a recreate-from-scratch.
- **Webhook URL:** https://bhaga-webhook-4yl5izovxq-uc.a.run.app
  - Routes: `POST /slack/events` (Events API), `POST /slack/commands` (`/bhaga refresh <date>`,
    `/bhaga status`), `POST /slack/interactions` (Slack interactivity — restock modal
    `view_submission`, Issue #137), `GET /health`.

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
| `BHAGA_SESSION_PERSIST` | `1` — persist/restore the ADP browser `storage_state` to/from `gs://bhaga-scrape-cache/_session/`. (Square no longer uses a browser — it uses the REST API via `square_palmetto_oauth`. ADP still scrapes via Chromium.) |
| `BHAGA_DATASTORE` | `bigquery` — enables BQ reads/writes in the **parent** orchestrator process (pipeline run recorder, `reconcile_model` gate, `update_model_sheet --data-source bigquery`). Child subprocesses also set this per-step; codified in `deploy.yml` since 2026-06-13. |
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

**Cold-start mitigation (`--cpu-boost`, Issue #137):** `bhaga-webhook` runs with `min-instances=0` (no idle cost), so a request after any idle period is a cold start. `init_app()` eagerly builds Firestore + BigQuery clients at import time, and Slack's slash-command/interaction ack deadline is 3s — a cold start can blow that deadline and surface to the operator as a Slack `operation_timeout` (seen 2026-07-02 on `/bhaga-cloud restock`). Fix: `deploy.yml`'s `gcloud run services update bhaga-webhook` passes `--cpu-boost`, which gives the instance 2x vCPU only during startup — **$0 extra cost**, `min-instances` stays 0. Rejected alternative: `--min-instances=1` eliminates cold starts entirely but keeps an instance warm 24/7, which is a real recurring cost the operator explicitly didn't want. `--cpu-boost` persists across a rollback (`gcloud run services update` only touches explicitly-passed flags, and the rollback step doesn't reset it). Verify: `gcloud run services describe bhaga-webhook --region us-central1 --format 'value(spec.template.metadata.annotations["run.googleapis.com/startup-cpu-boost"])'` should print `true`.

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
- **GCS** bucket `bhaga-scrape-cache` — **trusted-device browser sessions** (`_session/`) and
  **failure evidence** (`<date>/evidence/`) ONLY. The nightly pipeline parses scrape exports
  straight into BigQuery (the single source of truth) and does **not** write scrape data files to GCS
  or read them back. Legacy date-keyed `<date>/square|adp/*.csv|xlsx` blobs may linger from before the
  cutover and from offline backfill/`sandbox_e2e` tooling, but are not part of the live data path.
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
| `square_palmetto_oauth` | refresh job (Square API) | Square OAuth 2.0 access + refresh tokens for production Square API |
| `google_palmetto` | refresh job (Sheets/Drive) | Google OAuth creds for the `palmetto` account |
| `jarvis-clickup-palmetto-pat` | refresh job (`CLICKUP_PAT`) | ClickUp PAT — read review channel + closing-form inventory ingest |
| `operator-console-gemini-token` | operator-console (`GEMINI_TOKEN`) | Gemini API key (Generative Language API), restricted to that one API — restock photo parsing (`lib/restock/gemini.ts`). Wired via `--set-secrets` in `operator-console-deploy.yml`; default compute SA holds `secretAccessor` on it. |

> **Local bootstrap (all providers):** If a secret is missing from your macOS Keychain on a fresh
> clone, use:
> ```bash
> python3 -m skills.credentials.registry hydrate jarvis-clickup-palmetto-pat
> python3 -m skills.credentials.registry hydrate-all   # hydrate all missing secrets
> python3 -m skills.credentials.registry audit          # shows fix command for each missing cred
> ```
> `hydrate` reads from GCP Secret Manager via ADC (no `gcloud` binary required) and writes to
> Keychain without printing the value. Works for ClickUp, Google, Square, ADP, and Slack.
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

### Default behaviour (inline autostart, since PR #94)

The nightly job **no longer sends a READY-handshake Slack message before starting**. It proceeds
directly to the ADP/Square scrapes. If ADP's browser session is trusted (the usual case), no OTP
challenge fires at all.

If ADP *does* challenge for a 2FA code:

1. The runner posts a Slack OTP-code ask to **`D0B67MW6J02`** ("bhaga cloud"): "Reply with your ADP SMS code".
2. The operator replies in that DM within **15 minutes** (`BHAGA_OTP_WAIT_S=900`). Slack delivers
   the event to `POST /slack/events` on the webhook, which writes the code into `otps/adp` in
   Firestore. The runner unblocks and submits the code.
3. If the operator does **not** reply within 15 minutes, the ADP step is **gracefully skipped**:
   `otp_skipped_alert` is posted to Slack, the run completes on existing ADP data (exit 0), and
   the next nightly retries with a fresh browser session.

There is **no laptop listener** anymore. If the OTP code is not being accepted, debug the
**webhook** (logs below) and the Slack app's Events API subscription, not any local process.

### Rollback: restore the legacy READY handshake

Set `BHAGA_OTP_REQUIRE_READY=1` on the Cloud Run Job to re-enable the two-step flow:
1. Job posts a READY request to the operator DM, writes a Firestore checkpoint, and exits 0.
2. Operator replies `READY` (any time within 48 h). Webhook triggers a fresh Cloud Run execution.
3. Fresh job reads the checkpoint, sees `ready_received=True`, drives the OTP portal inline, and
   completes normally. After 48 h with no READY: skips OTP steps, alerts, next nightly retries.

See `FEATURE_FLAGS.md` — `BHAGA_OTP_REQUIRE_READY` — for the cleanup timeline.

### `/bhaga-cloud refresh` — multi-date, lists, and ranges

The refresh command accepts a single date, a comma/space list, an inclusive range (`..` or `to`),
or any mix:

```
/bhaga-cloud refresh 2026-06-14
/bhaga-cloud refresh 2026-06-14,2026-06-15,2026-06-16
/bhaga-cloud refresh 2026-06-14 2026-06-15
/bhaga-cloud refresh 2026-06-14..2026-06-20
/bhaga-cloud refresh 2026-06-14 to 2026-06-20
/bhaga-cloud refresh 2026-06-14,2026-06-20..2026-06-22,2026-06-25
```

Each resolved date triggers **one Cloud Run Job execution** (fan-out). Dates are deduped and sorted
ascending. Up to 31 dates per command; larger ranges are rejected with an error.

**Coverage-aware mode selection per date** (mirrors `scripts/trigger_dated_refresh.py`):

- Date already covered in **both** `bhaga.square_daily_rollup` (Square) **and** `bhaga.adp_shifts`
  (ADP) → **recompute-only**: executes with `BHAGA_SKIP_SQUARE=1`, `BHAGA_SKIP_ADP=1`,
  `BHAGA_SKIP_KDS=1`. No portal login, no OTP.
- Either source missing, or BQ probe fails (fail-open) → **full scrape (inline OTP)**: starts
  inline; ADP will only request an OTP code if the browser session is challenged. No READY prompt
  is sent up-front. `BHAGA_OTP_FORCE_REQUEST` is no longer injected.

Both modes add `BHAGA_IGNORE_HALT=1` (operator-driven backfill includes the fix).

**Two-phase ack** (prevents Slack's "Something went wrong" timeout on multi-date fan-out):

1. **Immediate ack (<3s, synchronous):** Slack receives a generic queued message as soon as parsing succeeds, e.g.:
   > ⏳ Refresh queued for 2 date(s) — probing coverage + triggering; per-date summary to follow.
2. **Follow-up (async, via `response_url`):** After the BQ coverage probe and Cloud Run triggers complete, the real per-date mode-label summary is posted back to the channel where the command was run (visible to all members — `response_type: in_channel`), e.g.:
   > ⏳ Refresh triggered: 2026-06-23 (full+OTP), 2026-06-24 (full+OTP).

Parse errors (bad date, over-cap, unknown token) are still synchronous and appear inline.

The same two-phase pattern applies to every `/bhaga-cloud` command: `status`, `config get/set`, `training set/rm`, `alias set`, and `exclude set` all return an immediate ack and post their real result as an ephemeral `response_url` follow-up (operator-private).

### `/bhaga-cloud restock` — register a restock delivery date + upload/reset actuals (Issue #137)

```
/bhaga-cloud restock
```

Opens a modal (`views.open`, needs a live `trigger_id` — bypasses the async response_url pattern
used by every other command since the modal must open within Slack's 3s ack window):

- **Action** — one of `Register date only (estimated)`, `Add order (actuals)`, `Reset to estimated`.
- **Restock delivery date** — a date picker.
- **Order CSV (base,quantity)** — a `file_input` block, required only for `Add order`. Header row is
  optional; bases are validated against the same `ACTIVE_BASES` list the pipeline uses elsewhere
  (case-sensitive, exact match) and quantities must be non-negative numbers.

On submit, the handler always MERGEs the delivery date into `bhaga.inventory_restock_schedule`
(idempotent — the date becomes "registered" whether or not actuals exist for it). Then:

- `Register date only` — no further write; the date participates in migration 031's dual-date
  recommendation as an *estimated* delivery.
- `Add order` — downloads the CSV via `files:read` + the bot token, parses it, and does a
  **replace-per-date** write to `bhaga.inventory_restock_orders` (DELETE all rows for
  `(store, delivery_date)`, then INSERT the parsed rows) — re-uploading a corrected CSV for the same
  date always converges rather than accumulating duplicates.
- `Reset to estimated` — DELETEs all `inventory_restock_orders` rows for that date, reverting the
  date back to estimated-only.

Validation errors (missing date, missing CSV for `Add order`, unknown base, non-numeric or negative
quantity) are returned inline via the modal's `response_action: errors` — this only works because the
Slack app has **Interactivity** enabled with a request URL pointed at
`POST /slack/interactions` (see `agents/bhaga/setup/slack-app-manifest-cloud.yaml`). Success is
confirmed via a DM to the submitting operator, not the modal (which just closes).

**OTP concurrency caveat:** if multiple full-scrape dates are enqueued concurrently, `_find_pending_portal_for_agent` picks the *newest* pending OTP. ADP's distributed scrape lock serialises browser logins; Square uses the REST API (no browser, no OTP since 2026-06-23). The only OTP portal that fires a live SMS is ADP.

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
      The bot account has 2FA (TOTP) enrolled (enrolled 2026-06-28); TOTP secret is in Keychain `github-bot-totp`; recovery codes in `github-bot-recovery`. The `origin` remote is tokenless (`https://github.com/aditya2kx/jarvis.git`) and `gh auth setup-git` provides credentials via the Keychain-backed `GH_TOKEN`. When rotating the bot PAT, update Keychain `github-bot-pat` using: `security add-generic-password -a jarvis-agent-bot328 -s github-bot-pat -w <new_token> -U`
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
   script or extension recipe → `agents/bhaga/scripts/README.md`; an invariant → `.cursor/rules/bhaga.mdc`;
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

### Auto-rerun fixed dates on deploy (Retry-Dates trailer)

When a PR fixes a broken date (e.g. the nightly for June 13 failed due to stale input data), add a
`Retry-Dates:` trailer to the PR body so deploy automatically re-runs that date when the PR merges:

```
Retry-Dates: 2026-06-13
```

Multiple dates are comma-separated: `Retry-Dates: 2026-06-12, 2026-06-13`.

**Smart mode selection per date** (via `scripts/trigger_dated_refresh.py`):
- If the date is covered by **both** `square_daily_rollup` (Square) **and** `adp_shifts` (ADP) in
  BQ → **recompute-only**: sets `BHAGA_SKIP_SQUARE=1`, `BHAGA_SKIP_ADP=1`, `BHAGA_SKIP_KDS=1` so
  no browser/OTP; only the model is rebuilt from updated human inputs (`training_shifts`,
  `store_config`). Both sources must be present: a date where ADP failed (e.g. a sorry.adp.com
  throttle night) has Square coverage but missing ADP — that date triggers a full scrape even though
  Square is already in BQ.
- If either source is missing, or BQ probes fail (fail-open) → **full refresh**: normal scrape + OTP
  flow.

The rerun uses Cloud Run v2 per-execution env overrides (`RunJobRequest.Overrides`) so the job
definition is **never mutated** (a persisted `REFRESH_DATE` would corrupt future nightlies). The
step is best-effort: a failure never fails the deploy and logs a `::warning::`.

Manual one-off rerun (using the same smart logic):
```bash
python3 scripts/trigger_dated_refresh.py --date 2026-06-13 --dry-run   # check mode
python3 scripts/trigger_dated_refresh.py --date 2026-06-13              # trigger
python3 scripts/trigger_dated_refresh.py --date 2026-06-14 --force-scrape  # force full scrape
```

IAM: the WIF SA needs `run.jobs.run`. `roles/run.admin` on `bhaga-orchestrator` covers this.

> **Note:** `verify_model_bq` KDS date range query uses `date_local` column from `square_kds_daily` (the table's date column is `date_local`, not `date`).

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

`trigger_dated_refresh.py` (used by `Retry-Dates:` deploy trailers) always injects `BHAGA_IGNORE_HALT=1`
so deploy-triggered retries automatically bypass the breaker — the fix is baked into the image by
definition and a healthy run will auto-clear it.

### OTP-portal recovery (auto-invalidate stale downstream markers)

When a previously-failed OTP portal (Square/ADP) succeeds on a later run **while** the downstream
markers are already `done` from the prior partial run, those steps would short-circuit and the fresh
data would never reach the Model sheet (`data_window_end` stuck — the 2026-05-31 incident).
`daily_refresh` now **always** detects this and clears those markers (via `state_adapter.clear_step`,
the sanctioned path — never a shell `rm`) so they recompute on the fresh data; the post-condition guard
then verifies `data_window_end` advanced.

The invalidated set (`_RECOVERY_DOWNSTREAM_STEPS`) is **every** step that carries portal data to the
model, in pipeline order: `load_raw_bigquery` → `materialize_model_bq` → `process_reviews`
(post-Sheets-exit; Sheet projection steps deleted 2026-06-15). If you ever clear markers by hand
to recover, clear **all** of these, not just the first/last.

This is **not** behind a feature flag — it's safe by construction: the trigger is precisely "a portal
produced fresh data *and* a downstream marker is already done" (a prior partial run; on a normal first
run the markers don't exist yet, so nothing is cleared), and the downstream re-run only upserts by
natural key, so it can never duplicate or corrupt rows. The worst case — a forced full re-scrape of an
already-complete date — merely recomputes idempotently.

### Raw-vs-model reconciliation (2026-06-09 fix)

`_recover_stale_downstream_markers` only fires when a portal scrape **succeeds this run**. A pure
retrigger (scrape SKIPped as "already covered") never triggered it, so a concurrent-execution race that
wrote `model_daily = $0` while the rollup had real sales would survive every retrigger, leaving Grafana
panels empty indefinitely.

`daily_refresh` now runs `_detect_and_clear_stale_model` on **every** execution (including pure
retriggers) **before Phase 2**. It queries `square_daily_rollup` vs `model_daily` over a 14-day
window; if any date has rollup gross_sales > $1 but model_daily = $0, it clears the model-recompute
markers (post-Sheets-exit: `materialize_model_bq` only) so Phase 2 re-runs `materialize_model_bq` (a full DELETE+reload that heals all dates in one pass).

After the model step, `_assert_model_matches_raw_rollup` re-queries; if drift persists it raises
`RuntimeError` → `failure_alert` Slack DM → non-zero exit. This converts silent "$0-inside-window" drift
into a loud same-night failure. Best-effort: BQ errors in either function log a breadcrumb and return
`[]` — the run is never blocked.

**Implementation note (2026-06-11 bugfix):** `_model_vs_rollup_drift` instantiates
`google.cloud.bigquery.Client()` directly using Application Default Credentials (ADC) rather than
`core.datastore.get_client()`. The latter is gated by the `BHAGA_DATASTORE=bigquery` env var, which
is only set for child subprocesses in the daily refresh, not the orchestrator process itself — so using
`get_client()` would silently return `None` and the reconciliation query would no-op every run.
The same parent-env pattern affected `_record_pipeline_run` until 2026-06-13 (see §14 Pipeline Health).

**Preferred recompute path (2026-06 hardening):** use `trigger_dated_refresh --recompute-only`.
This now automatically injects `BHAGA_FORCE_MODEL_RECOMPUTE=1`, which makes `daily_refresh` clear
the `_MODEL_RECOMPUTE_STEPS` markers via `state_adapter.clear_step` at startup — backend-agnostic
(works for both local and Firestore state). No manual Firestore incantation needed:

```bash
# Recompute 2026-06-19 (no scrape; markers cleared automatically via BHAGA_FORCE_MODEL_RECOMPUTE)
python3 scripts/trigger_dated_refresh.py --date 2026-06-19 --recompute-only
```

**Manual recovery** (only if `trigger_dated_refresh` is unavailable): clear model markers by hand:
```bash
BHAGA_STATE_BACKEND=firestore BHAGA_SECRETS_BACKEND=gcp python3 -c "
import sys, datetime; sys.path.insert(0,'.')
from skills.bhaga_config import state_adapter as sa
rd = datetime.date(2026, 6, 9)  # replace with affected date
for step in ('materialize_model_bq',):
    sa.clear_step(rd, step); print('cleared', step)"
```
Then retrigger the job for that date (or use `trigger_dated_refresh --recompute-only`).

### Recover a partial-failure date (e.g. the 2026-05-31 Square-launch crash)

Concrete runbook for "an OTP portal crashed on launch, downstream ran on stale data, `data_window_end`
is stuck and bonuses are held back":

1. **Confirm the state.** Read Firestore `runs/2026-05-31`: `square_transactions` will be **absent**
   (it failed) while `load_raw_bigquery` / `update_model_sheet` / `process_reviews` are **present** (they
   ran on stale data). Read the Model `config` tab — `data_window_end` will be stuck at `2026-05-30`.
2. **Announce the OTP (ADP only).** Square uses the REST API — no OTP needed. ADP will fire **one SMS**
   to the operator. Post in the BHAGA DM before triggering it (Operating rule / HL#8).
3. **Re-run the date as a Cloud Run job** (never a laptop). ADP skips its browser/OTP via the GCS
   cache if already done. The recovery is automatic — when the Square API and ADP succeed, the stale
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
exemption**, only a BQ command, then a model rebuild.

**BQ-canonical (post-2026-06-15 Sheets exit):** all human inputs live in BQ, edited via `/bhaga-cloud` Slack commands — no Sheet editing. Quick reference:

- **Permanent** (manager/owner): `/bhaga-cloud exclude set "Last, First"` — appends to
  `store_config.excluded_from_tip_pool` in BQ. Alternatively set via
  `/bhaga-cloud config set excluded_from_tip_pool "Name1;Name2"`.
- **Through a date** (bulk "all shifts were training up to X"):
  `/bhaga-cloud exclude set "Last, First" YYYY-MM-DD` — sets `store_config.training_excluded:Last, First`.
- **One specific shift**: `/bhaga-cloud training set "Last, First" YYYY-MM-DD [note]` — MERGEs into
  `bhaga.training_shifts` BQ table. The Grafana `6. Payroll → Training Shifts (current)` panel shows
  all active marks immediately. Remove with `/bhaga-cloud training rm "Last, First" YYYY-MM-DD`.

After editing, the nightly job picks up the change automatically. For an immediate rebuild trigger:
`/bhaga-cloud refresh YYYY-MM-DD`. Verify in Grafana: confirm `tip_alloc_period` shows $0 for the
exempted shift and the pool total is conserved.

### Run the sandbox e2e (prod-like, zero-OTP) — opt-in

> **Policy change (2026-06-09):** `Sandbox e2e` is **no longer a required CI gate on every PR**.
> It runs only when the `run-sandbox-e2e` label is added to a PR or via manual `workflow_dispatch`.
> Use it when a plan specifically calls for it, or for changes touching the core model pipeline.
> `Sandbox e2e` has been removed from the "Protect Master" ruleset required checks.

`agents/bhaga/scripts/sandbox_e2e.py` is the prod-like end-to-end that proves a change without
touching the production workbooks and **without ever calling Square / ADP / Google Reviews or
triggering an OTP**. It **leases a slot** from a pre-created sheet pool (see below), clears +
re-seeds it, seeds the sandbox raw sheets, builds the sandbox model, asserts the model tabs are
populated, prints evidence, then releases the slot.

**Two seeding sources (`--source`):**

- `prod-raw` (**the per-PR default when opted in**): reads the **PROD** raw Square+ADP sheets directly
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

**Cost ledger lives in BigQuery (jarvis_dev), not in git:** the per-PR cost ledger is stored in
`jarvis-bhaga-prod.jarvis_dev` (tables `pr_cost_pr`, `pr_cost_build_session`, `pr_cost_review_run`,
view `vw_pr_cost`). There are no committed `PR-*.json` files or `report.html` in the repo.

- **Pre-merge:** record build cost locally (`pr_cost_ledger.py capture-build` or `record-build`),
  then `validate --pr <n> --require-build`. The pre-commit hook (`bash scripts/install-git-hooks.sh`
  once per clone) captures review cost into BQ on each commit (no `git add`, no staged files).
- **Post-merge:** `pr-cost-finalize.yml` writes `merged_at` + final review cost to BQ via WIF.
- **Dashboard:** https://steadyangelfish2985.grafana.net/d/jarvis-dev-cost-v1/jarvis-development
  ("Jarvis Development" folder, uid `jarvis-dev-cost-v1`). Deploy/update:
  `python3 grafana/jarvis_dev/deploy.py`. Verify BQ panels: `python3 grafana/jarvis_dev/verify_panels.py`.
  Dashboard has three rows: **Development cost**, **Deploys & releases**, **Runtime & free tier**.
- **Datasources:** "BHAGA BigQuery" (uid bound at deploy time); "Jarvis GCP Monitoring" (Stackdriver,
  uid `cfovr14odnpxca`) — `grafana-bq-reader` SA needs `roles/monitoring.viewer` (granted 2026-06-12).
- **Deploy events:** every `deploy.yml` run records a row to `jarvis_dev.deploys` and posts a Grafana
  annotation. Script: `python3 scripts/deploy_events.py record --agent bhaga --unit orchestrator ...`.
- **WIF SA:** `bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com` — has
  `roles/bigquery.dataEditor` + `roles/bigquery.jobUser` on `jarvis-bhaga-prod`.
- **Backfill/migration:** `pr_cost_ledger.py migrate-json-to-bq` (one-shot, already run for PRs 12-47).

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
#    posts evidence as a PR comment. The label is a SINGLE-SHOT trigger — the
#    workflow's `delabel` job removes it automatically after every run (see below).
#    Empty .github/sandbox-live.yml back to `scenarios: []` before merge.
#
# 2. PR comment (works once this workflow is on main): control a one-shot run —
#    /sandbox run item-sales-live date=2026-05-31
#
# 3. Manual dispatch (post-merge):
gh workflow run sandbox-live-run.yml \
  -f scenario=item-sales-live -f date=2026-05-31 -f pr_number=<PR#>
```

**The `sandbox-live` label is mechanically single-shot.** The convention is
"add the label only to gather new evidence, remove it straight after" — this is
enforced by code, not memory. `sandbox-live-run.yml` has a `delabel` job
(`needs: [resolve, live]`, `if: always() && github.event_name == 'pull_request'`)
that removes the `sandbox-live` label after **every** PR-triggered run — pass,
fail, or no-run. So the label is gone by the time the run finishes; re-add it to
trigger fresh evidence. The committed `.github/sandbox-live.yml` scenario list is
the separate "what runs" knob — empty it (`scenarios: []`) before merge.

Forks are refused (secrets never exposed) and comment commands require an
OWNER/COLLABORATOR/MEMBER author. Add a scenario by extending
`sandbox_scenarios.SCENARIOS`. A scenario may declare **`skip`** (pipeline steps to omit so the run is
focused — e.g. `item-sales-live` skips `adp,reviews,model` to exercise **only** the Square download that
broke; threaded as `--skip` → `BHAGA_SKIP_<STEP>` env, read by `daily_refresh.main`) and **`verify`** (a
post-run gate; `item_sales` asserts `<date>/square/items-*.csv` exists with >0 data rows, so a "green"
run truly means the deliverable landed — it fails the run even if the job exited 0 because login broke
before item-sales). The verdict is shown in the auto-posted PR evidence comment.

**Gate-only infra scenario: `otp-reprompt`.** Proves infra-layer changes (OTP gate, Firestore
checkpoint, Cloud Run env injection) on the **real stack** without a live scrape — cheap, no operator
OTP reply needed, the job exits `EXIT_PENDING`. Pattern and knobs:

| Knob | What it does |
|---|---|
| `otp_force_request: True` | Sets `BHAGA_OTP_REQUIRE_READY=1` and `BHAGA_OTP_FORCE_REQUEST=1` in the Cloud Run job env; drops `BHAGA_OTP_ASSUME_READY` so `otp_gate.evaluate` exercises the real READY checkpoint path (rollback mode) |
| `seed_stale_otp_hours: 72` | Before the job runs, seeds a stale `pending_otp` checkpoint (72h old) in `sandbox_runs` so the job finds an unanswered marker |
| `verify: otp_reprompt` | After the job exits, reads `sandbox_runs` and asserts `requested_at` advanced past the seeded value (proves re-prompt fired) and `ready_received=False` |

The `otp-reprompt` scenario requires `BHAGA_OTP_REQUIRE_READY=1` to exercise the READY checkpoint
path (the default inline-autostart mode would skip checkpointing and proceed directly).
The run posts **one** `[SANDBOX]` Slack READY ping — part of the proof; ignore it (no reply needed).
Use this pattern as the prototype for any PR whose key logic fires at the Firestore / OTP gate / Cloud
Run env layer (unit tests can only mock those). Adapt: change `skip` to keep only the steps that
reach your gate, add a seed function for your precondition, add a `verify` gate that reads Firestore
or BQ state after the run.

**Proving webhook slash-command changes end-to-end — direct sandbox trigger.**

The webhook exposes a direct, auth'd entry point that bypasses the Slack HMAC signature check so
the agent (or any bearer-token holder) can trigger the sandbox job without a human typing a Slack
slash command or running `gcloud` on a laptop:

```
POST https://bhaga-webhook-4yl5izovxq-uc.a.run.app/slack/commands
X-Sandbox-Trigger: <SANDBOX_TRIGGER_TOKEN>
Content-Type: application/x-www-form-urlencoded

text=refresh 2026-06-23,2026-06-24&user_name=agent
```

**What the bypass does (invariants):**
- Always routes to `bhaga-sandbox-refresh` (never `bhaga-daily-refresh` / prod).
- BQ coverage probe reads `bhaga_sandbox.square_daily_rollup` (empty for new dates → full live
  scrape + OTP). Module-global `_BQ_DATASET` is never mutated.
- Ack text is prefixed with `:test_tube: [SANDBOX]` so sandbox and prod acks are unambiguous.
- OTP: the sandbox job runs with `BHAGA_OTP_ASSUME_READY=1`, so full+OTP dates service ADP
  inline (no Slack OTP prompt to reply to). See the OTP note below.

**Security rationale:** sandbox targets are fully isolated (separate Cloud Run job, BQ dataset,
Firestore collection, GCS bucket); a request on this bypass path can never touch prod data or prod
runs. Auth is relaxed accordingly. Prod slash commands still require a valid Slack HMAC signature.
Fail-closed: if `SANDBOX_TRIGGER_TOKEN` env var is empty, no bypass is possible.

**Provisioning (one-time, ADC — no `gcloud` CLI required):**

The token is provisioned by `scripts/provision_sandbox_token.py`, which uses the Python
`google-cloud-secret-manager` + `google-cloud-run` libraries with Application Default
Credentials. It works from any machine with ADC (laptop or CI); no `gcloud` binary needed.

```bash
# Dry-run first — prints the intended secret + env mutation, no changes:
python3 scripts/provision_sandbox_token.py --dry-run

# Provision (idempotent — creates the secret, adds a random token version, mounts it
# as SANDBOX_TRIGGER_TOKEN on bhaga-webhook, waits for the new revision to be ACTIVE):
python3 scripts/provision_sandbox_token.py

# Rotate the token (generates a new version; "latest" is re-pinned automatically,
# so the webhook picks it up on the next request with no redeploy):
python3 scripts/provision_sandbox_token.py --rotate

# Non-default targets (defaults: project=jarvis-bhaga-prod, region=us-central1,
# service=bhaga-webhook, secret-name=sandbox-trigger-token, env-var=SANDBOX_TRIGGER_TOKEN):
python3 scripts/provision_sandbox_token.py --project <p> --service <svc> --secret-name <s>
```

The script preserves all other env vars and secret mounts on the webhook; it only
adds/replaces the `SANDBOX_TRIGGER_TOKEN` entry. The token value is never printed.

**Mount survival across deploys:** `deploy.yml` uses
`gcloud run services update --image ...`, which preserves existing secret mounts — so
routine image-only deploys do NOT require re-running the provisioning script. Re-run it
only when the service is recreated from scratch (e.g. `gcloud run services delete` +
recreate), or to rotate the token.

**Evidence harness — two modes:**

*Mode 1 — direct HTTP (post-merge, exercises the deployed bypass end-to-end):*
```python
import urllib.request, urllib.parse
from google.cloud import secretmanager
sm = secretmanager.SecretManagerServiceClient()
resp = sm.access_secret_version(
    name="projects/jarvis-bhaga-prod/secrets/sandbox-trigger-token/versions/latest")
token = resp.payload.data.decode()
url = "https://bhaga-webhook-4yl5izovxq-uc.a.run.app/slack/commands"
body = urllib.parse.urlencode({"text": "refresh 2026-06-23,2026-06-24", "user_name": "agent"}).encode()
req = urllib.request.Request(url, data=body, headers={
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Sandbox-Trigger": token,
})
print(urllib.request.urlopen(req).read().decode())
# Expect: {"text": ":test_tube: [SANDBOX] Refresh triggered: 2026-06-23 (recompute), 2026-06-24 (full+OTP)..."}
```

*Mode 2 — in-process driver (pre-merge, exercises the PR-branch handler code directly
without needing the merged image deployed):*
```bash
# Imports handler in-process, calls _handle_slash_command(sandbox=True), triggers the
# real bhaga-sandbox-refresh job via run_v2, polls executions to terminal, verifies
# BQ rows + Firestore. All via ADC — no gcloud/bq CLI needed.
GCP_PROJECT=jarvis-bhaga-prod python3 cloud/webhook/sandbox_refresh_driver.py \
    --dates 2026-06-23,2026-06-24 --wait-minutes 30
```

The driver prints a markdown evidence table (per date: execution state, BQ row count,
Firestore status). Exit 0 = all dates PASS.

**OTP note:** the sandbox job runs with `BHAGA_OTP_ASSUME_READY=1`, so full+OTP dates
service ADP inline via the `otps` collection and do **not** post a Slack OTP prompt for
the operator to reply to. (Prod runs use the real OTP handshake.) If a sandbox execution
appears stuck at the ADP step, check `BHAGA_OTP_ASSUME_READY` on the job rather than
waiting for a Slack prompt.

**Square uses OAuth REST API — no browser, no OTP.** As of 2026-06-23, Square transactions, item
sales, and KDS data are ingested directly via the Square REST API (OAuth 2.0) through
`skills/square_api/ingest.py` and `skills/square_api/kds_reporting.py`. The browser-based
Playwright scraper, magic-link 2FA flow, trusted-device session management, and Square OTP path
are fully removed. The only OTP portal that still fires a live SMS is **ADP**.

**Square OAuth token refresh.** The `square_palmetto_oauth` secret in GCP Secret Manager holds
`{"access_token": "…", "refresh_token": "…", "expires_at": "…"}`. The `skills/square_api/auth.py`
module auto-refreshes the access token when it approaches expiry and writes the updated token back
to the secret. If the refresh token ever expires (Square tokens are long-lived; expiry means the
OAuth app was disconnected), re-authorize via the Square Developer Console and update the secret:
```bash
gcloud secrets versions add square_palmetto_oauth --data-file=- --project jarvis-bhaga-prod
# Paste the new JSON token blob on stdin, then Ctrl-D
```

**ADP login URL changed — bare runpayroll.adp.com retired (2026-06-28 root cause).**
ADP retired the bare `https://runpayroll.adp.com` entry point; it now **server-redirects to
`https://sorry.adp.com/sorry/`** (verify with `curl -sIL https://runpayroll.adp.com` — it's a
plain redirect, not an IP block; the same redirect happens from any network). This broke the
2026-06-28 nightly at the `adp` step. **Fix:** `LOGIN_URL` now points at
`https://runpayroll.adp.com/enrollment.aspx`, which routes through ADP's federation redirector
to the live sign-in SPA (`online.adp.com/signin/v1/?APPID=RUN&productId=…`) with the correct,
ADP-supplied `productId`. Set in `skills/adp_run_automation/runner.py`,
`compensation_backend.py`, `shift_backend.py`, and the two selector JSONs. If ADP changes the
entry point again, re-derive it by opening `runpayroll.adp.com/enrollment.aspx` in a browser and
confirming the **User ID** box renders.

**ADP sorry.adp.com throttle resilience (2026-06-28 safety net).** As a complement to the URL
fix, if any future `goto` still lands on `sorry.adp.com`, the runner (`_wait_for_login_form`)
detects it via `sorry.adp.com in page.url` and issues a fresh `page.goto(LOGIN_URL)` — **never**
`page.reload()` (which would stay on the sorry URL) — with exponential backoff (base 3 s). If the
throttle persists across all attempts, `AdpLoginThrottled` is raised (from
`agents/bhaga/scripts/otp_gate.py`); `daily_refresh` treats it as a **graceful ADP skip**
(Slack alert via `info_ping`, `source_pulls.status = skipped_adp_throttle`, exit 0) — the
same pattern as an OTP-wait timeout. The next nightly or `Retry-Dates` rerun re-attempts.
A date where ADP was skipped has Square in BQ but missing `adp_shifts` → `trigger_dated_refresh.py`
correctly selects **full scrape** (not recompute-only) on a `Retry-Dates` rerun.

**Post-login maintenance interstitial (RUN maintenance window) + smart retry.** ADP also serves
a maintenance/throttle interstitial **after** a valid login during scheduled RUN maintenance.
It uses **two distinct URLs** (`_is_maintenance_interstitial` matches both):
- `https://sorry.adp.com/sorry/` — throttle/sorry page (sometimes carries a window-end banner like
  *"Planned RUN Maintenance Sun 10pm ET → Mon 2am ET"*).
- `https://runpayroll.adp.com/public/maintenance/maintenance.html` — generic *"We'll be back
  soon"* page with **no published end time** (the 2026-06-29 incident; the old sorry-only check
  missed it → hard `RuntimeError` + Slack alert).

This surfaces in `_ensure_logged_in` when `wait_for_url(POST_LOGIN_URL_RE)` lands on a maintenance
URL instead of the dashboard. It is a graceful skip (`exc_factory=AdpLoginThrottled` → `info_ping`
alert → exit 0) — **never** a hard failure.

Because BHAGA's nightly (21:31 CT ≈ 22:31 ET) can fall inside a 10pm–2am ET maintenance window,
the runner computes a `retry_at` for `AdpLoginThrottled`:
- **Known end:** parse the window-end from the banner (`skills/adp_run_automation/maintenance.py`,
  DST-aware via `zoneinfo`; "ET" = `America/New_York`) → `retry_at = window_end + 7 min`.
- **Unknown end** (generic `maintenance.html`): fall back to `retry_at = now + 30 min`
  (`default_retry_at`, override `BHAGA_MAINT_RETRY_DEFAULT_DELAY_MIN`) so the run still self-heals,
  bounded by the attempt cap, instead of waiting ~24h.

`daily_refresh` then schedules a **one-shot smart retry**:

- `agents/bhaga/scripts/retry_scheduler.py` creates an ephemeral Cloud Scheduler job
  `bhaga-retry-<date>` that mirrors `bhaga-nightly` (HTTP target → `bhaga-daily-refresh:run`,
  OAuth as `bhaga-orchestrator`) but fires once at `retry_at` and carries a `REFRESH_DATE`
  override + `BHAGA_MAINT_RETRY_ATTEMPT`.
- The retry run **deletes its own scheduler at start** (`delete_retry_schedule`), so it is
  self-cleaning, and skips the BQ-coverage probe via the `REFRESH_DATE` full-scrape path.
- A **stateless attempt cap** (`BHAGA_MAINT_RETRY_MAX`, default 3) prevents infinite reschedule
  if the window slips; on cap, it degrades to `skipped_adp_throttle` (next nightly retries).
- Status on a scheduled skip is `skipped_adp_maintenance`. `skipped_adp_throttle` is reserved for
  the login-form throttle (no `retry_at`) or when the attempt cap is hit. If scheduling itself
  fails (e.g. IAM), it degrades gracefully to the plain skip + next nightly — never blocks the run.

IAM (one-time, provisioned 2026-06-29): `bhaga-orchestrator` has `roles/cloudscheduler.admin`
(project) + `roles/iam.serviceAccountUser` on itself (to set the scheduler's OAuth SA). The
`google-cloud-scheduler` dep is in `requirements.txt`. Manual fallback if needed:
`Retry-Dates: <date>` after the window closes still works.

**ADP earnings ready-dialog timeout (configurable, 2026-06-25 fix).** After the
"Download → Excel (.xlsx)" click, ADP queues async report generation and shows a
"Your report is ready to download" modal when it finishes. This can take 3–90+ seconds
on loaded servers. The default wait is **90 s** (raised from the original 45 s that caused
the 2026-06-23 nightly failure). Set `BHAGA_ADP_EARNINGS_READY_TIMEOUT_MS` to override:

| Env var | Default | Effect |
|---|---|---|
| `BHAGA_ADP_EARNINGS_READY_TIMEOUT_MS` | `90000` | Max ms to wait for the ready-dialog button |

The wait polls a ranked list of fallback selectors (`[data-test-id="download-report"]`,
`getByRole("button", name=/Download report/i)`, `[aria-label="Download report"]`) every 1 s.
On total timeout a full-page screenshot + HTML snapshot are written to
`~/.bhaga/state/screenshots/adp-earnings-ready-dialog-missing-<ts>.{png,html}` for post-mortem
review. (Files are local only; no automatic GCS upload is implemented.)

**Concurrent-execution guard (ADP — distributed scrape lock).** ADP still uses a browser. If two
Cloud Run executions run simultaneously, the ADP scrape lock (`ScrapeLockHeldError`) prevents
duplicate SMS OTPs. This guard remains active for ADP. For Square there is no lock needed
(the REST API is stateless and idempotent).

1. **Webhook dedup** — the Slack webhook (`cloud/webhook/handler.py`) discards Slack-retry deliveries
   (`X-Slack-Retry-Num > 0`) and stores event IDs in Firestore (`webhook_events/<event_id>`) with a
   5-minute TTL. Before triggering a Cloud Run job it checks for a non-terminal execution of the same
   date (`_is_already_running`; fail-open).

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

**Rendering/verifying/comparing/screenshotting a dashboard panel does NOT need
this `BHAGA_SECRETS_BACKEND`/`BHAGA_IMPERSONATE_SA`/ADC dance at all** — see
`agents/bhaga/grafana/README.md` § Auth model. That tooling (`verify_panels.py`,
`compare_panels.py`, `capture_screenshot.py`, `evidence.py`) talks to Grafana
Cloud with a Bearer token; Grafana queries BigQuery server-side. Only applying
schema DDL (`ensure_schema()`) needs the cloud service account. A `config.yaml
not found` error from `status.py` is unrelated and does not block Grafana work.

### Pipeline Health row (updated to two-table design, PR feat/bhaga-dashboard-pipeline-health, 2026-06-12)

The top "0. Pipeline Health" row on the BHAGA Analytics dashboard shows two side-by-side tables:

**Table 1 — Pipeline Runs** (left half, `vw_pipeline_runs`): last 30 nightly `daily_refresh` outcomes, newest first.

| Column | Source | Notes |
|---|---|---|
| Run Time (CT) | `pipeline_runs.started_at_utc` | Formatted `America/Chicago`; empty until first nightly run |
| Status | `pipeline_runs.status` | `success` (green) / `failed` / `halted` (red) / `otp_pending` (yellow) |
| Failed Step | `pipeline_runs.failed_step` | Step name from `run_step()` or OTP/phase guard; blank when healthy |
| Error | `pipeline_runs.error` | Exception message; blank when healthy |

**Table 2 — Data Source Pulls** (right half, `vw_source_pulls`): last 50 per-source pull attempts, newest first.

| Column | Source | Notes |
|---|---|---|
| Source | `source_pulls.source` | `square` / `adp` / `google_reviews` |
| Pull Time (CT) | `source_pulls.started_at_utc` | Formatted `America/Chicago` |
| Status | `source_pulls.status` | `success` (green) / `failed` (red) |
| Error | `source_pulls.error` | Exception type + message; blank on success |

**Attempt-only semantics:** only sources that actually ran a scrape appear in `source_pulls`. Sources skipped because their step marker was already present (`step_already_done`) or suppressed via `--skip-*` flags never enter the phase-1 results dict and are not recorded. Both tables are empty until the first nightly run after migration 017 is applied.

**How run outcomes are recorded:** `daily_refresh.main()` generates a `run_id` (UUID4 hex, 32 chars) at startup, then calls `_run_refresh()`. In its `finally` block it calls `_record_pipeline_run(run_id=…)`, which (best-effort, skipped on `--dry-run`, never raises) MERGEs one row into `pipeline_runs` (merge key: `run_id`) and one row per attempted source into `source_pulls` (merge keys: `run_id` + `source`). **Prod-only gate (CLOUD_RUN_JOB):** records ONLY when the `CLOUD_RUN_JOB` env var is set (present in all real Cloud Run job/execution environments) OR when `BHAGA_RECORD_PIPELINE_RUN=1` is explicitly set (opt-in for intentional cloud-shell backfills). Laptop and GitHub CI runs never set `CLOUD_RUN_JOB` and therefore never write to `pipeline_runs` — this prevents Pipeline Health from showing non-prod rows. Greppable log lines: `[pipeline_runs] skip: <reason>` or `[pipeline_runs] recorded run_id=…`. Sandbox staging runs write only to `BHAGA_BQ_DATASET` (default prod `bhaga` blocked by `datastore._assert_sandbox_write_isolation`). Using MERGE means a re-run of the recorder converges to the same row rather than duplicating. Possible run statuses:

- `success` — `_run_refresh()` returned 0 and model was verified OK
- `failed` — returned 1 (any step/guard failure; `failed_step` records the first one)
- `halted` — returned `EXIT_HALTED` (3) because the circuit breaker is tripped
- `otp_pending` — returned 0 early because the OTP handshake is awaited

`vw_pipeline_runs` = last 30 rows from `pipeline_runs` ordered by `recorded_at_utc DESC`. `vw_source_pulls` = last 50 rows from `source_pulls` ordered by `started_at_utc DESC`.

**KDS: Order Date picker** defaults to the most recent successfully-completed run date (falls back to the latest KDS date if no successful run recorded yet, or if KDS was skipped that night).

### Architecture

- **BQ dataset:** `jarvis-bhaga-prod.bhaga`
- **Raw tables:** `square_transactions`, `adp_shifts`, `adp_punches`, `adp_wage_rates`, `square_daily_rollup`
- **Curated views:** `vw_daily_sales`, `vw_tips_by_hour`, `vw_labor_daily`, `vw_labor_weekly`, `vw_sales_labor_daily`, `vw_employee_hours_summary`
- **Model tables:** `model_daily`, `model_labor_daily`, `model_labor_weekly`, `model_labor_period`, `model_tip_alloc_period`, `model_tip_alloc_daily`, `model_period_summary`, `model_forecast_daily`
- **Pipeline run log:** `pipeline_runs` (migration 016) — one appended row per terminal outcome; queried via `vw_pipeline_runs`
- **Source pull log:** `source_pulls` (migration 017) — one appended row per per-source pull attempt; queried via `vw_source_pulls`
- **Model views (Grafana BI contract):** `vw_model_labor_daily`, `vw_model_period_summary`, `vw_model_forecast`, `vw_forecast_accuracy`, `vw_forecast_exclusions`, `vw_pipeline_runs`, `vw_source_pulls`
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

The model compute is a single BQ-canonical path:
- **`materialize_model_bq`** computes the model from BQ raw into `model_*` tables.
- **`render_model_sheet_from_bq`** incrementally upserts Sheet model tabs from BQ (by natural key, `--since` window).

After model writes, `reconcile_model.py` (non-fatal) compares each Sheet model tab against its BQ table and alerts on drift. See `agents/bhaga/scripts/reconcile_model.py`.

**Recovery retrigger (`/bhaga-cloud refresh <date>`):** When BQ raw data is already present and scrape markers are done, `_prepare_projection_recovery` auto-clears projection step markers if the prior run for that date failed (or a drift probe fails). The retrigger skips OTP/login and re-runs `render_raw_sheets` → `materialize_model_bq` → `render_model_sheet_from_bq` only. Grafana Pipeline Health shows `recovery_retrigger=TRUE` on such runs; an empty Data Source Pulls list is expected (no new scrapes).

### Re-deploying the dashboard

```bash
# from repo root, after activating venv:
python3 agents/bhaga/grafana/deploy.py --org-slug steadyangelfish2985

# or CI: push a change to agents/bhaga/grafana/** → GitHub Action auto-deploys
```

**Datasource UID is bound at deploy time (don't commit it).** `dashboard.json` keeps
panels datasource-agnostic by pointing every `datasource` at the `${ds_bigquery}`
template variable. A `type: datasource` variable's value is the datasource *name*, but
panels reference it as `"uid": "${ds_bigquery}"` — so Grafana looks up a datasource whose
UID equals the *name*, fails with "Data source not found", and **every panel shows "No
data".** `deploy.py` fixes this by calling `bind_datasource_uid()` (see
`agents/bhaga/grafana/deploy.py`): it resolves the real datasource UID via
`get_bigquery_datasource_uid()` and rewrites every `${ds_bigquery}` ref + the var's
`current` value to that literal UID before `push_dashboard`. The repo stays UID-free.

**Panel SQL must use BigQuery-valid column aliases.** BigQuery Standard SQL treats
`"..."` as a *string literal*, so `AS "Orders"` is a syntax error — use backticks
(`` AS `Orders` ``). Output field names also may not contain `/` or `$` (spaces and
hyphens are fine), so use e.g. `` AS `Hrs per 1k Net Sales` `` not `Hrs / $1k …`. Field
names still drive the `byName` field overrides, so keep them in sync. Prefer
BigQuery-valid snake_case aliases (e.g. `` AS `hrs_per_net_sales` ``) and set the
human label with a `displayName` field override — that sidesteps the `/`/`$`
restriction entirely.

**Hour fields use the `suffix: h` custom unit, not the built-in `h`.** Grafana's
built-in `h` (and `m`) units are *durations* that auto-scale — `60` renders as
`2.5 day` and `0.15` as `9 min`, which is wrong for shift-hours and hours-per-X
panels. Use the custom unit `suffix: h` (and `suffix: min` for the slow-orders
table) so the raw number is shown with a unit and no rescaling.

**Verify panels return data (read-only, end-to-end):** full tool catalog
(including prod-vs-branch parity and the one-command PR-evidence entrypoint)
is `agents/bhaga/grafana/README.md` — start there.

```bash
# Runs every panel's rawSql through Grafana /api/ds/query with the real datasource UID
# and the dashboard's template-var defaults; prints section | id | status | rows.
python3 agents/bhaga/grafana/verify_panels.py
python3 agents/bhaga/grafana/verify_panels.py --var date_from=2026-05-01   # override a var
python3 agents/bhaga/grafana/verify_panels.py --fail-on-empty             # 0-row = failure
```

> **Incident (2026-06-07, PR for grafana-datasource-uid).** Every BQ panel showed "No
> data" from two independent bugs: (1) the `ds_bigquery` variable carried the datasource
> *name* instead of its UID (panels → "Data source not found"); (2) the 11 timeseries
> panels used invalid double-quoted aliases (`AS "Orders"`). `verify_panels.py` is the
> regression guard — it caught the alias bug that earlier ad-hoc testing had masked.

### Running SQL migrations

Migrations in `core/migrations/*.sql` are applied automatically:

1. **On merge to `main`** — `.github/workflows/deploy.yml` runs `ensure_schema()` after the Cloud Run image deploy; `.github/workflows/grafana-dashboard-sync.yml` runs it before pushing dashboard JSON (so new panel SQL never references columns that do not exist yet).
2. **On every Cloud Run nightly / manual job** — `daily_refresh` calls `ensure_schema()` at startup when `BHAGA_SECRETS_BACKEND=gcp`.

Manual apply (one-off / laptop sandbox only):

```bash
python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
```

Migrations are idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE OR REPLACE VIEW`, `ADD COLUMN IF NOT EXISTS`). Latest: `019_pipeline_recovery.sql` (`recovery_retrigger` on `pipeline_runs`).

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

## 15. Labor Forecast (added 2026-06-09)

### Overview

Daily order/item forecasts are BQ-authoritative (since 2026-06-10), replacing the retired `labor_daily_forecast`
Sheet tab. The nightly pipeline (`materialize_model_bq.py`) generates a 30-day forward window and
loads it into `model_forecast_daily` (merge key: `date`). **Future rows** are merged each night — they update until the day passes. **Past rows** are gap-fill-only: backfill rows are only inserted for dates not already present, so historical forecasts are immutable. Each row is tagged with `forecast_model_version` so accuracy tracking knows which model predicted a given day.

**Growth model (wow_median_4wk_v2):** `growth = median` of consecutive same-weekday week-over-week order ratios over the trailing 28 days, clamped to [0.80, 1.20]. Each ratio is a true 1-week step (orders[d] / orders[d-7] for matching weekdays). Pooling ~19 ratios + taking the median is robust to one anomalous week (e.g. holiday). Returns 1.0 when fewer than 2 valid pairs exist.

**AOV auto-exclusion:** `compute_outlier_stats` now also detects anomalously-low Average Order Value days (AOV = net_sales / orders). A robust z-score on daily AOV values (same MAD scheme as order volume) flags comped / heavy-discount days that the order-volume signal would miss (`aov_z < -2.5`). These days are set `exclude_default=True` so the nightly pipeline marks them `forecast_exclude=TRUE`.

### Tables and views

| Object | Type | Description |
|---|---|---|
| `model_forecast_daily` | Table | One row per date: `date`, `forecast_orders`, `forecast_items`, `forecast_generated_at`, `forecast_model_version`, `materialized_at_utc` |
| `vw_model_forecast` | View | Next 30 days with COALESCE(actual@-7d, forecast@-7d) prior-week comparison, % change, `goal_shift_hours`, and `scheduled_hours` (from ADP) |
| `vw_forecast_accuracy` | View | Past forecast days joined to actuals (forecast vs actual orders/items) |
| `vw_forecast_exclusions` | View | Recent 60 days of input rows with `forecast_exclude` flag, reasons, `net_sales`, `aov`, and prior-week comparisons |

### Grafana dashboard section

Section 7 "Labor Forecast" on the BHAGA Analytics dashboard shows:
- **Labor Forecast — next 30 days table** (panel 71, `vw_model_forecast`): Day-of-week (`dow`), date, forecast orders/items, prior-week actuals (falling back to that day's forecast when orders=0 / failed), % change vs prior week, **Goal Total Hours** (forecast_items × goal_hours_per_item; covers part-time + full-time), **Scheduled Part Time** (ADP-scheduled hours, excludes the one full-time employee; current+next week only), Sched PT − Goal Total gap (abs hrs + %)
- **Goal Total Hours vs Scheduled Part Time** (panel 74, `vw_model_forecast`): two-line chart — dashed Goal Total Hours vs solid Scheduled Part Time (same inputs as panel 71; goal updates on nightly forecast rebuild for upcoming days, frozen for past dates in `model_forecast_daily`)
- **Forecast vs Actual — Orders** (panel 72, `vw_forecast_accuracy`): order forecast vs actual history — split to half-width in v33
- **Forecast vs Actual — Items** (panel 75, `vw_forecast_accuracy`): item forecast vs actual history — new panel in v33, side-by-side with panel 72
- **Forecast Inputs / Exclusions table** (panel 73, `vw_forecast_exclusions`): recent input days with exclusion flags, `net_sales`, `aov`, prior-week comparisons — v33 adds AOV/net-sales columns
- **KDS goal** uses `$goal_kds_p95_min` (default 8 min) as of v33; previously p99.
  Data for scheduled hours comes from the nightly **best-effort** ADP schedule scrape
  (`adp_scheduled_daily`, migration 013); a scrape failure does not fail the nightly run.

### Applying the migration (one-time)

```bash
BHAGA_DATASTORE=bigquery BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \
  python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
```

Migrations `011`, `012`, `013`, `014`, and `015` are all idempotent. They are applied automatically on deploy and at nightly job startup via `ensure_schema()` (see § Running SQL migrations). After applying, the nightly job will populate `model_forecast_daily` on its next run (or trigger a manual refresh — see §6).

Migration `015` adds `dow` (Mon/Tue/…) to `vw_model_forecast` and zero-gates the prior-week actual (`IF(orders > 0, orders, NULL)`) so failed/closed prior days (orders=0) fall back to that day's stored forecast row instead of showing NULL.

### Forecast nightly cadence

Each successful `daily_refresh` (nightly Cloud Run job at 21:30 CT):

1. **Rebuilds `model_labor_daily`** — re-evaluates outlier flags, `forecast_exclude`, and AOV-based auto-exclusion for all historical operating days. The 5/24 AOV exclusion (`aov_z ≈ -7.0`) becomes `forecast_exclude=TRUE` after the first successful full materialize (requires Sheets/ADP creds that are only available in the Cloud Run environment).
2. **Gap-fills `model_forecast_daily`** — writes forecast rows only for dates not already present (historical rows are frozen, model version preserved). **Today is now included in the forward window**, so the current day always has a forecast row — this acts as the prior-week fallback for next Tuesday's panel-71 `prior_wk_orders`.
3. **Re-scrapes ADP scheduled hours** (current + next week) into `adp_scheduled_daily`; `Scheduled Part Time` in panel 71 updates automatically.
4. **`prior_wk_orders` / `prior_wk_items`** in `vw_model_forecast` reflect the actual from 7 days ago (when orders > 0), falling back to the forecast row for that day (covers today-as-prior-week). So these values evolve each night as more actuals land.
5. **Goal Total Hours** follow directly from `forecast_items × $goal_hours_per_item`, so they update whenever the forecast rows are rebuilt.

### Forecast exclusion override

To flag an anomaly day so it's excluded from the forecast seed: set `forecast_exclude = TRUE` on the
`model_labor_daily` BQ row for that date. The forecaster reads `model_labor_daily` via
`_get_parsed_rows(exclude_flagged=True)` and will skip it on the next run.

### Disabling the forecast step

Set `BHAGA_SKIP_FORECAST=1` in the Cloud Run job environment to skip the forecast load entirely
(e.g. during debugging). The step is non-fatal by default — a failure emits a `WARNING` log and
continues.

---

## 16. BQ as single source of truth (added PR #33, 2026-06-05)

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

> **`order_reco_max_tubs` (default `120`, Issue #137)** — the freezer capacity ceiling for the
> dual-date Order Recommendation (Grafana panels 81/82). Change it with
> `/bhaga-cloud config set order_reco_max_tubs <N>` — expected to change *seldomly* (only when the
> shop's freezer capacity itself changes, e.g. a bigger freezer), never per-day. Setting it triggers
> an immediate recompute (`cloud/webhook/handler.py::_handle_config_set` dispatches
> `_refresh_order_reco` async). The recommendation is a MATERIALIZED table, `bhaga.inventory_order_reco`
> — see `agents/bhaga/knowledge-base/DOMAIN.md` § migration 031 for why (BQ query-planning complexity
> limit) and `.cursor/rules/bhaga.mdc` for the full invariant. It is recomputed by
> `core.order_reco.refresh_order_reco()` from **three** triggers: (1) the nightly `daily_refresh.py`
> step `refresh_order_reco` (runs after `ingest_inventory`, non-fatal), (2) the restock modal's
> `view_submission` handler after any schedule/orders write, and (3) this config-set. If the tables
> on the dashboard look stale, check which of the three last ran via
> `python3 -m agents.bhaga.scripts.status --store palmetto` (nightly step) or re-trigger manually:
> ```bash
> BHAGA_DATASTORE=bigquery python3 -c "from core.order_reco import refresh_order_reco; refresh_order_reco('palmetto')"
> ```

> **`data_window_end` is DERIVED — never stored in `store_config`.** It is computed
> live as `MAX(square_transactions.date_local)` via `core.store_config.resolve_data_window_end()`.
> `set_config()` raises `ValueError` if you attempt to write this key. The review crediting
> pipeline (`process_reviews`), status doctor, and Slack `/bhaga status` all use the derived
> value so the review window always tracks the latest Square data.
>
> **Troubleshooting: reviews held back / window frozen at a stale date.**
> Run: `bq query --project_id=jarvis-bhaga-prod 'SELECT * FROM bhaga.store_config WHERE key="data_window_end"'`
> If any rows appear, a past migration wrote a stale value. Remove it with:
> ```python
> from core.store_config import delete_config
> delete_config("palmetto", "data_window_end")
> ```
> Then retrigger: `/bhaga-cloud refresh <date>` (or `gcloud run jobs execute …`). The next run
> will derive the correct window and release any held-back reviews.

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

### Post-merge cutover checklist (one-time)

After PR #33 merges and the new image deploys, run the **one-time prod cutover**:
migrations → seed `store_config` → **full-history `--replace` rebuild of raw BQ** (retires the
`square_item_lines` double-count; needs OTP) → verify parity → flip `BHAGA_SHEET_FROM_BQ=1`.

The ordered, copy-pasteable runbook is **[`docs/POST_MERGE_BQ_CUTOVER.md`](docs/POST_MERGE_BQ_CUTOVER.md)**.

**Fresh-scrape `--replace` (`BHAGA_RAW_REPLACE=1`).** TRUNCATE-then-load each raw table so a fresh
full-history scrape owns the whole table. This is the correct mode for the cutover rebuild (and for
the `full-history-bq-sandbox` sandbox scenario) because (a) the scrape is authoritative for the full
window and (b) it sidesteps the MERGE "must match at most one source row per target row" error when a
single scrape batch carries duplicate natural keys (e.g. ADP earnings line-items). **Only ever use it
with a full-history window** — a partial-window `--replace` would TRUNCATE then drop out-of-window
rows. Nightly runs never set it (they MERGE-upsert the coverage gap, idempotent by natural key).

### `square_item_lines` dedupe — why a one-time rebuild, then normal incremental

`line_seq` used to be a file-global row index; it is now a **stable per-group counter**
(`skills/square_tips/transactions_backend.py`). Legacy rows with the old global index (e.g. `3500`)
never collided with new per-group rows (`0`) under the merge key, so prod accumulated ~2× duplicate
item lines. The fix is the one-time Step 3 `--replace` rebuild above; after that, nightly MERGE is
idempotent (re-scrapes hit identical keys) and the model self-heals via `materialize_model_bq`. No
daily wipe. Full reasoning in `docs/POST_MERGE_BQ_CUTOVER.md` § "Why a one-time rebuild".

---

## 17. Operator Console (Issue #132, replaces Grafana as the primary UI)

### What it is

A Next.js app (`apps/operator-console/`) reading/writing the same `bhaga` BQ dataset as
this pipeline, deployed as its own Cloud Run **service** (`operator-console`, not a job) with
`--no-allow-unauthenticated` + Cloud Run **direct IAP** (GA, no load balancer). Operators open the
plain `https://operator-console-…run.app` URL in a browser and sign in with Google — no terminal,
no proxy command. The legacy `gcloud iap oauth-brands` API requires a Google Workspace
organization and is deprecated project-wide (confirmed 2026-07-04), but a custom **"External"**
OAuth client — provisioned once via the Cloud Console's Google Auth Platform + the Cloud Run
Security tab's IAP checkbox — works fine without an org (reversing the earlier "no IAP" pivot; see
`docs/operator-console/PLAN.md` decisions log, 2026-07-05). Grafana **stays live** (coexistence,
not a replacement in v1) — the console is additive, giving the operator navigation, goal tracking,
and write-backs Grafana never had.

Full design/build docs: [`docs/operator-console/`](docs/operator-console/) (`PLAN.md` — living plan
+ decisions log + milestones; `ARCHITECTURE.md`; `EXECUTION.md` — step-by-step; `COST.md`).
App-level dev loop: [`apps/operator-console/README.md`](apps/operator-console/README.md).

### Deploy

Automatic on push to `main` touching `apps/operator-console/**` via
[`.github/workflows/operator-console-deploy.yml`](.github/workflows/operator-console-deploy.yml):
builds the container, applies any pending `core/migrations/*.sql` (same runner as this pipeline —
`core.datastore.ensure_schema()`), deploys `--no-allow-unauthenticated --iap`, then grants
`roles/iap.httpsResourceAccessor` to each operator account. No manual deploy step. `--iap` is
idempotent against the one-time Console-only provisioning below — it does not redo it.

### One-time IAP provisioning (Console-only, cannot be scripted)

Two Console steps, done once per project (already done for `jarvis-bhaga-prod` — see
`docs/operator-console/PLAN.md` decisions log, 2026-07-05):
1. **Google Auth Platform branding** — `console.cloud.google.com/auth/overview` → "Get started" →
   App name "Palmetto Operator Console", **External** audience (Internal is greyed out — no
   Workspace org), any contact email you're signed in as.
2. **Enable IAP on the Cloud Run service** — service → **Security** tab → check
   **Identity Aware Proxy (IAP)** alongside IAM → Save. Google auto-creates the OAuth client tied
   to the branding above; no manual client creation.

Both steps only need to happen again if the branding/OAuth client is ever deleted.

### Accessing the console (Cloud Run direct IAP — browser sign-in)

Open `https://operator-console-887772634501.us-central1.run.app` in a browser. IAP redirects to
Google's account chooser ("Sign in to Palmetto Operator Console"), and after picking an account
either loads the console (if authorized) or shows IAP's own "You don't have access" page with the
denied email printed (if not). No terminal, no proxy command, no app-level allowlist — access is
pure IAM.

**Granting a new admin/operator** — one command, one layer:
```
gcloud iap web add-iam-policy-binding \
  --resource-type=cloud-run --service=operator-console --region=us-central1 --project=jarvis-bhaga-prod \
  --member=user:NEW@EMAIL --role=roles/iap.httpsResourceAccessor
```
Any Google account works — no domain restriction, since IAP's IAM is the sole gate. IAM changes
can take up to a couple of minutes to propagate; if a just-granted account still sees "no access",
visit `<service-url>?gcp-iap-mode=CLEAR_LOGIN_COOKIE` to force IAP to re-check the current session
against the latest policy instead of a cached session decision. Revoke with
`gcloud iap web remove-iam-policy-binding` (same flags). A Google Groups (`admin`/`operator`) model
for `mypalmetto.co` is a documented follow-up so grants don't need per-user `gcloud` calls; per-user
grants are the current mechanism.

### Operating

- **Identity:** the signed-in operator's email is available server-side via
  `lib/auth/identity.ts::operatorEmail()`. IAP forwards the caller's email in the plain
  `X-Goog-Authenticated-User-Email` header (trustworthy because only the IAP service agent holds
  `run.invoker` on the Cloud Run service — end users hold `roles/iap.httpsResourceAccessor`
  instead, never direct invoker), and additionally signs a JWT in `X-Goog-IAP-JWT-Assertion`.
  `operatorEmail()` verifies that JWT via `google-auth-library` (`OAuth2Client.getIapPublicKeys()` +
  `verifySignedJwtWithCertsAsync()`) against the direct-Cloud-Run-IAP audience format
  `/projects/{PROJECT_NUMBER}/locations/{REGION}/services/{SERVICE_NAME}`, and requires the JWT's
  `email` claim to agree with the plain header before trusting either — so a header-forwarding
  misconfiguration can't silently downgrade this to an unverified header. Used as `updated_by` on
  every write, same field the Slack `/bhaga-cloud` commands stamp. Local dev has no IAP headers at
  all — `BYPASS_IAP_EMAIL` in `.env.local` stands in.
- **Caching:** every page uses `export const dynamic = "force-dynamic"`, never `revalidate` —
  Next's Full Route Cache would otherwise serve a cached render at the CDN edge to a new
  unauthenticated caller regardless of Cloud Run's IAM check on *that* request (found + fixed
  2026-07-05 while re-locking the preview after the temporary public-access review window).
- **Write parity with Slack:** every console write in `lib/bq/writes.ts` mirrors the exact
  MERGE/DELETE/INSERT statement `cloud/webhook/handler.py` uses for the equivalent Slack command
  (restock, training, config/goals) — the two paths converge on identical rows, never diverge.
- **New table (M4):** `recognition_bonuses` (migration `033_recognition_bonuses.sql`) — manual
  per-employee bonus, separate from the automated `vw_review_bonus_detail` (migration 026).
- **New goal keys:** `goal_net_sales_weekly`, `goal_net_sales_monthly`, `goal_labor_pct_max`,
  `goal_food_cost_pct_max`, `goal_speed_on_time_pct_min`, `goal_inventory_runway_days_min` — all in
  `store_config`, edited via the console's Home → "Edit goals" drawer (no Slack command for these).
- **Troubleshooting a stuck write:** every write function in `lib/bq/writes.ts` is a plain
  parameterized query — reproduce it directly against BQ (see `docs/operator-console/PLAN.md`
  decisions log for two real bugs already caught this way: a row-sanitizer that corrupted any
  column literally named `value`, and an INT64-vs-FLOAT64 TVF param mismatch).
- **Local dev against live BQ:** `apps/operator-console/README.md` § Local development
  (`gcloud auth application-default login`, `BYPASS_IAP_EMAIL` for local identity).
