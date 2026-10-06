# Issue #358 — a punch fix written from /labor must land in the hours /payroll sums

Jam + §4 approved in chat 2026-10-05 and recorded via `phase_state.py advance --operator-approved`.

Evidence tier: sandbox-e2e

## Root cause (prod, read-only diagnosis)

- 2026-10-05 04:26Z the operator accepted Cimino Denton's 2026-10-04 `no_entry` (06:30–13:30) on /labor.
  `bhaga-schedule-preview-z7l6q` wrote it into ADP Timecards at 04:30:11Z (`status=applied`, read back OK).
- 40 s later the same job's timecard-only resync downloaded the Sep 21–Oct 4 Timecard XLSX
  (`[adp_timecard] selected pay period 2026-09-21 → 2026-10-04`). Every other Oct 4 punch was in it;
  Cimino's new entry was **not**. ADP's Timecard report lags a Timecards-UI save.
- A read-only resync at 05:31Z (`bhaga-daily-refresh-48hcc`) picked it up: `adp_shifts` 2026-10-04
  Denton 06:30–13:30 7.0 h; `vw_model_payroll_period` Sep 21 total 378.05 → **385.05**, Cimino 17.25.
- Why two pages disagreed: /labor and /payroll already read the same hours tables (`adp_shifts` /
  `adp_punches`). But /labor's Punches badge shows `punch_gap_decisions.status` ("Written to ADP ✓"),
  which the job sets from the UI read-back, never from the hours tables. Nothing reconciles the two.
- Secondary: the resync `REFRESH_DATE` is "yesterday at click time" (`apps/operator-console/lib/bhaga/punch-fix.ts:46`),
  so the `scraped_at_utc` stamp (`agents/bhaga/scripts/daily_refresh.py:2861`) stopped at 2026-10-03.

## Invariant (the single source of truth)

`adp_shifts` / `adp_punches` are the only hours every page sums. A decision is `applied` only when its
punch is **in `adp_punches`**. A punch ADP saved but the export does not yet contain is `not_in_hours`,
and both /labor and /payroll show the same list from one query.

Preserved: read-only toward payroll (never Approve/Submit; bhaga.mdc invariant 6), Save-once/no auto
retry of the ADP **write** (only the read-only download repeats), America/Chicago dates, idempotent MERGE
loads, sandbox isolation (`datastore._assert_sandbox_write_isolation`).

Feature flag: none new. The write path is already behind `PUNCH_FIX_WRITEBACK`; this change cannot
produce wrong numbers — it only re-reads and reports (docs/FEATURE_FLAGS.md unchanged).

## Milestone 1 — write job verifies in the same ADP login (Sonnet)

`agents/bhaga/scripts/punch_fix_apply.py`

1. New pure helper:

```python
def punch_in_export(row: dict, punches: list[dict]) -> bool:
    """True when the parsed Timecard XLSX holds the punch this decision wrote.
    no_entry: a punch on (date, employee) with in == row.in_time and out == row.out_time.
    missing_out: a punch on (date, employee) with out == row.out_time."""
```

2. Inside the existing `with runner.adp_session(...)` block (`punch_fix_apply.py:116`), after the
   per-row loop (`:134-175`), for rows recorded `applied`:

```python
RESYNC_WAIT_S = int(os.environ.get("BHAGA_PUNCH_FIX_RESYNC_WAIT_S", "90"))
RESYNC_ATTEMPTS = 3

def _resync_in_session(page, dashboard_url, store, applied, target_date) -> list[dict]:
    """Download the Timecard XLSX in this login until every applied punch is in it
    (wait RESYNC_WAIT_S before each attempt). Returns the rows still missing.
    Leaves Timecard-<today>.xlsx (+ target meta) and TimecardsUI-<today>.json on disk."""
```

   Each attempt: `page.goto(dashboard_url)` (same-domain reset pattern, `runner.py:2570-2598`),
   `runner._timecard_within_session(page, target_date=target_date, store=store)` (`runner.py:1107`),
   `runner._write_target_meta(path, target_date)` (`runner.py:751`),
   `shift_backend.parse_xlsx(path, employee_aliases=load_aliases(store))` (`shift_backend.py:264`).
   Log `[punch-fix] resync attempt=N/3 missing=K`. After the loop, capture the gaps JSON in the same
   login: `runner._write_timecards_ui_json(runner._timecard_gaps_within_session(page), store=store)`
   (`runner.py:2092`, `:2150`).
