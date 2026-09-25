---
name: i342 labor open shifts
overview: Scrape ADP Team Schedule open (unassigned) shifts into a new BQ table, refresh them on the nightly, Sync scheduled shifts and Sync clocked hours, and show them on /labor (Staffing coverage lanes + Hours chart stack) with a visual language distinct from assigned scheduled shifts. Also let local ADP runs attach to one long-lived signed-in Chrome so the operator is not asked for an OTP on every run.
isProject: false
---

# Labor open shifts (Issue #342)

Jam + §4 approved in chat 2026-09-25 (recorded via `phase_state.py advance --operator-approved`).
Branch `fix/i-want-to-see-open-shifts`. Consulted: [CONTRIBUTING.md](../../CONTRIBUTING.md),
[bhaga.mdc](../../.cursor/rules/bhaga.mdc) (invariants 3, 7, 13; OTP + `adp_session` rules),
[docs/operator-console/adp-forward-labor-spike.md](../operator-console/adp-forward-labor-spike.md),
prior plans [i335](i335-labor-hours-per-person-scheduled.md), [i243](i243-labor-coverage-schedule-always.md),
`user-preferences.mdc` Design #27 (UI polish) and B4 (`capture_evidence.py`).

**Evidence tier: sandbox-e2e** (parser/loader/console unit tests + the per-PR Sandbox e2e check;
plus live prod ADP verification — backfill + nightly + both Sync buttons — per preference J2).
Operator reviews the localhost build before the PR opens.

## Spike findings (2026-09-25, live ADP, read-only)

- The Open Shifts row is the first `.calendar-row` in `iframe[name="timePartnerFrame"]`; its
  label reads `Open Shifts <N> Shifts, <HH:MM> HRS`. It has **no** `.worker-name`, so the existing
  `/Open Shifts/i` skip in `schedule_backend.py:106` / `:159` never even saw it.
- Each day with open shifts has one `.open-shift-count` cell (`Open Shifts (2) Drafts: 0 Published: 2
  Claims Pending: 0`) with **no times**. Clicking it opens an `sdf-focus-pane` (heading
  `Open Shifts on Saturday, Oct 03`) whose light DOM holds one `sdf-quick-stat` per shift:
  value = `10:00 AM - 4:00 PM`, shadow label = `06:00 hours` (paid hours). Back button closes it.
- Week Sep 28 – Oct 4: 7 slots, 45.5 h — equals ADP's row label `7 Shifts, 45:30 HRS`.
- ADP footer day totals exclude open shifts (BQ: footer ≈ Σ `adp_scheduled_shifts` within 0.5 h
  every day since Aug), so nothing existing double-counts.
- ‹ paging back works (Jun 29 → Sep 20 checked) but every past week's Open Shifts row is empty —
  backfill = the existing forward horizon (current week + up to 8), no backward scrape needed.
- OTP: every fresh browser launch re-authenticates (SMSESSION is a session cookie) and ADP's
  risk engine sent a code each time (4 codes in one hour). Attaching to one long-lived Chrome
  (`--remote-debugging-port`, same `~/.bhaga/browser-profiles/adp` profile) cost one code, then
  zero for all later runs.

## M1 — Scrape + parse + local OTP reuse (Python)

[skills/adp_run_automation/schedule_backend.py](../../skills/adp_run_automation/schedule_backend.py):

```python
OPEN_SHIFT_CELLS_JS  # -> {"row_label": str|None, "cells": [{"index": int, "header_index": int|None}]}
OPEN_SHIFT_PANE_JS   # -> {"heading": str|None, "shifts": [{"range": str, "hours_text": str}]}
def parse_open_pane_date(heading: str | None, week_start: date) -> date | None
def build_open_shift_records(weeks: list[dict]) -> list[dict]
    # {date, slot_index, shift_range, scheduled_hours, week_start}; hours from "06:00 hours"
    # via parse_hhmm_hours; date from the pane heading (fallback week_start + header_index)
def open_shift_weeks(weeks: list[dict]) -> list[str]
    # week_start ISO for every week whose open-shift extract succeeded (purge scope)
```

