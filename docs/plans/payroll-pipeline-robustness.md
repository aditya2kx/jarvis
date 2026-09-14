# BHAGA pipeline robustness — gaps and fixes

Branch: `fix/i-just-ran-the-payroll-this` · Target: one PR

Origin: payroll ran 2026-09-11; nightly cron had been failing since 09-07. Diagnosis
found duplicate rows in `model_tip_alloc_daily`, a tripped circuit breaker that held
for six days, and a nightly wage-rate alert that had been firing since August.

---

## What the diagnosis established

**Money and history are correct.** Audited all 11 `model_*` tables across full
history for duplicate natural keys:

| Table | Rows | Distinct keys | Duplicates |
|---|---|---|---|
| `model_tip_alloc_daily` | 1178 | 789 | **389** |
| all 10 others | — | — | 0 |

- The 389 duplicates are exactly 2 copies each, agreeing on every **business** column
  — same `our_share`, `hours_worked`, `day_pool`. They differ only in the metadata
  column `materialized_at_utc` (all 389 keys), because the two racing runs stamped
  different write times. `SELECT DISTINCT` therefore does **not** collapse them;
  dedupe must be latest-wins on `materialized_at_utc`.
- Span: 2026-06-16 → 2026-09-06 (83 days).
- `model_tip_alloc_period` (what payroll reads) reconciles **exactly** against the
  deduped daily: 144 employee-periods, 0 mismatches, max difference $0.00.

The aggregates never ingested the duplication because they are built from raw data,
not from the daily table. The semantic guard correctly detected a real defect — but
an inert one.

**This time** the duplicates were identical because both writers computed from the
same raw snapshot. Two writers with *different* inputs (a recompute racing a fresh
ingest) would produce divergent rows, which would not be recoverable without a
rebuild. The fix must prevent the state, not rely on the copies matching.

**ADP spike (2026-09-13, live RUN session).** The two chronically failing wage-rate
scrapes have *different* root causes:

- **Flores, Juan** — status `Terminated`. The People Directory's Status filter
  defaults to `Active` only (`Leave of absence` and `Terminated` unchecked). The
  scraper never touches the filter, so he is structurally invisible to it. Enabling
  the filters reveals him: roster goes 14 → 20. **Structural, reproducible.**
- **Majdinasab, Tina** — status `Active`, present in the default list, and her name
  resolves cleanly: search returns exactly one row `Majdinasab, Tina` within 2.5 s in
  the expected `Last, First` format. Her failure is **an unhandled blocking modal**,
  found live during the spike (below).

**ADP fires a full-viewport "Session Timeout" modal on idle.** Clicking a Directory
row failed with pointer-event interception by `div.message-box-outer`:

> Session Timeout — Your session is about to be timed out. Click OK now to continue
> working, or Cancel to sign out. `[Ok] [Cancel]`

It is `position: fixed`, `z-index: 20000`, and covers the entire 1440×900 viewport.
It produces exactly the observed failure signature —
`TimeoutError: Locator.click: Timeout 10000ms exceeded` — and it is timing-dependent,
which is why the same two names do not fail deterministically.

`_close_overlays()` (`pay_info_backend.py:128`) only presses Escape twice. Nothing in
`skills/adp_run_automation/` references `message-box` or "Session Timeout". A custom
Ok/Cancel dialog is very unlikely to yield to Escape. Worse: left unanswered the
modal signs the session out, so every subsequent employee in the loop fails too. This
is a strong candidate for the Monday/Tuesday `adp_bundle` timeouts as well — the long
earnings scrape leaves the page idle for exactly the window that triggers it.
- Both already have valid rates ($15.25) in `adp_wage_rates` via `rate_source =
  earnings`. Nothing downstream is missing data.
**Matching key: we do not currently have a real employee ID.** In `adp_punches` over
the last 60 days there are 16 rows-worth of people, 16 distinct `employee_id` values,
0 nulls — but `employee_id` is **identical to `canonical_name`** for every one of
them. It is a name-derived key, not an ADP identifier. The Directory DOM does not
expose an ID either: row buttons carry only
`data-test-id="active-name-cell-button"` and
`aria-label="Go to the profile page for <Name>"`, with the nearest identifiers being
row indices (`aeed-name-3`). So ID-based matching is the right target but is not
available yet — it requires capturing ADP's associate ID / file number from the
profile page and persisting it, after which matching can become ID-based.

This matters because of a live collision: the 60-day punch roster contains
`Johnson, Dolce`, while the Directory holds **two** such records — `Johnson, Dolce`
(`Terminated`) and `Johnson, Dolce J` (`Active`). Our roster name matches the
*terminated* record exactly. Once the status filter is cleared, a name match would
resolve to the wrong person.

