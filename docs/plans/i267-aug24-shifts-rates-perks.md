# Issue #267 — Aug 24 shifts, pay_info rate floor, payroll reimbursements

Operator jam (2026-08-25): fold all three into this PR; **localhost console review before opening a PR**.

Evidence tier: sandbox-live
scenario: full-live

(Localhost `/labor` + `/payroll` is the pre-PR operator gate. Sandbox-live + prod ADP dated refresh remain PR §4 after feedback.)

## Invariants
- America/Chicago dates; integer cents; idempotent MERGE/upserts; sandbox never writes prod.
- Read-only toward ADP Submit. pay_info MERGE must not clobber salaried/earnings rates with token hourlies.
- Gym $20 biweekly (pay_period `''`) stays; one-shot mileage is period-scoped.

## Feature-flag decision
- **No pipeline flag** (silent wrong hours/wages).
- Console: `FEATURES.writePerks` (default **on** for localhost), `docs/FEATURE_FLAGS.md` row. Additive `employee_perks.pay_period`.

## UX polish
Reuse Payroll `RecognitionDrawer` / `Sheet` / `Button` / `Badge` / `EmployeeCombobox` / `useConsoleAction`. Hover/focus-visible/pending on the new button; ~44px tap. Hosted screenshots after localhost OK (G5).

## Milestone 1 — Rate floor + restore Lindsay (Sonnet)
`skills/adp_run_automation/pay_info_backend.py:445` `prepare_pay_info_writes`: skip MERGE when `new < 0.5 * old`; breadcrumb `refused_rate_drop`. Tests in `skills/adp_run_automation/test_pay_info_backend.py:52`. Restore prod `Krause, Lindsay` `wage_rate_dollars=25` via MERGE (existing OT 37.5).

**Verify:** `python3 -m pytest skills/adp_run_automation/test_pay_info_backend.py -q`

## Milestone 2 — Timecard marker + Labor coverage fallback (Sonnet)
`agents/bhaga/scripts/daily_refresh.py:2931` after ADP load: if `adp_shifts` has 0 rows for `refresh_date`, `clear_step(..., "adp_reports")` + breadcrumb `adp_shifts_missing_refresh_date`.
`apps/operator-console/lib/bq/queries.ts:592` `laborScheduledShiftDays`: `allowPast?: boolean` omits `date >= CURRENT_DATE`.
`apps/operator-console/app/labor/page.tsx:185`: coverage fetch `allowPast: true`; filter scheduled for past days only when that date has no actuals (`apps/operator-console/lib/labor/coverage-model.ts:148`).
Empty copy: punches missing vs closed (`apps/operator-console/components/labor/LaborCoveragePanel.tsx:423`).

**Verify:** `npx vitest run apps/operator-console/__tests__/coverage-model.test.ts apps/operator-console/__tests__/actual-schedule-windows.test.ts`

## Milestone 3 — Period-scoped perks + Payroll drawer (Sonnet)
`core/migrations/068_employee_perks_pay_period.sql`: `pay_period STRING` default `''`; `CREATE OR REPLACE` `vw_model_payroll_period` perks join (copy `core/migrations/064_payroll_adp_wage_rounding.sql:54`) so `pay_period=''` OR `pay_period = start..end`.
`apps/operator-console/lib/bq/writes.ts:607` MERGE perk on `(store, employee, perk_id, pay_period)`.
`apps/operator-console/components/drawers/PerkDrawer.tsx` clone of `apps/operator-console/components/drawers/RecognitionDrawer.tsx:24`. Types: mileage / gym / food_handler / other; cadence once vs biweekly.
`apps/operator-console/lib/config/features.ts:25` `writePerks: true`. `apps/operator-console/lib/payroll/perkLabels.ts:9` labels.

**Verify:** `npx vitest run apps/operator-console/lib/payroll/perkLabels.test.ts` + localhost below.

## Milestone 4 — Localhost (operator feedback, before PR) (Sonnet)
```bash
BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
cd apps/operator-console && BYPASS_IAP_EMAIL=operator@mypalmetto.co npm run dev
```
Pass: `/labor?day=2026-08-24` scheduled swimlanes (hatch) not blank “No shifts” while sales exist; `/payroll` period 8/10–8/23 Lindsay ~$25 wages; Add reimbursement… adds mileage chip. **Sync clocked hours** visible on Labor (next to Sync scheduled shifts) and Payroll (next to Run ADP Preview); do not click live scrape unless OTP is acceptable.

## Milestone 5 — Sync clocked hours button (Sonnet)
`BHAGA_ADP_TIMECARD_ONLY` early-exit in `agents/bhaga/scripts/daily_refresh.py` after schedule-only: Timecard + `backfill_from_downloads` skipping square/rates/schedule/liability/rollup (not pay_info). Console: `SyncClockedHoursButton` on `/labor` + `/payroll`; poll `MAX(adp_shifts.scraped_at_utc)`. Local `BYPASS_IAP` spawn vs Cloud `runJob`. Timeout 10 min.

**Verify:** `python3 -m pytest agents/bhaga/scripts/test_daily_refresh.py -q -k TimecardOnly` + `npx vitest run apps/operator-console/__tests__/actual-schedule-windows.test.ts`

## Milestone 6 — Labor % live rates + weekly hours goal on Labor (Sonnet)
`laborDaily` / `laborByGrain` (non-hour) overlay `adp_shifts × adp_wage_rates` for labor $ (sales still from `vw_model_labor_daily`). Frozen model dollars after the $1.25 pay_info scrape showed PT 17.8% / 12.8% for weeks of 8/10 and 8/17; live rates are ~30.5% / ~23.4%.
Labor page `LaborWeeklyHoursGoal` writes `goal_labor_hours_week` via existing `saveGoalAction`; `revalidatePath("/labor")` + `/home`.

**Verify:** `npx vitest run apps/operator-console/__tests__/goal-fields.test.ts` + localhost `/labor` week grain, Hours chart unit = % of net sales, weeks of Aug 10 / Aug 17 PT in mid-20s; pencil edits weekly hours goal and Home Goals drawer shows the same value.

## Milestone 7 — Catch frozen labor $ everywhere + mechanical gate (Sonnet)
`core/migrations/069_labor_cost_live_rates.sql`: `vw_labor_daily_live` / `vw_labor_weekly_live`. Console `laborDaily` / `laborByGrain` / `laborForwardSummary` / `laborWeekly` + Grafana panels 32/36 read those views. Gate `scripts/check_live_labor_cost.py` (wired in `verify.py` + grafana-dashboard-sync). Formula tests `live-labor-cost.test.ts` (12.8/17.8 vs 23.4/30.5 fixture).

**Verify:** `python3 scripts/check_live_labor_cost.py` + `npx vitest run apps/operator-console/__tests__/live-labor-cost.test.ts` + `python3 -m pytest scripts/test_check_live_labor_cost.py -q`

## PR §4 (after localhost OK)
Happy path + failure/recovery + legacy as jammed in chat. Docs: `RUNBOOK.md`, `DOMAIN.md`, `FEATURE_FLAGS.md`, `ARCHITECTURE.md` payroll perks. `python3 scripts/check_doc_freshness.py`.

**PR mechanics:** one branch `fix/sharing-screenshot-where-no-shifts-are`; `gh pr create --base main`; push as `jarvis-agent-bot328`; never self-merge; babysit after PR exists. Do **not** open the PR until operator localhost feedback.

**Model routing:** M1–M4 Sonnet; jam already used Opus.
