---
description: BHAGA principles card — always-on invariants, operational rules, and consult-first pointers. Loads even when no agents/bhaga/** file is attached.
alwaysApply: true
---

# BHAGA principles (consult-first card)

This is the **always-loaded** summary of BHAGA's non-negotiables, so the principles are in context
**before** you plan or design — even from a fresh chat on another machine with no `agents/bhaga/**`
file attached. The verbose persona + full detail live in the glob-gated
[`.cursor/rules/bhaga.md`](bhaga.md); **read it (and the docs below) before proposing changes** and
derive proposals from them rather than from memory.

## Consult before planning/design (cite what you used)

- **[`CONTRIBUTING.md`](../../CONTRIBUTING.md)** — the development loop (branch → PR → Claude review →
  CI → merge → deploy), milestone structure, secret-scan + `git push --no-verify` policy.
- **[`.cursor/rules/bhaga.md`](bhaga.md)** — the correctness invariants + operational rules (full text).
- **[`.cursor/rules/jarvis.md`](jarvis.md)** — cross-agent Hard Lessons + conventions.
- **[`RUNBOOK.md`](../../RUNBOOK.md)** — live cloud operation: Cloud Run units, scheduler, secrets,
  Operating rules, Common tasks (force-rerun, backfill, recovery).
- **[`agents/bhaga/scripts/README.md`](../../agents/bhaga/scripts/README.md)** — script-by-script code
  map + how to extend the model.

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

- **Branch → PR → Claude review → CI → merge → deploy.** Never push to `main` directly. Local edits do
  nothing in prod until the image redeploys.
- **Sheets/BQ are the production database — write only through the sanctioned layer**
  (`skills/tip_ledger_writer/`). No ad-hoc `values:clear`, `python -c`, or raw API writes against prod.
  Marker clears go through `skills/bhaga_config/state_adapter.py::clear_step`, never a shell `rm`.
- **Cloud reads from GCS (`bhaga-scrape-cache`), secrets from Secret Manager** — never laptop files.
- **Prove changes with the per-PR sandbox e2e** (`sandbox_e2e.py`), not by touching prod sheets.
- **Sandbox runs are read-only toward prod data sources.** A sandbox/staging run (`BHAGA_SHEET_MODE=staging`)
  may **read** prod data (GCS cache, raw sheets) but must **never write** to any prod data source —
  prod sheets, the prod GCS cache (`bhaga-scrape-cache`), or prod Firestore state. All sandbox writes
  divert to isolated sandbox targets (staging sheets, `BHAGA_GCS_CACHE_WRITE_BUCKET`, a sandbox Firestore
  namespace). Enforced by hard guards: `config_loader._assert_not_production_sheet` (sheets),
  `gcs_cache._assert_sandbox_write_isolation` (cache), and `state_adapter._assert_sandbox_state_isolation`
  (Firestore run-state) — all fail loud rather than mutate prod.
- **OTP via Slack/Firestore+webhook, never the IDE.** Announce any action that fires an SMS/email/DM
  before triggering it.
- **Never reflexively retry a transient error when a retry can fire a side effect** (OTP/SMS/email/DM).
  Check `ps` first; `SIGTERM` + grace, never `kill -9` mid-2FA. Only infra-only launch crashes
  (no side effect) auto-retry — bounded + classified in `skills/_browser_runtime/runtime.py`.
- **Leave a breadcrumb on every failure** — a precise, greppable one-line cause distinct from library
  noise, plus enough state (refresh_date/window, attempt `N/M`, evidence path, skipped/cleared markers)
  to diagnose from Cloud Run logs + Firestore alone on another machine.
- **Drive end-to-end; own the whole loop.** Build, test, push, open/iterate the PR, and run the proof —
  don't stop at a handoff you can do yourself. When credentials are present, do the operator setup with
  the available tooling (`gcloud`/`gh`): create the sandbox bucket + IAM grants, deploy/execute the
  sandbox job, trigger the workflow (add the `sandbox-live` label / dispatch), watch the run, and **fix
  any failure (Python, IAM, config) and re-run** rather than asking. Inspect real artifacts (describe
  JSON, Cloud Run logs, GCS evidence) instead of assuming a schema. Pause **only** for the irreducible
  human step (replying to the OTP DM on the operator's phone), a truly destructive/irreversible **prod**
  action, or a genuine architecture fork. Announce side-effecting actions; then proceed.
- **Keep docs in lock-step** — pipeline/step/sheet/invariant changes update `RUNBOOK.md` +
  `agents/bhaga/scripts/README.md` + `bhaga.md` + a dated `PROGRESS.md` entry in the same change.

## Recovery & resilience (2026-05-31 incident class)

- A headless browser launch that crashes (`TargetClosedError`) is transient infra — the runtime retries
  the **launch setup** (never the yielded body) with container-stability flags. Auth/2FA errors are
  **never** retried.
- When a previously-failed OTP portal recovers with fresh data while downstream markers
  (`write_raw_sheets`/`update_model_sheet`/`process_reviews`) are already done from a partial run,
  those markers are invalidated so they recompute. Always on (no flag) — safe by construction
  (idempotent upserts; the post-condition guard still verifies `data_window_end` advanced).
