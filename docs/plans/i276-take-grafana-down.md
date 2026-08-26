# #276 Take BHAGA Grafana down (Operator Console is the UI)

Evidence tier: unit-only
waiver: no pipeline/tip/payroll runtime change; hosted BHAGA Grafana retire + CI/docs; live dashboard 404 is post-merge verify. jarvis-dev Grafana kept.

Jam 2026-08-26: Option **A** — BHAGA Analytics only. Keep `grafana/jarvis_dev` + `grafana-jarvis-dev-sync.yml`. Accept Issue #213 forecast UI gone with BHAGA Grafana. Do not cancel Grafana Cloud org. Do not restore `/forecast`.

## Citations (consulted)

- `CONTRIBUTING.md` lines 9–36 (dev loop, operator gates, evidence before build)
- `.cursor/rules/bhaga-principles.mdc` (BQ SoT; console not Grafana for operator)
- `docs/operator-console/ARCHITECTURE.md:36-37` coexistence → this PR ends it
- `docs/operator-console/EXECUTION.md:588-609` panel coverage (forecast Grafana-only)
- `.github/workflows/grafana-dashboard-sync.yml:1-75` BHAGA sync to retire
- `.github/workflows/grafana-jarvis-dev-sync.yml:1-30` **keep**
- `scripts/check_evidence_readiness.py:127-141,192-233` G3 must not fire on BHAGA teardown
- `scripts/verify.py:118-150` grafana-no-logic gate + CI_SCRIPT_NAMES
- `scripts/check_live_labor_cost.py:23-86` Grafana panel loop skip if no dashboard.json
- `agents/bhaga/scripts/status.py:65,142` GRAFANA_VIEWS stay as BQ freshness (console)
- `apps/operator-console/scripts/capture_evidence.py:31` imports GitHub upload from `capture_screenshot.py` — **keep that module**

## Invariants (must not break)

- Nightly / `ensure_schema()` / BQ views unchanged (idempotent, America/Chicago, integer cents)
- Operator Console remains the operator UI; no metric math moved into Grafana or the app
- `grafana-bq-reader` SA + jarvis-dev dashboard stay (cost UI)
- `upload_screenshot` / `_get_github_token` keep working for console §4 evidence
- Feature flag: **none** — cannot silently produce wrong numbers; this removes a visualization surface. Flag not needed.

## Out of scope

- Cancel Grafana Cloud org / revoke all tokens
- Console `/forecast` restore (new requirement)
- Console cost page (new requirement)
- Deleting `grafana/jarvis_dev/**`

Model routing (`docs/contributing/cost.md`): M1 Sonnet · M2 Sonnet · M3 Sonnet (Composer for copy-only doc polish).

## Milestone 1 — Stop BHAGA sync + delete live BHAGA dashboard — Sonnet

Remove `.github/workflows/grafana-dashboard-sync.yml`. Keep `grafana-jarvis-dev-sync.yml`.

Add `delete_dashboard(uid, *, org_slug)` in `skills/grafana_cloud_provisioning/register.py` after `get_dashboard_url` (~line 294):

```python
def delete_dashboard(uid: str, *, org_slug: str) -> str:
    """DELETE /api/dashboards/uid/{uid}. 404 = already gone (ok)."""
```

Replace BHAGA `agents/bhaga/grafana/deploy.py` push path with `--delete-bhaga-analytics` (uid `bhaga-analytics-v1`). Delete `dashboard.json`, `verify_panels.py`, `compare_panels.py`, `evidence.py`, `test_compare_panels.py`, `test_deploy_bind_uid.py`. Keep `capture_screenshot.py` (GitHub upload used by console).

**Verify:**
```bash
test ! -f .github/workflows/grafana-dashboard-sync.yml
test -f .github/workflows/grafana-jarvis-dev-sync.yml
python3 agents/bhaga/grafana/deploy.py --delete-bhaga-analytics
# GET https://steadyangelfish2985.grafana.net/api/dashboards/uid/bhaga-analytics-v1 → 404
```

## Milestone 2 — Retire Grafana-assuming gates — Sonnet

