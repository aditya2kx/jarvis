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
| **BQ-canonical Sheet projector** | `BHAGA_SHEET_FROM_BQ` | PR #XX (2026-06) | **off** | When `1`/`true`/`yes`: `daily_refresh.py` runs `materialize_model_bq` first (BQ is canonical) then `render_model_sheet_from_bq` projects the Sheet from BQ. When off (default): legacy `update_model_sheet` computes from Sheet raw → writes Sheet; `materialize_model_bq` mirrors to BQ as a non-fatal step. | **After ≥ 1 release cycle** (≥ 2 consecutive nights) of green `reconcile_model` in prod on the real canonical path. Record the flip date in this file. Cleanup: delete the legacy `update_model_sheet` Sheet-write path from `daily_refresh.py` and remove the branch. | Planned: separate cleanup PR after flag flip is confirmed stable. |

---

## "Safe by construction → no flag" precedents

Some changes are safe to apply directly without a flag because they are additive and idempotent:
- New BQ tables and views (migration 004) — `CREATE TABLE IF NOT EXISTS` / `CREATE OR REPLACE VIEW`
- New Sheet tabs (`add_sheet_if_missing`) — no-op if the tab already exists
- New Grafana dashboard panels — always additive

These are noted here so future reviewers understand the policy: flags gate **cutover** risk, not additive additions.

Migration 005 raw-parity tables and the 5-section Grafana dashboard redesign fall into this category — all changes are additive and are applied without a flag.

---

## Flag flip log

| Flag | Flipped to | Date | PR / Run | Notes |
|------|-----------|------|----------|-------|
| `BHAGA_SHEET_FROM_BQ` | `1` (on) | TBD | TBD | Flip only after reconcile_model green in prod for ≥ 2 nights. |

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
