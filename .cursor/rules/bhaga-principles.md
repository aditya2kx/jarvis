---
description: BHAGA principles card — domain-specific invariants and operational rules. Loaded only when working in agents/bhaga/**.
globs:
  - "agents/bhaga/**"
alwaysApply: false
---

# BHAGA principles

> **Common principles** (consult-first, branch→PR→merge, plan-readiness, drive-end-to-end,
> doc-lockstep, retry-side-effect guard) live in the **Spine** (AGENTS.md + pr-workflow.mdc +
> self-drive rule + jarvis routing card).  This card contains only BHAGA-specific content.

## Before working on BHAGA
Read these first and derive proposals from them (see also AGENTS.md § consult-first):
- **[`bhaga.md`](bhaga.md)** — full correctness invariants + operational rules
- **[`RUNBOOK.md`](../../RUNBOOK.md)** — cloud operation, scheduler, secrets, Common tasks
- **[`agents/bhaga/scripts/README.md`](../../agents/bhaga/scripts/README.md)** — script-by-script code map

## Correctness invariants (never break)

1. **Allocation is pure** — `skills/tip_pool_allocation/` has no network/IO/clock.
2. **Pool-by-day fairness** — share per date, summed across the period; never pool-by-period.
3. **Idempotent writes** — re-running a date upserts by natural key (`skills/tip_ledger_writer/`);
   reviews dedupe by review identity. Never append duplicates.
4. **Money = integer cents** internally; dollars only at the sheet boundary. Never floats for currency.
5. **Rounding residuals** distribute by largest-remainder so shares sum to the day's pool exactly.
6. **Read-only toward ADP** for payroll — BHAGA produces outputs, never auto-writes back to RUN.
7. **Timezone = America/Chicago** for every date boundary; a date is "today" only after the 21:30 CT
   nightly. Never let local/PST/UTC leak in.

## Operational rules (cloud-primary; laptop retired 2026-05-29)

> Common process rules (PR flow, end-to-end ownership, doc lock-step, side-effect retry guard)
> live in the **Spine** — see CONTRIBUTING.md, pr-workflow.mdc, self-drive.md, doc-maintenance.md.

- **Sheets/BQ are the production database — write only through the sanctioned layer**
  (`skills/tip_ledger_writer/`). No ad-hoc `values:clear`, `python -c`, or raw API writes against prod.
  Marker clears go through `skills/bhaga_config/state_adapter.py::clear_step`, never a shell `rm`.
- **BQ is the single source of truth for all data.** Raw scraped data, ADP earnings, and operator
  tunables all live in BigQuery (`jarvis-bhaga-prod.bhaga`). GCS retains only browser sessions and
  failure evidence. Sheets are read-only projections. Never read data files from GCS in pipeline code.
- **Coverage-aware scraping.** Before scraping, check `bq_coverage.missing_ranges` to determine which
  business days need upstream data. A fully-covered window scrapes nothing new.
- **Operator tunables live in `bhaga.store_config`.** Edit via `/bhaga-cloud config set <key> <value>`
  (Slack). Never manually edit the Sheet config tab — it is a read-only projection of BQ.
  Read tunables in code via `core.store_config.get_config(store, key)`.
- **Cloud reads from BQ, secrets from Secret Manager** — never laptop files or GCS data files.
- Plan readiness → apply `plan-execution-readiness.md` before moving from Plan to Agent mode (Spine rule).
- **Prove changes with the per-PR sandbox e2e** (`sandbox_e2e.py`), not by touching prod sheets.
- **Sandbox runs are read-only toward prod data sources.** A sandbox/staging run (`BHAGA_SHEET_MODE=staging`)
  may **read** prod data (GCS cache, raw sheets) but must **never write** to any prod data source —
  prod sheets, the prod GCS cache (`bhaga-scrape-cache`), the prod BQ dataset (`bhaga`), or prod
  Firestore state. All sandbox writes divert to isolated sandbox targets (staging sheets,
  `BHAGA_GCS_CACHE_WRITE_BUCKET`, `BHAGA_BQ_DATASET=bhaga_sandbox`, a sandbox Firestore namespace).
  Enforced by hard guards: `config_loader._assert_not_production_sheet` (sheets),
  `gcs_cache._assert_sandbox_write_isolation` (cache), `datastore._assert_sandbox_write_isolation`
  (BQ dataset), and `state_adapter._assert_sandbox_state_isolation` (Firestore run-state) — all fail
  loud rather than mutate prod. The BQ guard is the fix for the leak that previously let a sandbox test
  row strand itself in prod `bhaga` (sandbox writes shared the prod dataset before `BHAGA_BQ_DATASET`).
- **OTP via Slack/Firestore+webhook, never the IDE.** Announce any action that fires an SMS/email/DM
  before triggering it.
- **BHAGA-specific retry classification:** Never retry OTP/SMS/email/DM side effects. Only infra-only
  launch crashes auto-retry — bounded + classified in `skills/_browser_runtime/runtime.py`.
  (Generic rule: "never reflexively retry when a side effect can fire" lives in jarvis routing card.)
- **Leave a breadcrumb on every failure** — a precise, greppable one-line cause distinct from library
  noise, plus enough state (refresh_date/window, attempt `N/M`, evidence path, skipped/cleared markers)
  to diagnose from Cloud Run logs + Firestore alone on another machine.
- **Run `status` first for any operational question about whether a run landed.** Before hand-investigating whether yesterday's incremental run landed in Sheets, BigQuery, and Grafana, run `python3 -m agents.bhaga.scripts.status --store palmetto` — it prints a compact freshness table across all three layers and exits nonzero if any layer is missing the date. Don't re-derive coordinates or hand-write queries.

## Recovery & resilience (2026-05-31 incident class)

- A headless browser launch that crashes (`TargetClosedError`) is transient infra — the runtime retries
  the **launch setup** (never the yielded body) with container-stability flags. Auth/2FA errors are
  **never** retried.
- When a previously-failed OTP portal recovers with fresh data while downstream markers are already
  done from a partial run, those markers are invalidated so they recompute. The set must be **every**
  step that carries portal data to the window (`load_raw_bigquery`, `render_raw_sheets`,
  `update_model_sheet`, `materialize_model_bq`, `render_model_sheet_from_bq`, `process_reviews`) — a
  missing member (the 2026-06-08 stale-projection bug) leaves `data_window_end` stuck. Always on (no
  flag) — safe by construction (idempotent upserts; the post-condition guard still verifies
  `data_window_end` advanced).
