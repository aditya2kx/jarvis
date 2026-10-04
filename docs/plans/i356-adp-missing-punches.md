# Issue #356 — ADP missing punches: detect → suggest → operator decides → write back

Branch `fix/operator-console-labor-weekly-hours-don` · tracking issue #356 (rescoped in jam
2026-10-03 from the /labor weekly-hours display mismatch; that display fix is parked as a
follow-up task).

Consulted: `CONTRIBUTING.md` (dev loop + §4 evidence), `.cursor/rules/bhaga.mdc` (invariants 3, 6,
7, 10; ADP session + OTP rules), `.cursor/rules/bhaga-principles.mdc`, `RUNBOOK.md` § ADP local
attach, `apps/operator-console/lib/actions/MUTATING_ACTIONS.md`, `docs/contributing/ui-polish.md`.

## Problem (diagnosed in jam, read-only)

ADP's Timecard **Export To Excel omits open punches** (clock-in with no clock-out) —
`skills/adp_run_automation/selectors/timecards.json:137`, and our parser also skips rows without
`End Work` (`skills/adp_run_automation/shift_backend.py:343-347`). The Timecards UI
(Time → Timecards, iframe `timePartnerFrame`) shows them as
`In Time 09:00 AM · Out Time Blank · 0:00 HRS · Missing Out Punch` and flags the day
"There are errors for this day". Pay period 2026-09-21 → 2026-10-04 has **15 gap person-days**:

| kind | person-days |
|---|---|
| `missing_out` (only entry) | Emp A 9/21, 9/23, 9/24, 9/30; Emp B 9/22 |
| `missing_out_after_break` (2nd entry open) | Emp C 9/25, Emp D 9/30, Emp D 9/22 |
| `in_progress` (open, day not over / no error yet) | Emp B 10/1, Emp A 10/2 |
| `no_entry` (scheduled, nothing punched) | Emp E 9/28, Emp F 9/28, Emp G 10/2, Emp H 10/2, Emp E 10/2 |

Operator precedent (punch note on Emp E 9/18): "Updated missing close out as per scheduled shift
timings" → the starter suggestion rule.

## Scope

In scope: (1) scrape the Timecards UI for gap days; (2) BQ tables for gaps + decisions;
(3) `/labor` "Punch gaps" review table with context and Accept / Edit / Reject;
(4) flagged write-back of accepted/edited out-punches into ADP, read-back verify, re-sync.
Out of scope: the weekly-hours display reconciliation (follow-up issue), auto-apply without an
operator click, editing in-times of existing complete punches, payroll Submit (still never).

Operator gate added at define-evidence: **build and review on localhost first; no PR, push or
babysit watcher until the operator is aligned on the localhost portal.**

## Invariants preserved

- **Idempotent upserts** (bhaga.mdc #3): `adp_timecard_gaps` is purged per scraped pay period then
  MERGEd on `(store, date, employee_id)`; re-running converges. `punch_gap_decisions` MERGE on
  `decision_id`. `adp_shifts` / `adp_punches` are untouched by M1–M2 (must not break existing hours).
- **Never fewer hours than punched**: a suggestion only ever closes an open entry; it never edits a
  completed entry. Suggested out ≥ the open entry's in-time + 1 min.
- **Pay at least the scheduled hours** (operator, 2026-10-03): suggested out = the later of the
  entry's scheduled end (capped at `shop_hours.close_local_time`, `store-profiles/palmetto.json:15`)
  and clock-in + the day's scheduled hours still owed (scheduled minutes up to shop close − completed
  entries). A late clock-in may push it past the scheduled end or close — rule `pay_scheduled_hours`.
- **No invented hours**: `no_entry` rows get no suggestion; only an explicit operator Edit (in+out)
  can create hours.
- **America/Chicago** for every date and "day is over" decision (`in_progress` until the day's
  last scheduled end + 60 min has passed in CT).
