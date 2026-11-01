# Issue #471 — Hotfix empty Cloud Run Jobs image path in wait-deploy

Consulted: `CONTRIBUTING.md` (dev loop), `.cursor/rules/plan-execution-readiness.mdc`,
`.cursor/rules/bhaga-principles.mdc` (breadcrumbs), `.cursor/rules/pr-workflow.mdc`,
`scripts/prod_ops.py` lines 187–210 (`expected_deploy_sha`, `job_image_tag`),
`scripts/test_prod_ops.py` lines 47–80,
`.github/workflows/deploy.yml` lines 155–157 (known-good Jobs image format),
Actions run 41345895880 (#468 post-merge `/prod-ops` failure).

Jam + §4: operator "approved" #468 finish line; post-merge verification exposed the break
(`could not read image for job 'bhaga-daily-refresh': ''`).

**Evidence tier: unit-only**
waiver: one format-string correction; live failure already observed on prod-ops; deploy.yml path is the oracle.
No sandbox-live / money path / ADP write.

Feature flag: **none** — format string cannot silently produce wrong numbers; it unblocks
`run-job` that is currently hard-failing. Out of scope: changing wait-deploy timeout / skip hatch.

Invariants preserved: wait-deploy still required before execute; ADP read-only unchanged;
America/Chicago N/A; integer cents N/A; sandbox isolation N/A.

Docs lock-step: `PROGRESS.md` dated entry. `RUNBOOK.md` already documents wait-deploy (no
behavior change beyond reading the image). Then `python3 scripts/check_doc_freshness.py`.

Branch/PR mechanics: one branch `cursor/471-fix-prod-ops-job-image-path` → one PR →
bot account `jarvis-agent-bot328` → never self-merge → babysit → reply-to-every-comment.
Cloud Agent: push as bot; `cloud-agent-pr.yml` opens draft; ManagePullRequest updates body.
Do not `--no-verify` unless hooks block a known false positive.

Model routing: Composer for all milestones (mechanical one-liner + unit assert). One chat per PR.

## Milestone 1 — Fix Jobs image format string (Composer)

`scripts/prod_ops.py` ~190–210: introduce module constant and use it in `job_image_tag`:

```python
_JOB_IMAGE_FORMAT = "value(spec.template.spec.template.spec.containers[0].image)"

def job_image_tag(job: str) -> str:
    ...
    out = subprocess.run(
        ["gcloud", "run", "jobs", "describe", job, *loc, f"--format={_JOB_IMAGE_FORMAT}"],
        ...
    )
```

Also append truncated stderr to `RequestError` when image is empty (diagnose future path drift).

**Verify:**
```bash
python3 -c "from scripts import prod_ops; assert 'template.spec.template.spec' in prod_ops._JOB_IMAGE_FORMAT"
```
Pass: nested path present; shallow path gone from the gcloud call.

## Milestone 2 — Guardrail unit test vs deploy.yml (Composer)

`scripts/test_prod_ops.py` `test_job_image_format_matches_deploy_yml`:

```python
def test_job_image_format_matches_deploy_yml(self):
    deploy = (Path(__file__).resolve().parent.parent / ".github/workflows/deploy.yml").read_text()
    self.assertIn(prod_ops._JOB_IMAGE_FORMAT, deploy)
```

**Verify:**
```bash
python3 -m pytest -q scripts/test_prod_ops.py -k 'job_image_format or wait_deploy or run_job'
```
Pass: 7 tests green; format constant ∈ deploy.yml.

## Milestone 3 — Docs + plan gate + verify (Composer)

`PROGRESS.md` #471 incident/fix entry. Plan file passes readiness ≥9.

**Verify:**
```bash
python3 scripts/check_plan_readiness.py --plan docs/plans/i471-fix-prod-ops-job-image-path.md --skip-phase-check
python3 scripts/check_doc_freshness.py
python3 scripts/verify.py --full --plan docs/plans/i471-fix-prod-ops-job-image-path.md
```
Pass: plan ≥9/10; doc freshness exit 0; verify --full exit 0 (CI is source of truth on Cloud Agent noise).

## Milestone 4 — Post-merge recovery evidence (Composer)

After merge + deploy: `/prod-ops run-job bhaga-daily-refresh …` (or describe-only wait-deploy)
must print `deployed … matches expect=…` with a non-empty image tag == main SHA.

**Verify (post-merge, hosted):**
```bash
# via Actions /prod-ops comment on the merged PR or tracking issue
# expect: wait-deploy succeeds; no empty-image RequestError
```
Pass: Actions log shows non-empty job image; execute proceeds (or plan-only prints wait then execute).

## Per-scenario evidence (PR §4)

| Scenario | Expect |
|---|---|
| Happy: format matches deploy.yml | `test_job_image_format_matches_deploy_yml` passes |
| Happy: wait-deploy unit path | existing `wait_deploy` / `run_job` tests still green |
| Failure (legacy): shallow path | #468 post-merge Actions run 41345895880 showed `image: ''` |
| Failure: empty image now | RequestError includes stderr snippet |
| Recovery post-merge | `/prod-ops` prints `deployed … matches expect=<full sha>` |
| Legacy: `PROD_OPS_SKIP_DEPLOY_WAIT=1` | still skips wait (unchanged) |

## Docs lock-step targets

- `PROGRESS.md`
- `docs/plans/i471-fix-prod-ops-job-image-path.md`
- `python3 scripts/check_doc_freshness.py`
- RUNBOOK unchanged (wait-deploy already documented; only the gcloud format string was wrong)
