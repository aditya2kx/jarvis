# BHAGA Scripts

Agent-specific orchestration for the BHAGA nightly pipeline. Reusable logic lives in `skills/`
(`square_tips`, `adp_run_automation`, `tip_pool_allocation`, `tip_ledger_writer`, `slack`,
`bhaga_config`); these scripts are the glue that wires those skills together and run **in the cloud**
as a Cloud Run Job.

> **Operate the live system from [`RUNBOOK.md`](../../../RUNBOOK.md).** Behavioral invariants are in
> [`.cursor/rules/bhaga.md`](../../../.cursor/rules/bhaga.md). What the data *means* (orders, items,
> labor, hourly vs full-time, KDS, tips, reviews, every metric) is in the domain data dictionary
> [`../knowledge-base/DOMAIN.md`](../knowledge-base/DOMAIN.md). This file is the **code map** + the
> guide for **extending the data model**.

---

## The nightly pipeline (what `daily_refresh.py` does)

Entry point for the Cloud Run Job is `daily_refresh.py` (via `daily_refresh_wrapper.py`). Order:

1. **BQ coverage gap resolver**: query `bhaga.square_transactions` via `bq_coverage.missing_ranges`
   to find which business days are absent → set `gap_start = earliest_missing_day`. If BQ is
   unavailable, falls back to reading `data_window_end` from the Model sheet config tab. This replaces
   the old sheet-based `_read_data_window_end_from_sheet` / `compute_gap_window` as the primary path.
2. **Scrape Square** transactions for the gap (`skills/square_tips/`), dedupe-append.
3. **Scrape ADP** timecards / earnings for overlapping pay periods (`skills/adp_run_automation/`).
   2FA, if challenged, goes through the **OTP gate** (see below).
4. **Load scrapes → BigQuery (primary)** (`backfill_from_downloads.py`, requires `BHAGA_DATASTORE=bigquery`):
   maps parse-output dicts through `map_*` functions and calls `load_rows` (MERGE upsert) for all 11 raw
   BQ tables. BQ is the **single source of truth**. Handles: `square_transactions`, `square_daily_rollup`,
   `square_item_lines`, `square_item_daily`, `square_kds_daily`, `square_kds_tickets`,
   `adp_shifts`, `adp_punches`, `adp_wage_rates`, `adp_earnings`.
   If `load_raw_bigquery` fails, `square.done` and `adp.done` markers are **cleared** so the next
   retry re-scrapes fresh data (retry-skips-rescrape guarantee).
4b. **Render raw Sheets from BQ** (`render_raw_sheet_from_bq.py`, non-fatal): inverse-maps each BQ raw
   table row → Sheet-header dict and calls `write_raw_*` upsert functions. Preserves historical rows
   outside the `--since` window. Reviews tab rendered separately after `process_reviews`.
   **Contract: raw Sheets are projections of BQ — BQ is authoritative.**
6. **Recompute the Model tabs** (`update_model_sheet.py`): `config, daily, labor_daily,
   labor_weekly, labor_period, tip_alloc_period, tip_alloc_daily, period_summary`
   (+ `labor_daily_forecast` via `forecast.py`), then **upsert `item_operations`** for the gap
   window (`item_operations.py` — incremental, not full-tab rewrite).
   - Config/tunables are read from `bhaga.store_config` via `core.store_config.get_config` (BQ-first,
     Sheet fallback while being seeded). Edit tunables via `/bhaga-cloud config set`.
   - ADP earnings actuals (`adp_paid`, `diff`, `diff_pct`) read from `bhaga.adp_earnings` via
     `load_cc_tips_earnings_from_bq` (hard cutover; GCS XLSX no longer consulted).
7. **Materialize Model → BigQuery** (`materialize_model_bq.py` called via `materialize_model_bq` step,
   non-fatal on legacy path; **canonical model producer** on BQ-canonical path): rebuilds all 7+
   model tabs from BQ raw data (using the same `build_*` functions) and writes to `model_*` BQ
   tables via MERGE. Includes a post-build tip-pool **conservation check** (raises on drift > $0.01).
   Exposes `load_model_rows()` as a shared BQ-write helper used by `process_reviews.py` (M3) and
   `render_model_sheet_from_bq.py` (M2). See `docs/FEATURE_FLAGS.md` for `BHAGA_SHEET_FROM_BQ` cutover.
