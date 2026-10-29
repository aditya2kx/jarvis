# Issue #434 — create / assign / edit / delete ADP shifts from Coverage, one "Changes to ADP" tray, hide suggestions

Branch `cursor/434-want-to-similarly-add-capabilities-to-create-open-shifts-assign-open-shifts-up-e989` ·
jam agreed 2026-10-28 (Decision 1A full end-to-end with live ADP writes; 2A one tray that also applies
unavailability). Consulted: `CONTRIBUTING.md` (dev loop, §4 evidence), `docs/contributing/ui-polish.md`
(D1–D10), `.cursor/rules/bhaga-principles.mdc` + `bhaga.mdc` invariant 6, `plan-execution-readiness.mdc`,
`docs/plans/i412-draft-shift-options.md` (assign pattern), PR #428 (unavailability staging).

## Problem

ADP Team Schedule lets the operator create an open shift, assign it, and edit or delete any shift
(open or published, from its Shift details pane). An assigned shift cannot be reassigned; delete +
create is the workaround. The console only does: Save suggested drafts, Publish week, assign an
existing open shift (`OpenShiftAssignBar`, immediate write). Unavailability changes stage into
`UnavailabilityApplyTray` (`components/labor/availability/UnavailabilityRequests.tsx:200`) whose Apply has
no handler and no job. The "Hide draft shifts" button (`LaborCoveragePanel.tsx:1807-1815`) only shows when
the day's week has a draft, resets on reload, and leaves the Finalize card, chip counts and the
open-shift fill suggestions visible.

## Design (D1–D10 check)

- **D1 enhance existing view.** Everything lives in Labor › Staffing coverage: the person drawer
  (`EntrySheet` `PersonBody`, `EntrySheet.tsx:251`) gains a **Shifts** section; the day's open-shift list
  (`OpenShiftDayList`, `OpenShiftAssign.tsx:71`) gains **Add open shift**, per-row **Edit** / **Delete**.
  No new page, tab or card.
- **D2 one way.** One staged queue + one sticky tray **"Changes to ADP"** replaces both
  `UnavailabilityApplyTray` and `OpenShiftAssignBar`. Assigning an open shift stages into the tray (no
  second immediate "Write to ADP" path). Scheduled shifts are edited only from the person's name; open
  shifts only from the day's open-shift list. Lanes stay inspect-only (tooltip).
- **D3 no regressions.** Tooltip on tap (pointer events) unchanged; existing assign picks
  (`labor-open-assign:v1:<store>`) migrate into the queue on load; Save to ADP drafts / Publish week /
  Send ClickUp message unchanged. iPhone WebKit touch check in evidence.
- **D4 each fact once.** Tray lines say what will change, nothing restating the chart.
- **D5 encodings.** A staged change shows on its lane as the existing shift outline with a primary-colour
  ring + "pending" in the tooltip; a staged delete dims the bar (opacity 40%). No new hatch.
- **D6 same interaction.** Shift and unavailability rows in the drawer use the same row + Edit/Delete
  buttons and the same Pick-days editor (`DayPicker`, `EntrySheet.tsx:128`).
- **D7 bulk, batched.** Add open shift / Add shift pick many days at once (`DayPicker`, count per day);
  Apply = one Cloud Run execution for the whole tray.
- **D8 plain wording.** "Add open shift", "Edit times", "Delete shift", "Reassign" (= delete + new shift,
  explained in one line), switch label "Suggestions".
- **D9 compact default.** Drawer Shifts section lists the next 14 days; "Show earlier" for past.
- **D10 ADP source of truth.** A change is "applied" only after the job re-reads ADP; the page refreshes
  from the re-scraped `adp_scheduled_shifts` / `adp_open_shifts`. History is append-only.

### Suggestions switch
`LaborCoveragePanel.tsx:1211` `useState(true)` → persisted `labor-show-suggestions:v1:<store>` (read in an
effect, default `true` so nothing changes until the operator turns it off). A `Switch`-styled toggle
(`components/ui/button.tsx` `variant="outline"` + `aria-pressed`, label "Suggestions") always shows in the
Coverage header (not only when `weekDraft`). Off hides: draft lanes, `+N draft` chip counts, the
"With draft —" short line, `ShiftDraftSummary`, `AdpScheduleFinalize`, and suggested fill names in
`OpenShiftDayList` (picker shows "Choose who"). Does not hide ADP's real shifts or staged changes.

