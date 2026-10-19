# Issue #412 — pick from every option for a draft shift

Branch `fix/want-to-improve-the-labor-page-2` · jam + define-evidence approved 2026-10-19.
Consulted: `CONTRIBUTING.md` (dev loop, §4 evidence), `docs/contributing/ui-polish.md`,
`plan-execution-readiness.mdc`, `RUNBOOK.md` line 1787 (draft-shift paragraph).

## Problem

`draftCore` in `apps/operator-console/lib/labor/shift-draft.ts` (lines 371–416) builds the full
`eligible` list for every drafted shift and keeps only `pick`; `fillOpenShift` (lines 467–481) does the
same for ADP open shifts. `ShiftDraftSummary.tsx` (lines 243–270) renders that one name.

## Design

Engine (`lib/labor/shift-draft.ts`):

```ts
export type ShiftOption = {
  employee: string | null; // null = leave open
  startMin: number; endMin: number; hours: number; trimmed: boolean;
  weekHours: number;  // paid hours already this week, before this shift
  cap: number;        // weekCap(a)
  target?: number; recentHours: number;
};
// DraftShift gains:
//   slot?: string;            stable id of the step that drafted it
//   options?: ShiftOption[];  ranked: starts-the-gap by priority, then by gain; null option last
//   picked?: boolean;         operator pick applied
// DraftDayArgs gains: pins?: ReadonlyMap<string, string | null>; slotPrefix?: string;
export function fillOpenShift(args: {...; pins?: ReadonlyMap<string, string | null>}): DraftShift | null;
```

- Slot id = `${slotPrefix ?? iso}|${t0}|${nth}`, t0 = first short minute of the step, nth = shifts
  already in `out` with the same t0 (so seeded lookahead rollouts compute the same id). Pass 1 uses
  prefix `${iso}|f`, pass 2 `${iso}|p${placedOnDay}`. ADP open-shift fills: `${iso}|adp|${start}|${end}`.
- In the step loop: a pin for the slot is tried first (`{ person }`, no `long`); if the pinned person is not
  in `eligible` the pin is ignored and the step falls back to the normal choice (never `return null`).
  A pin skips `bestChoice` lookahead. The block/eligible computation moves into a `candidates(choice)` closure.
- Options are recorded only when `opts.lookahead` (top-level runs); rollouts skip them.

Panel (`components/labor/LaborCoveragePanel.tsx` `buildDrafts`, lines 963–1198):
- `picks` state: `Record<weekStart, Record<slot, string | null>>`, persisted in `localStorage`
  `labor-draft-picks:v1:<store>`; read in an effect (no SSR mismatch). Locked weeks (`plans.has(week)`) ignore
  and clear their picks.
- `draftWeek` passes the week's pins to `run` and `fillOpenShift`; returns `pickCount` and `droppedPicks`
  (pins no shift applied). Both rule previews (`drafts`, `savedDrafts`) apply the same pins.

UI (new `components/labor/ShiftPersonPicker.tsx`, wired into `ShiftDraftSummary.tsx` Suggested cell):
- Reuses `components/ui/select.tsx` (`Select`, `SelectTrigger`, `SelectContent`, `SelectItem`,
  `SelectSeparator`, `SelectLabel`), `Badge`, `Button` variant `link`, muted text tokens.
- Trigger: name (or "Leave open"), `Your pick` badge (DRAFT_COLOR outline) when picked, else muted
  "+N options". `h-9 sm:h-8` (tap target), focus-visible ring from the primitive, disabled while locked.
- Items: name + "Best fit" badge on rank 1; second line muted: `9:00–2:00 · 5.5h · 12h of 30h this week ·
  shortened to availability`. Last item "Leave open — posts as an open shift" (drafted shifts only).
- Row reset link "Reset" when picked; header line "N picks · Reset all"; amber note when `droppedPicks > 0`
  ("2 picks no longer fit after your edits — reset").
- No picker when `locked` or a shift has < 2 options (shows the name as today).

Scope / feature flag: additive UI; no feature flag — a pick can't silently produce wrong numbers (it is the
operator's explicit choice and Save to ADP still shows a confirm drawer). Out of scope: ranking changes,
whole-plan alternatives, server-side pick storage.

