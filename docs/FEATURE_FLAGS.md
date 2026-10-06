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
| **Operator Console Accounting** | `FEATURES.accounting` / `FEATURES.writePlaidLink` in `apps/operator-console/lib/config/features.ts` | 2026-07 (Issue #158) | **on** | Gates Accounting nav/page and bank Link+sync writes. Code-level flags (not env). Accounting KPIs/ledger are linked-bank feed only (no Square mix on Finance). Runtime `PLAID_ENV=production` after Issue #168 cutover. **#160:** Palmetto taxonomy + Copilot rules live; PFC debug-only. **#189:** exclude-from-accounting on taxonomy; Home Finance=bank / Sales=Square / Labor=rates+bank payroll twin; propose-rule + multi-select filters. **#220:** `PLAID_WEBHOOK_URL` required on console (bhaga-webhook `/plaid/webhook`); nightly `daily_refresh` best-effort `plaid_sync` catch-up on existing `bhaga-nightly` (no dedicated Plaid scheduler); Manual Sync for backfill. No new flag. | Remove once Accounting+bank link have run stably in prod ≥ 14 days with successful sync screenshots. | follow-up |
| **Operator Console Tip Exemptions** | `FEATURES.writeTipExemptions` (and `writeTraining=false`) in `features.ts` | 2026-07 (Issue #167) | **on** | Gates Payroll Tip Exemptions batch Update. Additive BQ columns (`exempt_start`/`exempt_end`); NULL/NULL remains whole-day bit-identical — no pipeline feature flag. | Fold into permanent Payroll UX once stable ≥ 14 days; then drop the unused `writeTraining` quick-add path. | follow-up |
| **Operator Console usage-day overrides** | `FEATURES.writeInventoryDayOverrides` in `features.ts` | 2026-07 (Issue #194) | **on** | Gates tap-to-override on `/inventory` Base usage-by-day table (`force_include` / `force_exclude` → `inventory_usage_day_overrides`). Changes avg/day + order reco. Flag-off = read-only chips. | Fold into permanent inventory UX after ≥ 14 days stable; keep table. | follow-up |
| **Operator Console reimbursements** | `FEATURES.writePerks` in `apps/operator-console/lib/config/features.ts` | 2026-08 (Issue #267) | **on** | Gates Payroll **Add reimbursement…** MERGE into `employee_perks`. Recurring gym uses `pay_period=''`; mileage/cert use `YYYY-MM-DD..YYYY-MM-DD`. Flag-off hides the drawer; view still shows existing chips. | Fold into permanent Payroll UX after ≥ 14 days stable. | follow-up |
| **Scoped model materialize** | `BHAGA_SCOPED_MATERIALIZE=1` | 2026-09 (Issue #285) | **on** — set on `bhaga-daily-refresh` 2026-09-14 (operator-approved); default in code remains off | Makes `materialize_model_bq --dates <iso,…>` scope the *write* to the grain units those dates touch (the days themselves; the containing ISO week and pay period, rebuilt as whole units). Off, `--dates` is ignored and every run rewrites all history — the behaviour that gave a one-date console recompute an 83-day blast radius. Computation stays full-history either way, so a scoped write is a subset of the full write, never a different one. **The scope must be as wide as the ingest window** — `daily_refresh` derives it with `ingested_dates(gap_start, refresh_date, …)`, which also folds in `--square-from`/`--adp-to` overrides. Passing only `refresh_date` left stale model rows under fresh raw data on every catch-up day but the last (Issue #295). | One full pay period has closed with the flag on and `assert_tip_pool_conserved` clean over the whole window. Clock started 2026-09-14. | follow-up |
| **ADP payroll draft** | `FEATURES.adpPayrollDraft` / `BHAGA_ADP_PAYROLL_DRAFT=1` (overrides only) | 2026-08 (Issue #251) | **on** (console) | Headless Playwright Start→Preview on ADP RUN; **leaves In Progress**. Never Approve/Submit/Save. `/payroll` closed unpaid: **Run ADP Preview** or **Preview done** + hours/total-pay vs last Preview (no Preview URL). Paid: **Open ADP payroll**. Operator submits in their own login. **`bhaga-nightly` 21:30 CT does not Start payroll**. Dedicated `bhaga-payroll-draft` Monday 07:00 CT. `BHAGA_ADP_HEADED=1` for a visible browser. | After ≥1 period the operator submits from ADP with no accidental Approve from Jarvis. | follow-up |

| **ADP solo-rate-2 keying** | `BHAGA_ADP_SOLO_RATE2=0` (kill switch) | 2026-09 (Issue #309) | **on** — default in code since Issue #358 (2026-10-06); every draft keys it, including the `/payroll` button and the stale-draft re-run | Makes the payroll draft key the solo-shift premium itself: per eligible employee, row overflow → **Add row**, pick the premium rate via **Select Available Rates**, put the premium hours on that line, and reduce the original Regular cell by the same amount so total paid hours still equal the imported timecard. Off, the draft prints the rate-1/rate-2 lines (`BREADCRUMB solo_premium_keying`) and the operator keys them. This is the one path that rewrites **hours** on a live draft — every other fill is a money column — so a selector drift here produces a wrong paycheck rather than an error; the per-employee Preview gross check reports it. Runs only after the hours guardrail passes; each employee is verified by re-reading the grid and a mismatch is reported, never retried (a retry on a half-applied split would double the premium line). **Turning it off is a job env change, never a deploy** (user-preferences #29). The operator opens the draft via **Skip import** (keeps the split, spike 2026-10-06) — never **Import latest timecards**. | The split has run clean on ≥1 full period and the console's Total pay matches ADP Preview gross with no manual keying. Keyed live 2026-09-22 for the 09-07→09-20 cycle: 7 rate-2 lines, 371.30h and $7,559.25 both reconciling to ADP Preview, operator-approved. | follow-up |
| **Stale payroll-draft re-run** | `BHAGA_PAYROLL_DRAFT_AUTO_REFRESH=0` (kill switch) | 2026-10 (Issue #358) | **on** | After Sync ADP / punch-fix resync and each verified nightly, re-runs an In Progress ADP draft whose keyed totals (`payroll_draft_runs.packet_hours`/`packet_pay`) no longer match the console, for periods closed ≤10 days. Resume only (never Starts or Deletes a payroll) and only when Payroll Home's In Progress row shows the same period. Proven live 2026-10-06 on the 09-21→10-04 draft: 5 rate-2 lines, Preview 393.05h / $9,875.97 against console 393.05h / $9,875.98. Writes hours and money into ADP, hence the switch. | One period where a post-draft punch fix lands in the ADP Preview with no operator click. | follow-up |
| **Missing-punch write-back** | `FEATURES.punchFixWriteback` (console) + `BHAGA_PUNCH_FIX_WRITEBACK=1` (job, set only by the console trigger) | 2026-10 (Issue #356) | **on** (console); default in code off for the job | Console **Write N to ADP** on the `/labor` Punches table: the job (`BHAGA_PUNCH_FIX_APPLY_ONLY=1`, `agents/bhaga/scripts/punch_fix_apply.py`) opens each day in ADP Timecards, re-reads it, fills the blank Out Time of the open entry with the operator-approved clock-out plus a comment naming the approver, Saves once and reads it back. A written fix is `applied` only once the same login's Timecard export shows it (up to 3 read-only downloads, Issue #358), else `not_in_hours` until a later sync loads it. An entry ADP already closed is `already_resolved` (no write); any other mismatch is `failed` with a `[punch-fix] FAIL` breadcrumb and is never retried automatically. Writes hours into ADP, so a selector drift could put a wrong clock-out on a timecard — hence the flag. Never approves, deletes or touches payroll. | One full pay period of console write-backs with every `applied` row matching ADP on the next timecard sync. | follow-up |
| **ADP schedule writes** | `CONSOLE_ADP_SCHEDULE_WRITE=1` on the operator-console service → `FEATURES.adpScheduleWrite` | 2026-09 (Issue #337) | **on** (2026-10-06) | Labor draft card **Save to ADP as drafts** (queues `labor_schedule_pushes`, then `BHAGA_ADP_SCHEDULE_WRITE=drafts` creates each shift via ADP Team Schedule Create shift / Create open shift → **Save as draft**) and **Publish week** (`BHAGA_ADP_SCHEDULE_WRITE=publish`: ADP **Publish drafts**, then DMs the operator a ready-to-post ClickUp team note listing the open shifts — nothing posted to the team; a week published directly in ADP also shows published after the next schedule load). Each click opens a drawer listing exactly what will be written. Drafts are invisible to employees until published. Service env (user-preferences #29), so turning it on/off is a config change, not a deploy: `gcloud run services update operator-console --region us-central1 --update-env-vars CONSOLE_ADP_SCHEDULE_WRITE=1` (or `=0`). The console deploy uses `--update-env-vars`, so a later deploy keeps it. Off = buttons render disabled; the draft is still a preview. | ≥2 weeks published from the console with every drafted shift matching ADP and no manual fix-ups. Week 1 (Oct 12–18) published 2026-10-05 from a localhost console against the branch job. | follow-up |
| **ADP unavailability approve** | `CONSOLE_ADP_UNAVAIL_APPROVE=1` on the operator-console service → `FEATURES.adpUnavailabilityApprove` | 2026-10 (Issue #337) | **off** | Labor **Unavailability** card **Approve** on a pending request → `BHAGA_ADP_UNAVAIL_APPROVE=<row_key>` Cloud Run job clicks that one card's APPROVE in Team Schedule › Pending requests (never Reject, never retried), then the schedule-only refresh. Off = the card is read-only. |
| **Recognition gift cards** | `CONSOLE_RECOGNITION_GIFT_CARDS=1` on the operator-console service → `FEATURES.recognitionGiftCards` | 2026-10 (Issue #369) | **off** until set on the service post-merge | `/automations/monthly-recognition` **Approve & issue gift cards** and **Send me a $1 test card**: Square Gift Cards API create → activate (comp instrument) → read-back, then one email per winner from `adi@mypalmetto.co` and a recap DM. Moves real money, hence the flag. Ledger `recognition_gift_cards` keyed by `rec-<store>-<month>-<user>-<n>` so a re-click never issues twice; failures are never auto-retried (**Retry failed** resumes). Off = those two buttons are disabled; Sync, drafting, reshape, **DM me the post** and **I already issued these** still work. Service env (user-preferences #29): `gcloud run services update operator-console --region us-central1 --update-env-vars CONSOLE_RECOGNITION_GIFT_CARDS=1` (or `=0`). | One month issued from the console with every card ACTIVE at the right balance and every winner emailed, no manual fix-ups. | follow-up |

**Removed flags:**

| Name | Env Var | Removed | Notes |
|------|---------|---------|-------|
| BQ-canonical Sheet projector | `BHAGA_SHEET_FROM_BQ` | 2026-06-14 (PR TBD) | Path is unconditional: `daily_refresh` always runs `materialize_model_bq` → `render_model_sheet_from_bq`. Legacy `update_model_sheet` nightly step removed. |
| Operator Console async order-reco | `FEATURES.asyncOrderReco` + job `BHAGA_ORDER_RECO_ONLY=1` | 2026-09 (Issue #350) | Console now CALLs `sp_refresh_order_reco` directly (submitted without waiting; the status banner follows the runs ledger). The Cloud Run order-reco-only job path is deleted. |

---

## "Safe by construction → no flag" precedents

Some changes are safe to apply directly without a flag because they are additive and idempotent:
- New BQ tables and views (migration 004) — `CREATE TABLE IF NOT EXISTS` / `CREATE OR REPLACE VIEW`
- New Sheet tabs (`add_sheet_if_missing`) — no-op if the tab already exists
- New Grafana dashboard panels — always additive

These are noted here so future reviewers understand the policy: flags gate **cutover** risk, not additive additions.

**Tip exemption windows (Issue #167 / migration 038):** additive `exempt_start`/`exempt_end` on
`bhaga.training_shifts`. NULL/NULL keeps legacy whole-day exclusion — no pipeline env flag.

**Effective-dated rates + admin-punch tips (Issue #343 / migrations 074–075):** N/A — no env flag.
The history table is seeded from today's `adp_wage_rates`, so every employee without a recorded
change prices bit-identically (prod parity: 0 rows differ on `vw_model_payroll_period` /
`vw_labor_daily_live`). Admin-punch matching has a runtime kill switch instead of a flag:
`store_config tip_exempt_punch_note_keywords = ""` disables it (the default `admin` applies when
the row is absent).

**Atomic order-reco refresh (Issue #350 / migrations 080–081):** hard cutover, no flag — operator
decision 2026-09-28 after sandbox proof. `sp_refresh_order_reco` matches the pre-#350 TVF chain and an
independent Python reference cell-for-cell at every capacity tested, and it is atomic: a failed run
writes a `failed` ledger row and leaves the live table untouched, so the failure mode is "stale", never
"wrong". The runtime identities that CALL it already hold the needed BigQuery roles (console
`roles/editor`; webhook/job `bigquery.dataEditor` + `jobUser`), and deploy applies migrations before
rolling out code. Rollback is a revert + deploy.

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