### Staged change model (`apps/operator-console/lib/labor/adp-changes.ts`, new; `availability-changes.ts` kept for entry types)
```ts
export type ShiftFields = { date: string; startMin: number; endMin: number };
export type ShiftChange =
  | ({ kind: "open-add"; count: number; summary: string } & ShiftFields)
  | ({ kind: "open-assign"; employee: string; summary: string } & ShiftFields)
  | ({ kind: "open-edit"; from: ShiftFields; summary: string } & ShiftFields)
  | ({ kind: "open-delete"; summary: string } & ShiftFields)
  | ({ kind: "shift-add"; employee: string; summary: string } & ShiftFields)
  | ({ kind: "shift-edit"; employee: string; from: ShiftFields; summary: string } & ShiftFields)
  | ({ kind: "shift-delete"; employee: string; summary: string } & ShiftFields);
export type AdpChange = { area: "shift"; c: ShiftChange } | { area: "unavailability"; c: StagedChange };
export function adpChangeKey(c: AdpChange): string;          // exhaustive switch + never
export function stageAdp(q: AdpChange[], c: AdpChange): AdpChange[];
export function shiftError(c: ShiftChange, todayIso: string): string | null; // future date, end > start, ≤ 16h
export function trayLines(q: AdpChange[]): TrayLine[];       // person/day order = job order
```
Reassign in the drawer stages `shift-delete` + `shift-add` together (one tray line "Reassign").

### Server
- `lib/labor/adp-change-store.ts` (new): `queueAdpChanges(store, batchId, shift[], unavail[], by)` inserts
  `adp_shift_changes` + `adp_unavailability_changes` rows (`status='queued'`), `batchStatus(batchId)`.
- `app/labor/actions.ts`: `applyAdpChangesAction(changes: AdpChange[])` — shift rows need
  `FEATURES.adpScheduleWrite`, unavailability rows need `FEATURES.adpUnavailabilityWrite`, refused in
  preview (`isConsolePreview()`), refuses while a schedule write runs; `adpChangesStatusAction(batchId)`.
  Registry entries in `lib/actions/registry.ts` + `MUTATING_ACTIONS.md`. Old `assignOpenShiftsAction` kept
  for one release (unused by UI), removed in follow-up.
- `lib/bhaga/recompute.ts:251` `triggerAdpScheduleWrite` gains `{ mode: "apply"; batchId }` →
  env `BHAGA_SCHEDULE_BATCH_ID`.