Invariants preserved: weekly cap / per-period / last-day / ADP-unavailability filters still gate every pick
(pins only choose among `eligible`); draft output without pins is byte-identical (existing tests unchanged);
Save to ADP / `labor_schedule_pushes` write path untouched (side-effect free until confirm); America/Chicago
dates unchanged; no hardcoded store ids (store from page props).

Evidence tier: unit-only — waiver: ADP has no sandbox company for Team Schedule; Scope 1 is console-only, and
Scope 2's assign job is proven by unit tests, a live prod-ADP dry run, and one live prod-ADP assignment of an
operator-chosen open shift (B4), plus a prod-data localhost demo with hosted screenshots (approved in jam 2).

## Milestone 1 — engine options + pins (Opus)

`shift-draft.ts` + tests in `__tests__/shift-draft.test.ts`:
happy path: options list every eligible person ranked + null option, marks trimmed; failure: excludes busy /
unavailable / over-cap / past-last-day; pin applies and caps hold across the week; recovery: ineligible pin
ignored without dropping the day; no-pin output equal to before.

**Verify**
```bash
cd apps/operator-console && npx vitest run __tests__/shift-draft.test.ts
```
Pass criterion: all green, existing cases unchanged.

## Milestone 2 — panel wiring + persistence (Opus)

`LaborCoveragePanel.tsx`: picks state, localStorage, pins into `draftWeek`, pickCount / droppedPicks, locked-week clear.

**Verify**
```bash
cd apps/operator-console && npx tsc --noEmit && npx vitest run
```
Pass criterion: typecheck + full vitest green.

## Milestone 3 — picker UI + component test + evidence (Opus)

`ShiftPersonPicker.tsx`, `ShiftDraftSummary.tsx` props `onPick(slot, employee | null | undefined)`,
`pickCount`, `droppedPicks`, `onResetAll`. Test `__tests__/shift-person-picker.test.tsx`: renders N options,
pick calls `onPick`, Reset calls `onPick(slot, undefined)`, locked → no picker.
Screenshots via `apps/operator-console/scripts/capture_evidence.py` (desktop + 390px), live demo on localhost
with prod data before `gh pr create`.

**Verify**
```bash
cd apps/operator-console && npx vitest run && npm run lint
python3 scripts/verify.py --full
python3 scripts/check_doc_freshness.py
```
Pass criterion: all green; operator OK on the demo.

## Scope 2 — select ADP open shifts across days, then Write to ADP (jam 2, approved 10:57)

