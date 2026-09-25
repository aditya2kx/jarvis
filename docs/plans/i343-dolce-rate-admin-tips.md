# Issue #343 — Effective-dated wage rates + Admin-punch tip exclusion

- **Evidence tier: sandbox-live** · **scenario: full-live** (date `2026-09-22`, the first Admin punch)
- **Branch:** `fix/i-have-updated-dolce-s-rate` · tracking issue #343
- **Consulted:** `CONTRIBUTING.md` (dev loop as success criteria), `docs/WORKFLOW.md`,
  `.cursor/rules/bhaga.mdc` (invariants 2/3/4/7/9/10/12/13), `.cursor/rules/bhaga-principles.mdc`
  (sandbox isolation, tunables in `store_config`), `agents/bhaga/knowledge-base/DOMAIN.md`
  (`wage_rates`, tip exemptions), `docs/plans/i167-tip-exemptions.md` (window math),
  `RUNBOOK.md` §13 (sandbox-live).

## Requirement (agreed at jam 2026-09-25)

Dolce (`Johnson, Dolce`) is $18.00/h from Monday 2026-09-21 (assistant manager). When she does
admin work the operator adds a separate ADP punch with a note; that punch earns **no tips** but is
**paid at her normal rate**. Closed periods keep the rate that applied then.

## Diagnosis (read-only, 2026-09-25)

| Finding | Evidence |
|---|---|
| Prod rate still $16.25 (`rate_source=earnings`) | `adp_wage_rates` row for `Johnson, Dolce` |
| Nightly pay_info refuses her every night | `gs://bhaga-scrape-cache/2026-09-24/evidence/adp-pay-info-Johnson_Dolce-fail-*.meta.txt`: `AmbiguousEmployeeError: no Directory record is exactly 'Johnson, Dolce'; closest are ['Johnson, Dolce J']` — raised by `select_directory_match` at `skills/adp_run_automation/pay_info_backend.py:304-328`, called at line 403 |
| Alias table already ties the spellings | `employee_aliases`: `Johnson, Dolce J` → `Johnson, Dolce` (and 3 other spellings) |
| One rate per employee, applied to every period | `core/migrations/068_employee_perks_pay_period.sql:313-314` (payroll), `069_labor_cost_live_rates.sql:37-38, 96-97` (live labor), `apps/operator-console/lib/bq/queries.ts:92-93` (hour-grain cost) |
| Earnings load MERGEs `adp_wage_rates` by `employee_id` (Mon/Tue), so it would put $16.25 back until her first $18 check | `agents/bhaga/scripts/backfill_from_downloads.py:566-567` |
| ADP punch **Notes** are dropped | `skills/adp_run_automation/shift_backend.py:239,246` (not in `_REQUIRED_DETAILS_COLUMNS`; not in the record at lines 357-370); `adp_punches` has no note column |
| Note format (live Timecard export, pay period 2026-09-21→10-04) | `"Bipinchandra Parikh, Aditya   Admin Work"` (Tue 09/22 3:00–3:30 PM) and `"…   Admin work"` (Fri 09/25 10:00–10:30 AM). Pattern = `<editor "Last, First">` + 3 spaces + free text; case varies. Other notes exist and must NOT exclude tips: `"…   Updated missing close out as per scheduled shift timings"` (Huynh 09/14, Dolce 09/18) |
| Admin time is its own punch | both Admin rows are standalone 30-min punches; the 09/22 one is not yet in BQ (added after the 09/24 nightly — the nightly reloads the whole open pay period, `backfill_from_downloads.py:342` `_in_window` is unbounded in the nightly call at `daily_refresh.py:2308-2312`) |
| Scoped materialize is ON in prod (`BHAGA_SCOPED_MATERIALIZE=1`), model write scope = `ingested_dates(gap_start, refresh_date)` at `daily_refresh.py:3433-3441` | A retro Admin note for an earlier day in the open period lands in raw but that day's `model_tip_alloc_daily` rows are not rewritten |