3. Rows still missing → `_set_status(client, id, "not_in_hours", "<msg>")` (`:49`) and breadcrumb
   `[punch-fix] NOT_IN_HOURS date=… emp=… attempts=3`. `apply_decisions` returns
   `{"applied", "already_resolved", "failed", "not_in_hours", "max_date"}`.
4. `reconcile_not_in_hours(client, store) -> int`: one UPDATE flipping `not_in_hours` → `applied`
   where a matching `adp_punches` row now exists (same match rule as `punch_in_export`, in SQL).

`agents/bhaga/scripts/daily_refresh.py`

5. `:2768-2782`: after `run_from_env`, set `refresh_date = counts["max_date"]` (latest applied /
   already_resolved date) and keep `after_punch_fix = True`. *Implementation note:* exact, not
   `max(yesterday, …)` — the export covers the pay period containing its target date and ADP only
   accepts writes inside its open period, so "yesterday" can name the next period; Layer A also
   needs the target-meta to match exactly. `:2798-2805`: do **not** delete the cached
   Timecard/TimecardsUI files when the write job reports `export_on_disk` (its in-session download
   succeeded) — `download_adp_bundle` Layer A (`runner.py:2472`) then reuses them, so the resync
   needs no second ADP login. Without `export_on_disk` (e.g. only `already_resolved`) the cache is
   deleted as before.

`agents/bhaga/scripts/backfill_from_downloads.py`

6. After the `adp_punches` load (`:369-379`): `pfa.reconcile_not_in_hours(client, args.store)` and
   print `  punch fixes reconciled: N`. Runs on every load path (nightly, Sync clocked hours, write job),
   so a lagging export self-heals on the next sync.

**Verify**

```bash
python3 -m pytest agents/bhaga/scripts/test_punch_fix_apply.py agents/bhaga/scripts/test_daily_refresh.py -q -k "punch_fix or resync or reconcile"
```

Pass: new tests — present on first download → `applied`, 1 download; missing then present → `applied`,
2 downloads; missing 3× → `not_in_hours` + `NOT_IN_HOURS` breadcrumb; `max_date` drives `refresh_date`;
cached files kept after punch fix; reconcile SQL flips only matching rows.

## Milestone 2 — one shared "not in hours yet" query on /labor and /payroll (Sonnet)

1. `apps/operator-console/lib/bq/queries.ts` after `adpHoursScrapedAt` (`:972-978`):

```ts
export interface PunchFixNotInHoursRow { date: string; employee: string; in_time: string | null; out_time: string | null; status: string; }
/** Punch fixes written to ADP whose punch is not in adp_punches (status not_in_hours, or applied with no match). */
export function punchFixesNotInHours(store: string, start: string, end: string): Promise<PunchFixNotInHoursRow[]>
```

2. `apps/operator-console/lib/labor/punch-gaps.ts`: add `"not_in_hours"` to `PunchGapStatus` (`:20-27`);
   `decisionLabel` (`:340-347`) → `"In ADP, not in hours yet"`; `isOpenGap` (`:313-319`) keeps it open.
3. `apps/operator-console/components/labor/PunchGapsPanel.tsx` `DecisionBadge` map (`:112-128`):
   `not_in_hours: { text: "In ADP — not in hours yet", tone: "text-amber-700 dark:text-amber-400" }`.
