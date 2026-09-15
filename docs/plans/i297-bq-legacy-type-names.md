# Issue #297 — Nightly dies on `CAST(NULL AS FLOAT)`: normalize BigQuery legacy type names

Consulted: `CONTRIBUTING.md` (dev loop as success criteria, evidence-definition step),
`.cursor/rules/bhaga-principles.mdc` (breadcrumbs, sandbox isolation),
`.cursor/rules/plan-execution-readiness.mdc` (this checklist),
`.cursor/rules/doc-maintenance.mdc` (code↔doc coupling table),
`.cursor/rules/pr-workflow.mdc` (branch → PR → babysit → operator merge),
`core/datastore.py` lines 449–511 (`_merge_rows`), 484 (the failing `CAST`), 612–638
(`table_column_types`), 641–669 (`_resolve_col_types`), 672–686 (`_infer_bq_type`),
`core/test_datastore_column_types.py` lines 26–29 (`_Field`), 50–56 (`LIABILITY_SCHEMA`),
133–158 (`test_null_is_cast_to_the_column_type`),
`agents/bhaga/scripts/backfill_from_downloads.py` lines 336–353 (payroll-liability load),
`agents/bhaga/scripts/daily_refresh.py` lines 2143–2203 (`run_step`), 2228–2280
(`_record_pipeline_run`), 3210–3215 (`load_raw_bigquery` step),
`agents/bhaga/scripts/status.py` (`main()` 560–695, `BQ_TARGETS` 117–125).

Jam + §4 approved in chat 2026-09-15 (issue #297 labels `approved:jam`,
`approved:define-evidence`; gate record in issue comment 5681102304).

Evidence tier: unit-only
waiver: the change is a four-entry type-name mapping with no arithmetic, data-shape, or
money-path surface. The failure is a deterministic BigQuery *parse* error that the corrected
unit fixture reproduces exactly (red before, green after). The post-merge prod rerun of
`2026-09-14` is itself a live ADP + Square + BigQuery end-to-end run, so a separate
OTP-gated sandbox `full-live` adds latency without adding signal at this scope. Operator
chose this tier explicitly over `sandbox-live` and over `sandbox-e2e`.

Scope, as the operator set it: **option A only** — the minimal unblocking fix, so a rerun of
`2026-09-14` can happen immediately. The three hardening items surfaced during jam
(per-table isolation, real error breadcrumbs, a mechanical type-name gate) are **out of
scope** here and become follow-up issues.

## Root cause

Nightly `2026-09-14` (Cloud Run Job execution `bhaga-daily-refresh-j2kdb`, started
21:30 CT) failed at `load_raw_bigquery`:

```
google.api_core.exceptions.BadRequest: 400 ... Type not found: FLOAT at [1:224]
```

