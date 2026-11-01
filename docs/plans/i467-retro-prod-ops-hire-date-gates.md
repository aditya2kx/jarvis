# Issue #467 — Retro #450/#465: deploy wait + fq() gate + hire_dates_empty + Actions log dump gate

Consulted: `CONTRIBUTING.md` (dev loop, evidence-definition), `.cursor/rules/plan-execution-readiness.mdc`,
`.cursor/rules/bhaga-principles.mdc` (breadcrumbs, sandbox isolation), `.cursor/rules/pr-workflow.mdc`,
`scripts/prod_ops.py` lines 167–221 (`commands`, `dump_failed_job`), 275–318 (`main`),
`skills/adp_run_automation/pay_info_backend.py` lines 1509–1577 (`write_hire_dates_bq`, `backfill_hire_dates`),
`skills/adp_run_automation/test_pay_info_backend.py` lines 799–819,
`scripts/test_prod_ops.py` lines 47–105, `scripts/verify.py` GATES lines 49–170,
`.github/workflows/deploy.yml` lines 132–160 (image tag == `github.sha`),
`.github/workflows/prod-ops.yml` lines 105–139 (checkout main, then run).

Jam + §4 approved in chat 2026-11-01 (operator: "lets fix all of them in one pr in this chat except changing llm model").

**Evidence tier: unit-only**
waiver: mechanical gates + pure control-flow/breadcrumb string changes; no money path, no ADP write, no schema.
Happy path and failure scenarios are covered by unit tests that plant the forbidden patterns / mock deploy tags.
No sandbox-live required.

Feature flag: **none** — wait-before-execute cannot silently produce wrong numbers; breadcrumb string change cannot; gates cannot. Escape hatch `PROD_OPS_SKIP_DEPLOY_WAIT=1` is an ops override, not a silent number path (document in RUNBOOK).

Invariants preserved: read-only ADP for hire-date sync; integer cents N/A; America/Chicago N/A; idempotent hire-date MERGE unchanged; sandbox isolation N/A.

Docs lock-step: `RUNBOOK.md` (prod-ops deploy wait + hire_dates nothing_new breadcrumb), `PROGRESS.md` (dated entry), `docs/contributing/dev-anywhere.md` (one-liner on wait). Then `python3 scripts/check_doc_freshness.py`.

Branch/PR: one branch `cursor/467-retro-prod-ops-hire-date-gates` → one PR → bot account → never self-merge → reply-to-every-comment. Push without `--no-verify` only if hooks installed; Cloud Agent uses normal push.

Model routing: Composer for all milestones (mechanical gates + small control flow). One chat per PR.

## Milestone 1 — R4 breadcrumb + R3 SQL string test (Composer)

`skills/adp_run_automation/pay_info_backend.py` `backfill_hire_dates` (~1546–1577): when `found` is empty after the skip pass and `have_keys` is non-empty and not `force`, print
`[pay_info] hire_dates nothing_new known=N — BQ already complete` and **do not** call `write_hire_dates_bq` (so `hire_dates_empty` never fires on intentional skip-all).

`write_hire_dates_bq` (~1509–1517) keeps `hire_dates_empty` only for a true empty MERGE payload (first run / no directory dates and no profile reads).

R3: extend `test_known_hire_names_query_uses_fq_without_extra_backticks` to also run the SQL through a shared `assert_no_double_wrapped_fq(sql: str) -> None` helper (same regex as the R2 gate), so a mocked client that never talks to BQ still fails if the string is invalid.

**Verify:**
```bash
python3 -m pytest -q skills/adp_run_automation/test_pay_info_backend.py -k 'hire_date or known_hire'
```
Pass: skip-all test asserts `write_hire_dates_bq` not called + printed `nothing_new`; no `hire_dates_empty`.

## Milestone 2 — R1 prod-ops waits for deployed image (Composer)

Add to `scripts/prod_ops.py`:

```python
def expected_deploy_sha() -> str:
    """main tip the Cloud Run job must be running. Override: PROD_OPS_EXPECT_SHA."""

def job_image_tag(job: str) -> str:
    """Tag from gcloud run jobs describe … containers[0].image (after final ':')."""

def wait_for_deployed_job(job: str, expect_sha: str, *, timeout_s: int, poll_s: int = 15) -> None:
    """Poll until tag == expect_sha; raise RequestError on timeout."""
```

`commands()` for `run-job` prepends:
`[sys.executable, __file__, "wait-deploy", job]` unless `PROD_OPS_SKIP_DEPLOY_WAIT=1`.

New CLI mode `wait-deploy` (like `clone-job`). Default timeout `PROD_OPS_DEPLOY_WAIT_S` (1200).

**Verify:**
```bash
python3 -m pytest -q scripts/test_prod_ops.py -k 'wait_deploy or run_job'
```
Happy path: tag already matches → one describe, then execute.
Failure: tag never matches → RequestError / nonzero, execute not called.
Recovery: `PROD_OPS_SKIP_DEPLOY_WAIT=1` skips the wait step (documented).

## Milestone 3 — R2 + R5 mechanical gates (Composer)

New `scripts/check_fq_no_double_backticks.py`: scan `core/`, `skills/`, `agents/`, `cloud/`, `scripts/` `*.py` for `` `{fq( `` (and `` `{fq" ``). Exit 1 with file:line.

New `scripts/check_no_public_job_logs.py`: scan `scripts/prod_ops.py` and `.github/workflows/*.yml` for `gcloud logging read` / `logging read` (job log dump into public Actions). Exit 1 with file:line. Allowlist only this checker + its test.

Wire both into `scripts/verify.py` GATES (`fast` + `full`, hard=True) next to `append-only-history` (~line 141).

Unit tests: `scripts/test_check_fq_no_double_backticks.py`, `scripts/test_check_no_public_job_logs.py` — plant a temp file that fails, assert clean tree passes.

**Verify:**
```bash
python3 scripts/check_fq_no_double_backticks.py
python3 scripts/check_no_public_job_logs.py
python3 -m pytest -q scripts/test_check_fq_no_double_backticks.py scripts/test_check_no_public_job_logs.py
```

## Milestone 4 — Docs + verify --full (Composer)

Update `RUNBOOK.md` (prod-ops section: deploy wait; hire-date breadcrumb `nothing_new`), `docs/contributing/dev-anywhere.md` (one line), `PROGRESS.md` dated entry for #467.

**Verify:**
```bash
python3 scripts/check_doc_freshness.py
python3 scripts/verify.py --full --plan docs/plans/i467-retro-prod-ops-hire-date-gates.md
```
Pass criterion: exit 0.

## Per-scenario evidence (PR §4)

| Scenario | Expect |
|---|---|
| Happy: job tag already == main SHA | wait-deploy one-shots; execute runs |
| Failure: tag stuck on old SHA | wait-deploy times out; execute never runs; PR reply shows reject reason |
| Recovery: `PROD_OPS_SKIP_DEPLOY_WAIT=1` | execute runs without wait (ops escape) |
| Happy: skip-all hire dates | log `nothing_new`; no `hire_dates_empty` |
| Failure: true empty roster | still prints `hire_dates_empty` |
| Gate: plant `` `{fq('t')}` `` | check exits 1 |
| Gate: plant `gcloud logging read` in prod_ops | check exits 1 |
| Legacy: existing `test_known_hire_names_query_uses_fq_without_extra_backticks` still green | |

## Docs lock-step targets

- `RUNBOOK.md`
- `docs/contributing/dev-anywhere.md`
- `PROGRESS.md`
- `python3 scripts/check_doc_freshness.py`