4. New `apps/operator-console/components/labor/PunchFixesNotInHoursNotice.tsx`: amber notice using the
   existing `Card` + `Badge` primitives and muted text (same tokens as the payroll `HeadlineStat` hint,
   `app/payroll/page.tsx:78-84`). Copy: "N punch fix(es) written to ADP aren't in hours yet — Sync
   clocked hours to pull them in." Lists `Name · Day · in–out`. Renders nothing when empty. Rendered
   beside `SyncClockedHoursButton` on `app/payroll/page.tsx:413-419` and `app/labor/page.tsx:617-623`.
   States: no hover actions; mobile wraps; dark mode tokens only.

**Verify**

```bash
cd apps/operator-console && npx vitest run __tests__/punch-gaps.test.ts __tests__/punch-fix-not-in-hours.test.tsx && npx tsc --noEmit
```

Pass: label/isOpen for `not_in_hours`; notice renders the count + rows and renders null for `[]`.

## Milestone 3 — mechanical gate + docs (Composer)

1. `agents/bhaga/scripts/status.py` Layer 2 (`:690-692`): append
   `CheckResult("bq", "punch_fixes_in_hours", present=n == 0, rows=n, note="decisions applied/not_in_hours with no adp_punches row")`
   so `status` exits 1 when any written fix is missing from hours.
2. Docs lock-step: `RUNBOOK.md` § Missing punches (statuses add `not_in_hours`; resync is in the same
   login; reconcile on every load), `agents/bhaga/scripts/README.md` (punch_fix_apply row),
   `.cursor/rules/bhaga.mdc` invariant 6 addendum (applied = in adp_punches), `docs/FEATURE_FLAGS.md`
   row text, `apps/operator-console/lib/actions/MUTATING_ACTIONS.md`.

**Verify**

```bash
python3 -m pytest agents/bhaga/scripts/test_status.py -q -k punch_fixes
python3 scripts/check_doc_freshness.py --base origin/main
python3 scripts/verify.py --full
```

## Per-scenario evidence (PR §4)

| # | Scenario | Evidence |
|---|---|---|
| E1 | Happy path: export already has the punch | unit: 1 download, `applied` |
| E2 | Recovery: export lags one download (2026-10-05 Cimino case) | unit: 2 downloads, `applied` |
| E3 | Failure: export never shows it | unit: 3 downloads, `not_in_hours` + `[punch-fix] NOT_IN_HOURS` |
| E4 | Recovery: later Sync / nightly picks it up | unit: reconcile flips `not_in_hours` → `applied` only on match |
| E5 | Resync covers the corrected day | unit: `refresh_date = max_date`; stamp includes it |
| E6 | Legacy: no punch fix | existing `test_daily_refresh` timecard-only tests unchanged |
| E7 | Prod: Cimino 2026-10-04 in hours | BQ `adp_shifts` row 06:30–13:30 7.0; /payroll Cimino 17.25, total 385.05 (screenshot via `apps/operator-console/scripts/capture_evidence.py`) |
| E8 | Prod: both pages agree | /labor + /payroll screenshots; `status --store palmetto` `punch_fixes_in_hours` present (0 rows) |
| E9 | Sandbox | `sandbox_e2e.py` full-live scenario green (pipeline load path unchanged apart from reconcile) |

Out of scope: Skyler 25 h / Tina 10 h hand-keyed on ADP Enter payroll (no punches) — payroll stays
34.57 h under ADP's 419.62 for that reason.

## Branch / PR mechanics

One branch `fix/want-to-understand-why-https-operator`, one PR `--base main` with `Closes #358`, pushed as
`jarvis-agent-bot328` (`--no-verify` only after `verify.py --full` is green); reply to every review thread;
never self-merge; operator squash-merges.

## Model routing

M1 Sonnet (job logic + tests), M2 Sonnet (console), M3 Composer (status + docs). One chat per PR.