7b. **Render Sheet from BQ** (`render_model_sheet_from_bq.py`, `render_model_sheet_from_bq` step,
    flag-gated `BHAGA_SHEET_FROM_BQ=1`): reads each BQ `model_*` table and writes the corresponding
    Sheet model tab as a projection. Only runs when `BHAGA_SHEET_FROM_BQ=1`; otherwise the legacy
    `update_model_sheet` Sheet-write path runs unchanged.
7c. **Reconcile Sheet ⇆ BQ** (`reconcile_model.py`, `reconcile_model` step, non-fatal nightly):
    compares each Sheet model tab against its BQ model table cell-by-cell using the same helpers as
    `verify_bq_parity.py` (`_cells_match`, `_compare_tabs`). Fails CI on drift; Slacks on nightly
    drift. See also `.github/workflows/model-reconciliation.yml`.
8. **Reviews** (`process_reviews.py`): pull Google reviews from ClickUp, allocate bonuses via the
   date-bracketed pool model ($20 pool split equally among in-hours part-time staff, effective
   2026-06-08; legacy $10-base / $20-named-shoutout for reviews before that date), rebuild
   `review_bonus_period`. Idempotent on rerun.
7. **Verify the rebuilt Model** — first **mechanically** (`assert_model_tabs_populated`: tabs non-empty,
   KDS joined), then **semantically** (`model_semantics.assert_model_semantics`: tip-pool conservation,
   a closed period's `adp_paid` reconciles **only when** a covering GCS Earnings export actually carries
   that period's "Credit Card Tips Owed" lines — i.e. payroll has run, the cadence-safe gate
   `update_model_sheet.period_has_cc_tip_actuals` — and credited review bonuses survived the rebuild). A
   just-closed/unpaid period legitimately shows `N/A` and is skipped, not failed. A semantic failure
   clears the `update_model_sheet`
   marker (so a rerun REBUILDS) **and trips the circuit breaker** (next §). A green run **auto-clears**
   the breaker.
8. **Heartbeat** success/failure DM to the BHAGA Slack channel (`notify.py`).

**Pipeline halt circuit breaker** (`state_adapter.{get,set,clear}_pipeline_halt`): a semantic-verify
failure is a known-bad regression that would otherwise repeat every night, so it trips a GLOBAL
breaker (Firestore `<collection>/_pipeline_state`; local `~/.bhaga/state/pipeline_state.json`). While
tripped, a fresh `daily_refresh` refuses to run and exits `EXIT_HALTED` (=3, **distinct** from the
OTP-pending `return 0`) with a loud alert; an in-flight OTP READY resume and `--ignore-halt` /
`BHAGA_IGNORE_HALT` pass through, and a fully-healthy verified run auto-clears it. See `RUNBOOK.md` §13.

Per-step **idempotency markers** live in Firestore `runs/<YYYY-MM-DD>`
(`skills/bhaga_config/state_adapter.py`: `mark_step_done` / `step_already_done` / `clear_step`). A
re-run skips steps already marked done. To force a step, clear its marker — see `RUNBOOK.md` § Common
tasks. **Recovery:** when an OTP portal (Square/ADP) succeeds on a later run while downstream markers
are already done from a prior partial run, `daily_refresh._recover_stale_downstream_markers`
invalidates them (via `clear_step`, the sanctioned path) so they recompute on the fresh data. The set
(`_RECOVERY_DOWNSTREAM_STEPS`) is every step that carries portal data to the window, in pipeline order:
`load_raw_bigquery` → `render_raw_sheets` → `update_model_sheet` → `materialize_model_bq` →
`render_model_sheet_from_bq` → `process_reviews` (the `render_*`/`materialize` members were added after
2026-06-08, when a stale projection marker left `data_window_end` stuck despite fresh BQ raw). Always
on (no flag) — safe by construction: idempotent upserts + the post-condition guard verifies
`data_window_end` advanced (RUNBOOK §13).

**Browser-launch resilience:** all scrapes launch Chromium through
`skills/_browser_runtime/runtime.py::launch_persistent`, which retries the launch _setup_ (not the
scrape body, never an auth error) on transient `TargetClosedError` crashes, adds headless-only
container-stability flags, and exposes `browser_healthcheck()` (pre-flight smoke test before an OTP is
spent). Config: `BHAGA_BROWSER_LAUNCH_RETRIES` / `BHAGA_BROWSER_LAUNCH_BACKOFF_MS`. See RUNBOOK §13.

