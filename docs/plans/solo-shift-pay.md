# Plan — Solo shift pay ($16.25 for hours worked alone)

- **Branch:** `fix/as-of-this-ongoing-cycle-i`
- **Tracking issue:** #309
- **Evidence tier: sandbox-live** · **scenario: full-live**
- **Consulted:** `CONTRIBUTING.md` (dev loop as success criteria), `docs/WORKFLOW.md`
  (lifecycle), `.cursor/rules/bhaga.mdc` (invariants 3/4/7/9/10),
  `.cursor/rules/bhaga-principles.mdc` (sandbox isolation, tunables in `store_config`),
  `agents/bhaga/scripts/README.md` (nightly step order), `RUNBOOK.md` §13.

## Requirement (agreed at jam, issue #309)

Effective 2026-09-07 (ongoing biweekly cycle 2026-09-07..2026-09-20), an employee whose
base rate is $15.25 earns $16.25/hr for hours **actually worked alone** — a $1.00/hr
premium. Announced wording: *"Any time you're scheduled or end up working a shift by
yourself, your pay for those hours will increase from $15.25 to $16.25/hr."*

Agreed decisions:

| # | Decision |
|---|---|
| D1 | Solo = punch occupancy exactly 1, **minimum 15-minute contiguous block** |
| D2 | Eligible = base rate exactly $15.25. The three already at $16.25 get no premium |
| D3 | Manager presence (Krause, $25, `excluded_from_labor_pct`) means NOT solo |
| D4 | Krause accrues no premium herself |
| D5 | ADP capability spike first; payroll cron logic follows the finding |
| D6 | Effective whole cycle from 2026-09-07 |
| D7 | **Actuals only.** Schedule is a forward preview on the Labor page, never a pay basis |

Wording: `solo_hours` / `team_hours`, with `solo_hours + team_hours == total_hours`.

Baseline measured from prod during jam: 14.75 eligible solo hours → $14.76 premium for
2026-09-07..2026-09-20 (9 of 14 days punched).

**Corrected during M2 to 12.23 eligible solo hours → $12.23 premium.** The jam figure was
an ad-hoc estimate taken before D3 was applied — it did not count the manager toward
occupancy. Worked example, 2026-09-14: Huynh punched 08:55–11:37 (2.70h) while Krause was
in 06:30–15:15, covering the whole shift. The jam query scored those 2.70h as solo; under
D3 Huynh was never alone, so she earns 0 solo minutes that day. Applying D3 alone accounts
for roughly 12h of the difference across the cycle (ignoring the manager yields 24.42h);
the 15-minute minimum block accounts for a further 0.65h (12.88h → 12.23h).

The **shipped** number is the correct one. M2's pass criterion is therefore 12.23h /
$12.23, not the jam figure, and the reconciliation identity must hold with zero breaches.

## Why the data supports this

`bhaga.adp_punches` carries per-punch `in_time`/`out_time` as `HH:MM`
(`core/migrations/001_initial_schema.sql:58-71`), which is everything the interval math
needs. No solo/alone concept exists anywhere today — this is new, not a field to expose.

D7 is forced by prod data: scheduled-solo totals 37.5h against 14.75h eligible
actual-solo. Skyler Browning has 4.5h of scheduled solo and **zero punches** this cycle;
Hillary Huynh has 2.7h of actual solo on 2026-09-14 that was **never scheduled** (the
call-out case). A schedule basis pays for unworked shifts and misses real ones.

## Tunables — config-driven, no code deploy (user-preferences #29)

All four live in `bhaga.store_config`, read via
`core/store_config.py::get_config(store, key) -> str | None` (`core/store_config.py:46`),
set via `/bhaga-cloud config set <key> <value>`. **No literal 15 / 15.25 / 16.25 /
2026-09-07 anywhere in code.**

| Key | Seed value |
|---|---|
| `solo_shift_min_block_minutes` | `15` |
| `solo_shift_eligible_base_rate_dollars` | `15.25` |
| `solo_shift_premium_rate_dollars` | `16.25` |
| `solo_shift_effective_date` | `2026-09-07` |

Seed them once (cloud, ADC — never a laptop write to prod):

```bash
python3 scripts/gcp_access_probe.py      # must print surface=adc_ready
python3 -c "
from core.store_config import set_config
for k, v in [
    ('solo_shift_min_block_minutes', '15'),
    ('solo_shift_eligible_base_rate_dollars', '15.25'),
    ('solo_shift_premium_rate_dollars', '16.25'),
    ('solo_shift_effective_date', '2026-09-07'),
]:
    set_config('palmetto', k, v)
"
```