The roster also confirms terminated employees legitimately carry recent hours:
`Flores, Juan` and `Urrutia, Emely` are both `Terminated` yet both appear in the
last-60-day punch roster. Filtering the scrape by employment status is therefore
wrong by construction — hours, not status, define who needs a rate.

---

## Milestone 1 — Restore correct state

- Dedupe `model_tip_alloc_daily` to 789 rows, keeping the latest row per
  `(date, employee)` by `materialized_at_utc`; assert row count equals distinct-key
  count afterward. **Done 2026-09-13** — backup at
  `bhaga.model_tip_alloc_daily_bak_20260913` (1178 rows); post-dedupe 789 rows /
  0 duplicate keys; payroll reconciliation 144 compared / 0 mismatches / $0.00;
  tip-pool conservation 173 days / 0 violations / 0c max residual.
- Audit the raw tables (`adp_wage_rates` and siblings) for duplicate keys the same
  way. Only the model layer has been proven so far — this is a known unknown.
- Clear the circuit breaker; backfill 2026-09-07 → 2026-09-13 (7 missing days).
  **Done 2026-09-13, by the nightly itself.** Once the breaker was cleared, the
  21:30 CDT cron (execution `bhaga-daily-refresh-mvsvx`) computed its own gap window
  from `config.data_window_end` and closed all seven days unaided — the self-healing
  path working as designed, so no manual range command was needed. It exited 0 in
  614.7s with `semantics OK — tip_pool_conservation: 180 dates checked, max residual
  0c`. `model_tip_alloc_daily` is now 814 rows / 814 distinct `(date, employee)` keys
  spanning 2026-03-09 → 2026-09-13: the gap is closed and the dedupe held through a
  full-history rebuild. That rebuild is also the cleanest possible check on Milestone
  1 — it regenerated every row from raw and landed on exactly the deduped set.

Verify:

```bash
# dedupe: latest-wins on materialized_at_utc (NOT SELECT DISTINCT — see above)
bq query --use_legacy_sql=false '
CREATE OR REPLACE TABLE `jarvis-bhaga-prod.bhaga.model_tip_alloc_daily` AS
SELECT * EXCEPT(_rn) FROM (
  SELECT t.*, ROW_NUMBER() OVER (
      PARTITION BY date, employee ORDER BY materialized_at_utc DESC) AS _rn
  FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_daily` t
) WHERE _rn = 1'

bq query --use_legacy_sql=false '
SELECT COUNT(*) n, COUNT(DISTINCT FORMAT("%t|%t",date,employee)) k,
       COUNT(*) - COUNT(DISTINCT FORMAT("%t|%t",date,employee)) dupe
FROM `jarvis-bhaga-prod.bhaga.model_tip_alloc_daily`'
python3 -m agents.bhaga.scripts.status --store palmetto
```

Pass criterion: `dupe = 0` and `n = 789`; `status` shows the data window reaching the
current date; `model_tip_alloc_period` unchanged (payroll figures identical).

Model routing: **Composer** (mechanical SQL + CLI, no design judgement).

## Milestone 2 — Make the duplicate state unreachable · **Done 2026-09-13**

- Replace the two-job DELETE-then-MERGE in `load_model_rows`
  (`agents/bhaga/scripts/materialize_model_bq.py`) with a single atomic MERGE using
  `WHEN NOT MATCHED BY SOURCE` scoped to the batch. Closes the race window rather
  than narrowing it.
- Add a post-write natural-key uniqueness assertion that **fails the write itself**,
  so a duplicate cannot persist even if a new race appears.
- Re-key the dispatch lock on the resource (store + model tables) rather than
  `REFRESH_DATE`. The date-keyed guard in `cloud/webhook/handler.py::_is_already_running`
  was answering the wrong question: two different dates both rebuild everything.
- Wire the existing `hasRunningBhagaJob()` into `triggerModelRecompute`
  (`apps/operator-console/lib/bhaga/recompute.ts`). It is already used on the
  order-reco path; the model recompute path never calls it.

Target signature (`materialize_model_bq.py`, replacing the `replace_scope` branch at
line 275):

```python
def load_model_rows(
    table: str,
    dicts: list[dict],
    *,
    replace_scope: bool = False,
    dry_run: bool = False,
) -> int:
    """Atomic scoped upsert. Single MERGE; no DELETE-then-MERGE window."""


def assert_unique_natural_key(table: str, key_cols: list[str]) -> None:
    """Raise if row count != distinct natural-key count. Called after every write."""
