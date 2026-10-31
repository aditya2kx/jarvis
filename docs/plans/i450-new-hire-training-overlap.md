# Issue #450 — new hires only work beside experienced staff until trained

Branch `cursor/450-labor-shift-scheduling-for-new-hires-joined-in-past-3-months-for-backfill-e-d45a`
· jam 2026-10-30 (Cloud Agent chat). Consulted: `CONTRIBUTING.md` (dev loop, §4 evidence),
`.cursor/rules/bhaga-principles.mdc` (read-only ADP, BQ source of truth, breadcrumbs),
`docs/contributing/ui-polish.md` (D1–D10), `docs/plans/i412-draft-shift-options.md` (picker / pins),
`plan-execution-readiness.mdc`.

## Jam outcome (operator, 2026-10-30)

- **New hire** = ADP hire date on or after `TRAINING_FROM` (2026-08-01 — about three months back at launch, then every hire; operator follow-up: no window setting). Hire date comes from ADP; a
  backfill run is fine.
- **One-time requirement**: before a new hire may work with non-experienced staff or solo they must
  complete *O* opening + *C* closing shifts (default 2 + 2; operator may use 3 + 3), each overlapping an
  experienced person by at least *M* minutes (default 180). Counted across weeks, not per period.
- **Hard rule**: the draft never gives a new hire an unsupervised shift; when no pairing fits, the
  shift goes to someone else or stays an open shift.
- **Tags**: wherever people are picked (draft picker, ADP open-shift picker) and on the coverage
  lanes, show *New hire · 1/2 openers · 0/2 closers* or *Experienced*, and why a new hire was not
  drafted, so a manual ADP edit is informed.
- Experienced staff: the operator's list (two senior staff, both assistant managers, one more) — picked in the console, not seeded in git.

## Design

### Data — ADP hire date (Python, read-only toward ADP)

Migration `core/migrations/097_adp_hire_dates.sql`:

```sql
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_hire_dates` (
  store          STRING    NOT NULL,
  employee_name  STRING    NOT NULL,  -- ADP Directory / profile name (alias-resolved on read)
  hire_date      DATE      NOT NULL,
  source         STRING    NOT NULL,  -- directory | profile
  scraped_at_utc TIMESTAMP NOT NULL
);
```

MERGE key `(store, employee_name)` — one current hire date per ADP record (idempotent).

`skills/adp_run_automation/pay_info_backend.py`:
- `parse_hire_date(text: str) -> Optional[str]` — `Hire date` / `Hired` / `Date of hire` followed by
  `MM/DD/YYYY` → ISO; pure, unit-tested.
- `read_profile_hire_date(page) -> Optional[str]` — body text of the open profile; when absent,
  clicks (read-only) the profile's non-payroll section tabs (`[id^="EMPLOYEE_"]`) and retries.
  Breadcrumb `[pay_info] BREADCRUMB hire_date_missing name=… tabs=[…] excerpt=…` on miss.
- `scrape_one_pay_info` (line ~624) reads the hire date on the profile it already opens → `hire_date`
  on the rate record; `write_hire_dates_bq(rows)` MERGEs into `adp_hire_dates`.
- `backfill_from_downloads.py` (line ~830) loads `hire_dates` from the PayInfoRates JSON next to
  `adp_directory_status`.
- Backfill: `python3 -m skills.adp_run_automation.pay_info_backend --hire-dates-active --write-bq`
  reads every Active Directory person's profile once; wired in `daily_refresh.py` as
  `BHAGA_ADP_HIRE_DATES=1` so it runs via `/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_HIRE_DATES=1`.

### Console — rules, progress, draft

`lib/labor/schedule-inputs.ts`: `StaffingBasics.training: TrainingRules`

```ts
export type TrainingRules = {
  openShifts: number;       // 0–10
  closeShifts: number;      // 0–10
  overlapMin: number;       // 60–600, 15-min steps
  experienced: string[];    // canonical names
};
```

Versions saved before #450 load `DEFAULT_TRAINING` (2 + 2, 3 h, no experienced staff — names are picked in the console, never in git; gate off until saved) — `parseStaffing`.

New `lib/labor/new-hire-training.ts` (pure):

```ts
export function shiftKindOf(startMin: number, endMin: number): ShiftKind;
export function overlapMinutes(s: number, e: number, spans: readonly [number, number][]): number;
export type TrainingStatus = { employee: string; hireDate: string | null; firstShift: string | null;
  open: number; close: number; needOpen: number; needClose: number; cleared: boolean;
  unsupervised: string[] };
export function trainingProgress(args: { todayIso: string; rules: TrainingRules;
  hireDates: ReadonlyMap<string, string>; days: ReadonlyMap<string, CoveragePersonDay[]> }): Map<string, TrainingStatus>;