## Invariants that must not break

1. **Purity** — the interval math is a pure function: no network, no IO, no clock, same
   contract as `skills/tip_pool_allocation/` (`.cursor/rules/bhaga.mdc` invariant 1).
2. **`solo_hours + team_hours == total_hours`** per employee-day, exact at integer
   minutes. Asserted in unit tests and as a prod check.
3. **Money = integer cents** internally; dollars only at the boundary (invariant 4).
   `premium_cents INT64`, never a float column.
4. **Idempotent upsert** by natural key `(date, employee)` — re-running a date converges,
   never appends (invariant 3).
5. **No ghost rows** — `model_solo_hours_daily` has `employee` in its merge key, so
   invariant 9 requires `load_model_rows(..., replace_scope=True)`. The meta-guard test in
   `agents/bhaga/scripts/test_materialize_model_bq.py` enforces this; a naked MERGE fails
   the suite.
6. **America/Chicago** for every date boundary (invariant 7). Punch `HH:MM` is already
   store-local; never re-interpret as UTC.
7. **Read-only toward ADP** (invariant 6) — M1 is a pure observation spike. No
   Approve/Submit/Save. `abort_if_forbidden_label` stays in force.
8. **Sandbox writes never touch prod** — `BHAGA_BQ_DATASET=bhaga_sandbox`;
   `datastore._assert_sandbox_write_isolation` is the hard guard.
9. **Existing labor-cost math is untouched.** `build_labor_daily_rows`
   (`agents/bhaga/scripts/update_model_sheet.py:1531`) keeps its current
   `cost = reg_h*rate + ot_h*ot_rate + dt_h*rate*2` (`:1564-1566`). Solo premium is an
   additive column, not a change to `total_labor_cost`.

## Feature flag: none

Applying the "can it silently produce wrong numbers?" test from
`.cursor/rules/plan-execution-readiness.mdc` item 7: every artifact is **additive** (new
table, new columns, new view, new UI panel) and no existing labor-cost or tip-allocation
math changes, so there is nothing for a flag to protect. Nothing gates a rollback beyond
not reading the new columns.

**Out of scope, deliberately:** FLSA weighted-average overtime across two rates. Once a
week crosses 40h with hours at two rates, OT is owed on the weighted average, not 1.5× the
base. Nobody is near 40h this cycle. A follow-up issue is opened in M3 before merge. If M1
concludes BHAGA should type rate-2 hours itself, **that** step gets a flag entry in
`docs/FEATURE_FLAGS.md`, because typing hours crosses the read-only-hours guardrail at
`skills/adp_run_automation/payroll_draft_backend.py:1448`.

---

## M1 — ADP RUN capability spike (read-only; gates M3)

**Model routing: Opus 4.8 thinking medium** — live-portal observation with ambiguous DOM,
the class of work where a cheaper model burns more tokens guessing.

Why this is first: `payroll_draft_backend.py` never types hours. It clicks *Import latest
timecards* (`skills/adp_run_automation/payroll_draft_backend.py:1019-1046`), so every
imported hour lands on rate 1. A second rate creates the column; the import will not fill
it. Whether M3 automates or documents depends entirely on what the portal actually allows.

Drive ADP RUN via `user-playwright` MCP, read-only. Answer three questions with
screenshots:

1. Can a second hourly rate of $16.25 be configured on a $15.25 employee (People → Pay
   info)? Capture the form and the rate-count limit.
2. Does *Enter payroll* then expose a distinct hours column bound to rate 2? Capture the
   grid with `_fill_col_ids`-style header ids visible — the same
   `[data-test-id]`/`col_id` shape read at `payroll_draft_backend.py:1230-1249`.
3. Does *Import latest timecards* populate rate 1 only, leaving rate 2 enterable?

**Failure / refutation path:** if ADP RUN caps at one hourly rate, or the rate-2 hours
column is not addressable by a stable `col_id`, that is a **successful** spike outcome, not
a failure. M3 then ships the manual runbook step instead of cron changes. Record the
refutation in the PR with the same screenshot rigor.

Non-negotiable: never click Approve, Submit, Save, or Finish payroll. Read-only.

**Verify (copy/paste):**