**Browser observability:** `runtime._capture_failure_evidence` uploads screenshot + DOM + meta to
`gs://<cache>/<date>/evidence/` on failure, and `runtime.trace_step(page, label)` captures the full
browser **after each login + item-sales action** to `gs://<cache>/<date>/trace/NN-<label>.png` so the
whole flow is reviewable frame-by-frame with zero reruns. Trace is best-effort/never-raises and off by
default; `BHAGA_TRACE_SCREENSHOTS=1` enables it (set automatically for sandbox runs, off for the prod
nightly). Both honor sandbox write-bucket isolation via `gcs_cache`.

---

## Script catalog

| Script | Role |
|---|---|
| `status.py` | **Run this first for any operational question about whether a run landed.** Read-only freshness checker across all three layers — Sheets (`data_window_end`, `daily`, `tip_alloc_daily`), BigQuery (model_* + raw tables), and Grafana BI contract views (vw_*). Prints a compact table and exits nonzero if any layer is missing the date (alert/CI usable). Anti-drift: its declarative registry is kept in sync with `core/migrations/*.sql` and `agents/bhaga/grafana/dashboard.json` (through dashboard v28) by CI-enforced coupling in `scripts/check_doc_freshness.py` and by sync tests in `test_status.py`. CLI: `python3 -m agents.bhaga.scripts.status --store palmetto [--date YYYY-MM-DD] [--json] [--check-schema]`. |
| `daily_refresh.py` | **Nightly orchestrator.** Gap compute → scrape → raw → model → reviews → **mechanical + semantic verify** → notify. After `assert_model_tabs_populated` it runs `model_semantics.assert_model_semantics` (conservation + adp reconciliation + review-bonus survival) and trips the **pipeline halt circuit breaker** on a semantic failure (refuses fresh runs with `EXIT_HALTED` until a healthy run / `--ignore-halt` clears it). CLI: `python3 -m agents.bhaga.scripts.daily_refresh --store palmetto [--date YYYY-MM-DD] [--skip-reviews] [--ignore-halt] [--dry-run]`. |
| `daily_refresh_wrapper.py` | Thin wrapper / Cloud Run entrypoint around `daily_refresh`. |
| `otp_gate.py` | OTP **checkpoint-and-resume**: writes a pending request to Firestore + Slack, blocks until the webhook records the operator's reply. |
| `backfill_from_downloads.py` | **BQ-primary scrape sink.** Parse the just-downloaded scrape exports (local `extracted/downloads/`) directly into BigQuery raw tables via `map_*` + `load_rows` (MERGE upsert). **Does not read from GCS.** Requires `BHAGA_DATASTORE=bigquery`. Raw Sheets are rendered afterward by `render_raw_sheet_from_bq.py`. **`--replace`** (or `BHAGA_RAW_REPLACE=1`) = fresh full-history mode: TRUNCATE each target table before load, so the scrape fully owns the table and duplicate natural keys in one batch don't trip MERGE. Use ONLY for a full-history backfill (a windowed `--replace` drops out-of-window rows). |
| `bq_coverage.py` | **BQ coverage helper.** `present_days(table, date_col, start, end) -> set[date]` and `missing_ranges(table, date_col, start, end) -> [(start, end), ...]`. Used by `daily_refresh` to determine which business days are absent from BQ and need upstream scraping. `SOURCE_COVERAGE` maps logical source names to `(table, date_col)` pairs. |
| `backfill_item_lines_from_cache.py` | **No extra OTP** — replay GCS-cached `items-*.csv` into raw `item_lines` (GCS default; `--local-only` for tests). |
| `item_operations.py` | Build + upsert Model `item_operations` from `item_lines` + punches. |
| `update_model_sheet.py` | Recompute the **Model** workbook tabs from the raw sheets. Houses the `build_*_rows` functions (one per tab). Loads ADP "Credit Card Tips Owed" **actuals from BQ** via `load_cc_tips_earnings_from_bq` (reads `bhaga.adp_earnings`, returns ISO-string date keys) to populate `adp_paid`/`diff`/`diff_pct` + `period_summary.check_dates`. Reads operator tunables via `_read_config_value` (BQ-first via `core.store_config.get_config`, Sheet fallback). |
| `model_semantics.py` | **Pure, shared semantic post-conditions** (no I/O): `assert_tip_pool_conserved`, the cadence-safe `assert_period_reconciled`, `assert_review_bonus_present`, and the cadence-gating `assert_model_semantics`. One source of truth used by BOTH `sandbox_e2e` (per-PR gate) and `daily_refresh` (nightly), so a regression can't pass one and fail the other. The reconciliation cadence gate itself (`period_has_cc_tip_actuals`) lives in `update_model_sheet` next to the earnings loader it depends on. |
| `process_reviews.py` | Reviews → date-bracketed bonus allocation ($20 pool for on/after 2026-06-08; legacy $10/$20 per-person before) → rebuild `review_bonus_period`. |
| `forecast.py` | Builds `labor_daily_forecast` (staffing solver, guardrails, anomaly detection). |
| `notify.py` | Slack DMs under the BHAGA identity. Always DM through here, never `send_message` directly. |
| `gcs_cache.py` | **Sessions + failure evidence ONLY — not a data pipeline.** `upload_session()`/`download_session()`/`delete_session()` persist / restore / **discard** a portal browser session (`storage_state`) under `<bucket>/_session/` for **trusted-device** reuse (skips 2FA next run); `delete_session()` drops a *poisoned* session (e.g. after a Square anti-bot block) so the next login starts fresh. `evidence_prefix()` / `upload_evidence()` persist failure screenshots+DOM under `gs://<bucket>/<date>/evidence/` so a postmortem needs no rerun. Writes honor `BHAGA_GCS_CACHE_WRITE_BUCKET` (sandbox isolation: write sandbox bucket). The data-file helpers (`upload_file`/`upload_scrape_artifacts`/`download_cached_files`) are **LEGACY** (offline backfill + `sandbox_e2e` replay only) — the nightly pipeline never reads/writes scrape data here; BQ is the single source of truth. |