[skills/adp_run_automation/runner.py](../../skills/adp_run_automation/runner.py):
- `_scrape_one_week` (line 1732): after the employee extract, `_scrape_open_shifts(page, frame)`
  clicks each `.open-shift-count`, reads the visible pane, clicks Back. Payload gains
  `open_shift_cells` (list) or `open_shifts_error` (str). Never raises — breadcrumb
  `[adp_schedule] open_shifts=N week=<label>` / `[adp_schedule] WARN: open-shift extract failed`.
- `adp_session` (line 2070): when `BHAGA_ADP_CDP_URL` is set and not on Cloud Run
  (`K_SERVICE`/`CLOUD_RUN_JOB`), attach via `skills/_browser_runtime/runtime.py::attach_cdp(url)`;
  reuse a signed-in tab's URL, login only if needed; detach without closing the browser.
- `download_adp_bundle` (line 2108): new `include_liability: bool = True`.

[skills/_browser_runtime/runtime.py](../../skills/_browser_runtime/runtime.py): `attach_cdp(cdp_url: str) -> Iterator[tuple[BrowserContext, Page]]` (drives the context's single existing tab, closing strays; RUN's one-tab guard).

[agents/bhaga/scripts/daily_refresh.py](../../agents/bhaga/scripts/daily_refresh.py) line 2684
(`BHAGA_ADP_TIMECARD_ONLY`): replace `download_timecard` with
`download_adp_bundle(target_date=refresh_date, include_earnings=False, include_schedule=True,
include_liability=False)` after unlinking today's Schedule JSON; drop `--skip adp_schedule`
only when `result["schedule_json"]` exists and `errors` has no `adp_schedule` (else keep the skip
and print `[adp-timecard-only] WARN: schedule refresh failed: …`). Timecard failure still raises.

Test: `python3 -m pytest skills/adp_run_automation/test_schedule_backend.py skills/adp_run_automation/test_session_persist.py skills/_browser_runtime -q` — new cases: single slot, two slots same day, heading date across a year boundary, pane with no quick-stats, week with `open_shifts_error` excluded from purge scope, fixture `testdata/schedule_open_shifts_spike.json` sums to 45.5 h; `attach_cdp` path skipped on Cloud Run. Pass: all green.

## M2 — BQ table + loader

`core/migrations/073_adp_open_shifts.sql`:

```sql
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_open_shifts` (
  date                DATE NOT NULL,
  slot_index          INT64 NOT NULL,   -- order within the day's pane
  shift_range         STRING,           -- "10:00 AM - 4:00 PM" (wall clock)
  scheduled_hours     FLOAT64,          -- ADP paid hours from the pane
  week_start          DATE,
  scraped_at_utc      TIMESTAMP,
  materialized_at_utc TIMESTAMP
);
```

[agents/bhaga/scripts/backfill_from_downloads.py](../../agents/bhaga/scripts/backfill_from_downloads.py)
`_load_adp_schedule` (line 380): after the employee load, inside its own try (open-shift failure
must not undo the assigned load): `DELETE FROM adp_open_shifts WHERE week_start IN (<scraped
weeks>)`, then `load_rows("adp_open_shifts", rows, merge_keys=["date","slot_index"])`. Purge is by
**week** (not by dates present) so a week whose open shifts were all filled goes empty. Weeks with
`open_shifts_error` are not purged. Summary `adp_open_shifts (BQ): N rows`.

Apply: `bq query --use_legacy_sql=false < core/migrations/073_adp_open_shifts.sql` (prod) and the
sandbox dataset equivalent (`sed s/bhaga\./bhaga_sandbox./`).

Test: `python3 -m pytest agents/bhaga/scripts -q -k "schedule or backfill"` with a loader unit test
(fake client: purge scope = successful weeks only). Pass: all green.

## M3 — Console

[apps/operator-console/lib/bq/queries.ts](../../apps/operator-console/lib/bq/queries.ts):

```ts
export interface LaborOpenShiftHoursRow { date: string; open_hours: number; [k: string]: unknown }
export function laborOpenShiftHoursByGrain(win: DateWindow, grain: Grain): Promise<LaborOpenShiftHoursRow[]>
export interface LaborOpenShiftDayRow { date: string; slot_index: number; shift_range: string | null; scheduled_hours: number; [k: string]: unknown }
export function laborOpenShiftDays(win: DateWindow): Promise<LaborOpenShiftDayRow[]>
```
`adpScheduleHorizonEnd` (line 783) also considers `adp_open_shifts` (GREATEST of both maxima).

[apps/operator-console/lib/labor/schedule-fetch-gates.ts](../../apps/operator-console/lib/labor/schedule-fetch-gates.ts):
`showChartOpenShifts({includesToday, hasSchedWin, grain})` = `showChartSchedule(...) && grain !== "weekday"`.

[apps/operator-console/lib/labor/coverage-model.ts](../../apps/operator-console/lib/labor/coverage-model.ts):
`CoverageKind` gains `"open"`; `OpenShiftInput`; `buildOpenLanesForDate(date, open)` greedily packs
non-overlapping slots into lanes labelled `Open shift` (bucket `open`); `occupancySeries` adds
`open`; `dayChipSummary` counts people only and adds `open` (slot count). Open lanes ignore the
labor-type filter and are shown for past days too (unfilled gaps).

[apps/operator-console/components/labor/LaborCoveragePanel.tsx](../../apps/operator-console/components/labor/LaborCoveragePanel.tsx):
open lanes first; bar = transparent fill, 1.5px dashed violet border, light violet hatch, label
`Open · 6h`; ribbon adds a violet cap for open slots; tooltip lists `Open shift` rows; chip shows
`+N open` badge; legend `Open shift (unassigned)`; copy updated.

[apps/operator-console/components/labor/LaborHoursChart.tsx](../../apps/operator-console/components/labor/LaborHoursChart.tsx):
row field `open_hours`; series `open` (`Open shifts (unassigned)`, violet, `pattern: "hatch"`)
stacked last; tooltip `Open (unassigned)` + `Total if filled` (= actual + scheduled + open);
weekly goal % uses total-if-filled when open > 0.
[components/charts/BarChartCard.tsx](../../apps/operator-console/components/charts/BarChartCard.tsx) +
`Series` in `LineChartCard.tsx`: optional `pattern?: "hatch"` → SVG `<pattern>` fill + dashed stroke.
[lib/charts/palette.ts](../../apps/operator-console/lib/charts/palette.ts): `LABOR_CHART_COLORS.openShift = "#8b5cf6"` (violet-500 — distinct from rose PT, orange FT, slate schedule, gold goal).

[apps/operator-console/app/labor/page.tsx](../../apps/operator-console/app/labor/page.tsx): add both
queries to the `Promise.all` (line 197; `.catch(() => [])`); chart query over `schedWin` only when
`showChartOpenShifts`; coverage query over `chartWin` always; merge `open_hours` into `chartData`
(line 325); pass `open` to `LaborCoveragePanel`; note copy.

Test: `cd apps/operator-console && npx vitest run && npx tsc --noEmit && npm run lint` — new cases in
`coverage-model.test.ts` (lane packing, occupancy open, chip counts, labor-type ignores open),
`labor-hours-chart.test.ts` (series/tooltip/total-if-filled/goal), `labor-schedule-gates.test.ts`
(weekday + hour off). Pass: all green.

## M4 — Backfill, localhost review, evidence, PR

1. Apply migration 073 (prod + sandbox). Backfill: `BHAGA_ADP_CDP_URL=http://127.0.0.1:9333 BHAGA_ADP_SCHEDULE_ONLY=1 BHAGA_STORE=palmetto python3 -m agents.bhaga.scripts.daily_refresh --store palmetto` (attached Chrome, no new OTP), then compare BQ to the live grid.
2. `cd apps/operator-console && BHAGA_ADP_CDP_URL=http://127.0.0.1:9333 npm run dev`; post URLs; exercise both Sync buttons on localhost; **pause for operator OK** (AskQuestion, wait indefinitely — preference 24).
3. `python3 apps/operator-console/scripts/capture_evidence.py` from the approved build; `python3 scripts/verify.py --full`; `gh pr create --base main` as `jarvis-agent-bot328` with §4; babysit.