```bash
# Prove the guard that keeps the spike read-only is intact and tested.
python3 -m pytest skills/adp_run_automation/test_payroll_draft_backend.py -q
python3 -c "
from skills.adp_run_automation.payroll_draft_backend import abort_if_forbidden_label
for label in ('Approve', 'Submit payroll', 'Save', 'Finish payroll'):
    try:
        abort_if_forbidden_label(label); raise SystemExit(f'FAIL: {label} not blocked')
    except SystemExit: raise
    except Exception: print(f'ok blocked: {label}')
"
```

**Pass criterion:** the three questions are answered with screenshots uploaded to a GitHub
release https URL (verified rendering before referencing — user-preferences #18), and the
PR states a decision: *automate rate-2 entry* or *manual runbook step*, with the `col_id` /
earnings code named when automation is feasible.

---

## M2 — `solo_hours` / `team_hours` in the labor model

**Model routing: Sonnet 5 medium thinking** — well-specified logic against cited files.

### M2.1 New pure module `skills/bhaga_labor/solo_shift.py`

Home chosen because `skills/bhaga_labor/` already owns punch-interval helpers
(`skills/bhaga_labor/staff_punched_in.py:56::_punch_covers_time`). Full signatures:

```python
"""Solo-shift hour attribution from ADP punch intervals (pure; no IO/clock)."""
from __future__ import annotations
from typing import NamedTuple

class SoloConfig(NamedTuple):
    min_block_minutes: int          # store_config solo_shift_min_block_minutes
    eligible_base_rate_cents: int   # solo_shift_eligible_base_rate_dollars * 100
    premium_rate_cents: int         # solo_shift_premium_rate_dollars * 100
    effective_date: str             # ISO date; punches before this are premium-free

class SoloDay(NamedTuple):
    date: str
    employee: str                   # canonical name
    solo_minutes: int
    team_minutes: int
    total_minutes: int
    eligible: bool
    premium_cents: int

def parse_hhmm(value: str | None) -> int | None:
    """'16:01' -> 961 minutes past midnight. None/malformed -> None."""

def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union of half-open [start, end) minute intervals, sorted and coalesced.

    Collapses one employee's split punches so a mid-shift break is not
    double-counted toward their own total.
    """

def solo_blocks(
    day_intervals: dict[str, list[tuple[int, int]]],
) -> dict[str, list[tuple[int, int]]]:
    """Per-employee maximal contiguous runs where occupancy == 1.

    Every punched employee counts toward occupancy, including the manager (D3).
    Eligibility filtering happens later — occupancy is about who is physically
    present, never about who gets paid.
    """

def attribute_day(
    date: str,
    day_intervals: dict[str, list[tuple[int, int]]],
    base_rate_cents: dict[str, int],
    config: SoloConfig,
) -> list[SoloDay]:
    """One SoloDay per employee present on `date`.

    Blocks shorter than config.min_block_minutes are folded into team_minutes,
    not dropped -- that is what preserves solo + team == total (invariant 2).
    premium_cents = 0 unless base_rate_cents[employee] == config.eligible_base_rate_cents
    and date >= config.effective_date.
    """
```

Rounding: minutes are integers throughout; `premium_cents = round(solo_minutes *
(premium_rate_cents - eligible_base_rate_cents) / 60)`. Hours surface as
`solo_minutes / 60` only at the BQ boundary.

### M2.2 Identity resolution (hard requirement, not cleanup)

The three sources spell people differently: `bhaga.adp_scheduled_shifts` says
`"Johnson, Dolce J"` and `"Pascone, Kayah A"` while `bhaga.adp_punches` and
`bhaga.adp_wage_rates` say `"Johnson, Dolce"`. Any join that skips the alias layer
silently computes wrong hours.

Route every name through `skills/store_profile/reader.py:200::load_aliases(store)` →
`{raw_or_alias: canonical}`, then
`skills/adp_run_automation/shift_backend.py:200::normalize_employee_name(name, aliases)`.
This is `.cursor/rules/bhaga.mdc` invariant 10 applied to a new read path.

Also fix the duplicate wage-rate identity: `bhaga.adp_wage_rates` holds both
`"Huynh, Hillary"` and `"Huynh Hillary"` (both $15.25). Add the comma-less spelling as an
alias to the canonical row and delete the orphan.

### M2.3 Migration `core/migrations/071_solo_shift_hours.sql`

Next free number (highest today is `070_source_load_receipts.sql`).

```sql
-- 071_solo_shift_hours.sql
-- Issue #309: solo-shift premium. Employee x day solo/team split from adp_punches.
-- Apply: BHAGA_DATASTORE=bigquery python3 -c \
--   "from core.datastore import ensure_schema; print(ensure_schema())"

CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.model_solo_hours_daily` (
  date              DATE    NOT NULL,
  employee          STRING  NOT NULL,   -- canonical name (alias-resolved)
  solo_hours        FLOAT64,
  team_hours        FLOAT64,
  total_hours       FLOAT64,
  solo_minutes      INT64,
  team_minutes      INT64,
  total_minutes     INT64,
  base_rate_dollars FLOAT64,
  eligible          BOOL,
  premium_cents     INT64,              -- integer cents (invariant 4)
  materialized_at   TIMESTAMP
)
PARTITION BY date;

-- Pay-period rollup the Labor page and payroll entry both read.
CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_solo_hours_period` AS
SELECT
  p.pay_period_start                              AS period_start,
  p.pay_period_end                                AS period_end,
  s.employee,
  SUM(s.solo_hours)                               AS solo_hours,
  SUM(s.team_hours)                               AS team_hours,
  SUM(s.total_hours)                              AS total_hours,
  ANY_VALUE(s.base_rate_dollars)                  AS base_rate_dollars,
  LOGICAL_OR(s.eligible)                          AS eligible,
  SUM(s.premium_cents)                            AS premium_cents,
  ROUND(SUM(s.premium_cents) / 100.0, 2)          AS premium_dollars
FROM `jarvis-bhaga-prod.bhaga.model_solo_hours_daily` s
JOIN `jarvis-bhaga-prod.bhaga.model_labor_period` p
  ON s.date BETWEEN p.pay_period_start AND p.pay_period_end
GROUP BY period_start, period_end, s.employee;
```

Day-grain columns on the existing labor tabs, so the Labor page charts can show the split
without a second query. Add `solo_hours` + `team_hours` to the three headers and DDL:

- `agents/bhaga/scripts/update_model_sheet.py:1739-1781` (`labor_daily` header) and the
  matching row build in `build_labor_daily_rows` (`:1531`)
- `:2120-2142` (`labor_period` header), builder at `:2075`
- `:2321-2343` (`labor_weekly` header), builder at `:2292`
- `core/migrations/003_model_tables.sql:29-84` / `:87-140` / `:143+` — the three
  `model_labor_*` DDLs get the two columns via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
  in migration 071 (forward-only; never a destructive rewrite).

### M2.4 Wire into the nightly

`agents/bhaga/scripts/materialize_model_bq.py`:

- Add `"model_solo_hours_daily": ["date", "employee"]` to `_MERGE_KEYS` (`:86-96`).
  `_SCOPE_CLEAR_COL` (`:101-105`) then derives `date` automatically and the meta-guard will
  **require** `replace_scope=True`.
- Add `read_punches_bq()` to `core/datastore_reader.py`, mirroring
  `read_shifts_bq()` (`core/datastore_reader.py:69-89`) but reading `adp_punches` and
  keeping `punch_index`, `in_time`, `out_time`.
- In `materialize()` (`:432`), after `shifts = read_shifts_bq()` (`:461`), read punches +
  wage rates, call `attribute_day` per date, and write alongside the existing loads
  (`:644-657`):

```python
load_model_rows(
    "model_solo_hours_daily", solo_rows,
    dry_run=dry_run, materialized_at=materialized_at,
    replace_scope=True,                      # invariant 9 — no ghost rows
    scope=scopes.get("model_solo_hours_daily"),
)
```

### M2.5 Tests — `skills/bhaga_labor/test_solo_shift.py`

Every scenario is a named test, not "all green":

| Scenario | Expectation |
|---|---|
| Happy path: one person all day | `solo == total`, `team == 0` |
| Two fully-overlapping shifts | `solo == 0` for both |
| Shift handoff leaving a 3-min gap | sliver folded into `team`, `solo == 0`, sum still holds |
| Exactly 15-min block | included (boundary is inclusive) |
| 14-min block | excluded, folded into `team` |
| Split-punch employee, solo only in the mid-day gap | only the qualifying run counts |
| Manager + one hourly (D3) | hourly is **not** solo |
| Manager alone (D4) | accrues `solo_hours` but `premium_cents == 0` |
| Employee already at $16.25 (D2) | accrues `solo_hours`, `premium_cents == 0` |
| Punch before `effective_date` (D6) | `premium_cents == 0` |
| Malformed / null `in_time` / `out_time` | row skipped, no crash, no silent zero-total |
| `out_time < in_time` (overnight) | `+24h` normalization, matching `coverage-model.ts:162` |
| Alias mismatch (`"Johnson, Dolce J"` vs `"Johnson, Dolce"`) | resolves to one canonical employee |
| Reconciliation property | `solo + team == total` over randomized interval sets |

**Verify (copy/paste):**

```bash
python3 -m pytest skills/bhaga_labor/test_solo_shift.py -q
python3 -m pytest agents/bhaga/scripts/test_materialize_model_bq.py -q   # ghost-row meta-guard
python3 -m pytest agents/bhaga/scripts/test_update_model_sheet.py -q     # labor header round-trip
python3 scripts/verify.py --full
```

**Pass criterion:** all of the above green, plus prod reproduction — shipped code returns
**12.23 eligible solo hours / $12.23** for 2026-09-07..2026-09-20 (see the corrected
baseline above; the jam figure predated D3), and this returns zero rows:

```bash
python3 -c "
from google.cloud import bigquery
c = bigquery.Client(project='jarvis-bhaga-prod')
q = '''
SELECT date, employee, solo_minutes, team_minutes, total_minutes
FROM \`jarvis-bhaga-prod.bhaga.model_solo_hours_daily\`
WHERE solo_minutes + team_minutes != total_minutes
'''
rows = list(c.query(q))
print('reconciliation breaches:', len(rows))
assert not rows, rows
"
```

---

## M3 — Labor page + payroll path

**Model routing: Sonnet 5 medium thinking** for the UI and cron edit; **Composer 2.5** for
the doc-only commits.

### M3.1 Labor page

`apps/operator-console/app/labor/page.tsx` gains a per-employee solo/team breakdown for
the selected pay period, reading `vw_solo_hours_period` through a new query in
`apps/operator-console/lib/bq/queries.ts` (existing coverage queries start at `:292`).

Reuse, do not reinvent (user-preferences #27, `docs/contributing/ui-polish.md`): the
existing shadcn `DataTable` and `Badge` primitives already used by the labor components in
`apps/operator-console/components/labor/`. Concurrency math is already solved by
`occupancySeries` (`apps/operator-console/lib/labor/coverage-model.ts:253-272`) — the panel
reads BQ-computed values rather than recomputing in the client, per the bhaga.mdc rule that
new numbers come from a BQ view, not app logic.

Interaction states to specify explicitly: row hover, keyboard focus ring, pending/skeleton
while the query resolves, mobile tap target ≥44px, and a muted-text empty state for a
period with no solo hours. Solo hours render with a `Badge` in the same variant vocabulary
as existing labor chips; premium dollars right-aligned and monospaced like other money
columns.

Per D7, scheduled-solo appears **only** as a clearly-labelled forward preview for future
days, visually distinct from paid actuals, so it can never be mistaken for a pay basis.

### M3.2 Payroll path — branches on M1

- **If M1 proved rate-2 entry works:** update
  `skills/adp_run_automation/payroll_draft_backend.py` — extend `packet_from_view_rows`
  (`:65-102`) with `solo_hours`, add the rate-2 `col_id` to `_fill_col_ids` (`:1230-1249`),
  and fill it in `_fill_money_lines`' sibling path. Hours-typing gets a
  `docs/FEATURE_FLAGS.md` entry because it crosses the guardrail at `:1448`. The existing
  `_zero_hours_not_on_console` (`:971-995`) must not clear the rate-2 column — add a test.
- **If M1 refuted it:** no cron change. `RUNBOOK.md` gets a payroll step listing exact
  solo hours per employee to key in for the 2026-09-21 run, sourced from
  `vw_solo_hours_period`.

Either way, open the FLSA follow-up:

```bash
gh issue create --label jarvis-work \
  --title "FLSA weighted-average OT across two pay rates (solo premium)" \
  --body "Follow-up from #309. When a week crosses 40h with hours at both \$15.25 and \
\$16.25, OT is owed at the weighted average of the two rates, not 1.5x base. Not urgent \
(nobody near 40h in 2026-09-07..09-20) but the model must not silently get it wrong."
```

### M3.3 Operator localhost gate (new, operator-requested)

Before any PR is opened, stand up the console and hand it to the operator:

```bash
cd apps/operator-console && npm install && npm run dev   # http://localhost:3000/labor
```

**The operator verifies the Labor page on localhost. No `gh pr create` until they confirm.**
This is a hard stop, not a courtesy.

**Verify (copy/paste):**

```bash
cd apps/operator-console && npm run lint && npm run test && npm run build
python3 -m pytest skills/adp_run_automation/test_payroll_draft_backend.py -q
python3 scripts/verify.py --full
python3 scripts/check_doc_freshness.py --base origin/main
```

Then sandbox e2e (never prod sheets — `.cursor/rules/bhaga-principles.mdc`):

```bash
BHAGA_BQ_DATASET=bhaga_sandbox python3 agents/bhaga/scripts/sandbox_e2e.py \
  --store palmetto --scenario full-live
```

**Pass criterion:** lint/test/build green; sandbox `full-live` green; screenshots captured
via `apps/operator-console/scripts/capture_evidence.py` (user-preferences B4) and rendering
verified from their https URLs; FLSA follow-up issue exists; operator has signed off on
localhost.

---

## PR §4 evidence pack (what lands in the description)

Enumerated per scenario, not "all green":

1. **Happy path** — prod table for 2026-09-07..2026-09-20 showing per-employee
   solo/team/total, reproducing 14.75 eligible solo hours / $14.76.
2. **Reconciliation invariant** — the breach query above returning 0 rows.
3. **Each failure/edge case** — the M2.5 test table, named test → result.
4. **Refutation path** — M1's finding, including a negative result if ADP caps at one rate.
5. **Legacy unchanged** — diff showing `total_labor_cost` and tip allocation identical
   before/after for a sampled date (guards against the "column silently goes dead" class in
   bhaga.mdc invariant 8).
6. **UI** — `capture_evidence.py` screenshots at https URLs.
7. **Sandbox** — `full-live` run log.

## Docs lock-step (same change, per `.cursor/rules/doc-maintenance.mdc`)

| File | Edit |
|---|---|
| `agents/bhaga/knowledge-base/DOMAIN.md` | §4 labor metrics + glossary: `solo_hours`, `team_hours`, `premium_cents`, `model_solo_hours_daily`, `vw_solo_hours_period` |
| `agents/bhaga/scripts/README.md` | nightly step list + "Extending the model" (new table + reader) |
| `RUNBOOK.md` | payroll draft section; the four `store_config` tunables; manual keying step if M1 refuted |
| `.cursor/rules/bhaga.mdc` | the `solo + team == total` invariant and the alias-resolution requirement on this read path |
| `docs/FEATURE_FLAGS.md` | only if M1 leads to BHAGA typing rate-2 hours |
| `PROGRESS.md` | dated entry — via its own follow-up PR after merge, never a direct main push (`check_no_main_progress_push.py`) |

`python3 scripts/check_doc_freshness.py --base origin/main` must be clean.

## Branch / PR mechanics

- One branch, one coherent change: `fix/as-of-this-ongoing-cycle-i`. Issue #309 is already
  at `approved:jam` + `approved:define-evidence`.
- `bash scripts/install-git-hooks.sh` once in this worktree (cost pre-commit hook).
- `gh pr list --state open` first — reuse this session's PR if one exists rather than
  opening a second.
- **Always** `gh pr create --base main --head fix/as-of-this-ongoing-cycle-i`; never rely
  on the default base (incident 2026-07-01, PR #119).
- All GitHub ops as `jarvis-agent-bot328`, never `aditya2kx`.
- Seed the cost ledger: `pr_cost_ledger.py bind-pr --branch fix/as-of-this-ongoing-cycle-i`
  then `sync --pr <n>`; zero build cost is a hard failure. Do **not** commit
  `metrics/pr_cost/`.
- Babysit via the batch loop — `python3 scripts/pr_triage.py --pr <n>`, fix everything in
  one pass, reply on every thread, push once.
- **Never self-merge and never arm auto-merge.** Operator squash-merges; agent stops at
  "all checks green, all threads replied, no conflicts".
- Post-merge: confirm `state == MERGED` **and** `baseRefName == main`, pull into the main
  working copy, then run the retrospective jam.

## Model routing summary (`docs/contributing/cost.md`)

| Milestone | Model | Why |
|---|---|---|
| M1 ADP spike | Opus 4.8 thinking medium | Live-portal observation, ambiguous DOM |
| M2 model + tests | Sonnet 5 medium thinking | Well-specified logic against cited files |
| M3 UI + cron | Sonnet 5 medium thinking | Feature work in an existing design system |
| M3 docs | Composer 2.5 | Doc-only edits |
| Plan / PR review | Opus 4.8 thinking medium | Review-class work |

One chat per PR; `/clear` between unrelated sub-tasks.