**Square login anti-bot recovery (2026-06-09).** When Square soft-blocks the headless container it can
render the "Magic link sent" screen with a **blank recipient** and send no email — an undeliverable link.
`skills/square_tips/runner.py::_magic_link_recipient` detects the blank recipient and raises
`SquareDeviceBlockedError` instead of prompting for an impossible paste; `_drive_verification` (the
`attempt`-aware post-password router) discards the poisoned session via `gcs_cache.delete_session` and
raises `_RetryFreshLogin`. `daily_refresh._run_square_session_with_retry` then retries the Square session
**exactly once** with a fresh cookie jar (`storage_state=None`), which often re-presents the SMS-OTP path;
a second block fails cleanly via `notify.square_device_blocked_alert` (no paste prompt) and the next
nightly auto-retries on a fresh egress IP. See `RUNBOOK.md` §13 "Login escalation".

**Concurrent-execution guard (distributed scrape lock, 2026-06-10).** Multiple Cloud Run executions for
the same date can overlap (nightly scheduler + webhook READY-resume + manual `/bhaga refresh` + Slack retry
delivery). A second concurrent Square scrape would fire a duplicate SMS and corrupt the shared GCS session
blob. The guard is layered:
- `cloud/webhook/handler.py`: discards Slack-retry deliveries (`X-Slack-Retry-Num > 0`), stores seen
  `event_id`s in Firestore `webhook_events/<event_id>` (5 min TTL), and checks `_is_already_running`
  before calling `_trigger_cloud_run_job` (fail-open: listing errors allow the trigger).
- `skills/square_tips/runner.py::_acquire_scrape_lock`: acquires a TTL-based lock in
  `skills/bhaga_config/state_adapter.try_acquire_lock` (Firestore `runs/_lock_scrape-square-<store>` in
  cloud, local JSON file on laptop, TTL via `BHAGA_SCRAPE_LOCK_TTL_S`, default 3600 s). A refused
  execution raises `ScrapeLockHeldError` (carries `lock_name`, `held_by`, `acquired_at`, `expires_at`).
- `daily_refresh.py` classifies `ScrapeLockHeldError` via `_is_scrape_lock_held` and calls
  `notify.scrape_concurrency_alert` (distinct from device-blocked and generic failure alerts).
  Records `concurrent_execution` + holder details into `Firestore runs/<date>.failures.square` for
  postmortem-from-state. Every lock transition emits a greppable Cloud Run log:
  `[square lock] ACQUIRED/RELEASED/REFUSED name=… holder=… …`