`table_column_types` (`core/datastore.py:612`, added in `c2d12b8` / PR #291) reads column
types from the BigQuery **table API**, which returns *legacy* type names, and normalizes
only one of them:

```python
# core/datastore.py:631-633 — current
schema = client.get_table(key).schema
types = {f.name: ("BOOL" if f.field_type == "BOOLEAN" else f.field_type)
         for f in schema}
```

Verified against the live table — the API really does return the legacy spelling:

```
er_futa   field_type=FLOAT   mode=NULLABLE
```

That raw string reaches SQL text at `core/datastore.py:484`,
`fields.append(f"CAST(NULL AS {col_types[c]}) AS {c}")`, producing
`CAST(NULL AS FLOAT)`, which GoogleSQL rejects.

Verified against prod BigQuery, which spellings actually break:

| Usage | `FLOAT64` | `FLOAT` | `INT64` | `INTEGER` |
|---|---|---|---|---|
| `CAST(NULL AS …)` in SQL text | ok | **400 Type not found** | ok | ok (legacy alias) |
| `ScalarQueryParameter` (non-NULL) | ok | ok | ok | ok |

So the trigger is narrow: **a NULL in a `FLOAT64` column**. This is why `adp_shifts`
(103 rows) and `adp_punches` (183 rows) merged fine in the same run despite having `FLOAT`
columns — no NULLs in those batches — while `adp_payroll_liability` hit it, because ADP's
Payroll Liability body omitted the FUTA line. Exposure is repo-wide, not payroll-specific:
**395 nullable `FLOAT64` columns across 64 tables** in the `bhaga` dataset.

`INTEGER` survives only because BigQuery happens to accept it as a `CAST` alias. That is
luck, not design, so the fix normalizes the whole legacy set rather than just `FLOAT`.

### Why CI was green

`core/test_datastore_column_types.py:53` builds the fixture as `_Field("er_futa", "FLOAT64")`
— the GoogleSQL spelling the API does **not** return. The mock asserts the bug away:
`test_null_is_cast_to_the_column_type` passes while prod fails.

### Cascade (what the operator saw)

`load_raw_bigquery` aborted before `adp_earnings`; `materialize_model_bq` never ran (absent
from the logs entirely); `process_reviews` logged `SKIPPED — raw_sheets_ok=False`. Result:

| Table | Max date | Note |
|---|---|---|
| `model_daily` | 2026-09-13 | 09-14 missing — the console gap |
| `model_labor_daily` | 2026-09-13 | drives the labor page |
| `model_tip_alloc_daily` | 2026-09-13 | |
| `adp_payroll_liability` | 2026-08-28 | the failing table |
| `adp_shifts` / `adp_punches` | 2026-09-14 | loaded before the abort |
| `adp_scheduled_shifts` | 2026-09-27 | unaffected, scraped 09-15 02:38 |

Scheduled shifts were never missing at the data layer; the operator confirmed in chat that
the console view is fine, so no console change is in scope.

## Milestone 1 — Normalize legacy type names (Sonnet 5 medium thinking)

Single edit in `core/datastore.py::table_column_types`, replacing the inline `BOOL`
conditional at lines 631–633 with a module-level mapping:

```python
# core/datastore.py — new module constant, above table_column_types (line ~610)
# The table API reports legacy type names; GoogleSQL only accepts these spellings in
# SQL text such as CAST(NULL AS <type>). BOOLEAN was already handled; FLOAT was not,
# which failed the 2026-09-14 nightly with `Type not found: FLOAT`.
_LEGACY_BQ_TYPES = {
    "FLOAT": "FLOAT64",
    "INTEGER": "INT64",
    "BOOLEAN": "BOOL",
    "RECORD": "STRUCT",
}

# inside table_column_types, replacing lines 632-633:
        types = {f.name: _LEGACY_BQ_TYPES.get(f.field_type, f.field_type)
                 for f in schema}
```

No other call site changes: `_resolve_col_types` (line 641) and `_merge_rows` (line 449)
consume whatever `table_column_types` returns, so normalizing at the boundary fixes both
the `CAST(NULL AS …)` path and the `ScalarQueryParameter` path.

Test changes in `core/test_datastore_column_types.py`:

- `LIABILITY_SCHEMA` (line 50): `_Field("er_futa", "FLOAT64")` → `_Field("er_futa", "FLOAT")`,
  and `_Field("is_final", "BOOLEAN")` stays as-is (already correct).
- `test_reads_types_from_the_table` (line 62) keeps asserting `types["er_futa"] == "FLOAT64"`
  — now a real assertion about normalization rather than a pass-through.
- New `test_legacy_integer_and_record_are_normalised`: `INTEGER`→`INT64`, `RECORD`→`STRUCT`.
- New docstring note on `_Field` recording that `field_type` must use the **legacy**
  spelling because that is what `client.get_table().schema` returns.

**Verify (copy-paste):**

```bash
python3 -m pytest core/test_datastore_column_types.py -q
```

Pass criterion: all green. Negative control — `git stash` the `core/datastore.py` change and
the same command must fail with `CAST(NULL AS FLOAT) AS er_futa` present, proving the test
reproduces the prod defect rather than merely passing.

## Milestone 2 — Full local CI mirror green (Sonnet 5 medium thinking)

No new code. Confirms the mapping breaks nothing else that reads column types, since
`table_column_types` sits in the inner loop of every BigQuery write in the repo.

**Verify (copy-paste):**

```bash
python3 -m pytest core/ -q
python3 scripts/verify.py --full
python3 scripts/check_doc_freshness.py --base origin/main
```

Pass criterion: `verify.py --full` exits 0 (this is the same gate the `pre-push` hook runs),
and `check_doc_freshness.py` reports no unmet coupling.

## Milestone 3 — Unblock the cost gate: conversation attribution (Sonnet 5 medium thinking)

**Added mid-flight with operator approval**, after the gate turned out to be
unsatisfiable rather than merely unsatisfied (operator: "fix_here"). Scope A stands for
the pipeline fix; this is a dev-tooling defect discovered while babysitting this PR.

`scripts/cursor_usage.py::filter_events_for_conversations` intersected the usage-event
window with the `ai_code_hashes` edit window. Those are skewed by construction — usage
events are cumulative per-model rows stamped near the session's first request, edits land
later (56 min here, against `_CONVERSATION_EVENT_PAD_MS` of 5 min) — so the intersection
was empty, `capture-build` raised its hard `$0` failure, and `validate`
(`scripts/pr_cost_ledger.py:867`) demanded an `attribution_mode=conversation` that could
never be set. Measured on this PR:

| Source | Window (UTC) |
|---|---|
| usage events | 12:42:19, 12:42:41 |
| conversation profile (edits) | 13:38:42 → 13:45:15 |

The edit window is only a *disambiguator* between parallel chat spaces. When every
edit-active conversation in the caller's window is bound there is nothing to disambiguate,
so the caller's window is used as-is; with an unbound conversation also active the strict
edit window holds, since over-attributing another chat's cost is the real risk. The
model-tier check is unchanged, which correctly keeps a stray `composer-2.5` background
request out of this Opus-only conversation.

Filed as #302 before the operator expanded scope to fix it here.

**Verify (copy-paste):**

```bash
python3 -m pytest scripts/test_cursor_usage.py -q
python3 scripts/pr_cost_ledger.py capture-build --pr 298
python3 scripts/pr_cost_ledger.py validate --pr 298 --require-build
```

Pass criteria: new `TestUsageEditWindowSkew` green (and the two widening tests red when
`scripts/cursor_usage.py` is stashed); `capture-build` reports `[conversation]` attribution
with non-zero cost; `validate` prints `[OK]`. Docs lock-step: `docs/contributing/cost.md`
gains an attribution section (`check_doc_freshness.py` couples `scripts/cursor_usage.py` to it).

## Milestone 4 — Prod rerun of 2026-09-14 and regression watch (Opus 4.8 thinking medium)

Post-merge only. Reruns the single genuinely failed date. **`2026-09-15` is deliberately not
rerun**: today's business day is not over, so a rerun now would load a partial day, and the
21:30 CT nightly covers it cleanly — the operator chose "ship the fix and let tonight's
nightly run clean". Preference J3 (backfill the full gap) is satisfied because `2026-09-14`
is the only failed date; per J4 the date was derived from prod state
(`pipeline_runs.status='failed'`), not supplied by the operator.

```bash
# 1. Trigger the rerun for the failed date (recompute + reload, ignoring the halt breaker)
gcloud run jobs execute bhaga-daily-refresh --region us-central1 \
  --project jarvis-bhaga-prod --wait \
  --update-env-vars REFRESH_DATE=2026-09-14,BHAGA_IGNORE_HALT=1

# 2. Freshness verdict for the repaired date (preference B2 — never hand-write queries)
BHAGA_SECRETS_BACKEND=gcp \
python3 -m agents.bhaga.scripts.status --store palmetto --date 2026-09-14
```

Pass criteria, each independently checkable:

1. `status --store palmetto --date 2026-09-14` exits **0**.
2. `model_daily`, `model_labor_daily`, `model_tip_alloc_daily` all present for `2026-09-14`.
3. `adp_payroll_liability` max `check_date` advances past its current `2026-08-28` — the
   direct proof that the previously failing MERGE now succeeds against real data.
4. `pipeline_runs` shows `status='success'`, `failed_step IS NULL` for the rerun's `run_id`.
5. **Regression signal (next morning):** the unattended `2026-09-15` nightly records
   `status='success'`. This is the scenario that actually broke, so an operator-triggered
   rerun alone is not sufficient proof.

### Scenario evidence enumerated for PR §4

| Scenario | How it is exercised | Expected |
|---|---|---|
| Happy path — no NULLs in FLOAT columns | existing `adp_shifts` / `adp_punches` merges in the rerun | unchanged, still upsert |
| Failure case that broke prod — NULL in a FLOAT column | `adp_payroll_liability` with FUTA omitted, live in the rerun | merges; `check_date` advances |
| Unit reproduction of the same failure | `test_null_is_cast_to_the_column_type` with the legacy fixture | `CAST(NULL AS FLOAT64)`, never `CAST(NULL AS FLOAT)` |
| Legacy aliases not previously covered | new `INTEGER`/`RECORD` test | `INT64` / `STRUCT` |
| Schema unreadable (degrade, not fail) | existing `test_unreadable_schema_degrades_instead_of_raising` | returns `{}`, typing falls back to inference |
| Recovery of the lost date | prod rerun of `2026-09-14` | `status` exits 0 |
| Unattended regression | `2026-09-15` nightly | `success` |

## Invariants preserved

- **Idempotent upserts** — the fix changes only the *type name* in generated SQL, not the
  MERGE keys or semantics, so re-running a date still converges rather than duplicating.
- **Integer cents** unaffected — no arithmetic is touched; a `FLOAT64` column keeps its
  declared type, it is merely spelled correctly for GoogleSQL.
- **America/Chicago** — no date/timezone logic in scope; `REFRESH_DATE=2026-09-14` is a
  business date, matching existing marker namespacing (`run_step`, `daily_refresh.py:2143`).
- **Read-only ADP** — the rerun scrapes but never writes to ADP.
- **Sandbox isolation** — `_assert_sandbox_write_isolation` (`core/datastore.py:542`) is
  untouched; prod/sandbox routing is unchanged.
- **Backward compatible / must not break:** already-correct GoogleSQL spellings pass through
  the mapping unchanged (`.get(x, x)`), so explicit `column_bq_types` hints and inferred
  types behave exactly as before. No hardcoding introduced — the mapping is a closed set
  defined by BigQuery, not a per-table config.

## Feature-flag decision

**No flag.** Applying the "can it silently produce wrong numbers?" test: it cannot. A merge
either gets a type BigQuery accepts and writes correct values, or it raises a 400 and fails
loudly — there is no path where a wrong number lands silently. A flag would also be
self-defeating, since the flagged-off branch is precisely the broken one. No
`FEATURE_FLAGS.md` entry needed. No Operator Console UI in scope, so the UX-polish bar
(user-preferences Design #27) is N/A.

## Docs lock-step

Per `.cursor/rules/doc-maintenance.mdc`, `core/datastore.py` is not in the coupling table and
this change has no behavioral surface in `RUNBOOK.md`, `agents/bhaga/scripts/README.md`, or
`DOMAIN.md` — it is an internal type mapping. `PROGRESS.md` gets a dated entry, but per
`pr-workflow.mdc` that lands via its own follow-up PR after the retrospective, never a
direct main push. `python3 scripts/check_doc_freshness.py --base origin/main` is the
mechanical confirmation and runs in Milestone 2.

## Branch / PR mechanics

- One branch = one coherent change: `fix/slack-incremental-run-failed-last-night`, two files.
- `gh pr create --base main` — always explicit, never the repo default (incident 2026-07-01).
- All GitHub ops as the bot account `jarvis-agent-bot328`, never `aditya2kx`.
- Cost ledger: `pr_cost_ledger.py set-meta` + `capture-build`, then `bind-pr` and
  `validate --require-build` before merge. `metrics/pr_cost/` is **not** committed.
- Babysit to green per `pr-workflow.mdc` using `scripts/pr_triage.py --pr N` in a batch loop;
  reply on every review thread before pushing; push once per loop.
- **Never self-merge** — auto-merge is operator-only; the agent stops at "all checks green,
  all comments replied, no conflicts".
- Milestone 3 runs only after the operator squash-merges to `main`.

## Model routing (docs/contributing/cost.md)

| Milestone | Model | Why |
|---|---|---|
| M1 — the fix + tests | Sonnet 5 medium thinking | small, well-specified edit in two cited files |
| M2 — CI mirror green | Sonnet 5 medium thinking | mechanical; no design decisions |
| M3 — prod rerun + verify | Opus 4.8 thinking medium | prod operation with live data; judgement on the rerun verdict |

One chat per PR; this plan and implementation share the jam chat's space for cost
attribution.

## Follow-up issues (deferred hardening, opened with this PR)

1. **Per-table isolation in `load_raw_bigquery`** — `adp_payroll_liability` holds 4 rows and
   feeds no model table, yet its merge failure aborted `backfill_from_downloads`
   (`agents/bhaga/scripts/backfill_from_downloads.py:353`) before `adp_earnings`, which
   killed `materialize_model_bq` and emptied the console. `ingest_inventory` and
   `refresh_order_reco` are already non-fatal; the per-table loads are not.
2. **Real failure breadcrumbs** — `pipeline_runs.error IS NULL` for *every* failed run in the
   table's history. `_record_pipeline_run` (`daily_refresh.py:2249`) writes
   `error or _RUN_SUMMARY.get("error")`, but nothing populates it on step failure, and the
   step ran in a subprocess so the exception is a bare
   `CalledProcessError: ... exit status 1` — the informative BQ 400 lived only in the child's
   stderr. Diagnosing this incident required raw Cloud Run logs, which is exactly what
   preference B3 says the breadcrumb should prevent.
3. **Mechanical gate on type-name spelling** — a `verify.py` check rejecting legacy BigQuery
   type names in SQL-text `CAST` construction, per preference 19 (prefer a deterministic
   check over only recording a preference).

Separately, the same run logged four non-fatal `pay_info` scrape failures
(`Alvarez, Sebastian`, `Flores, Juan`, `Urrutia, Emely` on Playwright timeouts;
`Johnson, Dolce` on `AmbiguousEmployeeError` — closest Directory record `Johnson, Dolce J`),
leaving `adp_wage_rates` at 20 rows. Already tracked in issue #293; not in scope here.