### Per-scenario evidence (PR §4)

1. Spike fixture + screenshots (row, pane, past week empty).
2. Parser: single slot, 2 slots/day, year-boundary heading, empty pane.
3. Regression: `build_employee_schedule_records` byte-identical on existing fixtures; reconcile unchanged.
4. Loader: purge by scraped weeks only; errored week untouched.
5. Backfill: BQ `adp_open_shifts` = live grid (Sep 27: 1 × 6 h; Sep 28 wk: 7 slots 45.5 h).
6. Nightly: next `bhaga-nightly` logs `open_shifts=N`; `scraped_at_utc` advances.
7. Sync scheduled shifts (localhost + prod): scraped_at advances, rows match ADP.
8. Sync clocked hours: Timecard + open shifts refresh; schedule failure → sync still done + WARN (unit test).
9. Coverage: upcoming + past day with open lanes; no-open day unchanged.
10. Hours chart: Weekly/Daily open segment + tooltip; Weekday/Hour none; past-only Period none; PT filter still shows open.
11. Failure: open query error → page renders without open (unit test).
12. `vitest`, `pytest`, `verify.py --full` green.
13. Post-merge: prod `/labor` screenshots; prod BQ = live grid.

## Feature-flag decision

No flag. "Can it silently produce wrong numbers?" — no pay/tip/labor-% path reads `adp_open_shifts`;
the Hours chart shows open hours as a separate, labelled series. No FEATURE_FLAGS.md entry.
`BHAGA_ADP_CDP_URL` is a laptop-only dev/runtime switch (ignored on Cloud Run), documented in RUNBOOK.

## Invariants preserved

- Read-only ADP: only clicks that open/close the read-only details pane; never Create/Claim/Publish.
- Idempotent: week-scoped purge + MERGE on `(date, slot_index)`; re-running converges.
- America/Chicago dates (week labels are shop-local; console windows unchanged).
- Assigned-shift tables and totals untouched; tip/payroll math untouched; integer cents N/A.
- Sandbox isolation: loader goes through `datastore` (sandbox guard applies).
- Every ADP launch still goes through `adp_session` (test_session_persist gate).

## UX polish (Design #27)

Reuses `BarChartCard` stacking + custom tooltip entries, coverage lane/ribbon/legend primitives,
shadcn `Badge`. Open shifts get one consistent visual token everywhere (violet, dashed outline,
hatch) so they read as "needs a person", distinct from solid clocked bars and slate scheduled hatch.
Hover tooltips on both surfaces; chip badge truncates on mobile.

## Docs lock-step

`RUNBOOK.md` (open-shift channel, `BHAGA_ADP_CDP_URL`), `agents/bhaga/scripts/README.md` (loader),
`agents/bhaga/knowledge-base/DOMAIN.md` (adp_open_shifts), `skills/adp_run_automation/README.md`,
`docs/operator-console/ARCHITECTURE.md` (L-labor), `.cursor/rules/bhaga.mdc` (OTP: attach, don't relaunch),
`docs/operator-console/adp-forward-labor-spike.md` (open-shift DOM). Run `python3 scripts/check_doc_freshness.py`.

## Branch / PR mechanics

One branch, one PR (`--base main`, `Refs #342`), bot account `jarvis-agent-bot328`, `--no-verify`
push only after `verify.py --full` green, never self-merge, reply to every review comment, babysit
via `pr_triage.py`, bind cost ledger after the PR opens.

## Model routing

M1–M3: Sonnet 5 medium (feature work). M4 evidence/PR: Sonnet. Opus only if review blocks.
