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

1. **Read `data_window_end`** from the Model sheet `config` tab → compute the gap window
   `[data_window_end+1 .. refresh_date]` (Central time). Empty gap → nothing to scrape.
2. **Scrape Square** transactions for the gap (`skills/square_tips/`), cache to GCS, dedupe-append.
3. **Scrape ADP** timecards / earnings for overlapping pay periods (`skills/adp_run_automation/`).
   2FA, if challenged, goes through the **OTP gate** (see below).
4. **Mirror scrapes → raw Google Sheets** (`backfill_from_downloads.py`): `bhaga_adp_raw`,
   `bhaga_square_raw` (including **`item_lines`** from the same Item Sales CSV as
   `item_daily_rollup`). **Contract: downstream reads only the raw sheets, never local files.**
5. **Recompute the Model tabs** (`update_model_sheet.py`): `config, daily, labor_daily,
   labor_weekly, labor_period, tip_alloc_period, tip_alloc_daily, period_summary`
   (+ `labor_daily_forecast` via `forecast.py`), then **upsert `item_operations`** for the gap
   window (`item_operations.py` — incremental, not full-tab rewrite).
6. **Reviews** (`process_reviews.py`): pull Google reviews from ClickUp, allocate bonuses, rebuild
   `review_bonus_period`. Idempotent on rerun.
7. **Heartbeat** success/failure DM to the BHAGA Slack channel (`notify.py`).