## Design

### A. pay_info: alias-aware Directory match
`select_directory_match(candidates, search_name, *, accepted_names=())` — exact match on
`search_name` first (unchanged); if none, exact match on any name in `accepted_names` (spellings the
alias table maps to the **same** canonical, rendered through `directory_search_name`). Still raises
`AmbiguousEmployeeError` on >1 exact hit or near-miss-only. `scrape_one_pay_info` gains
`accepted_names` and uses the returned name for the click-through locator (line 419).

### B. Effective-dated wage rates
New table `adp_wage_rate_history` (append-only, one row per rate change) + view
`vw_wage_rate_effective` (`effective_from`/`effective_to` via `LEAD`). Seeded once from current
`adp_wage_rates` at `effective_date = 2000-01-01` (so every employee without a recorded change keeps
today's numbers **bit-identical**). The nightly pay_info write appends a row **only when the scraped
rate differs from the latest history row** (never compared against `adp_wage_rates`, which the
earnings load rewrites — that is what stops flapping from minting false change dates).

Build-time addition (2026-09-25): the **earnings load also appends** (`history_rows_from_earnings`,
hooked after the `adp_wage_rates` MERGE in `backfill_from_downloads._load_adp_rates`, soft-fail with
`BREADCRUMB wage_rate_history_earnings_failed`). Several employees fail the pay_info scrape nightly
and only get rates from paychecks; without this their seed rate would freeze. A paid rate is
effective at its newest check's `period_start`, and only if that is **after** the latest history
date — so Dolce's 09-07..09-20 check at $16.25 cannot undo the 09-21 $18 row.

Effective date rule (`resolve_effective_date`, pure):
1. `added_on` from the Payroll info page, **if** it is within the current or previous pay period and
   after the latest history row's date (guards against "Added on" being a hire date);
2. else the first day of the pay period containing today (America/Chicago) — ADP RUN pays a whole
   check at one rate, so a raise first seen mid-period starts at that period's start;
3. operator override: `python3 -m skills.adp_run_automation.wage_rate_history set …` writes a
   `source='operator'` row; runtime, no deploy (user-preferences #29).

Readers switch to rate-on-shift-date: `vw_model_payroll_period` (wages summed per rate segment),
`vw_labor_daily_live` / `vw_labor_weekly_live`, and `queries.ts` hour-grain cost. Labor-bucket flags
(`is_salaried`, `excluded_from_labor_pct`) still come from `adp_wage_rates`.

### C. Admin punches → tip exclusion
- Ingest: `parse_xlsx` keeps `note`; `map_adp_punch` + `adp_punches.note STRING`; `read_punches_bq`
  returns it.
- Match (operator decision 2026-09-25): case-insensitive substring — any keyword from
  `store_config` key `tip_exempt_punch_note_keywords` (semicolon list, default `admin`) appearing
  anywhere in the note, whoever left it. `"Updated missing close out"` does not match.
- Model: matching punches become extra exempt windows `(in_time, out_time)` for `(employee, date)`.
  `_eligible_tip_hours_for_shift` subtracts the **union** of the existing Tip Exemption window (if
  any) and these windows from the shift — no double subtraction if the operator also entered a
  console window. Whole-day exemption still wins. Wages untouched (payroll/labor use `adp_shifts`
  hours, `061_payroll_paid_hours.sql`).
- Scope: `ingested_dates` widens to the open pay period's start so retro notes rewrite their day.

## Invariants preserved
- Allocation stays pure; `allocate()` unchanged (it already takes fractional `daily_hours`).
- Pool-by-day fairness + largest-remainder residuals: the admin half-hour leaves the day's
  denominator, the day's pool is fully redistributed (conservation asserted by `model_semantics`).
- Idempotent upserts: history MERGE key `(employee_id, effective_date)`; punches keep
  `(date, employee_id, punch_index)`; seed is `INSERT … WHERE NOT EXISTS`.
- Integer cents for tips; wages stay `NUMERIC` half-up at the view boundary (bit-identical for a
  single-rate period, because segment hours are rounded exactly like today's period hours).
- America/Chicago for "today" and pay-period math. Read-only toward ADP (scrape only).
- No raw names in comparisons (invariant 10): accepted Directory names come from the alias map;
  punches are alias-normalized before keying windows.
- Invariant 12 unaffected: `_base_rate_from_history` untouched; the earnings history row uses the
  rate it already chose (lowest Regular on the newest check), so the solo premium never lifts it.
- Sandbox isolation: sandbox-live writes `bhaga_sandbox` only (`datastore._assert_sandbox_write_isolation`).
- Backward-compatible defaults: every new parameter defaults to `None` → existing behavior;
  `update_model_sheet.main` (sandbox replay path) passes nothing and is unchanged.

## Feature-flag decision
No new flag. The "can it silently produce wrong numbers?" test: (A) only widens an exact match to
alias-confirmed spellings; (B) is bit-identical for every employee until a real change row lands;
(C) only fires on notes matching a configurable keyword, and the keyword list itself is the runtime
kill switch (`/bhaga-cloud config set tip_exempt_punch_note_keywords ""` disables it). Documented
as N/A in `docs/FEATURE_FLAGS.md`. Out of scope: forward-looking scheduled-cost queries
(`queries.ts:1148-1285`) keep the current rate (correct for future dates); stopping the earnings
load from overwriting `adp_wage_rates` → follow-up issue; showing Admin punches in the console Tip
Exemptions table → follow-up issue.

---

## Milestone 1 — Directory match + rate history table (Sonnet)

**Files**
- `core/migrations/074_wage_rate_history_punch_notes.sql` (new):

```sql
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.adp_wage_rate_history` (
  employee_id       STRING NOT NULL,
  effective_date    DATE   NOT NULL,
  wage_rate_dollars FLOAT64,
  ot_rate_dollars   FLOAT64,
  source            STRING,     -- seed | pay_info | operator
  observed_at_utc   TIMESTAMP,
  note              STRING
);

INSERT INTO `jarvis-bhaga-prod.bhaga.adp_wage_rate_history`
  (employee_id, effective_date, wage_rate_dollars, ot_rate_dollars, source, observed_at_utc, note)
SELECT w.employee_id, DATE '2000-01-01', w.wage_rate_dollars, w.ot_rate_dollars,
       'seed', CURRENT_TIMESTAMP(), 'migration 074 seed from adp_wage_rates'
FROM `jarvis-bhaga-prod.bhaga.adp_wage_rates` w
WHERE w.wage_rate_dollars IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM `jarvis-bhaga-prod.bhaga.adp_wage_rate_history` h
    WHERE h.employee_id = w.employee_id
  );

CREATE OR REPLACE VIEW `jarvis-bhaga-prod.bhaga.vw_wage_rate_effective` AS
SELECT
  employee_id,
  effective_date AS effective_from,
  COALESCE(
    DATE_SUB(LEAD(effective_date) OVER (PARTITION BY employee_id ORDER BY effective_date),
             INTERVAL 1 DAY),
    DATE '9999-12-31'
  ) AS effective_to,
  wage_rate_dollars,
  ot_rate_dollars,
  source
FROM `jarvis-bhaga-prod.bhaga.adp_wage_rate_history`;

ALTER TABLE `jarvis-bhaga-prod.bhaga.adp_punches` ADD COLUMN IF NOT EXISTS note STRING;
```

- `skills/adp_run_automation/pay_info_backend.py`
  - line 304 `select_directory_match(candidates: list[str], search_name: str, *, accepted_names: Iterable[str] = ()) -> str`
  - new `def accepted_directory_names(canonical: str, aliases: dict[str, str]) -> list[str]` — every
    alias key whose value == `canonical`, mapped through `directory_search_name`, deduped, excluding
    `search_name` itself.
  - line 375 `scrape_one_pay_info(page, canonical_name, *, dashboard_url, accepted_names=())`; use
    the returned match (not `search_name`) at line 419 for the profile-link locator.
  - line 511 `scrape_pay_info_rates(..., aliases: Optional[dict] = None)` → builds `accepted_names`
    per employee (callers: nightly bundle in `runner.py` and `main()` line 920 load aliases via
    `skills.store_profile.load_aliases(store)`).
- `skills/adp_run_automation/wage_rate_history.py` (new; stdlib + `core.datastore`):

```python
def pay_period_start(d: datetime.date, *, anchor_end: datetime.date, length_days: int = 14) -> datetime.date: ...
def resolve_effective_date(*, today_ct: datetime.date, added_on: str | None,
                           latest_effective: datetime.date | None,
                           anchor_end: datetime.date, length_days: int = 14) -> datetime.date: ...
def latest_history_bq() -> dict[str, dict]: ...          # employee_id -> {effective_date, wage_rate_dollars}
def history_rows_for_changes(rates: list[dict], latest: dict[str, dict], *,
                             today_ct: datetime.date, anchor_end: datetime.date) -> list[dict]: ...
def write_history_rows(rows: list[dict], *, dry_run: bool = False) -> int:  # load_rows merge_keys=["employee_id","effective_date"]
def main(argv=None) -> int:  # argparse: `set --employee --effective --rate [--ot-rate] [--note]`, `show --employee`
```
  - Anchor comes from `palmetto.json` `adp_run.pay_periods_anchor_end_date` (`2026-05-17`, biweekly).
  - `write_pay_info_rates_bq` (line 766) calls `history_rows_for_changes` + `write_history_rows`
    after the existing MERGE, and prints
    `[pay_info] BREADCRUMB wage_rate_effective name=<n> rate=<r> effective=<d> basis=<added_on|period_start>`.
  - Name-normalize the operator CLI's `--employee` via `model_inputs.normalize_input_name`.

**Tests** — `skills/adp_run_automation/test_pay_info_backend.py` + new `test_wage_rate_history.py`:
happy path `Johnson, Dolce` with candidates `['Johnson, Dolce J']` + accepted → returns
`'Johnson, Dolce J'`; failure: accepted name that maps to another canonical → still raises; two
exact accepted hits → raises; `pay_period_start(2026-09-25) == 2026-09-21`,
`(2026-09-20) == 2026-09-07`; `added_on` = hire date → falls back to period start; `added_on` =
2026-09-21 → used; unchanged rate → no history row; first observation without history → effective
`2000-01-01`.

**Verify**
```bash
python3 -m pytest skills/adp_run_automation/test_pay_info_backend.py skills/adp_run_automation/test_wage_rate_history.py -q
python3 scripts/verify.py --quick
```
Pass criterion: all green; `select_directory_match` guard tests from payroll-pipeline-robustness still pass.

## Milestone 2 — Readers use rate-on-date (Sonnet; Opus review of the view SQL)

**Files**
- `core/migrations/075_payroll_labor_effective_rates.sql` (new) — copies 068's
  `vw_model_payroll_period` and 069's two live views with these edits:
  - payroll `shift_hours` CTE (068 lines 93-122) becomes segment-based:

```sql
seg AS (
  SELECT p.period_start, p.period_end, p.is_open, s.canonical_name AS employee,
         COALESCE(r.wage_rate_dollars, w.wage_rate_dollars) AS rate,
         COALESCE(r.ot_rate_dollars,  w.ot_rate_dollars)  AS ot_rate,
         ROUND(SUM(<same hours expr as 068 lines 100-114>), 2) AS seg_hours,
         ROUND(SUM(COALESCE(s.ot_hours, 0)), 2)                AS seg_ot
  FROM periods p
  JOIN `jarvis-bhaga-prod.bhaga.adp_shifts` s
    ON s.date BETWEEN p.period_start AND p.hours_end AND IFNULL(s.canonical_name, '') != ''
  LEFT JOIN `jarvis-bhaga-prod.bhaga.vw_wage_rate_effective` r
    ON r.employee_id = s.canonical_name AND s.date BETWEEN r.effective_from AND r.effective_to
  LEFT JOIN `jarvis-bhaga-prod.bhaga.adp_wage_rates` w ON w.canonical_name = s.canonical_name
  GROUP BY 1, 2, 3, 4, 5, 6
),
shift_hours AS (
  SELECT period_start, period_end, is_open, employee,
         SUM(seg_hours) AS hours_worked, SUM(seg_ot) AS ot_hours,
         IF(COUNTIF(rate IS NULL) > 0, NULL, SUM(
           CAST(GREATEST(seg_hours - seg_ot, 0) AS NUMERIC) * CAST(rate AS NUMERIC)
           + CAST(seg_ot AS NUMERIC) * COALESCE(CAST(ot_rate AS NUMERIC), CAST(rate AS NUMERIC) * 1.5)
         )) AS est_wages
  FROM seg GROUP BY 1, 2, 3, 4
),
rate_at_end AS (   -- displayed rate + fallback for tip rows with no ADP shift hours
  SELECT p.period_start, r.employee_id AS employee, r.wage_rate_dollars, r.ot_rate_dollars
  FROM periods p JOIN `jarvis-bhaga-prod.bhaga.vw_wage_rate_effective` r
    ON p.hours_end BETWEEN r.effective_from AND r.effective_to
)
```
    `tip_rows` / `punch_rows` / `carry_rows` carry `sh.est_wages` (NULL for carry). Final SELECT:
    `wage_rate_dollars = COALESCE(rae.wage_rate_dollars, w.wage_rate_dollars)`, and every wage
    expression (068 lines 245-257, 264-276, 287-300) becomes
    `COALESCE(r.est_wages, <068 expression using the as-of-end rate>)`, keeping `ROUND(…, 2)` on
    NUMERIC.
  - live views: add the same `LEFT JOIN vw_wage_rate_effective r` (on `employee_id` +
    `s.date BETWEEN`) and use `COALESCE(r.wage_rate_dollars, w.wage_rate_dollars)` in the two cost
    SUMs (069 lines 26-35 and 85-94).
- `apps/operator-console/lib/bq/queries.ts:84-95` — same join/COALESCE for `wage`.
- `scripts/check_live_labor_cost.py:51-57` — point `_MIGRATION` at 075 and also require
  `vw_wage_rate_effective`.

**Tests** — new `core/test_migration_075.py` (structural, like the 071/072 tests): payroll view
references `vw_wage_rate_effective`, keeps NUMERIC half-up `ROUND`, keeps 1:1 roster CTEs; live
views join on date range. BQ dry-run of each `CREATE VIEW` against `bhaga_sandbox`.

**Verify**
```bash
python3 -m pytest core/test_migration_075.py -q
python3 scripts/check_live_labor_cost.py
cd apps/operator-console && npm test && npm run lint && npm run build
```
Pass criterion: green; in `bhaga_sandbox` with only seed rows, `vw_model_payroll_period` for
2026-09-07..20 is identical to prod (0 row diffs on `est_gross_pay`, `wage_rate_dollars`).

## Milestone 3 — Admin notes → tip exclusion + scope widening (Sonnet)

**Files**
- `skills/adp_run_automation/shift_backend.py:357-370` — add `"note": str(row.get("Notes") or "").strip()`;
  update the comment at lines 245-249 (Notes is now read, still optional).
- `agents/bhaga/scripts/backfill_bigquery.py:205-228` `map_adp_punch` — add `"note": str(rec.get("note") or "")`.
- `core/datastore_reader.py:111` `read_punches_bq` — select `note`, return key `note`.
- `agents/bhaga/scripts/update_model_sheet.py` (next to `_overlap_hours`, line 1420):

```python
def punch_note_matches(note: str, keywords: list[str]) -> bool: ...
def tip_exempt_windows_from_punches(
    punches: list[dict], keywords: list[str]
) -> dict[tuple[str, str], list[tuple[str, str]]]: ...    # (employee, date) -> [(in, out), ...]
def _union_overlap_hours(in_time: str, out_time: str, windows: list[tuple[str, str]]) -> float: ...
```
  - `_eligible_tip_hours_for_shift` (line 803) gains `punch_exempt_windows: dict | None = None`;
    when the key has windows: whole-day mark → 0; else
    `max(0, total - _union_overlap_hours(in, out, [training_window?] + punch_windows))`.
    No windows → existing code path unchanged.
  - `build_daily_rows` (line 1331) and `build_period_results` (line 2501) accept and pass
    `punch_exempt_windows=None` (call sites lines 1349, 2530).
- `agents/bhaga/scripts/materialize_model_bq.py:597-760` — alias-normalize punches, read
  `get_config(store, "tip_exempt_punch_note_keywords")` (default `"admin"`, `;`-split, blank →
  disabled), build windows, pass to both builders, print
  `[materialize] BREADCRUMB admin_punch_windows n=<k> keys=<first 5>`.
- `agents/bhaga/scripts/daily_refresh.py:3433-3441` — add
  `(pay_period_start(refresh_date, anchor_end=…), refresh_date)` to `extra_windows` so retro notes
  in the open period rewrite their day (pure; reuse `wage_rate_history.pay_period_start`).

**Tests** — `agents/bhaga/scripts/test_update_model_sheet.py` (or new `test_admin_punch_tips.py`)
+ `skills/adp_run_automation/test_shift_backend.py` + `agents/bhaga/scripts/test_daily_refresh.py`:
- happy path: reproduction of 2026-09-22 (Dolce 06:50–10:42, 11:11–13:45, 15:00–15:30 "Admin Work"
  + two co-workers): Dolce's eligible hours drop by exactly 0.5; day pool conserved to the cent;
  co-workers' shares rise.
- `"Updated missing close out …"` note → no change. Case variants `Admin work` / `ADMIN` match.
- Admin window overlapping a console window → subtracted once. Whole-day exemption + admin → 0.
- keywords blank → identical output to today (snapshot of `build_period_results`).
- parser: Notes column absent → `note == ""`; present → kept.
- `ingested_dates` includes 2026-09-21 for refresh_date 2026-09-25.

**Verify**
```bash
python3 -m pytest agents/bhaga/scripts/test_update_model_sheet.py agents/bhaga/scripts/test_admin_punch_tips.py skills/adp_run_automation/test_shift_backend.py agents/bhaga/scripts/test_daily_refresh.py agents/bhaga/scripts/test_materialize_model_bq.py -q
python3 scripts/verify.py --full
```
Pass criterion: all green, including existing exemption + conservation tests.

## Milestone 4 — PR §4 evidence, docs, babysit (Sonnet; Composer for doc-only edits)

1. `.github/sandbox-live.yml` → `scenarios: [{name: full-live, date: 2026-09-22}]`, add the
   `sandbox-live` label (single-shot). This fires one ADP 2FA SMS via the Slack OTP flow — announce
   it to the operator first. Empty back to `scenarios: []` before merge.
2. Collect from `bhaga_sandbox`:
   `adp_wage_rate_history` for `Johnson, Dolce`, `vw_model_payroll_period` (both periods),
   `adp_punches.note` for 09-22, `model_tip_alloc_daily` for 09-22.
3. Assemble PR §4, `python3 scripts/verify.py --full`, push once, babysit via
   `python3 scripts/pr_triage.py --pr N`.

## PR §4 evidence contract (approved at define-evidence)

| # | Scenario | Pass criterion | Where |
|---|---|---|---|
| 1 | Happy path: rate scrape succeeds | `[pay_info] OK Johnson, Dolce → $18.0000`; `adp_wage_rate_history` row 18.00 with effective 2026-09-21; no failure evidence for Dolce | sandbox-live log + BQ |
| 2 | Failure: name guard still refuses | unit tests: unmapped / other-canonical / two-exact-hit Directory names raise | M1 pytest |
| 3 | Current period at $18 | Sep 21–Oct 4: `wage_rate_dollars = 18.00`, `est_gross_pay = hours × 18` (NUMERIC half-up) | sandbox BQ |
| 4 | Closed period unchanged | Sep 7–20: `16.25` and `$889.36`; live labor cost before 2026-09-21 unchanged | sandbox BQ diff vs prod |
| 5 | Effective-date rule | unit tests incl. hire-date `added_on` fallback + operator override wins | M1 pytest |
| 6 | Notes ingested | parser tests; sandbox `adp_punches` 09-22 carries `"…Admin Work"` on the 15:00 punch | M3 pytest + BQ |
| 7 | Admin punch drops tips only | 09-22: Dolce eligible hours −0.5, wages unchanged, pool conserved, co-workers up | M3 pytest + sandbox `model_tip_alloc_daily` |
| 8 | Legacy / recovery | no-note shifts identical; Hillary's exemptions unchanged; `assert_exemptions_applied` green; blank keyword list = today's output | pytest + sandbox |
| 9 | Post-merge | apply migrations, recompute 2026-09-21..latest, console payroll screenshot (Dolce at $18, admin tips excluded) | prod |

## Post-merge (agent)
```bash
BHAGA_DATASTORE=bigquery python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
python3 -m skills.adp_run_automation.wage_rate_history show --employee "Johnson, Dolce"
for d in 2026-09-21 2026-09-22 2026-09-23 2026-09-24 2026-09-25 <…latest>; do
  python3 scripts/trigger_dated_refresh.py --date "$d" --force-recompute
done
python3 -m agents.bhaga.scripts.status --store palmetto
```
If tonight's pay_info has not yet written the $18 row, the operator override
(`wage_rate_history set --employee "Johnson, Dolce" --effective 2026-09-21 --rate 18.00`) is the
sanctioned repair path — never hand-edit BQ.

## Docs lock-step
`agents/bhaga/knowledge-base/DOMAIN.md` (`wage_rates` → history + effective dates; Admin-punch
exemption), `agents/bhaga/scripts/README.md` (materialize inputs, scope widening),
`skills/adp_run_automation/README.md` (alias-aware match, Notes column, history CLI),
`RUNBOOK.md` (Common tasks: set an effective-dated rate; disable admin keyword),
`.cursor/rules/bhaga.mdc` (rate-on-date rule; punch-note exemptions), `docs/FEATURE_FLAGS.md`
(N/A entry), `PROGRESS.md` via retro follow-up PR. Run `python3 scripts/check_doc_freshness.py`.

## Branch / PR mechanics
Single branch `fix/i-have-updated-dolce-s-rate` → one PR via `gh pr create --base main` as
`jarvis-agent-bot328` (bot account), `Refs #343`. `bind-pr` + `pr_cost_ledger.py sync` after open.
Push with `--no-verify` only after `verify.py --full` is green. Babysit per `pr-workflow.mdc`; reply
to every thread; never self-merge — operator merges.

## Model routing
| Milestone | Model |
|---|---|
| M1 Directory match + history table/CLI | Sonnet |
| M2 view SQL rewrite | Sonnet; Opus review of 075 before push |
| M3 notes ingest + tip windows + scope | Sonnet |
| M4 evidence/docs | Sonnet (Composer for doc-only) |

## Follow-up issues (open at retro)
1. Earnings load should not overwrite a newer pay_info rate in `adp_wage_rates`.
2. Show Admin-punch exemptions in the console Tip Exemptions table.
3. Admin notes edited into an already-closed pay period are not re-scraped (nightly selects only the
   open period).