Read-only ADP probe 2026-10-19 (week of Oct 26, Tue 4:30–8:30 PM open shift): open-shift day pane →
tile "Select open shift 4:30 PM - 8:30 PM" → **Shift details** pane ("1 of 1 available", date, *Eligible
employees* list) with **Edit | Assign** → **Assign Open Shift** pane: search box + one `sdf-checkbox` per
eligible employee (only ADP's eligible list) + bottom **Assign** (disabled until a box is checked).
Approach A (assign in place) chosen. A person ADP does not list as eligible fails that row with the list in
the error — no create+delete fallback (B) in this PR.

Engine: `fillOpenShift(args & { nth?: number })` — slot `${iso}|adp|${start}|${end}` plus `|${nth}` when
nth > 0 (identical open shifts on one day).

Panel: picks for `|adp|` slots survive on locked weeks (`draftWeek` keeps them; the clear-effect only drops
non-adp picks of locked weeks). New `components/labor/OpenShiftAssign.tsx` under the day heading lists that
day's ADP open shifts (`lanesFor(day)` open segments, days after today) with a `Checkbox`
(`components/ui/checkbox.tsx`) + `ShiftPersonPicker` (no "Leave open" item). Selection = slot ids in
`localStorage` `labor-open-assign:v1:<store>`; a selected slot with no matching open shift is dropped with an
amber note. Sticky bar (`sticky bottom-3`, card surface, shadow): "N open shifts selected · Tue Oct 27, Thu
Oct 29 · Clear · Write to ADP". Write to ADP → `Sheet` drawer listing date / time / person, then
`assignOpenShiftsAction`. Polls `openShiftAssignStatusAction(assignId)` every 5 s; when the run ends:
`router.refresh()`, drop assigned slots from the selection, toast per-row failures.

Server: `lib/labor/open-shift-assign.ts` (types, `assignRowKey`, `validateAssignItems(items, todayIso)`),
`lib/labor/open-shift-assign-store.ts` (`queueAssign`, `assignRows`), `app/labor/actions.ts`
`assignOpenShiftsAction(items)` / `openShiftAssignStatusAction(assignId)` behind `FEATURES.adpScheduleWrite`;
`triggerAdpScheduleWrite` gains `{ mode: "assign"; assignId }` → env `BHAGA_SCHEDULE_ASSIGN_ID`.

Migration `core/migrations/089_labor_open_shift_assignments.sql`:
```sql
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.labor_open_shift_assignments` (
  store STRING NOT NULL, assign_id STRING NOT NULL, row_key STRING NOT NULL,
  date DATE NOT NULL, employee STRING NOT NULL, start_min INT64 NOT NULL, end_min INT64 NOT NULL,
  status STRING NOT NULL,  -- queued | assigned | skipped | failed
  error STRING, adp_name STRING, requested_by STRING NOT NULL,
  requested_at TIMESTAMP NOT NULL, updated_at TIMESTAMP);
```
Separate from `labor_schedule_pushes` so an assignment never turns a week into a locked saved plan.

Job: `adp_schedule_write.py --assign-id <id>` (`BHAGA_ADP_SCHEDULE_WRITE=assign`, wired in
`daily_refresh.py`): per week, read ADP open shifts once; a row whose slot ADP no longer lists open →
`failed` "no longer open in ADP"; row_key already `assigned` by another run → `skipped`; else
`wb.assign_open_shift(frame, page, date_iso, start_min, end_min, employee, dry_run)` and confirm by
re-reading that week's open shifts (count for the slot dropped by one) → `assigned`, else `failed`
"ADP did not confirm". Crash marks unsettled rows `failed`. Then `refresh_schedule` over the changed weeks.
Breadcrumb `BREADCRUMB adp_open_shift_assign assign_id=… row_key=… error=…`.

## Milestone 4 — ADP assign backend + job (Opus)

`schedule_write_backend.assign_open_shift`, `adp_schedule_write.run_assign`, `daily_refresh` mode, migration 089.
Tests `skills/adp_run_automation/test_schedule_write_backend.py` (eligible match / not-eligible error / dry-run backs
out) and `agents/bhaga/scripts/test_adp_schedule_write.py` (only queued rows, skip already-assigned, vanished slot,
unconfirmed, dry run leaves BQ, crash marks failed, refresh over changed weeks).

**Verify**
```bash
python3 -m pytest skills/adp_run_automation/test_schedule_write_backend.py agents/bhaga/scripts/test_adp_schedule_write.py -q
BHAGA_ADP_CDP_URL=http://127.0.0.1:9333 python3 -m agents.bhaga.scripts.adp_schedule_write --store palmetto --assign-id <id> --dry-run
```
Pass: green; dry run reaches the enabled final Assign and backs out (nothing saved).

## Milestone 5 — selection UI + actions + live write (Opus)

`OpenShiftAssign.tsx`, panel wiring, actions, store; tests `__tests__/open-shift-assign.test.tsx` (selection persists
across days + reload, count, Clear, vanished shift dropped with note) and `__tests__/open-shift-assign-lib.test.ts`
(validation). B4: one operator-chosen open shift written to prod ADP from localhost, page refreshes from ADP.

**Verify**
```bash
cd apps/operator-console && npx tsc --noEmit && npx vitest run && npm run lint
python3 scripts/verify.py --full
```
Pass: green; live assignment shows in ADP and on the refreshed page.

## Docs lock-step

`RUNBOOK.md` draft-shift paragraph (line ~1787): picker, picks kept per browser until Save to ADP; new open-shift
assign paragraph + `BHAGA_ADP_SCHEDULE_WRITE=assign`. `agents/bhaga/scripts/README.md` adp_schedule_write row.
`.cursor/rules/bhaga.mdc` invariant 6 (assign is an operator-clicked schedule write). `MUTATING_ACTIONS.md`,
`docs/FEATURE_FLAGS.md` ADP schedule writes row, `docs/operator-console/ARCHITECTURE.md` scheduling bullet.
`PROGRESS.md` via the retro follow-up only. Run `check_doc_freshness.py`.

## PR mechanics

One branch / one PR; push as bot account (`jarvis-agent-bot328`), `gh pr create --base main`, `Refs #412`;
babysit per pr-workflow; never self-merge — operator merges. Model routing: Opus throughout (single chat, cost playbook).