### Migration `core/migrations/094_adp_shift_changes.sql`
```sql
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_shift_changes` (
  store STRING NOT NULL, batch_id STRING NOT NULL, change_id STRING NOT NULL,
  kind STRING NOT NULL,          -- open-add|open-assign|open-edit|open-delete|shift-add|shift-edit|shift-delete
  date DATE NOT NULL, employee STRING, start_min INT64 NOT NULL, end_min INT64 NOT NULL,
  from_date DATE, from_start_min INT64, from_end_min INT64, count INT64,
  summary STRING, status STRING NOT NULL,   -- queued|applied|skipped|failed
  error STRING, adp_name STRING, requested_by STRING NOT NULL,
  requested_at TIMESTAMP NOT NULL, updated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_shift_events` (   -- append-only
  store STRING NOT NULL, event_at_utc TIMESTAMP NOT NULL, kind STRING NOT NULL,
  date DATE NOT NULL, employee STRING, summary STRING NOT NULL, actor STRING,
  batch_id STRING, change_id STRING);
```
`adp_unavailability_changes` (092) gets `batch_id` = the same Apply id. `adp_shift_events` added to
`APPEND_ONLY` in `scripts/check_append_only_history.py`.

### ADP backend (`skills/adp_run_automation/schedule_write_backend.py`)
Live spike first (M1) records each pane's text/buttons into the module docstring. New functions, each with
`dry_run` that walks to the final button and backs out:
```python
def open_shift_details(frame, page, *, date_iso, start_min, end_min) -> None   # rename of _open_shift_details
def edit_open_shift(frame, page, *, date_iso, start_min, end_min, new_start, new_end, dry_run=False) -> None
def delete_open_shift(frame, page, *, date_iso, start_min, end_min, dry_run=False) -> None
def open_shift_publish(frame, page, ...) -> None   # the created pane's own Publish (never toolbar "Publish drafts")
def edit_shift(frame, page, *, date_iso, employee, start_min, end_min, new_start, new_end, dry_run=False) -> None
def delete_any_shift(frame, page, *, date_iso, employee, start_min, end_min, dry_run=False) -> None  # draft or published
# unavailability (Issue #427 tail): grid "+" › Create unavailability; entry pane Edit/Delete; request Reject
def add_unavailability(frame, page, *, employee, first_date, all_day, from_min, to_min, weekly, until, dry_run=False) -> str
def edit_unavailability(...); def delete_unavailability(...); def deny_request(...)
```
`FIND_SHIFT_JS` gains a `draft_only` arg (published shifts have no "Draft" tag). Confirm by read-back:
open-shift counts (`read_open_shifts`) for open kinds, the person's day cell text for assigned kinds,
`unavailability` re-scrape for unavailability. Never "Delete all schedules", never toolbar "Publish drafts".
Manual creates/edits are published in place (created-pane / details-pane Publish) so staff see them;
if the spike shows editing a published shift leaves a draft, the job publishes that one shift from its
pane, never the week.

### Job (`agents/bhaga/scripts/adp_schedule_write.py`)
`run_apply(store, batch_id, *, headless, dry_run) -> int`: one `adp_session`; shift rows by week in order
deletes → edits → adds → assigns (frees a slot before a reassign's add); then unavailability rows per person
(`deny`, `approve` reuse `unavailability_approve_backend`, then add/edit/delete). Each row: idempotency
guard (open-add skipped when ADP already shows the requested count more than before; delete skipped when
ADP no longer shows it → `skipped`), click, read-back → `applied` + `adp_shift_events` /
`adp_unavailability_events` row, else `failed` with `BREADCRUMB adp_apply batch_id=… change_id=… error=…`.
Never retried. Crash marks unsettled rows `failed`. Finally `refresh_schedule` over changed weeks (+ the
unavailability scrape). CLI `--apply-batch <id>`; `daily_refresh.py:2861` dispatch gains
`elif write_mode == "apply": argv += ["--apply-batch", os.environ.get("BHAGA_SCHEDULE_BATCH_ID", "")]`.

## Feature-flag decision
Can it silently produce wrong numbers? No numbers change, but it writes to ADP. Shift kinds ride the existing
`CONSOLE_ADP_SCHEDULE_WRITE` (on in prod; every write is an explicit Apply with a confirm drawer).
Unavailability Apply stays behind `CONSOLE_ADP_UNAVAIL_WRITE` (off) until its live proof passes, then the
agent turns it on with `/prod-ops console-env set CONSOLE_ADP_UNAVAIL_WRITE=1`. `docs/FEATURE_FLAGS.md` rows updated.

## Invariants preserved
- Read-only toward ADP payroll; only Team Schedule writes, each operator-applied, never on the nightly,
  never auto-retried (bhaga.mdc invariant 6 text extended).
- Idempotent: change rows keyed by `change_id`; a re-run of a batch only touches `queued` rows; read-back
  guards stop double creates/deletes.
- America/Chicago for "today/future" (`todayIso` from page props; job `_today_ct()`).
- Append-only history (`adp_shift_events`, `adp_unavailability_events`).
- Preview read-only (`CONSOLE_PREVIEW` middleware 403 + explicit preview block); no money math.

Evidence tier: sandbox-e2e — console branch preview on prod data (read-only, iPhone 14 WebKit through IAP)
for every UI state, plus live prod-ADP runs for each write kind (ADP has no sandbox company; J2).

## Milestone 1 — ADP spike + backend functions (Opus)
Live session from this VM (needs the ADP login in vault `Shared with Bots`, OTP via `operator_ping.py`):
read-only walk of an open shift, a published assigned shift, an unavailability entry and a request; one
throwaway open shift on a far-future day: create+publish → edit → delete. Then implement the functions above.
Tests `skills/adp_run_automation/test_schedule_write_backend.py`: each function's dry run backs out; wrong
pane text raises; delete never matches "Delete all schedules"; FIND_SHIFT_JS published match.
```bash
python3 -m pytest skills/adp_run_automation/test_schedule_write_backend.py -q
python3 -m agents.bhaga.scripts.adp_schedule_write --store palmetto --apply-batch <id> --dry-run
```
Pass: green; dry run reaches each final button and backs out.

## Milestone 2 — migration 094 + apply job (Opus)
`094_adp_shift_changes.sql`, `run_apply`, `daily_refresh` mode, append-only registration. Tests
`agents/bhaga/scripts/test_adp_schedule_write.py`: only queued rows; order deletes→edits→adds→assigns;
skipped when ADP already matches; unconfirmed → failed; crash marks unsettled failed; events written only on
applied; refresh over changed weeks; dry run leaves BQ.
```bash
python3 -m pytest agents/bhaga/scripts/test_adp_schedule_write.py scripts/test_check_append_only_history.py -q
```
Pass: green.

## Milestone 3 — console queue, tray, drawer, open-shift list, suggestions switch (Opus)
Files above + `__tests__/adp-changes.test.ts` (keys, stage/unstage, reassign pair, errors, tray order,
exhaustive kinds) and `__tests__/adp-changes-tray.test.tsx` (one tray for both areas; apply blocked per
area flag/preview; legacy assign picks migrate; suggestions switch persists and hides the listed surfaces).
```bash
cd apps/operator-console && npx tsc --noEmit && npx vitest run && npm run lint
```
Pass: green.

## Milestone 4 — evidence + docs (Opus)
Preview screenshots (iPhone + desktop) via `scripts/console_preview.py`; live prod-ADP Apply runs per §4;
`python3 scripts/verify.py --full`; `python3 scripts/check_doc_freshness.py --base origin/main`.
Pass: all green; operator OK on the demo before marking ready.

## Docs lock-step
`RUNBOOK.md` Labor scheduling paragraphs (Changes to ADP tray, `BHAGA_ADP_SCHEDULE_WRITE=apply`);
`agents/bhaga/scripts/README.md` adp_schedule_write row; `.cursor/rules/bhaga.mdc` invariant 6;
`apps/operator-console/lib/actions/MUTATING_ACTIONS.md`; `docs/FEATURE_FLAGS.md`;
`docs/operator-console/ARCHITECTURE.md` scheduling bullet; `skills/adp_run_automation/README.md`.
`PROGRESS.md` via the retro follow-up only. Run `check_doc_freshness.py`.

## As built (amendments)
- **Model:** `ShiftChange` carries `startMin/endMin` = the shift as ADP has it now plus `to: Times` for edits,
  and a separate `shift-reassign { toEmployee }` kind (one tray line, one row; the job deletes then adds).
  Open-shift kinds carry the ADP `slot`. No `summary` in the client type (`shiftText` builds it server-side).
- **Migration 094 columns:** `to_start_min`, `to_end_min`, `to_employee` (no `from_*`); `adp_shift_events`
  has no `employee` column (the summary names the people).
- **D5:** no lane ring/dim — a staged change shows where it was made (open-shift row badge "Will delete" /
  "Will change to …" / "Will add N", drawer shift badge) and in the tray; lanes stay ADP-only.
- **Old assign UI removed** (`OpenShiftAssignBar`, `assignOpenShiftsAction`, `open-shift-assign*.ts`);
  the CLI `--queue-assign` / job mode `assign` path stays.
- **Backend:** open-add creates one open shift per count (the verified single-shift wizard) and presses its
  own wizard **Publish**; `FIND_SHIFT_JS` takes `state` = draft | published | any. Unavailability in the job:
  approve only (existing backend); deny/add/edit/delete fail without a click until proven live.
- **S0 sandbox demo** (operator ask before prod ADP): PR preview `operator-console-pr436` on prod data,
  desktop + iPhone 14 WebKit — Suggestions off/on, Add open shift (2 days × 2), staged add in the day's list,
  person drawer → shift → Reassign, tray Review with Apply blocked "Read-only preview".
- **Round 3 — iPhone review of the preview** (operator: chart taps opening a sheet killed the who's-on line,
  "a regression which keeps on happening"; names and drawer rows didn't look tappable; the Edit times /
  Reassign menu was an unnecessary extra screen). The round-2 bar/band taps are reverted, so the chart is
  read-only again and every tap shows the who's-on line. Names on the chart get a dotted underline + `›`;
  person-drawer rows are bordered cells (≥44px) with `›`; the day's open-shift list has an outlined
  **Change ›**. The shift, open-shift and approved-unavailability sheets open straight on their fields
  (Starts / Ends / Person or Assign to; days and hours) with **Delete** and **Save to changes**, and Save
  stays disabled until something changes. A new person plus new times stages delete + `shift-add`, because
  ADP can't retime and hand over a shift in one step. The switch reads "Show suggested shifts". These
  became `ui-polish.md` D3 (charts are for reading), D11 (what opens looks like it) and D12 (one screen per
  change), guarded by `__tests__/ux-principles.test.tsx`.
- **Out of scope:** unavailability deny/add/edit/delete ADP backends (follow-up once a live ADP session is
  available); editing past shifts; reassigning an open shift that someone already took.

## PR mechanics
One branch / one PR; pushes as `jarvis-agent-bot328` (`cloud-agent-pr.yml` opens the draft), title/body via
`ManagePullRequest update_pr`, `Closes #434`, `Cursor-Agent:` line; batch-fix babysit loop with
`pr_triage.py`; reply to every comment; never self-merge. Model routing: Opus throughout (one chat).