Per-step **idempotency markers** live in Firestore `runs/<YYYY-MM-DD>`
(`skills/bhaga_config/state_adapter.py`: `mark_step_done` / `step_already_done` / `clear_step`). A
re-run skips steps already marked done. To force a step, clear its marker — see `RUNBOOK.md` § Common
tasks. **Recovery:** when an OTP portal (Square/ADP) succeeds on a later run while downstream markers
(`write_raw_sheets`/`update_model_sheet`/`process_reviews`) are already done from a prior partial run,
`daily_refresh._recover_stale_downstream_markers` invalidates them (via `clear_step`, the sanctioned
path) so they recompute on the fresh data. Always on (no flag) — safe by construction: idempotent
upserts + the post-condition guard verifies `data_window_end` advanced (RUNBOOK §13).

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
| `daily_refresh.py` | **Nightly orchestrator.** Gap compute → scrape → raw → model → reviews → notify. CLI: `python3 -m agents.bhaga.scripts.daily_refresh --store palmetto [--date YYYY-MM-DD] [--skip-reviews] [--dry-run]`. |
| `daily_refresh_wrapper.py` | Thin wrapper / Cloud Run entrypoint around `daily_refresh`. |
| `otp_gate.py` | OTP **checkpoint-and-resume**: writes a pending request to Firestore + Slack, blocks until the webhook records the operator's reply. |
| `backfill_from_downloads.py` | Mirror local/GCS scrape artifacts into the canonical **raw** sheets (`_upsert_tab`, additive header migration). |
| `backfill_item_lines_from_cache.py` | **No extra OTP** — replay GCS-cached `items-*.csv` into raw `item_lines` (GCS default; `--local-only` for tests). |
| `item_operations.py` | Build + upsert Model `item_operations` from `item_lines` + punches. |
| `update_model_sheet.py` | Recompute the **Model** workbook tabs from the raw sheets. Houses the `build_*_rows` functions (one per tab). |
| `process_reviews.py` | Reviews → bonus allocation → rebuild `review_bonus_period`. |
| `forecast.py` | Builds `labor_daily_forecast` (staffing solver, guardrails, anomaly detection). |
| `notify.py` | Slack DMs under the BHAGA identity. Always DM through here, never `send_message` directly. |
| `gcs_cache.py` | Read/write scrape artifacts in GCS `bhaga-scrape-cache`. Writes honor `BHAGA_GCS_CACHE_WRITE_BUCKET` (sandbox isolation: read prod, write sandbox); `evidence_prefix()` / `upload_evidence()` persist failure screenshots+DOM under `gs://<bucket>/<date>/evidence/` so a postmortem needs no rerun. `upload_session()`/`download_session()` persist a portal browser session (`storage_state`) under `<bucket>/_session/` for **trusted-device** reuse (skips 2FA next run); stored in the run's OWN bucket so sandbox keeps its own session. |
| `bootstrap_sheets.py` / `share_sheets_with_sa.py` | One-time: create sheets / share with the service account. |
| `sandbox_provision.py` | **Pool-based** sandbox for per-PR e2e: `create-pool` (operator, user creds) pre-creates N slots × 4 sheets shared with the SA; `provision` leases + clears + re-seeds; `teardown` releases. Registry: `sandbox_pool.json`. |
| `sandbox_e2e.py` | **Prod-like, zero-OTP e2e.** provision → GCS-cache replay → backfill → model build → `assert_model_tabs_populated` → evidence → teardown. `--auto-window --max-days N` replays up to the **N most-recent *cached* dates** (the calendar span can be wider on a sparse cache). Imports **no** scrape/login code (enforced by `test_sandbox_e2e.py`). Runs on every PR via `.github/workflows/sandbox-e2e.yml` (plus a no-op `push` to `main` so the check registers for branch protection). See `RUNBOOK.md` §13. |
| `sandbox_live_run.py` | **LIVE sandbox run** (real Square/ADP scrape + OTP on **unmerged PR code**) — the only way to reproduce/prove a fix for selector drift. Builds the PR image → deploys `bhaga-sandbox-refresh` (self-wires by inheriting prod's secrets + SA) → live pipeline for a `REFRESH_DATE`. Enforces isolation (`assert_sandbox_isolation`: staging sheets + sandbox GCS write bucket + sandbox Firestore collection — reads prod OK, writes prod NEVER) before any deploy. OTP uses the prod Slack bot but the prompt is labeled `[SANDBOX · PR…]` and the reply resumes the **sandbox** job (sandbox precedence in the webhook); supervised runs set `BHAGA_OTP_ASSUME_READY=1` to take the code inline (no webhook resume needed). On create it inherits prod's secrets + SA + **resources/timeout** + **plain env vars** (`BHAGA_SECRETS_BACKEND=gcp`, …); describe-JSON parsing is schema-robust (v2 + KRM). Supports `--skip <steps>` (scenario scoping → `BHAGA_SKIP_<STEP>`) and `--verify item_sales` (`verify_item_sales()`: a post-run gate that fails the run if `<date>/square/items-*.csv` is absent/empty, even on a 0 job exit). The sandbox cache bucket is a one-time operator setup (`assert_sandbox_bucket` fails with remediation if absent). See `RUNBOOK.md` §13. |
| `sandbox_scenarios.py` | **Named scenario suite** for live sandbox runs (`item-sales-live` = Square-only via `skip:[adp,reviews,model]` + `verify:item_sales`; `full-live`; …). Selects what runs via committed `.github/sandbox-live.yml` (+ `sandbox-live` label, pre-merge), a `/sandbox run <scenario> [date=…]` PR comment (post-merge), or manual dispatch. `sandbox_workflow_resolve.py` turns the triggering event into a run plan for `.github/workflows/sandbox-live-run.yml`. Each scenario posts evidence as a PR comment. |
| `verify_drilldown.py`, `verify_bq_parity.py`, `verify_against_historical_payroll.py` | Verification harnesses (parity vs historical payroll / BigQuery). |
| `backfill_bigquery.py` | Backfill raw data into BigQuery. |
| `test_*.py` | Unit tests. Run: `python3 -m pytest agents/bhaga/scripts/`. |

---

## Raw → Model data flow (the mental model)

```
Square / ADP / ClickUp  ──scrape──▶  raw Google Sheets  ──read──▶  build_*_rows()  ──upsert──▶  Model tabs
  (skills/square_tips,              (bhaga_*_raw;          (skills/tip_ledger_      (update_model_sheet.py)   (config, daily,
   adp_run_automation,              schema in             writer/reader.py)                                   labor_*, tip_alloc_*,
   ClickUp)                         tip_ledger_writer)                                                         review_bonus_period…)
```

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