```

`lib/labor/shift-draft.ts`:
- `Availability.training?: { open: number; close: number }` (remaining supervised shifts) and
  `experienced?: boolean`.
- `DraftDayArgs.trainingOverlapMin?`, `experiencedOn?: [number, number][]`, `trainingLeft?: Map`
  (mutated like `weekShifts`, copied in lookahead rollouts).
- In `candidates()` (lines 376–447): a trainee is eligible only for an open/close block they still
  need, overlapping experienced spans (ADP lanes + this day's drafted experienced shifts) by
  ≥ overlap. Trainees blocked only by this land in `DraftShift.blocked: { employee, reason }[]`.
- `fillOpenShift` (line 522) applies the same gate. Draft output with no trainees is unchanged.

`components/labor/LaborCoveragePanel.tsx` `buildDrafts` (line 1276): roster gets `training` /
`experienced`; `trainingLeft` is carried across weeks in date order; `experiencedOn` per day from lanes.

UI (design-system `Badge`, `Select`, `NumberField`, muted text; tap targets `h-9 sm:h-7`):
- `ShiftPersonPicker` options + `OpenShiftAssign` picker: *New hire · 1/2 open · 0/2 close* (amber
  outline badge) / *Experienced* (secondary badge); blocked trainees listed disabled with the reason.
- `ShiftDraftSummary`: unassigned row explains *New hire X can't take it — no experienced person on
  for 3h*.
- Coverage lanes: same tag beside the name; amber "no experienced overlap" on a trainee's ADP shift.
- `ScheduleInputsPanel`: new **New-hire training** section — window, openers, closers, overlap,
  experienced chips + add select, and a progress list (hired date, x/y openers, x/y closers, cleared).

Data path: `app/labor/page.tsx` adds `adpHireDates(store)` and `laborActualShiftDays(since TRAINING_HISTORY_FROM)`
(`lib/bq/queries.ts`).

### Invariants

Read-only toward ADP (profile tab clicks only; no Save); BQ single source of truth; idempotent
MERGE on `(store, employee_name)`; America/Chicago dates (`chicagoTodayIso`); rules history stays
append-only (`labor_schedule_rules` versions); no hardcoded store ids; breadcrumbs on every scrape miss.

### Feature flag

No flag: the gate only removes candidates (can only leave a shift open, never invent hours), drafts
are suggestions until the operator's confirmed Save to ADP, and an empty experienced list turns it off at
runtime through the rules (preference 29). Silent-wrong-number test: no money or hours path changes.

## Milestones

### M1 — hire-date scrape + migration (Opus)

`pay_info_backend.py`, `backfill_from_downloads.py`, `daily_refresh.py`, migration 097, tests in
`skills/adp_run_automation/test_pay_info_backend.py` (parse formats, missing date, MERGE rows,
backfill mode wiring).

```bash
python3 -m pytest skills/adp_run_automation/test_pay_info_backend.py agents/bhaga/scripts/test_daily_refresh.py -q
```
Pass: green. Live: `/prod-ops apply-migrations` then `/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_HIRE_DATES=1`,
read back via `/prod-read bq` (`SELECT employee_name, hire_date, source FROM bhaga.adp_hire_dates`).

### M2 — engine + progress (Opus)

`new-hire-training.ts`, `shift-draft.ts`, `schedule-inputs.ts`; tests `__tests__/new-hire-training.test.ts`,
`__tests__/shift-draft.test.ts`, `__tests__/schedule-inputs.test.ts`.
Scenarios: happy (trainee paired on 2nd opener / 2nd closer, progress counts), failure (no experienced
→ open shift, blocked reason), recovery (cleared trainee drafted normally), legacy (no trainees → output
identical; old rules versions load defaults).

```bash
cd apps/operator-console && npx vitest run __tests__/new-hire-training.test.ts __tests__/shift-draft.test.ts __tests__/schedule-inputs.test.ts
```
Pass: green, existing cases unchanged.

### M3 — console wiring + UI + evidence (Opus)

`page.tsx`, `queries.ts`, `LaborCoveragePanel.tsx`, `ScheduleInputsPanel.tsx`, `ShiftPersonPicker.tsx`,
`ShiftDraftSummary.tsx`, `OpenShiftAssign.tsx`; component test `__tests__/shift-person-picker.test.tsx`.

```bash
cd apps/operator-console && npx tsc --noEmit && npx vitest run && npm run lint
python3 scripts/verify.py --full
python3 scripts/check_doc_freshness.py --base origin/main
```
Pass: green; PR preview screenshots (desktop + 390 px) of the rules section, picker tags and an
unassigned shift's reason; operator OK on the demo.

Evidence tier: unit-only — waiver: ADP has no sandbox company and the console change is draft-only
(no pipeline/model path for `sandbox_e2e.py` to exercise); live proof is the prod-ADP hire-date backfill
run (read-only) plus the PR preview on prod data.

## Docs lock-step

`RUNBOOK.md` draft-shift paragraph (new-hire training gate + backfill command), `agents/bhaga/scripts/README.md`
(hire-date scrape / `BHAGA_ADP_HIRE_DATES`), `agents/bhaga/knowledge-base/DOMAIN.md` (adp_hire_dates),
`docs/operator-console/ARCHITECTURE.md` scheduling bullet. `PROGRESS.md` dated entry in this PR; `agents/bhaga/scripts/status.py` note (not a freshness target).
Run `python3 scripts/check_doc_freshness.py`.

## PR mechanics

One branch / one PR; pushes as `jarvis-agent-bot328` (`cloud-agent-pr.yml` opens the draft), body set
with `ManagePullRequest update_pr`, `Closes #450`, `Cursor-Agent: bc-242fe175-737d-441e-8b49-e90c2215d5e1
model=claude-opus-5-5-medium`; push with `git push -u origin <branch>` (`--no-verify` only if a hook
blocks on a Cloud Agent surface); reply to every review comment; never self-merge. Model routing: Opus
throughout (one chat per PR, cost playbook).