```

Verify:

```bash
python3 -m pytest agents/bhaga/scripts/test_materialize_model_bq.py -k "concurrent or unique_key" -q
python3 scripts/verify.py --full
```

Pass criterion: the concurrency test fires two recomputes for different dates and
`model_tip_alloc_daily` ends with 0 duplicate keys; `assert_unique_natural_key` raises
on a seeded duplicate.

Model routing: **Sonnet** (BQ MERGE semantics + concurrency test design).

**Landed 2026-09-13.** `merge_rows_scoped` + `assert_unique_natural_key` in
`core/datastore.py` (staging table + single MERGE with `WHEN NOT MATCHED BY SOURCE
THEN DELETE`); `load_model_rows` routed through them; `_is_already_running` deleted and
replaced by the resource-keyed `_any_execution_running`, so a trigger is refused while
*any* execution is live rather than only one for the same date; `_trigger_cloud_run_job*`
now returns whether it started so a refused date is reported instead of implied;
`triggerModelRecompute` gated on `hasRunningBhagaJob()` with the console message saying
the recompute was not queued. 169 webhook tests, 34 materialize/datastore tests, 10
console tests, `verify.py --full` PASS.

## Milestone 3 — Scope the recompute to the request · **Done 2026-09-13**

Today `materialize(store)` takes no date at all and rebuilds all history. The console
asks to recompute one date; the backend rebuilds everything. The 83-day blast radius
is the absence of scoping, not a chosen window.

- Give `materialize()` a window parameter.
- Derive which tables and which records a given date touches, rather than either
  rebuilding everything or naively filtering on the day. Period- and week-grained
  outputs (`model_labor_weekly`, `model_period_summary`, `model_labor_period`,
  `model_tip_alloc_period`) must be rebuilt as **whole units** for the containing
  pay period and ISO week.
- Bound the full-table scans in `read_shifts_bq` / `read_transactions_bq`
  (`agents/bhaga/scripts/update_model_sheet.py`).
- Scope the semantic guard to the touched window instead of validating all 173 days.

Target signatures (`agents/bhaga/scripts/materialize_model_bq.py`, replacing
`materialize(store)` at line 358):

```python
def touched_scope(store: str, dates: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Map each model table to the key ranges a set of dates touches.

    Daily tables -> the dates themselves. Period/week tables -> the full
    containing pay period / ISO week, rebuilt as whole units.
    """


def materialize(
    store: str,
    *,
    dates: list[str] | None = None,   # None = full rebuild (backfill path only)
    dry_run: bool = False,
) -> None:
```

Verify:

```bash
python3 -m pytest agents/bhaga/scripts/test_materialize_model_bq.py -k "scope" -q
BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.materialize_model_bq \
  --store palmetto --dates 2026-09-08 --dry-run
```

Pass criterion: the dry-run plan touches only 2026-09-08 in daily tables and only the
containing pay period (2026-09-07..2026-09-20) and its ISO week in the aggregates;
row counts for all other keys unchanged.

Model routing: **Opus** (grain derivation is the design-heavy milestone).

**Landed 2026-09-13.** `touched_scope(dates, periods)` maps every model table to its
grain units — days for daily tables, the containing ISO week for
`model_labor_weekly`, the containing pay period for the three period-grained tables.
`load_model_rows(scope=…)` filters on `_grain_col(table)` before the write;
`materialize(store, dates=…)` threads the scope through all seven writes plus the
whole-day exemption eviction; `_assert_conservation(period_results, only_starts)`
checks only the periods the run actually wrote. `daily_refresh` passes
`--dates <refresh_date>`. Gated by `BHAGA_SCOPED_MATERIALIZE`, default off.

**Deliberately not done: bounding the raw reads.** `read_shifts_bq` /
`read_transactions_bq` still read full history, and should. Scoping the *write* is a
correctness fix — the rows we skip are byte-identical to what is already stored, so
skipping them cannot change a number. Scoping the *read* is a different and far more
dangerous thing: every week- and period-grained aggregate, the 8-week forecast
backfill, `discover_periods`, and `last_data_date` all derive from the full raw set,
so a truncated read would silently recompute aggregates from partial inputs — exactly
the "silently produce wrong numbers" failure the feature-flag test exists to catch.
The full read is a cost and latency concern, not a correctness one; it belongs in its
own change with its own evidence, not bundled into the scoping fix. Follow-up issue.

## Milestone 4 — Breaker and portal tell the truth · **Done 2026-09-13**

- Tier the breaker: a model-layer fault should stop model writes but let **raw ingest
  continue**. Today a model fault halts ingestion too, which is why six days of
  Square/ADP data were never collected.
- Give the halt a TTL and an escalation path so it cannot silently hold indefinitely
  (`skills/bhaga_config/state_adapter.py` has no expiry today).
- Add a server-derived health signal — breaker state, `data_window_end` age, last
  successful run — as a global banner on every Operator Console page, plus an
  explicit "data as of" stamp per view. Server-derived so it is identical for every
  viewer: a property of the system, not of a session. The console currently has no
  halt or staleness query at all and rendered six-day-old numbers as if current.
- Independent staleness alarm, so nobody needs to open the portal to find out.
- Disable the recompute action in the UI while an execution is live.

UI work follows `docs/contributing/ui-polish.md` and reuses existing design-system
primitives (shadcn `Badge`, `Alert`, muted text) — no ad-hoc banner styling.

Target signatures:

```python
# skills/bhaga_config/state_adapter.py
def set_pipeline_halt(reason: str, *, scope: str = "model", ttl_hours: int = 12) -> None:
    """scope: 'model' blocks model writes only; 'all' blocks ingest too."""
```

```ts
// apps/operator-console/lib/bq/queries.ts
export async function getSystemHealth(): Promise<{
  halted: boolean; haltReason: string | null; haltScope: "model" | "all" | null;
  dataWindowEnd: string; dataAgeDays: number; lastSuccessfulRun: string | null;
}>;
```

Verify:

```bash
python3 -m pytest skills/bhaga_config/test_state_adapter.py -k "ttl or scope" -q
cd apps/operator-console && npm test -- systemHealth && npm run build
```

Pass criterion: with the breaker tripped at `scope=model`, nightly ingest still writes
raw tables; a fresh browser session (no cookies) loads the console and sees the halt
reason and data age; the halt auto-expires after its TTL.

Model routing: **Sonnet** (Python + TS), **Opus** for the banner UX pass.

**Landed 2026-09-13.** `set_pipeline_halt(scope=…, ttl_hours=…)` with `scope` defaulting to
`model` and a 12 h TTL; `get_pipeline_halt(include_expired=…)` treats an expired halt as
not-halted, and the nightly escalates the expiry with a `pipeline_halt_expired`
alert before resuming. `daily_refresh` tiers the gate: a model-scoped halt sets
`skip_model` and lets raw ingest run; only `scope=all` still returns `EXIT_HALTED`.
Console: `lib/bhaga/health.ts` (`getSystemHealth`, Firestore-over-REST with ADC — no
new dependency) feeds `components/shell/HealthBanner.tsx` from the root layout, so
every page on every session shows the breaker state or the date the data stops at, and
says "unknown" rather than implying health when the lookup fails. The payroll Update
button is disabled while a run is live. The staleness alarm rides the existing 08:00 CT
`bhaga-team-pulse` kick (plus `POST /staleness-check`) rather than a second scheduler —
the only property it needs is a daily beat the nightly cannot silence. 173 webhook
tests, 42 state-adapter tests, 465 console tests pass; `tsc --noEmit` adds no new errors
(3 pre-existing, unchanged).

## Milestone 5 — ADP scraper resilience · **Done 2026-09-13**

- **Alert on outcome, not mechanism.** `report_pay_info_issues`
  (`skills/adp_run_automation/pay_info_backend.py`) fires when *any* of
  `scrape_errors` / `remaining_gaps` / `flow_error` is set. `remaining_gaps` (from
  `gap_names_from_bq`) is the real signal and has been empty every night. A failure
  of one redundant path should be a breadcrumb; Slack should fire only when the union
  of `pay_info` + `earnings` leaves someone without a rate.
- **Clear the Status filter** before searching the Directory, so terminated and
  on-leave employees are reachable (fixes Flores).
- **Handle the Session Timeout modal.** Teach `_close_overlays()` to detect
  `div.message-box-outer` and click `Ok` (never `Cancel`, which signs out). Escape
  alone does not dismiss it. Add a keep-alive or a pre-action overlay check before
  every click in the per-employee loop, since the modal can appear mid-loop and
  poisons every subsequent employee.
- **Replace fixed `wait_for_timeout` sleeps with settle-waits** on the SDF components.
- **Adopt a real employee ID.** Capture ADP's associate ID / file number when
  visiting a profile and persist it on `adp_wage_rates` / `adp_punches`, then match
  on it. Until then `employee_id` is just the name. Interim guard: match full name
  **and** disambiguate when more than one Directory record matches, given
  `Johnson, Dolce` (Terminated) vs `Johnson, Dolce J` (Active) both exist.
- Make the `pay_info` → `earnings` fallback deliberate rather than incidental.
- Restore failure-evidence capture to GCS (silently broken since 2026-08-24).

**Live confirmation from the 2026-09-13 nightly** (deployed code, pre-merge), which
reproduced both halves of this milestone in one run:

```
[pay_info] FAIL Alvarez, Sebastian: TimeoutError: Locator.click: Timeout 10000ms exceeded.
[pay_info] FAIL Flores, Juan:       TimeoutError: Locator.click: Timeout 10000ms exceeded.
[pay_info] OK — no punchers missing wage rates in last 60d
[pay_info] BREADCRUMB wage_rate_flow_issue attempted=16 ok=13 scrape_fail=3 gaps=[]
```

`gaps=[]` beside three failures is the false alarm stated above, measured: nobody
lacked a rate, and Slack got a "Failed scrapes" message anyway. The new alerting
condition would have stayed silent here.

`Alvarez, Sebastian` failing is the strongest evidence yet for the session-timeout
diagnosis, and it is new — he is the **first** name in the roster, scraped straight
after the schedule crawl left the page idle for roughly two minutes. The failure
tracks *time since last interaction*, not the employee, which is why the victim set
drifts night to night and why per-employee theories never explained it.

Target signatures (`skills/adp_run_automation/pay_info_backend.py`):

```python
def _dismiss_blocking_modals(page) -> bool:
    """Dismiss ADP's Session Timeout dialog (div.message-box-outer, z-index 20000).

    Clicks 'Ok' to extend the session; NEVER 'Cancel' (signs out). Returns True
    if a modal was dismissed. Escape does not close this dialog.
    """


def _clear_directory_status_filter(page) -> None:
    """Tick Terminated + Leave of absence in [data-test-id="filter-button"].

    Default is Active-only, which hides terminated punchers (e.g. Flores, Juan).
    """
```

Migration for the identity fix:

```sql
-- core/migrations/064_adp_associate_id.sql
ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_wage_rates` ADD COLUMN IF NOT EXISTS associate_id STRING;
ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_punches`    ADD COLUMN IF NOT EXISTS associate_id STRING;
```

Verify:

```bash
python3 -m pytest skills/adp_run_automation/test_pay_info_backend.py -q
python3 -m skills.adp_run_automation.pay_info_backend --store palmetto --from-bq-punchers --days 60
gcloud storage ls gs://jarvis-bhaga-evidence/pay_info/ | tail -3
```

Pass criterion: the live run resolves all 16 punchers including `Flores, Juan`
(Terminated), emits no Slack alert, and does not confuse `Johnson, Dolce` with
`Johnson, Dolce J`; an induced failure writes a screenshot to GCS.

Model routing: **Sonnet** (Playwright selectors calibrated against the 2026-09-13
spike findings recorded above).

**Landed 2026-09-13.** `report_pay_info_issues` alerts only on `remaining_gaps` or a
flow error — a scrape failure for someone who already has a rate via `earnings` is now
a breadcrumb, which retires the nightly false alarm. `dismiss_blocking_modals()` clicks
**Ok** on `div.message-box-outer` (never Cancel), re-checked at every click via
`_click_through_modals` and after each failure so one employee's timeout no longer
poisons the rest of the loop. `clear_directory_status_filter()` ticks Terminated +
Leave of absence before searching, which is what makes Flores reachable at all.
`_wait_for_directory_results` replaces the fixed 2.5 s sleep with a poll on the rendered
roster. `select_directory_match()` requires an exact name and raises
`AmbiguousEmployeeError` on a near match, guarding `Johnson, Dolce` vs
`Johnson, Dolce J`. `_capture_pay_info_failure` routes evidence to
`gs://<cache>/<date>/evidence/` instead of a local path that does not survive a Cloud
Run execution. 152 ADP-skill tests pass.

**Deliberately not done: the `associate_id` migration.** Adding the columns now would
add two columns nothing can populate: the spike established that the Directory list
view exposes no ADP identifier, so capturing an associate ID means visiting each
profile page — a separate scrape with its own failure modes and its own evidence.
Shipping the schema ahead of the capture buys nothing and invites code that reads a
column which is null for every row. The exact-match refusal above closes the actual
risk (a wrong-person match) today. Follow-up issue for the ID capture.

## Milestone 6 — Hygiene · **Done 2026-09-13**

- Plaid `INVALID_API_KEYS` — surfaced during diagnosis as an expired credential.
- Deduplicate the repeating review-anomaly alert.

Verify: two consecutive nightlies produce one review-anomaly Slack message, not N.

Model routing: **Composer**.

**The Plaid credential was never expired.** Probed live on 2026-09-13: the stored
`plaid_client_id` / `plaid_secret` authenticate against `production.plaid.com`
(HTTP 200) and are rejected by `sandbox.plaid.com` (`INVALID_API_KEYS`). The keys are
fine; the *host* was wrong. `plaid_env()` defaulted to `sandbox` whenever `PLAID_ENV`
was unset, while the credentials always come from Secret Manager, which only holds the
production pair. Both Cloud Run **services** set `PLAID_ENV=production` explicitly; the
**job** never did — and the job is what runs the nightly `plaid_sync` catch-up. So every
night it sent production keys to sandbox and got back an error that reads exactly like
an expired credential.

Reproduced once more on the 2026-09-13 nightly, still on deployed code:
`[plaid_sync] failed item=ya7xdVa8... (non-fatal): Plaid POST /transactions/sync
failed 400: INVALID_API_KEYS`. Non-fatal, so it has been degrading quietly rather
than failing the run.

Fixed in code rather than by editing job env, so it cannot regress the next time a
surface is added: on Cloud Run (`K_SERVICE` / `CLOUD_RUN_JOB`) the default now follows
the credentials — production — while off Cloud Run sandbox remains the safe default and
an explicit `PLAID_ENV` always wins. **No rotation was needed, and none was done.**

**Review-anomaly dedup.** Anomalies are recomputed over all review history nightly, so
the same unparseable post was re-reported forever. `partition_anomalies()` splits new
from carried-over against a remembered list (`state_adapter.get/set_notify_state`,
the same sandbox-isolated singleton pattern as the breaker); the DM now fires only when
something is genuinely new and notes how many previously-reported anomalies are still
open. A state-read failure falls back to reporting everything — noisy beats silently
dropping a real anomaly.

---

## Invariants that must not break

These hold before and after every milestone:

1. **Idempotent upserts** — re-running any date produces identical rows; the new
   atomic MERGE must preserve this, not merely avoid duplicates.
2. **Integer cents** — tip allocation stays in integer cents end to end; no float
   round-tripping introduced by the scoped rebuild.
3. **Tip pool conservation** — `assert_tip_pool_conserved`
   (`agents/bhaga/scripts/model_semantics.py:130`) must still hold exactly for every
   date in scope. Scoping the guard changes *which* dates it checks, never its
   tolerance.
4. **America/Chicago** — all date boundaries, pay periods and ISO weeks derive from
   CT, not UTC.
5. **Read-only ADP** — the scraper never mutates ADP. Dismissing the Session Timeout
   modal with `Ok` extends a session; it is not a data mutation. `Cancel` is
   forbidden.
6. **Sandbox write isolation** — `_assert_sandbox_write_isolation()` stays on every
   write path; the scoped rebuild must not bypass it.
7. **Payroll source unchanged** — `vw_model_payroll_period` continues to read from
   `model_tip_alloc_period`; no milestone repoints payroll at the daily table.

## Feature-flag decision

Applying the "can it silently produce wrong numbers?" test:

| Milestone | Silent-wrong-numbers risk | Flag |
|---|---|---|
| 1 dedupe/backfill | No — lossless, asserted | No flag |
| 2 atomic MERGE + key assertion | No — fails loudly by design | No flag |
| 3 scoped recompute | **Yes** — a wrong grain could leave stale aggregate rows that look valid | **`BHAGA_SCOPED_MATERIALIZE`**, default off |
| 4 breaker tiering + banner | No — observability; breaker defaults to current behaviour | No flag |
| 5 scraper resilience | No — missing rate is already detected by `gap_names_from_bq` | No flag |
| 6 hygiene | No | No flag |

Only Milestone 3 gets a flag. Add `BHAGA_SCOPED_MATERIALIZE` to `FEATURE_FLAGS.md`
with its removal condition: delete the flag once one full pay period has closed with
scoped recompute enabled and `assert_tip_pool_conserved` clean.

## Docs lock-step

| Change | Doc to update |
|---|---|
| Breaker tiering, TTL, halt scopes | `RUNBOOK.md` |
| Scoped recompute, `touched_scope` | `agents/bhaga/scripts/README.md` |
| Invariants 1-7 above | `.cursor/rules/bhaga.mdc` |
| Console health banner | `RUNBOOK.md` §17 + `apps/operator-console/` README |
| `BHAGA_SCOPED_MATERIALIZE` | `FEATURE_FLAGS.md` |
| Session Timeout handling, status filter, associate ID | `skills/adp_run_automation/README.md` |
| Dated entry for this work | `PROGRESS.md` (via follow-up PR, never a main push) |

Run `python3 scripts/check_doc_freshness.py` before the final push.

## Branch / PR mechanics

- One branch, one PR: `fix/i-just-ran-the-payroll-this` → **`--base main` explicitly**.
- All GitHub operations as `jarvis-agent-bot328`, never `aditya2kx`.
- Push with `--no-verify`; `scripts/verify.py --full` is the local CI mirror and must
  pass before every push.
- Reply to **every** review thread before pushing; batch fixes into one push per
  review cycle (each push triggers a paid Opus review).
- Never self-merge. Auto-merge is operator-only; the agent stops at "all green, all
  threads answered, no conflicts".
- Seed the cost ledger (`pr_cost_ledger.py set-meta` + build cost) in BQ; do not
  commit `metrics/pr_cost/`.

## Evidence tier

`Evidence tier: sandbox-live` · `scenario: full-live`

**Breaker tiering, proven live 2026-09-14** — three sandbox runs of
`bhaga-sandbox-refresh`, one per behaviour:

| Halt state | Result | Execution |
|---|---|---|
| `scope="model"` | `skipping model writes, CONTINUING raw ingest` · exit 0, 19.8s | `9flws` |
| `scope="all"` | `REFUSING TO RUN — pipeline HALTED` · `exit(3)`, `status=halted` | `bsvn8` |
| expired TTL | `breaker EXPIRED … auto-resuming` · exit 0 · halt doc cleared to `None` | `882dp` |

The first row is the whole point: under the old untiered breaker that same state
exited non-zero and collected nothing, which is how one bad model number cost a week
of Square and ADP data. Sandbox halt state was left clear.

**Captured 2026-09-14** — run
[34802593712](https://github.com/aditya2kx/jarvis/actions/runs/34802593712), date
`2026-09-12`: `DONE in 113.6s`, `semantics OK — tip_pool_conservation: 147 dates, 0c`.

Two fixes were needed to get there, both surfaced *by* the scenario and both the same
class of bug as the milestones themselves — a recoverable state that halted the run:

1. **`widen_tabs_to_fit`** (`bootstrap_sheets.py`). A slot sheet is created with
   `len(header) + 4` columns and leased for years; when a header spec grows, the grid
   stays narrow and `values.batchUpdate` rejects the batch (`Range (transactions!AI1)
   exceeds grid limits`) rather than expanding as appending rows does.
2. **Schema-derived column types** (`core/datastore.py`). An all-`None` batch has
   nothing to infer from, so typing fell back to STRING and BQ refused: `Value of type
   STRING cannot be assigned to T.er_futa, which has type FLOAT64`. ADP's liability
   body had simply omitted the FUTA line. `column_bq_types` hints existed to paper over
   this, but that asks every call site to remember what the table already knows.

**Milestone 5 partial result, stated plainly.** In the pre-fix sandbox run (this
branch's scraper code), `Majdinasab, Tina → $15.2500` succeeded — one of the two
chronic failures — and `Johnson, Dolce J` resolved via `select_directory_match` rather
than guessing between the colliding records. But three other names still timed out
(`Alvarez`, `Priyosha`, `Urrutia`). The modal handling helps and is not a cure. All
three had rates from the redundant source (`gaps=[]`), which is exactly the case the
new outcome-based alerting downgrades to a breadcrumb. Residual flakiness in the
`pay_info` path is a follow-up, not a claim this PR closes.

Per `user-preferences.mdc` J5 this reuses the existing `full-live` scenario rather
than adding a dedicated one — it already exercises the changed materialize path.
Per J2, acceptance evidence covers **both** sandbox e2e and prod ADP/Square live
verification. Per J3, the backfill re-runs **all** pending dates (2026-09-07 →
2026-09-13), not only the most recent.

---

## Milestone 7 — Inventory page must not go blank · **Done 2026-09-13**

Operator-reported, same night: `/inventory` showed "No delivery date registered yet."
over an empty table. Not a rendering bug — a data-dependency bug of exactly the kind
this PR exists to remove.

`orderRecoSlots()` INNER JOINs `vw_order_reco_next_dates`, and `refresh_order_reco`
*deletes* `inventory_order_reco` outright when that view is empty. No delivery date had
been registered since 2026-09-08 (the table is written only by an explicit operator
action — there is no cadence generator), so on 09-13 the view was empty and the page had
literally nothing to select.

The consequence is the failure mode this PR keeps hitting: a missing input in one layer
blanked a layer that does not depend on it. Stock on hand and burn rate are facts about
the store, not about an order. BQ knew them the whole time — the 20:32 closing count had
**Blade at 3.0 days left and Açaí at 5.9** — and the page withheld both behind
"No rows.", which reads as "nothing to do".

**Fix.** When no delivery date is live, `/inventory` falls back to
`inventoryStockLevels()` (`vw_inventory_order_assistant`, the same source the reco is
built from) and renders Item / Current Qty / Avg-per-day / **Days left**, ordered most-
urgent-first and using the existing `DAYS_LEFT_THRESHOLDS` colouring. The per-slot
ordering columns stay absent, because On Hand at Restock and Order Tubs are defined
relative to a delivery date and inventing them without one would be making numbers up.
The label now says what is missing *and* what still holds, and names the action that
restores the rest.

No feature flag: the change cannot produce a wrong number — it is strictly additive on
a page that previously rendered zero rows, and the reco path is untouched when a date
exists.

Verify:
```bash
cd apps/operator-console && npx vitest run __tests__/inventory-stock-only.test.ts
```
Pass criterion: stock rows survive the absence of a delivery date, no ordering columns
are fabricated, `Days left: null` is not coerced to `0`, and ordering is preserved.

Live: rendered against prod BQ in the real empty-schedule state —
[screenshot](https://github.com/aditya2kx/jarvis/releases/download/evidence-screenshots/inventory-stock-only-no-delivery-date-20260913-233439.png)
shows Blade 3 and Açaí 5.9 where the operator saw "No rows."

Model routing: **Composer**.

---

## Milestone 8 — Labor chart must not draw a forecast over a fact · **Done 2026-09-14**

Operator-reported: the week of Sep 7 had finished, but its bar still carried a
scheduled (slate) segment.

It was not a stale bar. `actualPunchWindow` clipped actuals at **yesterday** whenever
the Period reached today, and `laborScheduledHoursByGrain` admitted schedule from
`>= CURRENT_DATE('America/Chicago')`. So the current day was *always* drawn from its
schedule, even after its punches had been ingested — which happens every evening at
the 21:30 CT nightly. Between ingest and midnight, a day with real clocked hours was
rendered as a forecast.

Measured on the reported week, straight from BQ:

| | hours |
|---|---|
| Actual drawn (Sep 7–12 only) | 152.9 |
| Scheduled drawn (Sep 13) | 31.4 |
| **Combined, as shown** | **184.3** |
| Sep 13 hours actually clocked and in BQ | 28.3 |
| **True week total** | **181.2** |

The three numbers in the first block reproduce the operator's tooltip exactly. The
finished week was overstated by **3.1 hours**, and a real number was hidden behind a
prediction of itself — the same substitution this PR removes elsewhere.

**Fix.** The handoff now follows the data: `scheduleTakesOverFrom(today, actualsThrough)`
returns the day after the last date with clocked hours, clamped at today so a lagging
ingest can never pull the boundary backwards and paint schedule over days that are
merely awaiting punches. That one date drives both windows, and the redundant
`>= CURRENT_DATE` predicate in the scheduled SQL is gone — two boundaries for one
decision is how they drifted apart in the first place. A day can now show actual or
scheduled, never both. The page note no longer promises "through yesterday".

No feature flag: it strictly narrows what may be drawn as a forecast, and the failure
mode is falling back to exactly today's behaviour if `laborActualsThrough()` cannot be
read.

Verify:
```bash
cd apps/operator-console && npx vitest run __tests__/labor-actuals-boundary.test.ts
```
Pass criterion: with Sunday's hours ingested the week reports actual through 09-13 and
**no** schedule window; with them missing it still forecasts 09-13; the boundary never
moves earlier than today; an unreadable actuals date falls back to today.

Live: [screenshot](https://github.com/aditya2kx/jarvis/releases/download/evidence-screenshots/labor-weekly-actuals-boundary-20260914-000002.png)
— Wk of Sep 7 renders fully solid, Wk of Sep 14 / Sep 21 remain fully scheduled.
Stated plainly: that capture was taken at 00:00:02, so the calendar had also rolled;
it confirms the rendering but does not by itself separate the fix from midnight. The
BQ table above and the unit tests are what pin the behaviour at the reported moment.

The [hovered tooltip](https://github.com/aditya2kx/jarvis/releases/download/evidence-screenshots/labor-weekly-sep7-tooltip-actual-only-20260914-002617.png)
on that same bar is the direct comparison:

| | reported | after fix |
|---|---|---|
| Total (actual) | 152.9 | **181.2** |
| Total (scheduled) | 31.4 | *absent* |
| Total (combined) | 184.3 | *absent* |
| Goal line | `87.8% of goal` | `86.3% of goal · 34.4% of sales` |

The scheduled and combined rows are gone because no day in the week is a forecast any
more. The Goal line gaining `· 34.4% of sales` is the same fix surfacing through
`completedWeek = hasActual && !hasSched` — the chart now recognises the week as
finished, which is precisely what the operator observed it failing to do.

Model routing: **Composer**.