| File | Change |
|---|---|
| `scripts/verify.py:118-150` | Remove `grafana-no-logic` Gate; drop `check_grafana_no_logic.py` from `CI_SCRIPT_NAMES` |
| `scripts/check_grafana_no_logic.py` + `scripts/test_check_grafana_no_logic.py` | Delete |
| `scripts/check_live_labor_cost.py:69` | If `_DASHBOARD` missing, skip Grafana loop; keep console `queries.ts` checks |
| `scripts/check_evidence_readiness.py:128` | `_GRAFANA_DIRS = ("grafana/",)` only (jarvis-dev). G3 command text → `grafana/jarvis_dev/verify_panels.py` |
| `scripts/test_check_evidence_readiness.py:93` | G3 still valid for `grafana/` |
| `agents/bhaga/scripts/test_status.py:365-411` | Skip `TestGrafanaContractInSync` when `dashboard.json` absent |
| `scripts/check_doc_freshness.py:201-220` | Drop `dashboard.json` from coupling; grafana `*.py` → README “BHAGA retired; jarvis-dev + screenshot upload” |
| `.github/claude-review-guidelines.md:125-139` | D2b: BHAGA Grafana retired; G3 only for `grafana/jarvis_dev` |

**Verify:**
```bash
python3 scripts/check_live_labor_cost.py
python3 -m pytest scripts/test_check_evidence_readiness.py scripts/test_check_live_labor_cost.py scripts/test_verify.py agents/bhaga/scripts/test_status.py -q
```

## Milestone 3 — Docs lock-step + evidence — Sonnet then Composer for copy

Update: `RUNBOOK.md` (~1326–1454 Grafana BI operator path → Operator Console), `AGENTS.md:68`, `agents/bhaga/grafana/README.md`, `.cursor/rules/bhaga.mdc` Grafana evidence / panel-83 as console `/inventory`, `docs/operator-console/ARCHITECTURE.md:36-37`, `docs/operator-console/EXECUTION.md` §7, `docs/contributing/prod-changes.md:16-24`, `docs/contributing/gcp-access.md` Grafana README pointer, `docs/WORKFLOW.md:190,365`, `docs/operator-console/PLAN.md` coexistence, `agents/bhaga/scripts/README.md` + `status.py` comments, `PROGRESS.md` dated line, `skills/grafana_cloud_provisioning/register.py:32` workflow name.

`docs/contributing/cost.md:20` **unchanged** (jarvis-dev URL).

PR mechanics: branch `fix/take-the-grafana-down-as-we`; `gh pr create --base main`; bot `jarvis-agent-bot328`; babysit; never self-merge; `Closes #276`.

**Verify:**
```bash
python3 scripts/check_doc_freshness.py --base origin/main
python3 scripts/verify.py --full
python3 apps/operator-console/scripts/capture_evidence.py --path /home --label i276-home
python3 apps/operator-console/scripts/capture_evidence.py --path /sales --label i276-sales
python3 apps/operator-console/scripts/capture_evidence.py --path /labor --label i276-labor
python3 apps/operator-console/scripts/capture_evidence.py --path /inventory --label i276-inventory
```

## Per-scenario evidence (PR §4)

1. **Happy — operator path:** hosted console screenshots `/home` `/sales` `/labor` `/inventory`.
2. **Happy — BHAGA Grafana gone:** workflow file absent; `GET` uid `bhaga-analytics-v1` 404 (or post-merge if token missing in this session).
3. **Happy — jarvis-dev kept:** workflow + `docs/contributing/cost.md` URL unchanged.
4. **Failure — no GRAFANA_API_TOKEN:** `verify.py --full` green; nightly/console do not call Grafana.
5. **Legacy:** no BQ migration; `check_live_labor_cost.py` still guards console queries.

Post-merge (read-only):
```bash
curl -s -o /dev/null -w "%{http_code}" https://steadyangelfish2985.grafana.net/d/bhaga-analytics-v1/bhaga-analytics
# expect 404 or login-with-no-dashboard
python3 grafana/jarvis_dev/verify_panels.py   # still OK if token present
```