| `bootstrap_sheets.py` / `share_sheets_with_sa.py` | One-time: create sheets / share with the service account. |
| `sandbox_provision.py` | **Pool-based** sandbox for per-PR e2e: `create-pool` (operator, user creds) pre-creates N slots × 4 sheets shared with the SA; `provision` leases + clears + re-seeds; `teardown` releases. Registry: `sandbox_pool.json`. |
| `sandbox_e2e.py` | **Prod-like, zero-OTP e2e.** provision → seed sandbox raw → **mirror the prod `training_shifts` overlay** → model build → `assert_model_tabs_populated` → evidence → teardown. **`--source prod-raw --period last-closed`** (the CI default when opted in) reads the **PROD raw** Square+ADP sheets directly for the most-recent **closed** pay period (`most_recent_closed_period`) and writes only to the sandbox (read-prod/write-sandbox, hard-asserted), then runs the **strict full-period verify** incl. `assert_tip_pool_conserved` (per-day allocations == pool, cent-exact), **cadence-safe `adp_paid` reconciliation** (`assert_period_reconciled` when `period_has_cc_tip_actuals` confirms a covering Earnings export with CC-tip lines exists — no longer the blessed-`N/A` of commit 6f87f9c; an unpaid just-closed period is skipped, not failed), **and `assert_exemptions_applied`** (proves each worked training shift is dropped from tips, the day's pool redistributes to the rest, whole-period-exempt staff get $0 while partial-exempt staff keep their non-exempt earnings with exempt hours removed, and the period conserves). The overlay mirror (`seed_sandbox_training_shifts_from_prod`) copies the human-owned prod `training_shifts` rows for the window into the sandbox model so the build applies the SAME exemptions as prod. **`--source gcs-replay --auto-window --max-days N`** replays the GCS scrape cache for a small window (local smoke). Imports **no** scrape/login code (enforced by `test_sandbox_e2e.py`). **Opt-in only** (2026-06-09): add the `run-sandbox-e2e` label to a PR or trigger via `workflow_dispatch` — no longer runs on every PR automatically. See `RUNBOOK.md` §13. |
| `sandbox_live_run.py` | **LIVE sandbox run** (real Square/ADP scrape + OTP on **unmerged PR code**) — the only way to reproduce/prove a fix for selector drift. Builds the PR image → deploys `bhaga-sandbox-refresh` (self-wires by inheriting prod's secrets + SA) → live pipeline for a `REFRESH_DATE`. Enforces isolation (`assert_sandbox_isolation`: staging sheets + sandbox GCS write bucket + sandbox Firestore collection — reads prod OK, writes prod NEVER) before any deploy. OTP uses the prod Slack bot but the prompt is labeled `[SANDBOX · PR…]` and the reply resumes the **sandbox** job (sandbox precedence in the webhook); supervised runs set `BHAGA_OTP_ASSUME_READY=1` to take the code inline (no webhook resume needed). On create it inherits prod's secrets + SA + **resources/timeout** + **plain env vars** (`BHAGA_SECRETS_BACKEND=gcp`, …); describe-JSON parsing is schema-robust (v2 + KRM). Supports `--skip <steps>` (scenario scoping → `BHAGA_SKIP_<STEP>`) and `--verify item_sales` (`verify_item_sales()`: a post-run gate that fails the run unless `<dataset>.square_item_lines` has rows for the date — **BQ is the source of truth; GCS is deprecated for data loads and is NOT consulted** — even on a 0 job exit). The sandbox cache bucket is a one-time operator setup (`assert_sandbox_bucket` fails with remediation if absent). See `RUNBOOK.md` §13. |
| `sandbox_scenarios.py` | **Named scenario suite** for live sandbox runs (`item-sales-live` = Square-only via `skip:[adp,reviews,model]` + `verify:item_sales`; `full-live`; …). Selects what runs via committed `.github/sandbox-live.yml` (+ `sandbox-live` label, pre-merge), a `/sandbox run <scenario> [date=…]` PR comment (post-merge), or manual dispatch. `sandbox_workflow_resolve.py` turns the triggering event into a run plan for `.github/workflows/sandbox-live-run.yml`. Each scenario posts evidence as a PR comment. |
| `verify_drilldown.py`, `verify_bq_parity.py`, `verify_against_historical_payroll.py` | Verification harnesses (parity vs historical payroll / BigQuery). |
| `verify_prod_parity.py` | **Cloud-runnable e2e parity tool.** Diffs BQ (raw + model) against the prod Google Sheets for a full window: per-source row counts (BQ vs Sheet tabs, same date filter) plus key-joined, unit-aware value comparison (handles `%`/currency/bool normalization). Dataset is env-driven (`BHAGA_BQ_DATASET`), so it verifies prod `bhaga` or an isolated `bhaga_sandbox`. Needs Sheets auth (`BHAGA_SECRETS_BACKEND=gcp` or `BHAGA_IMPERSONATE_SA`) + `BHAGA_DATASTORE=bigquery`. |
| `backfill_bigquery.py` | **One-shot historical backfill only.** Reads existing raw Sheets → writes BQ. NOT the nightly path. Use to bootstrap BQ raw tables from Sheet history or repair BQ after a migration/truncation. The nightly path is `backfill_from_downloads.py` (scrape files → BQ directly). |
| `materialize_model_bq.py` | Rebuild the computed model from BQ raw data and write to `model_*` BigQuery tables via MERGE. Called by `materialize_model_bq` step in `daily_refresh`. Reuses the same `build_*_rows` functions as `update_model_sheet.py`. Used by the Grafana Cloud dashboard. **Requires the orchestrator SA to hold `roles/bigquery.jobUser` + `roles/bigquery.dataEditor`** (RUNBOOK §14) — without them every BQ job 403s. Guards an **empty BQ raw `square_transactions`** read with a precise `RuntimeError` breadcrumb instead of the old cryptic `max() iterable argument is empty` (run `backfill_bigquery` first). Access errors in `core.datastore.read_query` are re-raised (no longer swallowed into `[]`). Also exposes `load_model_rows()` as the canonical BQ-write helper (used by `process_reviews.py` and `render_model_sheet_from_bq.py`). |
| `render_raw_sheet_from_bq.py` | **Raw Sheet projector.** Reads each BQ raw table (windowed by `--since`; `wage_rates` always all), inverse-maps rows to Sheet-header dicts, and incrementally upserts via `write_raw_*` functions. Non-fatal nightly step. Reviews tab rendered after `process_reviews`. |
| `render_model_sheet_from_bq.py` | **BQ-canonical path only** (`BHAGA_SHEET_FROM_BQ=1`): reads each BQ `model_*` table and **incrementally upserts** (by natural key, `--since` windowing) the corresponding Sheet model tab. Historical rows outside the window are preserved. Called by `render_model_sheet_from_bq` step. See `docs/FEATURE_FLAGS.md` for flip criteria and cleanup plan. |
| `reconcile_model.py` | Compares Sheet model tabs against BQ model tables cell-by-cell (reusing `verify_bq_parity._compare_tabs`). Non-fatal nightly step; CI-blocking when run in the `model-reconciliation` workflow. Reports tip-pool conservation violations. |
| `test_*.py` | Unit tests. Run: `python3 -m pytest agents/bhaga/scripts/`. |

---

## Raw → Model data flow (the mental model)

```
Square / ADP / ClickUp  ──scrape──▶  BigQuery raw tables  ──read──▶  build_*_rows()  ──upsert──▶  Model tabs
  (skills/square_tips,              (bhaga dataset;          (skills/tip_ledger_      (update_model_sheet.py)   (config, daily,
   adp_run_automation,              11 tables via            writer/reader.py)                                   labor_*, tip_alloc_*,
   ClickUp)                         backfill_from_downloads)                                                     review_bonus_period…)
                                         │
                                         ▼  (non-fatal projection)
                               raw Google Sheets  (bhaga_*_raw;
                               schema in tip_ledger_writer;
                               rendered by render_raw_sheet_from_bq)

            │ (parallel)                                           │ (parallel, via materialize_model_bq.py)
            ▼                                                      ▼
       BigQuery raw tables                                   model_* BigQuery tables
       (square_transactions,                                 (model_daily, model_labor_*,
        adp_shifts, adp_punches,                             model_tip_alloc_*, model_period_summary)
        adp_wage_rates)                                            │
            │                                                      │
            └──────────────────── vw_* BigQuery views ─────────────┘
                                        │
                                        ▼
                              Grafana Cloud Dashboard
                      (https://steadyangelfish2985.grafana.net/d/bhaga-analytics-v1)
```

Sheets is the **source of truth**; BigQuery is a **parallel read-only mirror** updated each daily cron run. The Grafana Cloud dashboard reads from BQ views (`vw_daily_sales`, `vw_model_labor_daily`, etc.) via the `grafana-bq-reader` service account. See `agents/bhaga/grafana/` for dashboard-as-code and `skills/grafana_cloud_provisioning/` for provisioning helpers.

> **Dashboard gotchas (RUNBOOK §14):** panels are datasource-agnostic in `dashboard.json` (they point at the `${ds_bigquery}` variable); `deploy.py` binds the **real datasource UID** at push time — committing a name there yields "No data" on every panel. Panel SQL must use **backtick** column aliases (BigQuery rejects `AS "x"`), and output field names can't contain `/` or `$`. Validate any panel change with `python3 agents/bhaga/grafana/verify_panels.py` (runs each panel's SQL via Grafana `/api/ds/query`).

- **Schema registry:** `skills/tip_ledger_writer/schema.py` (`WORKBOOK_SCHEMAS`) defines every tab's
  `header` + `natural_key_columns`. `get_tab_spec(workbook_title, tab_name)` returns it.
- **Writing:** `skills/tip_ledger_writer/writer.py::_upsert_tab` reads the tab, overlays incoming
  records by natural key, reconciles the header, writes back.
- **Reading raw:** `skills/tip_ledger_writer/reader.py` exposes typed readers (below).

---

## Extending the model

Three supported ways to add information. Recipes A & B keep the raw → model contract intact (read raw
sheets, write derived tabs); Recipe C is for when the data isn't scraped yet. For what each field
*means*, see the domain data dictionary [`../knowledge-base/DOMAIN.md`](../knowledge-base/DOMAIN.md)
(§8 explains the two directions; §3 lists every existing field — check it before scraping, the raw
sheets often already have what you want).

### Raw reader catalog (`skills/tip_ledger_writer/reader.py`)

Use these to consume already-scraped data when building a derived column or tab:

| Reader | Returns |
|---|---|
| `read_raw_adp_shifts(sid)` | ADP shift rows |
| `read_raw_adp_punches(sid)` | ADP punch rows (per-punch granularity) |
| `read_raw_adp_rates(sid)` | ADP per-employee pay rates |
| `read_raw_square_transactions(sid)` | Square transaction rows |
| `read_raw_square_daily_rollup(sid)` | Square per-day rollup |
| `read_raw_square_item_daily_rollup(sid)` | Square per-item per-day rollup |
| `read_raw_square_item_lines(sid)` | Square per-item line rows |
| `read_raw_kds_daily(sid)` | Square KDS per-day metrics |

(All take `account="palmetto"` by default. Resolve `sid` from the store profile, never hardcode.)

### Recipe A — add a column to an existing Model tab

Use when the new field is naturally part of an existing tab (e.g. add `tips_per_labor_hour` to
`labor_daily`).

1. **Append the column to the schema header** for that tab in
   `skills/tip_ledger_writer/schema.py` (`WORKBOOK_SCHEMAS[...]["header"]`). **Append at the end** —
   additive changes auto-migrate; reordering/renaming/removing does **not** and will raise.
2. **Emit the new value** in the matching `build_*_rows` function in `update_model_sheet.py`
   (e.g. `build_labor_daily_rows`). Produce the column in the same position you appended it.
3. **Run it.** On the next write, `_reconcile_header` detects the additive drift, widens row 1, and
   pads existing rows with blanks — **no manual sheet edit, no destructive rewrite**. (Old rows show
   blank for the new column until they're recomputed; a full backfill recomputes history.)
4. **Test** (extend the relevant `test_update_model_sheet*.py`) and verify on the sheet.

> The additive-migration contract is the whole reason adding a column is safe: see
> `_reconcile_header` / `_upsert_tab` in `skills/tip_ledger_writer/writer.py`. Only **appended**
> columns migrate automatically.

### Recipe B — create a new derived tab from raw data

Use when the new view doesn't belong on an existing tab (e.g. a `daypart_summary` tab from Square
item rollups).

1. **Register the tab** in `WORKBOOK_SCHEMAS` (`skills/tip_ledger_writer/schema.py`): pick a
   `tab_name`, define `header`, and choose `natural_key_columns` (the columns that uniquely identify
   a row, so reruns upsert instead of duplicate — e.g. `("date_local",)`).
2. **Write a `build_<tab>_rows(...)` function** in `update_model_sheet.py` that reads raw via the
   reader catalog above and returns rows aligned to your header.
3. **Wire it into `main()`** in `update_model_sheet.py` next to the other tab builds, and write it
   with the `tip_ledger_writer` upsert path (same as the existing tabs) so idempotency + header
   reconciliation apply for free.
4. **Add the sheet/tab if new workbook** — most derived tabs live in the existing Model workbook, so
   no new spreadsheet is needed. If you genuinely need a new spreadsheet, add its ID to the store
   profile `google_sheets` block (`palmetto.json`) and resolve via `resolve_sheet_id`.
5. **Test + verify + deploy.**

### Recipe C — capture a NEW field straight from a source (source → raw sheet)

Use when the field **isn't scraped yet** (a new Square column, an ADP field, a review attribute).
Direction 1 in `DOMAIN.md` § Growing the data model.

1. **Emit the field in the scrape backend:**
   - Square → `skills/square_tips/transactions_backend.py` (`parse_csv` / `aggregate_*`).
   - ADP → `skills/adp_run_automation/shift_backend.py` (`daily_shifts` / `raw_punches`) or
     `compensation_backend.py` (`compensation`).
   - Reviews → the parser in `process_reviews.py`.
2. **Append the column to the raw tab's header:** ADP/Square in
   `skills/tip_ledger_writer/schema.py` (`WORKBOOK_SCHEMAS`); reviews in the `*_HEADER_ROW` constants
   in `process_reviews.py`. **Append at the end** so the additive migration handles the live sheet.
3. **Backfill:** re-scrape the window (`backfill_from_downloads.py` / re-run the gap) so the column
   populates history; old rows stay blank until re-scraped.
4. **Surface it in the model** if needed (Recipe A/B), then document it in `DOMAIN.md` §3.

### Recipe D — a high-volume model tab that upserts incrementally (NOT clear-and-write)

Most model tabs are rebuilt every night with `clear_and_write_tab` (cheap at ~hundreds of rows).
**Don't do that for a large tab** (thousands+ of rows, e.g. per-item-line) — a nightly full rewrite
burns Sheets quota and is slow. `item_operations` is the reference implementation of the incremental
pattern:

1. **Register the tab in `schema.py`** with a natural key (so rows upsert, not duplicate).
2. **Build rows in a dedicated module** (`agents/bhaga/scripts/item_operations.py`) reading raw via
   `reader.py`; compute in memory (load punches/rates once, index, then per-row).
3. **Upsert via `tip_ledger_writer`** (`write_model_*`) keyed by natural key — same idempotent path
   as raw tabs — instead of `clear_and_write_tab`.
4. **Scope the nightly run to the gap window.** Wire `daily_refresh.py` to recompute only the gap
   dates, and expose explicit flags on `update_model_sheet.py` for ops/backfill, e.g.
   `--item-operations-only`, `--all-item-operations`, `--item-ops-date-from/to`.
5. **Backfill is a separate one-off script** (`backfill_item_lines_from_cache.py`) that replays the
   GCS scrape cache — see RUNBOOK § "Run a one-off backfill against prod" (**cloud = GCS, never local
   downloads**).

> Rule of thumb: if a model tab can exceed ~1–2k rows or grows unbounded with history, use Recipe D
> (incremental upsert + gap-scoped recompute), not the default clear-and-write of Recipe B.

### Recipe E — exempt an employee/shift from the tip pool

All exclusions funnel through **one chokepoint**, `_is_excluded(employee, date, ...)` in
`update_model_sheet.py`. An excluded `(employee, date)` is dropped from that day's **tip hours
denominator only** (labor% is unaffected), so the full pool redistributes to the other tipped staff.
There are three sources, all sheet-driven (no code change to add an exemption):

| Source | Where | Granularity | Use for |
|---|---|---|---|
| `excluded_from_tip_pool_and_labor_pct` | store profile (`palmetto.json`) | permanent | managers/owners who never tip-pool |
| `training_excluded:<name> = <through-date>` | `config` tab | through a date (inclusive) | bulk "all shifts up to date X were training" |
| `training_shifts` tab (`employee_name \| date \| note`) | own tab | a single `(employee, date)` | precise per-shift training marks |

The per-shift overlay is read by `_read_training_shifts_from_sheet` (mirrors
`_read_training_excluded_from_sheet`), returns `set[(canonical_name, date_iso)]`, and degrades to a
no-op if the tab is absent. It's threaded through `build_daily_rows`, `build_period_results`,
`main()`, **and the verifiers** (`verify_bq_parity.py`) so recomputed parity stays honest. Seed/maintain
rows via `tip_ledger_writer.write_training_shifts` (create-if-missing + idempotent `(employee,date)`
upsert; it preserves rows a human added for other pairs). The tab is **human-owned** — Lindsay/operator
keep it current; the pipeline only reads it.

### After any recipe

- **Tests:** `python3 -m pytest agents/bhaga/scripts/ skills/tip_ledger_writer/`.
- **Deploy:** commit → push `main` → GitHub Actions builds/deploys the image. Local edits don't
  affect prod until deployed (`RUNBOOK.md` § Operating rules).
- **Backfill history** if the new field should be populated for past dates: re-run the model step for
  the historical window (force-rerun per `RUNBOOK.md` § Common tasks).
- **Document:** add the field/tab to the domain dictionary `../knowledge-base/DOMAIN.md` (§3 + the
  relevant metric section), note it in `RUNBOOK.md` § Sheet topology, and add a dated line to
  `PROGRESS.md`.
