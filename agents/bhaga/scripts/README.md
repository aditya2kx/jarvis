# BHAGA Scripts

Agent-specific orchestration for the BHAGA nightly pipeline. Reusable logic lives in `skills/`
(`square_tips`, `adp_run_automation`, `tip_pool_allocation`, `tip_ledger_writer`, `slack`,
`bhaga_config`); these scripts are the glue that wires those skills together and run **in the cloud**
as a Cloud Run Job.

> **Operate the live system from [`RUNBOOK.md`](../../../RUNBOOK.md).** Behavioral invariants are in
> [`.cursor/rules/bhaga.mdc`](../../../.cursor/rules/bhaga.mdc). What the data *means* (orders, items,
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
2. **Ingest Square** transactions, item sales, and KDS via Square OAuth REST API
   (`skills/square_api/ingest.py` + `skills/square_api/kds_reporting.py`) — no browser, no
   CSV files, no OTP. Data flows: Square API → in-memory rows → `map_square_*` → BQ.
3. **Scrape ADP** timecards / earnings / **team schedule** for overlapping pay periods
   (`skills/adp_run_automation/`). 2FA, if challenged, goes through the **OTP gate** (see below).
   The schedule scrape (forward scheduled hours, current + next week) is **best-effort** — a
   failure is non-fatal to the nightly run (see `daily_refresh._adp_bundle_then_raise`).
4. **Load ADP → BigQuery (primary)** (`backfill_from_downloads.py --skip square`, requires
   `BHAGA_DATASTORE=bigquery`): maps ADP parse-output dicts through `map_*` functions and calls
   `load_rows` (MERGE upsert). Square data is written directly by step 2 (no download file). BQ is
   the **single source of truth**. Handles: `adp_shifts`, `adp_punches`, `adp_wage_rates`,
   `adp_earnings`, `adp_scheduled_daily` (per-day scheduled hours, parsed from `Schedule-*.json`
   via `schedule_backend.build_schedule_records`). Square tables (`square_transactions`,
   `square_daily_rollup`, `square_item_lines`, `square_item_daily`, `square_kds_daily`,
   `square_kds_tickets`) are populated in step 2.
   If `load_raw_bigquery` fails, `square.done` and `adp.done` markers are **cleared** so the next
   retry re-runs fresh data (retry-skips-rescrape guarantee).
4b. **Render raw Sheets from BQ** (`render_raw_sheet_from_bq.py`, non-fatal): inverse-maps each BQ raw
   table row → Sheet-header dict and calls `write_raw_*` upsert functions. Preserves historical rows
   outside the `--since` window. Reviews tab rendered separately after `process_reviews`.
   **Contract: raw Sheets are projections of BQ — BQ is authoritative.**
6. **Materialize Model → BigQuery** (`materialize_model_bq` step): computes all model tabs from BQ
   raw data (shared `build_*` functions in `update_model_sheet.py`) and writes to `model_*` BQ tables.
   Includes post-build tip-pool conservation check.
7. **BQ-internal verify** (`verify_model_bq()`): queries model BQ tables directly (row counts +
   KDS column check + semantic tip/ADP/review checks). Replaces Sheet-reading verify. No Sheet
   projection steps (deleted 2026-06-15 Sheets exit). **Recovery retrigger:** `_prepare_projection_recovery`
   clears `materialize_model_bq` marker when BQ raw is present and a prior run failed.
8. **Reviews** (`process_reviews.py`): pull Google reviews from ClickUp, allocate bonuses via the
   date-bracketed pool model ($20 pool split equally among in-hours part-time staff, effective
   2026-06-08; legacy $10-base / $20-named-shoutout for reviews before that date), rebuild

9. **Inventory ingest** (`ingest_inventory.py`): pull ClickUp closing-form submissions from the
   "Closing" list, parse per-base inventory quantities via `skills/inventory_parse/parse.py`, and
   MERGE-upsert into `bhaga.inventory_closing_daily`. **Non-fatal** — a failure here does not abort
   the tip/payroll pipeline. Incremental: reads the high-water mark from BQ (`MAX(submitted_ts)`)
   and fetches only newer tasks. Backfill: `--backfill-from YYYY-MM-DD`. Grafana source:
   `vw_inventory_base_latest_daily` (latest submission per base per day, used by Order Assistant
   section). Run standalone: `BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.ingest_inventory --store palmetto`.
   `review_bonus_period`. Idempotent on rerun.
7. **Verify the rebuilt Model** — first **mechanically** (`assert_model_tabs_populated`: tabs non-empty,
   KDS joined), then **semantically** (`model_semantics.assert_model_semantics`: tip-pool conservation,
   a closed period's `adp_paid` reconciles **only when** a covering GCS Earnings export actually carries
   that period's "Credit Card Tips Owed" lines — i.e. payroll has run, the cadence-safe gate
   `update_model_sheet.period_has_cc_tip_actuals` — and credited review bonuses survived the rebuild). A
   just-closed/unpaid period legitimately shows `N/A` and is skipped, not failed. A semantic failure
   clears the `update_model_sheet`
   marker (so a rerun REBUILDS) **and trips the circuit breaker** (next §). A green run **auto-clears**
   the breaker. The `review_bonus` semantic grid queries `model_review_bonus_period` on column
   `total_bonus` (not the phantom `review_bonus_dollars`).
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
`load_raw_bigquery` → `materialize_model_bq` → `process_reviews` (post-Sheets-exit)

**Raw-vs-model reconciliation (2026-06-09 fix).** `_recover_stale_downstream_markers` only fires when a
portal scrape *succeeds this run*, so a pure retrigger (scrape skipped as "already covered") never
triggered recovery. The 6/9 concurrent-execution race wrote `model_daily` Jun 9 = $0 while
`square_daily_rollup` had $1,964.51 — every subsequent retrigger SKIPped the stale model marker and Grafana
showed empty panels. Two new layers catch this:

1. **State-driven detector (`_detect_and_clear_stale_model`)** — runs on *every* execution before Phase 2.
   Single BQ query joins `square_daily_rollup` (raw) and `model_daily` (materialized) over a 14-day
   lookback. If any date has rollup gross_sales > $1 but model = $0, it clears `_MODEL_RECOMPUTE_STEPS`
   (`materialize_model_bq`, post-Sheets-exit) so the model recomputes on the next phase. Best-effort: a BQ error logs a breadcrumb and returns
   `[]` — the run is never blocked.

   Auth note: uses `google.cloud.bigquery.Client()` with ADC directly (not `core.datastore.get_client`,
   which is gated on `BHAGA_DATASTORE=bigquery` — not set in the parent `daily_refresh` process).

2. **Value-level post-condition guard (`_assert_model_matches_raw_rollup`)** — runs after the model step,
   alongside the existing boundary-check (`_assert_data_advanced_post_condition`). If residual drift remains
   after recompute, it raises `RuntimeError` → `failure_alert` Slack DM → non-zero exit. Converts silent
   "$0-inside-window" into a loud failure on the same night.

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
| `status.py` | **Run this first for any operational question about whether a run landed.** Read-only freshness checker across all three layers — Sheets (`data_window_end`, `daily`, `tip_alloc_daily`), BigQuery (model_* + raw tables), and Grafana BI contract views (vw_*). Prints a compact table and exits nonzero if any layer is missing the date (alert/CI usable). Anti-drift: its declarative registry is kept in sync with `core/migrations/*.sql` and `agents/bhaga/grafana/dashboard.json` by CI-enforced coupling in `scripts/check_doc_freshness.py` and by sync tests in `test_status.py`. `GRAFANA_VIEWS` registry includes `vw_kds_order_quality_by_source_daily` (migration 025, per-source P95 KDS time backing panel 51) and `vw_labor_weekly` (migration 002, per-employee weekly hours backing panel 38 — Weekly Shift Hours per Person). CLI: `python3 -m agents.bhaga.scripts.status --store palmetto [--date YYYY-MM-DD] [--json] [--check-schema]`. |
| `daily_refresh.py` | **Nightly orchestrator.** Gap compute → scrape → raw → model → reviews → **mechanical + semantic verify** → notify → **record outcome**. After `assert_model_tabs_populated` it runs `model_semantics.assert_model_semantics` (conservation + adp reconciliation + review-bonus survival) and trips the **pipeline halt circuit breaker** on a semantic failure (refuses fresh runs with `EXIT_HALTED` until a healthy run / `--ignore-halt` clears it). The public `main()` is a wrapper around the internal `_run_refresh()`; its `finally` block calls `_record_pipeline_run(run_id=…)` (best-effort, skipped on `--dry-run`) which MERGEs one row into `pipeline_runs` and one row per attempted source into `source_pulls`. **Recorder gate (prod-only):** `_should_record_pipeline_run()` returns True only when `CLOUD_RUN_JOB` env var is set (present in real Cloud Run jobs) or `BHAGA_RECORD_PIPELINE_RUN=1` is explicitly set (cloud-shell backfill opt-in). Laptop and GitHub CI never set `CLOUD_RUN_JOB` and therefore never write to `pipeline_runs` — this keeps Pipeline Health showing prod-only data. **`verify_model_bq` KDS query:** uses `date_local` column (not `date`) when querying `square_kds_daily` for the date range. Powers the "0. Pipeline Health" Grafana section (RUNBOOK §14). **Tests:** `conftest.py` stubs `_record_pipeline_run` for all tests except `test_pipeline_runs_recorder`. CLI: `python3 -m agents.bhaga.scripts.daily_refresh --store palmetto [--date YYYY-MM-DD] [--skip-reviews] [--ignore-halt] [--dry-run]`. |
| `daily_refresh_wrapper.py` | Thin wrapper / Cloud Run entrypoint around `daily_refresh`. |
| `retry_scheduler.py` | **One-shot smart-retry scheduler** for ADP maintenance skips. `schedule_one_shot_retry(date, retry_at_utc, env=…)` creates an ephemeral Cloud Scheduler job `bhaga-retry-<date>` mirroring `bhaga-nightly` (HTTP → `bhaga-daily-refresh:run`, OAuth as `bhaga-orchestrator`) that fires once at `retry_at` with a `REFRESH_DATE` override; `delete_retry_schedule(date)` (called at dated-run start) makes it self-cleaning. Spec build (`build_retry_job`/`cron_for`) is pure; the Cloud Scheduler client is injectable for tests. Needs `roles/cloudscheduler.admin` + `roles/iam.serviceAccountUser` on the orchestrator SA. |
| `otp_gate.py` | OTP availability gate + typed ADP exception home. **Default (inline autostart):** `evaluate()` returns PROCEED immediately; if ADP challenges for a 2FA code the runner posts an inline Slack OTP-code ask and raises `OtpWaitTimeout` on no reply (caught by `daily_refresh` as a graceful ADP skip via `_handle_adp_throttle_skip`, not a hard failure). **ADP throttle:** `AdpLoginThrottled` (also in this file) is raised by `_wait_for_login_form` when `sorry.adp.com` persists across all login attempts — `daily_refresh._handle_adp_throttle_skip` treats it identically to `OtpWaitTimeout` (alert, exit 0, `skipped_adp_throttle`). **Maintenance smart retry:** when ADP redirects to `sorry.adp.com` *after* a valid login (scheduled RUN maintenance), `_ensure_logged_in` parses the window-end from the banner (`skills/adp_run_automation/maintenance.py`) and raises `AdpLoginThrottled(retry_at=window_end+7min)`; `_handle_adp_throttle_skip` then schedules a one-shot Cloud Scheduler retry (`retry_scheduler.py`), status `skipped_adp_maintenance` (stateless cap `BHAGA_MAINT_RETRY_MAX`, default 3; degrades to `skipped_adp_throttle` on cap or scheduling failure). **Rollback mode** (`BHAGA_OTP_REQUIRE_READY=1`): restores the legacy checkpoint-and-resume READY handshake; `evaluate()` can return `EXIT_PENDING` (checkpoint + exit 0), `PROCEED` (READY received), or `SKIP_OTP` (48 h elapsed). The `force_request` flag (`BHAGA_OTP_FORCE_REQUEST=1`) is only meaningful in rollback mode. |
| `backfill_from_downloads.py` | **BQ-primary scrape sink.** Parse the just-downloaded scrape exports (local `extracted/downloads/`) directly into BigQuery raw tables via `map_*` + `load_rows` (MERGE upsert). **Does not read from GCS.** Requires `BHAGA_DATASTORE=bigquery`. Raw Sheets are rendered afterward by `render_raw_sheet_from_bq.py`. **`--replace`** (or `BHAGA_RAW_REPLACE=1`) = fresh full-history mode: TRUNCATE each target table before load, so the scrape fully owns the table and duplicate natural keys in one batch don't trip MERGE. Use ONLY for a full-history backfill (a windowed `--replace` drops out-of-window rows). |
| `bq_coverage.py` | **BQ coverage helper.** `present_days(table, date_col, start, end) -> set[date]` and `missing_ranges(table, date_col, start, end) -> [(start, end), ...]`. Used by `daily_refresh` to determine which business days are absent from BQ and need upstream scraping. `SOURCE_COVERAGE` maps logical source names to `(table, date_col)` pairs. |
| `backfill_item_lines_from_cache.py` | **No extra OTP** — replay GCS-cached `items-*.csv` into raw `item_lines` (GCS default; `--local-only` for tests). |
| `item_operations.py` | Build + upsert Model `item_operations` from `item_lines` + punches. |
| `update_model_sheet.py` | Recompute the **Model** workbook tabs from the raw sheets. Houses the `build_*_rows` functions (one per tab). Loads ADP "Credit Card Tips Owed" **actuals from BQ** via `load_cc_tips_earnings_from_bq` (reads `bhaga.adp_earnings`, returns ISO-string date keys) to populate `adp_paid`/`diff`/`diff_pct` + `period_summary.check_dates`. Reads operator tunables via `_read_config_value` (BQ-first via `core.store_config.get_config`, Sheet fallback). |
| `model_semantics.py` | **Pure, shared semantic post-conditions** (no I/O): `assert_tip_pool_conserved`, the cadence-safe `assert_period_reconciled`, `assert_review_bonus_present`, and the cadence-gating `assert_model_semantics`. One source of truth used by BOTH `sandbox_e2e` (per-PR gate) and `daily_refresh` (nightly), so a regression can't pass one and fail the other. The reconciliation cadence gate itself (`period_has_cc_tip_actuals`) lives in `update_model_sheet` next to the earnings loader it depends on. |
| `process_reviews.py` | Reviews → date-bracketed bonus allocation ($20 pool for on/after 2026-06-08; legacy $10/$20 per-person before) → rebuild `review_bonus_period`. **`data_window_end`** (the review crediting upper bound) is derived live from `MAX(square_transactions.date_local)` via `core.store_config.resolve_data_window_end()` — never read from `store_config` (a stale stored value would freeze the review window; see 2026-06-15 incident). **`HELD-BACK: N` counts only genuine review-bot posts** (not operational chatter) that land after `data_window_end`; the `_is_review_message` filter runs before the window cap so duty checklists and team messages never inflate the counter (2026-06-25 incident). |
| `ingest_inventory.py` | **Order Assistant ingest.** Fetches ClickUp closing-form tasks from the "Closing" list, parses per-base quantities via `skills/inventory_parse/parse.py`, and MERGE-upserts into `bhaga.inventory_closing_daily` on natural key `(store, source_task_id, field_id)`. Incremental via `MAX(submitted_ts)` high-water mark; supports `--backfill-from YYYY-MM-DD` and `--date YYYY-MM-DD`. Non-fatal in the nightly — failure leaves today's inventory data stale but does NOT abort the tip/payroll run *only if `_run_refresh()` actually reaches the `run_step` call* (see 2026-07-01 incident below). Grafana sources: `vw_inventory_base_latest_daily` (freshness-checked in GRAFANA_VIEWS) and `vw_inventory_order_assistant` (analytics view — per-base current stock, last-7-eligible-days usage, avg/day, days remaining, restock date; see migration 028). **Since Issue #126, panels 79/81 are pure `SELECT * FROM <object>` pass-throughs** (`scripts/check_grafana_no_logic.py` enforces this): panel 79 reads `bhaga.vw_order_assistant_table` (analytics table + TOTAL row) and panel 81 reads `bhaga.tvf_order_reco(ship_days, max_tubs)` (max-min water-fill order recommendation, parameterized by the `oa_ship_days`/`oa_max_tubs` Grafana variables), both defined in `core/migrations/029_order_assistant_functions.sql` — algorithm changes now require a new migration, not a panel edit. Panel 81 also shows `Order Weight (lbs)` per row (`order_tubs × {Açaí 18 \| other bases 20} lbs/tub`, Blade = NA) and a pallet-aware TOTAL (`+50 lbs` per 40-tub pallet, `CEIL`-rounded), all inside the TVF body now. **2026-07-01 incident:** the ingest step's env-building code referenced `run_id` from `main()`'s scope while executing inside the separate `_run_refresh()` function, raising an unhandled `NameError` that aborted the *entire* nightly (not just inventory) the first night this shipped — fixed by threading `run_id` explicitly (`main() → _run_refresh(run_id) → _build_ingest_inventory_env(run_id)`, the last one unit-tested in `test_ingest_inventory_env_wiring.py`). |
| `forecast.py` | Pure forecast helpers — `_get_parsed_rows` (operating-day parser, honors `forecast_exclude`), `compute_outlier_stats` (trend-aware robust outlier detection: order-volume AND AOV robust-z DOWN signals; `exclude_default = order_down OR aov_down`), plus the legacy DOW-trend functions. **The Sheet-writing path (`build_labor_daily_forecast_rows`) is retired.** `forecast_bq.py` only reuses `_get_parsed_rows`; `compute_outlier_stats` is called from `update_model_sheet.py` and now requires `net_sales` in each `operating_days` dict to enable AOV-based auto-exclusion. |
| `forecast_bq.py` | **BQ-authoritative daily forecast (model: wow_median_4wk_v2).** `build_forecast_rows()` returns forward rows `{date, forecast_orders, forecast_items, forecast_generated_at, forecast_model_version}` for today..today+horizon (default 30) — **today is included** so next week's panel-71 `prior_wk_orders` always has a fallback for the current day. Panels 72 & 75 (Forecast vs Actual) query `model_forecast_daily LEFT JOIN vw_model_labor_daily` directly so the forecast line extends to today+30 days with the actual line stopping when data ends. Each day = the most recent **same-weekday actual** × a **growth multiplier**, compounded by the whole weeks between anchor and forecast day. **Growth (wow_median_4wk_v2):** `median` of consecutive same-weekday WoW ratios (orders[d]/orders[d-7]) over the past 28 days, clamped [0.80, 1.20]. ~19 ratios in a clean 4-week window; median is robust to one anomalous week. Returns 1.0 when <2 valid pairs. Excluded/closed anchor days are skipped a **whole week at a time** (DOW preserved). `build_backfill_rows()` writes leakage-free PAST forecasts (each computed cutoff=D); the caller makes this **gap-fill-only** to freeze history. `CURRENT_FORECAST_VERSION = "wow_median_4wk_v2"`; `_GROWTH_STRATEGIES` registry enables future model comparison. Called by `materialize_model_bq.py`; loaded via `load_rows("model_forecast_daily", …, merge_keys=["date"])`. Skip with `BHAGA_SKIP_FORECAST=1`. |
| `notify.py` | Slack DMs under the BHAGA identity. Always DM through here, never `send_message` directly. |
| `gcs_cache.py` | **Sessions + failure evidence ONLY — not a data pipeline.** `upload_session()`/`download_session()`/`delete_session()` persist / restore / **discard** a portal browser session (`storage_state`) under `<bucket>/_session/` for **trusted-device** reuse (skips 2FA next run); `delete_session()` drops a *poisoned* session (e.g. after a Square anti-bot block) so the next login starts fresh. `evidence_prefix()` / `upload_evidence()` persist failure screenshots+DOM under `gs://<bucket>/<date>/evidence/` so a postmortem needs no rerun. Writes honor `BHAGA_GCS_CACHE_WRITE_BUCKET` (sandbox isolation: write sandbox bucket). The data-file helpers (`upload_file`/`upload_scrape_artifacts`/`download_cached_files`) are **LEGACY** (offline backfill + `sandbox_e2e` replay only) — the nightly pipeline never reads/writes scrape data here; BQ is the single source of truth. |

**Square uses OAuth REST API — no browser, no OTP (2026-06-23).** Square transactions, item
sales, and KDS are ingested via `skills/square_api/ingest.py` (payments/refunds/orders) and
`skills/square_api/kds_reporting.py` (Reporting API KDS cube). No Chromium, no magic-link 2FA,
no session management, no OTP for Square. Token auto-refresh via `skills/square_api/auth.py`; the
`square_palmetto_oauth` GCP secret holds the OAuth token JSON.

**Concurrent-execution guard (ADP — distributed scrape lock).** ADP still uses a browser. Multiple Cloud
Run executions for the same date can overlap (nightly scheduler + webhook READY-resume + manual
`/bhaga refresh` + Slack retry delivery). The guard is layered:
- `cloud/webhook/handler.py`: discards Slack-retry deliveries (`X-Slack-Retry-Num > 0`), stores seen
  `event_id`s in Firestore `webhook_events/<event_id>` (5 min TTL), and checks `_is_already_running`
  before calling `_trigger_cloud_run_job` (fail-open: listing errors allow the trigger).
- ADP's own runner acquires a TTL-based lock so a second execution fails fast with `ScrapeLockHeldError`.
- `daily_refresh.py` classifies `ScrapeLockHeldError` via `_is_scrape_lock_held` and calls
  `notify.scrape_concurrency_alert`.
| `bootstrap_sheets.py` / `share_sheets_with_sa.py` | One-time: create sheets / share with the service account. |
| `sandbox_provision.py` | **Pool-based** sandbox for per-PR e2e: `create-pool` (operator, user creds) pre-creates N slots × 4 sheets shared with the SA; `provision` leases + clears + re-seeds; `teardown` releases. Registry: `sandbox_pool.json`. |
| `sandbox_e2e.py` | **Prod-like, zero-OTP e2e.** provision → seed sandbox raw → **mirror the prod `training_shifts` overlay** → model build → `assert_model_tabs_populated` (note: `labor_daily_forecast` removed from verification dict 2026-06-09 — tab is retired) → evidence → teardown. **`--source prod-raw --period last-closed`** (the CI default when opted in) reads the **PROD raw** Square+ADP sheets directly for the most-recent **closed** pay period (`most_recent_closed_period`) and writes only to the sandbox (read-prod/write-sandbox, hard-asserted), then runs the **strict full-period verify** incl. `assert_tip_pool_conserved` (per-day allocations == pool, cent-exact), **cadence-safe `adp_paid` reconciliation** (`assert_period_reconciled` when `period_has_cc_tip_actuals` confirms a covering Earnings export with CC-tip lines exists — no longer the blessed-`N/A` of commit 6f87f9c; an unpaid just-closed period is skipped, not failed), **and `assert_exemptions_applied`** (proves each worked training shift is dropped from tips, the day's pool redistributes to the rest, whole-period-exempt staff get $0 while partial-exempt staff keep their non-exempt earnings with exempt hours removed, and the period conserves). The overlay mirror (`seed_sandbox_training_shifts_from_prod`) copies the human-owned prod `training_shifts` rows for the window into the sandbox model so the build applies the SAME exemptions as prod. **`--source gcs-replay --auto-window --max-days N`** replays the GCS scrape cache for a small window (local smoke). Imports **no** scrape/login code (enforced by `test_sandbox_e2e.py`). **Opt-in only** (2026-06-09): add the `run-sandbox-e2e` label to a PR or trigger via `workflow_dispatch` — no longer runs on every PR automatically. See `RUNBOOK.md` §13. |
| `sandbox_live_run.py` | **LIVE sandbox run** (real Square/ADP scrape + OTP on **unmerged PR code**) — the only way to reproduce/prove a fix for selector drift OR an infra/gate change (see `otp-reprompt` pattern). Builds the PR image → deploys `bhaga-sandbox-refresh` (self-wires by inheriting prod's secrets + SA) → live pipeline for a `REFRESH_DATE`. Enforces isolation (`assert_sandbox_isolation`: staging sheets + sandbox GCS write bucket + sandbox Firestore collection — reads prod OK, writes prod NEVER) before any deploy. **OTP gate mode:** default supervised runs set `BHAGA_OTP_ASSUME_READY=1` (inline OTP, no webhook resume); `--otp-force-request` drops assume-ready and sets `BHAGA_OTP_FORCE_REQUEST=1` so `otp_gate.evaluate` exercises the real force re-prompt path instead of bypassing the gate. **Gate-only/infra scenarios:** `--seed-stale-otp-hours N` seeds a stale `pending_otp` checkpoint in `sandbox_runs` before the job runs (CI runner has WIF + Firestore backend); `--verify otp_reprompt` reads `sandbox_runs` after and asserts `requested_at` advanced past the seeded value (re-prompt fired, `ready_received=False`). On create it inherits prod's secrets + SA + **resources/timeout** + **plain env vars** (`BHAGA_SECRETS_BACKEND=gcp`, …); describe-JSON parsing is schema-robust (v2 + KRM). Supports `--skip <steps>` (scenario scoping → `BHAGA_SKIP_<STEP>`) and `--verify item_sales`/`otp_reprompt` gates. See `RUNBOOK.md` §13. |
| `sandbox_scenarios.py` | **Named scenario suite** for live sandbox runs. Scenarios: `item-sales-live` (Square-only, `skip:[adp,reviews,model]` + `verify:item_sales`); `full-live`; `unified-window`; `full-history-bq-sandbox`; **`otp-reprompt`** (gate-only infra proof: seeds stale Firestore checkpoint, `BHAGA_OTP_FORCE_REQUEST=1`, no scrape/OTP reply, `verify:otp_reprompt` — prototype for any PR whose key logic fires at the Firestore/OTP/Cloud-Run-env layer). Selects what runs via committed `.github/sandbox-live.yml` (+ `sandbox-live` label, pre-merge), a `/sandbox run <scenario> [date=…]` PR comment (post-merge), or manual dispatch. `sandbox_workflow_resolve.py` turns the triggering event into a run plan for `.github/workflows/sandbox-live-run.yml`. Each scenario posts evidence as a PR comment. |
| `verify_drilldown.py`, `verify_bq_parity.py`, `verify_against_historical_payroll.py` | Verification harnesses (parity vs historical payroll / BigQuery). |
| `verify_prod_parity.py` | **Cloud-runnable e2e parity tool.** Diffs BQ (raw + model) against the prod Google Sheets for a full window: per-source row counts (BQ vs Sheet tabs, same date filter) plus key-joined, unit-aware value comparison (handles `%`/currency/bool normalization). Dataset is env-driven (`BHAGA_BQ_DATASET`), so it verifies prod `bhaga` or an isolated `bhaga_sandbox`. Needs Sheets auth (`BHAGA_SECRETS_BACKEND=gcp` or `BHAGA_IMPERSONATE_SA`) + `BHAGA_DATASTORE=bigquery`. |
| `backfill_bigquery.py` | **One-shot historical backfill only.** Reads existing raw Sheets → writes BQ. NOT the nightly path. Use to bootstrap BQ raw tables from Sheet history or repair BQ after a migration/truncation. The nightly path is `backfill_from_downloads.py` (scrape files → BQ directly). |
| `materialize_model_bq.py` | Rebuild the computed model from BQ raw data and write to `model_*` BigQuery tables via MERGE. Called by `materialize_model_bq` step in `daily_refresh`. Reuses the same `build_*_rows` functions as `update_model_sheet.py`. Used by the Grafana Cloud dashboard. **Requires the orchestrator SA to hold `roles/bigquery.jobUser` + `roles/bigquery.dataEditor`** (RUNBOOK §14) — without them every BQ job 403s. Guards an **empty BQ raw `square_transactions`** read with a precise `RuntimeError` breadcrumb instead of the old cryptic `max() iterable argument is empty` (run `backfill_bigquery` first). Access errors in `core.datastore.read_query` are re-raised (no longer swallowed into `[]`). Also exposes `load_model_rows()` as the canonical BQ-write helper (used by `process_reviews.py` and `render_model_sheet_from_bq.py`). **Ghost-row invariant (2026-06 hardening):** per-employee tables (`model_tip_alloc_daily/period`, `model_review_bonus_period`) use `replace_scope=True` in `load_model_rows`, which deletes rows for the rebuilt partition values before the MERGE so a dropped employee leaves no ghost. `_SCOPE_CLEAR_COL` drives this mapping; a meta-guard test in `test_materialize_model_bq.py` enforces it for any future per-employee table. **Name-normalization (2026-06 hardening):** `model_inputs.normalize_input_name(store, raw)` resolves any raw employee name to its canonical form via `employee_aliases`; raises `ValueError` on unknown names. `materialize()` applies this to `training_shifts` and `training_through` inputs before comparison. |
| `render_raw_sheet_from_bq.py` | **Raw Sheet projector.** Reads each BQ raw table (windowed by `--since`; `wage_rates` always all), inverse-maps rows to Sheet-header dicts, and incrementally upserts via `write_raw_*` functions. Non-fatal nightly step. Reviews tab rendered after `process_reviews`. |
| ~~`render_model_sheet_from_bq.py`~~ | **Deleted 2026-06-15 (Sheets exit).** Sheet projection no longer needed — model lives in BQ. |
| ~~`reconcile_model.py`~~ | **Deleted 2026-06-15 (Sheets exit).** No Sheet to compare against. |
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

> **Dashboard gotchas (RUNBOOK §14):** panels are datasource-agnostic in `dashboard.json` (they point at the `${ds_bigquery}` variable); `deploy.py` binds the **real datasource UID** at push time — committing a name there yields "No data" on every panel. Panel SQL must use **backtick** column aliases (BigQuery rejects `AS "x"`), and output field names can't contain `/`, `$`, or parentheses. Validate any panel change with `python3 agents/bhaga/grafana/verify_panels.py` (runs each panel's SQL via Grafana `/api/ds/query`). **Variable prefix convention:** `date_from` (global time window) / `kds_*` (KDS section) / `goal_*` (labor thresholds) / `oa_*` (Order Assistant — `oa_ship_days` default 10, `oa_max_tubs` default 120). New sections must follow this convention.

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
| `excluded_from_tip_pool` | `bhaga.store_config` (BQ) | permanent | managers/owners who never tip-pool |
| `training_excluded:<name>` | `bhaga.store_config` (BQ) | through a date (inclusive) | bulk "all shifts up to date X were training" |
| `training_shifts` BQ table | `bhaga.training_shifts` | one `(store, employee, date)` | precise per-shift training marks |

**BQ-canonical (post-2026-06-15 Sheets exit):** read by `model_inputs.read_training_shifts()` (returns
`set[(canonical_name, date_iso)]`). All human inputs live in BigQuery; operators edit via
`/bhaga-cloud` Slack commands. No Sheet editing needed.

**Ingesting new training-shift rows from the Sheet (one-time / backfill):** use
`migrate_inputs_to_bq.py`.  By default (`open_period_only=True`) the script only ingests rows whose
date falls in the **current open pay period** and skips closed/paid-period rows with a clear
`[migrate] SKIP closed-period:` breadcrumb.  To ingest historical rows into a closed period (explicit
backfill), pass `--allow-closed-periods`.  Always run `--dry-run` first to confirm which rows will
land vs be skipped before the real MERGE.

### After any recipe

- **Tests:** `python3 -m pytest agents/bhaga/scripts/ skills/tip_ledger_writer/`.
- **Deploy:** commit → push `main` → GitHub Actions builds/deploys the image. Local edits don't
  affect prod until deployed (`RUNBOOK.md` § Operating rules).
- **Backfill history** if the new field should be populated for past dates: re-run the model step for
  the historical window (force-rerun per `RUNBOOK.md` § Common tasks).
- **Document:** add the field/tab to the domain dictionary `../knowledge-base/DOMAIN.md` (§3 + the
  relevant metric section), note it in `RUNBOOK.md` § Sheet topology, and add a dated line to
  `PROGRESS.md`.
