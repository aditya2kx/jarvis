# Feature Flag Tracker

This document enumerates all **behavioral** feature flags in the Jarvis/BHAGA codebase.
Deployment-env config variables (e.g. `BHAGA_SECRETS_BACKEND`, `BHAGA_STATE_BACKEND`) are infrastructure toggles and are documented in `RUNBOOK.md`, not here.

**Policy:** every new behavioral flag must be added here in the same PR that introduces it, with a clear safe-to-remove condition and a planned cleanup PR.

---

## Flag Registry

| Name | Env Var | Added | Default | What it gates | Safe to remove when… | Cleanup PR |
|------|---------|-------|---------|--------------|---------------------|-----------|
| **BQ datastore** | `BHAGA_DATASTORE=bigquery` | 2026-03 (early) | off | Enables BigQuery client for raw reads and model writes. Without it, all reads/writes go to Google Sheets only. | **Keep permanently** — this is a permanent infrastructure toggle, not a temporary flag. Setting to `bigquery` is the prod-normal state for Cloud Run. | — |
| **Sheet staging isolation** | `BHAGA_SHEET_MODE=staging` | 2026-04 | off | Redirects Sheet writes to the sandbox slot (read-prod / write-sandbox). Guards CI and dev runs from touching production Sheets. | **Keep permanently** — safety gate for all CI and sandbox runs. | — |
| **OTP READY handshake (rollback)** | `BHAGA_OTP_REQUIRE_READY=1` | 2026-06 (PR #94) | **off (new default: inline autostart)** | When set, restores the legacy two-step READY handshake: nightly posts a READY request, checkpoints to Firestore, exits 0, and resumes only after the operator replies READY via Slack. When unset (default), the nightly proceeds inline and only contacts the operator if ADP actually challenges for a 2FA code. | Remove once inline-autostart has run ≥ 14 consecutive nightly cycles without needing READY rollback. | Follow-up cleanup — remove flag + legacy branch from `otp_gate.evaluate()`. |

| **Local event auto-open** | `LOCAL_EVENT_AUTO_OPEN=0` to disable | 2026-06 (PR #101) | on | When on (default), a delivered signal opens/focuses the owning worktree's Cursor window (creates the worktree for `/jarvis-new-task` intake). Window management only — no agent is started. Set `=0` for notify-only. | Keep — window-focus UX, not a temporary cutover. | — |
| **Local event auto-dispatch** | `LOCAL_EVENT_AUTO_DISPATCH=0` to disable | 2026-06 (PR #101) | on | When on (default), after the window opens the listener seeds a prompt and starts the agent on actionable agent-zone events (babysit on `ci_failed`, retrospective draft). Operator gates (jam/define-evidence/merge) still require human approval. Set `=0` for notify-only. The queue is non-preemptive: events that arrive mid-turn sit in the FIFO inbox and drain at each turn boundary via the `stop`-hook follow-up loop. | Keep — autonomous dev loop is the intended steady state. | — |
| **Local event webhook** | `LOCAL_EVENT_WEBHOOK=1` | 2026-06 (PR #101) | off | Enables `dev_event_listener serve` HTTP push endpoint (Tailscale/smee). When unset (default), delivery is catch-up/`--watch` poll only. | Remove flag when webhook transport graduates from v2.1 experiment to default. | follow-up |
| **Operator Console Accounting** | `FEATURES.accounting` / `FEATURES.writePlaidLink` in `apps/operator-console/lib/config/features.ts` | 2026-07 (Issue #158) | **on** | Gates Accounting nav/page and Plaid Link+sync writes. Code-level flags (not env). Turn off to hide cash ledger UI without undeploying. Runtime `PLAID_ENV=sandbox` until Plaid production access is approved (then flip env + `plaid_secret`). | Remove once Accounting+Plaid have run stably in prod ≥ 14 days with successful sync screenshots. | follow-up |
| **Operator Console Tip Exemptions** | `FEATURES.writeTipExemptions` (and `writeTraining=false`) in `features.ts` | 2026-07 (Issue #167) | **on** | Gates Payroll Tip Exemptions batch Update. Additive BQ columns (`exempt_start`/`exempt_end`); NULL/NULL remains whole-day bit-identical — no pipeline feature flag. | Fold into permanent Payroll UX once stable ≥ 14 days; then drop the unused `writeTraining` quick-add path. | follow-up |

**Removed flags:**

| Name | Env Var | Removed | Notes |
|------|---------|---------|-------|
| BQ-canonical Sheet projector | `BHAGA_SHEET_FROM_BQ` | 2026-06-14 (PR TBD) | Path is unconditional: `daily_refresh` always runs `materialize_model_bq` → `render_model_sheet_from_bq`. Legacy `update_model_sheet` nightly step removed. |

---

## "Safe by construction → no flag" precedents

Some changes are safe to apply directly without a flag because they are additive and idempotent:
- New BQ tables and views (migration 004) — `CREATE TABLE IF NOT EXISTS` / `CREATE OR REPLACE VIEW`
- New Sheet tabs (`add_sheet_if_missing`) — no-op if the tab already exists
- New Grafana dashboard panels — always additive

These are noted here so future reviewers understand the policy: flags gate **cutover** risk, not additive additions.

**Tip exemption windows (Issue #167 / migration 038):** additive `exempt_start`/`exempt_end` on
`bhaga.training_shifts`. NULL/NULL keeps legacy whole-day exclusion — no pipeline env flag.

Migration 005 raw-parity tables, the 5-section Grafana dashboard redesign, and the **BQ-primary raw layer** (PR #33) fall into this category — all changes are additive and idempotent:

### BQ-primary raw layer (PR #33, 2026-06) — hard cutover, no flag

The switch from "scrape → Sheets (primary) → BQ (mirror)" to "scrape → BQ (primary) → Sheets (projection)" is implemented as a **hard cutover** (no environment flag):

- `backfill_from_downloads.py` now **requires** `BHAGA_DATASTORE=bigquery` (exits non-zero without it) and writes only to BQ via `load_rows` (MERGE upsert). Raw Sheets are no longer the primary sink.
- `render_raw_sheet_from_bq.py` (new) renders raw Sheets from BQ as non-fatal projections. Historical rows are preserved via incremental upsert by natural key.
- `render_model_sheet_from_bq.py` now uses **incremental upsert** (by natural key, `--since` windowing) instead of `clear_and_write_tab`. Historical model rows outside the window are preserved.
- `process_reviews.py` writes `google_reviews` to BQ as the **only** review sink. The `reviews` Sheet tab is rendered from BQ. `_latest_review_ts_ms` and `_read_all_reviews` read from BQ.
- Migration 006 adds `multi_rate BOOL` to `adp_wage_rates` for lossless wage-rate round-trip.

**Why no flag:** The load direction inversion is the architectural invariant. A half-flag state (writes go to Sheets but BQ is also written) was the dual-sink anti-pattern we're removing. The `BHAGA_DATASTORE=bigquery` env var (already permanent infrastructure toggle) enforces BQ writes; removing it would revert to Sheets-only which is no longer supported.

---

## Flag flip log

| Flag | Flipped to | Date | PR / Run | Notes |
|------|-----------|------|----------|-------|
| `BHAGA_SHEET_FROM_BQ` | removed | 2026-06-14 | PR TBD | Unconditional single path since this PR. |

---

## Updating this file

When you add a new flag:
1. Add a row to the **Flag Registry** table above.
2. Note the PR number and date in the **Added** column.
3. Define a concrete, measurable **safe-to-remove** condition.
4. Create a follow-up issue/task for the cleanup PR.

When you flip a flag in prod:
1. Add a row to the **Flag flip log**.
2. Schedule the cleanup PR once the flag has been stable for ≥ 1 release cycle.
