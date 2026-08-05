# Plan — #218 labor: paid PTO in scheduled hours + Exclude PTO filter

Evidence tier: sandbox-e2e
scenario: Schedule JSON reparse (Schedule-2026-08-03) + localhost Labor hover

## Scope
Labor only (inventory avg/day deferred to separate chat). Activity dock = follow-up issue.

## Citations
- `skills/adp_run_automation/schedule_backend.py:330` — `parse_day_cell_hours`
- `skills/adp_run_automation/schedule_backend.py:436` — `build_employee_schedule_records`
- `skills/adp_run_automation/schedule_backend.py:530` — `reconcile_employee_vs_footer`
- `skills/adp_run_automation/test_schedule_backend.py:140` — Krause PTO fixture
- `core/migrations/053_adp_scheduled_shifts_hour_kind.sql:6` — ADD COLUMN hour_kind
- `agents/bhaga/scripts/backfill_from_downloads.py:272` — persist hour_kind + reconcile warn
- `apps/operator-console/lib/bq/queries.ts:202` — `laborScheduledHoursByGrain` excludePto
- `apps/operator-console/lib/filters/pto-filter.ts:1` — URL `pto=exclude`
- `apps/operator-console/app/labor/page.tsx:70` — PTO FilterSelect
- Docs lock-step: `docs/operator-console/ARCHITECTURE.md`, `RUNBOOK.md` (schedule sync note if present), this plan

## Inline artifacts
```sql
ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_scheduled_shifts`
ADD COLUMN IF NOT EXISTS hour_kind STRING;
```
```bash
BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
python3 -m agents.bhaga.scripts.backfill_from_downloads --store palmetto \
  --skip square --skip adp_shifts --skip adp_punches --skip adp_rates \
  --skip adp_liability --skip square_rollup
pytest skills/adp_run_automation/test_schedule_backend.py -q
python3 scripts/verify.py --full
```

### Milestone 1 — Parser
PTO cell_text → hours + hour_kind; Krause fixture sums to 40 paid.
**Verify:** `pytest skills/adp_run_automation/test_schedule_backend.py -q`

### Milestone 2 — Load + schema
Migration 053; backfill writes hour_kind; footer≈emp (±0.1h).
**Verify:** `python3 scripts/verify.py --full` (unit gates) + BQ Aug 10 daily == shifts

### Milestone 3 — Console filter
Include PTO default; Exclude drops pto from charts.
**Verify:** localhost `/labor` Wk Aug 10 Total (scheduled) ≈ footer; Exclude drops ~23h

### Milestone 4 — PR ship
Labor-only commit; bot push; babysit CI.
**Verify:** `python3 scripts/pr_triage.py --pr N` green; never self-merge

## Evidence scenarios (PR §4)
- Happy path: Krause PTO cells → 40 paid; Aug 10 emp_sum == footer
- Failure/recovery: sparse empty cells still do not inflate (Issue #213); reconcile warn on gap
- Legacy: NULL hour_kind treated as shift
- Filter: excludePto omits hour_kind=pto
- Pass criterion: console Total (scheduled) matches ADP footer after Sync (±0.1h)

## Invariants
America/Chicago; read-only ADP; purge+upsert shifts; never invent hours without cells; PTO counts toward labor by default.

## Feature flag
No flag — silent wrong schedule hours; fix is always-on parse + optional UI filter.

## Docs lock-step
Update `docs/operator-console/ARCHITECTURE.md` and cite RUNBOOK schedule sync; plan lives at `docs/plans/i218-labor-pto-scheduled-hours.md`.

## Branch / PR
Branch `fix/want-to-work-on-2-things`; `gh pr create --base main`; GitHub as `jarvis-agent-bot328`; never self-merge; reply every review thread.

## Model routing
- Milestone 1–3: Sonnet 5 medium
- Hard CI / review: Opus if needed
- Docs: Composer ok
