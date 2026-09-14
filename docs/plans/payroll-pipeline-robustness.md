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

## Milestone 3 — Scope the recompute to the request

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

## Milestone 4 — Breaker and portal tell the truth

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

## Milestone 5 — ADP scraper resilience

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

## Milestone 6 — Hygiene

- Plaid `INVALID_API_KEYS` — separate expired credential surfaced during diagnosis.
- Deduplicate the repeating review-anomaly alert.

Verify: `python3 -m skills.credentials.registry audit` reports no expired entries;
two consecutive nightlies produce one review-anomaly Slack message, not N.

Model routing: **Composer**.

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

Per `user-preferences.mdc` J5 this reuses the existing `full-live` scenario rather
than adding a dedicated one — it already exercises the changed materialize path.
Per J2, acceptance evidence covers **both** sandbox e2e and prod ADP/Square live
verification. Per J3, the backfill re-runs **all** pending dates (2026-09-07 →
2026-09-13), not only the most recent.