- **ADP write safety** (bhaga.mdc #6 still holds — never Approve/Submit payroll): the only write is
  filling an open entry's Out Time (+ note) for a decision the operator clicked. Flag
  `PUNCH_FIX_WRITEBACK` default off. **Never retry** a Save automatically (side effect); a failed
  apply leaves a greppable breadcrumb `[punch-fix] FAIL date=… emp=… step=… evidence=…` and status
  `failed`; Retry is a manual button. Before writing, re-read the day: if ADP no longer shows the
  open entry → `already_resolved`, no write (no duplicate edits).
- Alias normalization (bhaga.mdc #10): Timecards UI shows "Emp E, Name J"; every employee name
  routes through `employee_aliases.derive_canonical` before keys/joins.
- Read-only toward ADP for everything except the flagged apply step; scraping opens detail
  dialogs and closes them with **Back**, never Save.

## Feature-flag decision

Write-back can silently produce wrong payroll hours → **flagged**. `FEATURE_FLAGS.md` entry
`PUNCH_FIX_WRITEBACK` (env `BHAGA_PUNCH_FIX_WRITEBACK=1` on the apply job; console
`FEATURES.punchFixWriteback`). Flag off: Accept/Edit/Reject still record decisions, status
`pending_write`. Detection + review table are additive read paths — no flag (no existing behavior
changes; backward-compatible).

## Data model — migration `core/migrations/084_adp_punch_gaps.sql`

```sql
-- 084: ADP missing punches (Issue #356). Timecard export omits open punches; these come from
-- the Timecards UI (runner._timecard_gaps_within_session). Purged per pay_period_start.
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_timecard_gaps` (
  store                  STRING NOT NULL,
  date                   DATE NOT NULL,
  employee_id            STRING NOT NULL,   -- canonical (employee_aliases)
  raw_employee_name      STRING,
  pay_period_start       DATE NOT NULL,
  kind                   STRING NOT NULL,   -- missing_out | missing_out_after_break | in_progress | no_entry
  entries_json           STRING,            -- [{"in":"09:00","out":null,"hours":0.0}, ...] shop-local HH:MM
  scheduled_ranges_json  STRING,            -- ["09:00-15:00", ...] HH:MM 24h
  open_entry_index       INT64,             -- index in entries_json of the open entry (NULL for no_entry)
  suggested_out          STRING,            -- HH:MM or NULL
  suggested_hours        FLOAT64,           -- hours added if suggestion accepted
  rule                   STRING,            -- e.g. 'close_at_scheduled_end'
  adp_error_flag         BOOL,
  scraped_at_utc         TIMESTAMP,
  materialized_at_utc    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.punch_gap_decisions` (
  decision_id      STRING NOT NULL,         -- uuid
  store            STRING NOT NULL,
  date             DATE NOT NULL,
  employee_id      STRING NOT NULL,
  action           STRING NOT NULL,         -- accept | edit | reject
  in_time          STRING,                  -- HH:MM (edit on no_entry only)
  out_time         STRING,                  -- HH:MM
  open_entry_index INT64,
  note             STRING,                  -- ADP audit note text
  status           STRING NOT NULL,         -- recorded | pending_write | applying | applied | failed | already_resolved | rejected
  error            STRING,
  decided_by       STRING,
  decided_at       TIMESTAMP,
  applied_at       TIMESTAMP,
  execution_name   STRING
);
```

Apply: `BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"`.

## Milestone 1 — Detect (scrape + classify + load) · Sonnet

1. **Pure classifier** `skills/bhaga_labor/punch_gaps.py` (new; pure like `solo_shift.py` — no IO):

   ```python
   @dataclass(frozen=True)
   class TimecardEntry:
       in_time: str | None   # "HH:MM"
       out_time: str | None
       hours: float

   @dataclass(frozen=True)
   class PunchGap:
       kind: str             # missing_out | missing_out_after_break | in_progress | no_entry
       open_entry_index: int | None
       suggested_out: str | None
       suggested_hours: float | None
       rule: str | None

   def classify_day(*, date: datetime.date, entries: list[TimecardEntry],
                    scheduled_ranges: list[tuple[str, str]], shop_close: str,
                    now_ct: datetime.datetime, adp_error_flag: bool) -> PunchGap | None: ...
   def parse_ampm(text: str) -> str: ...   # "09:00 AM" -> "09:00"
   ```
   Rules: no entries + scheduled → `no_entry`; an entry with in and no out → day over? (now_ct >
   last scheduled end + 60 min, or date < today CT) else `in_progress`; open entry index 0 →
   `missing_out`, index > 0 → `missing_out_after_break`; suggested_out = min(end of the scheduled
   range containing/after the open in-time, shop_close); if no scheduled range or suggested ≤ in →
   no suggestion (`rule=None`). Complete days → `None`.

2. **Timecards UI parser** `skills/adp_run_automation/timecard_ui_backend.py` (new, pure):
   `parse_month_cells(aria_snapshot: str) -> list[DayCell]` (date label, entry count, total
   "H:MM", `has_error`, scheduled ranges) and `parse_day_dialog(aria_snapshot: str) -> list[TimecardEntry]`
   (`In Time 09:00 AM Out Time Blank Total Hours 0:00HRS Missing Out Punch`). Fixtures from the
   live 2026-10-03 snapshots saved under `skills/adp_run_automation/testdata/timecards_ui/`
   (names replaced with fixture names — no PII in git).

3. **Scrape** `skills/adp_run_automation/runner.py` — new `_timecard_gaps_within_session(page, *,
   store, pay_periods=("current", "previous")) -> dict` after `_schedule_within_session`
   (runner.py:1967). Opens Time → Timecards (link "Timecards"; frame `timePartnerFrame`), iterates
   employees with the next arrow (`sdf-icon-button` nth(1), loop until a name repeats), for each
   day cell with `has_error` or `0:00` total **with** an entry opens "Show details for …", parses
   the dialog, clicks **Back** (never Save). Writes `TimecardGaps-<today>.json` next to the
   schedule JSON. Wired into `download_adp_bundle` (runner.py:2238) as `include_timecard_gaps=True`
   default, inside the same session, soft-fail (`result["errors"]["adp_timecard_gaps"]`, never
   fails Timecard/tips). Selectors recorded in `skills/adp_run_automation/selectors/timecards.json`
   under `timecards_ui`.

4. **Loader** `agents/bhaga/scripts/backfill_from_downloads.py` — `_load_adp_timecard_gaps`
   (pattern of `_load_adp_open_shifts`, line 472): purge `adp_timecard_gaps` for each scraped
   `pay_period_start`, then `load_rows(..., merge_keys=["store","date","employee_id"])`. `no_entry`
   rows are derived in the loader from `adp_scheduled_shifts` days in the scraped periods with no
   `adp_punches` row and no UI entry (UI also lists "Has a schedule" with no entry — cross-checked).

**Verify (M1):**
```bash
python3 -m pytest skills/bhaga_labor/test_punch_gaps.py skills/adp_run_automation/test_timecard_ui_backend.py agents/bhaga/scripts/test_backfill_timecard_gaps.py -q
# live, laptop, attached Chrome (one OTP at most — announce before):
BHAGA_ADP_CDP_URL=http://127.0.0.1:9333 BHAGA_ADP_TIMECARD_ONLY=1 BHAGA_DATASTORE=bigquery \
  python3 -m agents.bhaga.scripts.daily_refresh --store palmetto --date 2026-10-02 --headless
bq query --use_legacy_sql=false 'SELECT kind, COUNT(*) FROM bhaga.adp_timecard_gaps WHERE pay_period_start="2026-09-21" GROUP BY 1'
```
Pass criterion: unit tests green (happy path + each kind + complete-day None + no-schedule no
suggestion + shop-close cap + in_progress cutoff); live table holds the 15 person-days above
(counts by kind 5/3/2/5 ± anything newly fixed in ADP), `adp_shifts`/`adp_punches` row counts
unchanged before/after.

## Milestone 2 — Console review table (decisions recorded, no ADP write) · Sonnet

1. **Query** `apps/operator-console/lib/bq/queries.ts` (after `laborOpenShiftDays`, line 772):
   `laborPunchGaps(win: DateWindow, store: string): Promise<LaborPunchGapRow[]>` — gaps in window
   LEFT JOIN latest decision per `(date, employee_id)`; context columns: coworkers clocked in
   during the gap window (`adp_punches` same date, other employees, overlapping), Square net sales
   + order count in the gap window (`square_transactions` `COALESCE(ops_date_local, date_local)`,
   ops time between open-in and suggested-out).
2. **Writes** `apps/operator-console/lib/bq/writes.ts`: `recordPunchGapDecision(row)` MERGE on
   `decision_id` (pattern of `addRecognitionBonus`, writes.ts:500).
3. **Actions** `apps/operator-console/app/labor/actions.ts`: `decidePunchGapAction({date, employee,
   action, inTime?, outTime?})` → validates HH:MM, out > in, out ≤ shop close + 3h guard; records
   decision with status `pending_write` (flag off) or kicks apply (M3). Register in
   `lib/actions/registry.ts` + `MUTATING_ACTIONS.md`.
4. **UI** `apps/operator-console/components/labor/PunchGapsPanel.tsx` (new) rendered on
   `app/labor/page.tsx` above "Hours per person". Reuses `DataTable`
   (`components/tables/DataTable.tsx`), `Badge`, `Button`, `Input`, `Tooltip`, muted-foreground
   text tokens. Columns: Date, Employee, Kind (Badge: amber "Missing clock-out", violet "After
   break", slate "In progress", rose "No punch"), Punches (e.g. `06:28–11:10 · 11:30–?`),
   Scheduled, Suggestion (`→ 1:30 PM · +2.0 h`), Context (`2 others on floor · $184 / 12 orders
   in gap`), Status chip, Actions (Accept · Edit · Reject). Row expand → compact timeline
   (`PunchGapTimeline.tsx`): schedule band (slate), punched segments (solid), break gap (hatched),
   suggested close (dashed amber), coworkers as thin lanes — same colors as `LaborCoveragePanel`.
   Edit = inline time input (h-7, `inputMode="numeric"`, Enter/Escape like
   `LaborWeeklyHoursGoal.tsx:70-73`). States: hover/focus rings, pending spinner via
   `useConsoleAction`, disabled while applying, mobile: actions collapse into a row menu ≥ 44px
   tap targets. Empty state: "No punch gaps in this Period."
5. Page note copy updated (`app/labor/page.tsx:615-672`) with one sentence on Punch gaps.

**Verify (M2):**
```bash
cd apps/operator-console && npx vitest run __tests__/punch-gaps.test.ts && npx tsc --noEmit && npm run lint
python3 scripts/check_operator_console_actions.py
cd apps/operator-console && BYPASS_IAP_EMAIL=adi@mypalmetto.co npm run dev   # localhost:3000/labor?range=custom&from=2026-09-21&to=2026-10-04
```
Pass criterion: vitest covers row formatting, kind badges, decision validation (out ≤ in rejected,
bad HH:MM rejected, no_entry requires in+out); localhost shows the 15 rows with context; Accept /
Edit / Reject each write a `punch_gap_decisions` row (BQ check) with `pending_write`/`rejected`.
**Operator localhost review checkpoint #1** (UX feedback before M3).

## Milestone 3 — Write-back to ADP (flagged) · Opus

1. **Backend** `skills/adp_run_automation/timecard_fix_backend.py` (new):
   ```python
   @dataclass
   class ApplyResult:
       status: str      # applied | already_resolved | failed
       error: str | None
       evidence_path: str | None

   def apply_out_punch(page, *, employee_display: str, date: datetime.date,
                       open_entry_index: int, out_time: str, note: str) -> ApplyResult: ...
   def add_entry(page, *, employee_display: str, date: datetime.date,
                 in_time: str, out_time: str, note: str) -> ApplyResult: ...   # no_entry edit
   ```
   Flow: open Timecards → pick employee via name dropdown → pay period containing date → "Show
   details for <day>" → re-parse dialog (`parse_day_dialog`); if entry `open_entry_index` no
   longer open → `already_resolved` (Back, no write); else fill Out Time, add note
   ("BHAGA: closed per schedule — approved by <decided_by> <decided_at CT>"), **Save once**,
   re-open and re-parse; `applied` only if the out time now matches; otherwise `failed` + screenshot
   evidence. No automatic retry.
2. **Job entry** `agents/bhaga/scripts/daily_refresh.py` — new `BHAGA_PUNCH_FIX_APPLY_ONLY` branch
   next to `BHAGA_ADP_TIMECARD_ONLY` (line 2765): requires `BHAGA_PUNCH_FIX_WRITEBACK=1`; reads
   decisions with status `applying` for `BHAGA_PUNCH_FIX_DECISION_IDS`; one `adp_session`; applies
   each; MERGEs status; then in the same session runs the Timecard export + gaps scrape and the
   `backfill_from_downloads` load (same as timecard-only) so `adp_punches` and the console catch up.
3. **Console** `apps/operator-console/lib/bhaga/punch-fix.ts` (pattern of `payroll-draft.ts`:
   local spawn when `BYPASS_IAP_EMAIL`, else `runJob` in `recompute.ts` with env
   `BHAGA_PUNCH_FIX_APPLY_ONLY=1`, `BHAGA_PUNCH_FIX_WRITEBACK=1`, `BHAGA_PUNCH_FIX_DECISION_IDS`).
   Actions `applyPunchGapsAction(decisionIds)` / `pollPunchGapApplyAction`. Panel: "Write N to ADP"
   button when flag on; status chips Applying… / Applied ✓ / Failed (Retry) / Already fixed.

**Verify (M3):**
```bash
python3 -m pytest skills/adp_run_automation/test_timecard_fix_backend.py agents/bhaga/scripts/test_daily_refresh.py -q -k "punch_fix"
```
Pass criterion (mocked page): happy path writes once + verifies; already-resolved writes nothing;
Save failure → `failed`, breadcrumb, zero retries; flag off → refuses to run; edit-on-no_entry adds
one entry. **Live**: operator picks one real gap on localhost (e.g. Emp A 9/21), Accept → ADP shows
Out Time + note, next re-sync puts the punch in `adp_punches`, row → Applied ✓, `/labor` hours up
by the suggested hours. Calibrate edit selectors during this run (record in `timecards.json`).
**Operator localhost review checkpoint #2.**

## Milestone 4 — Docs, verify, PR (only after operator alignment on localhost) · Composer (docs) / Sonnet

Docs lock-step: `RUNBOOK.md` (new § Punch gaps: flag, apply job, recovering a failed write),
`agents/bhaga/knowledge-base/DOMAIN.md` (export omits open punches; new tables),
`agents/bhaga/scripts/README.md` (loader + apply branch), `.cursor/rules/bhaga.mdc` (ADP write
exception: flagged out-punch fill only), `docs/FEATURE_FLAGS.md` (`PUNCH_FIX_WRITEBACK`),
`apps/operator-console/lib/actions/MUTATING_ACTIONS.md`, `PROGRESS.md` via the retro follow-up.

```bash
python3 scripts/check_doc_freshness.py --base origin/main
python3 scripts/verify.py --full
```
Then (only after operator OK on localhost): `bash scripts/install-git-hooks.sh`, commit, push
with `--no-verify` only if the hook blocks on cost capture, `gh pr create --base main --head
fix/operator-console-labor-weekly-hours-don` as the bot account (`jarvis-agent-bot328`,
GH_TOKEN), `Refs #356`, cost ledger `bind-pr` + `sync`, then babysit per `pr-workflow.mdc`
(reply to every comment, one push per batch). Never self-merge — operator merges.

## Evidence (PR §4)

Evidence tier: sandbox-live, scenario: full-live — plus the operator-chosen prod live write (J2).

| # | Scenario | Evidence | Pass |
|---|---|---|---|
| 1 | Happy path: missing clock-out, only entry | unit (fixture Emp A 9/21) | missing_out @09:00, suggest 15:00 |
| 2 | Missing clock-out after break | unit (Emp C 9/25) | entry 0 kept; entry 1 open; suggest 13:30 |
| 3 | No entry | unit (Emp E 9/28) | no_entry, no suggestion |
| 4 | In progress | unit (Emp A 10/2 at 23:30 CT vs 21:30) | in_progress, no suggestion before cutoff |
| 5 | Never fewer hours; shop-close cap; no schedule | unit | hold |
| 6 | Idempotent re-run | sandbox full-live twice | same rows; decisions untouched |
| 7 | Prod read check PP 9/21–10/4 | BQ query + localhost screenshot | 15 rows; actions record decisions |
| 8 | Existing behavior unchanged | `verify.py --full`, existing labor/payroll tests | green; adp_shifts/adp_punches unchanged |
| 9 | Accept writes Out + note | unit (mock page) | one Save, verified |
| 10 | Edit time | unit | edited value written; out > in, ≤ close guard |
| 11 | Read-back verify | unit + live | applied only when ADP shows value |
| 12 | Already fixed / duplicate apply | unit | no write; `already_resolved` |
| 13 | Reject | unit | no write |
| 14 | Failure recovery: Save fails | unit | `failed`, breadcrumb, no retry; manual Retry |
| 15 | Live prod write | operator-chosen gap | ADP + adp_punches + console agree |
| 16 | Console reflects write | screenshot | Applied ✓, hours updated |
| post-merge | nightly records new gaps; operator clears one | BQ + console | row + decision present |

Dry-run: `backfill_from_downloads --dry-run` prints the purge/upsert plan for the gaps loader.

## As built (changes agreed during localhost review, 2026-10-03/04)

- **`no_entry` gets a suggestion** (operator): the scheduled shift — first scheduled start, scheduled
  hours clipped at shop close (`punch_gaps.suggest_entry`, rule `scheduled_shift`,
  `adp_timecard_gaps.suggested_in`). Accept writes a whole entry (`timecard_fix_backend.apply_new_entry`).
  Replaces "No invented hours" above — the operator still has to click.
- **ADP never runs in the operator's browser or laptop** (operator): the local spawn / attached-Chrome
  path was removed; Write to ADP and Sync clocked hours always start the Cloud Run job. Local dev
  points at a branch-built job via `BHAGA_ADP_PREVIEW_JOB`.
- **Bulk select**: row checkboxes with **Accept N** / **Dismiss N** (`dismissPunchGapsAction`).
- **Resync skips the Team Schedule** after a write; the Timecards reader reopens and retries an
  employee once, and accepts a pay-code suffix after the time range ("Weekly Overtime (0:26 HRS)").
- Live: two spike writes, then the operator's batch of 12 (7 clock-outs + 5 new entries) all
  `applied` on 2026-10-04; the next read left only the dismissed row.

## Model routing

M1 Sonnet (parsers/loaders), M2 Sonnet (UI), M3 Opus (the risky ADP write path + review),
M4 Composer (docs) / Sonnet (verify, PR). One chat per PR (cost playbook, `docs/contributing/cost.md`).
