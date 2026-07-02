# BHAGA Grafana tooling

`agents/bhaga/grafana/` is dashboard-as-code: `dashboard.json` is the single
source of truth, deployed to Grafana Cloud by CI on every merge to `main`
(`.github/workflows/grafana-dashboard-sync.yml`). This README is the hub for
everything else in this directory — read it before reaching for `gcloud`,
`bq`, or a browser.

## Auth model (read this first — the recurring discovery gap)

**Everything in this directory talks to Grafana Cloud with a Bearer token —
never `gcloud`/ADC/`config.yaml`, never Playwright.**

- Token resolution: `GRAFANA_API_TOKEN` env var (set in CI) → macOS Keychain
  fallback (`service=grafana-cloud-api-token`, `account=<org-slug>`, default
  org slug `steadyangelfish2985`). See
  `skills/grafana_cloud_provisioning/provision.py::get_api_token()`.
- **BigQuery is queried *by Grafana*, server-side**, via its BigQuery
  datasource plugin and the `/api/ds/query` HTTP endpoint — the same path the
  dashboard UI uses to render a panel. Every script below (`verify_panels.py`,
  `capture_screenshot.py`, `compare_panels.py`) sends SQL to that endpoint and
  gets rows back. **None of this needs a local `gcloud` install, Application
  Default Credentials, or `config.yaml`.**
- The **only** operation in this directory that needs cloud BQ credentials
  (a service account, via Workload Identity Federation in CI) is *applying
  schema DDL* — `core.datastore.ensure_schema()`, run by
  `grafana-dashboard-sync.yml`'s "Apply BQ schema migrations" step. If you're
  reading/rendering/verifying/comparing/screenshotting, you don't need it.
- If `agents/bhaga/scripts/status.py` fails locally with
  `config.yaml not found` — that is a **different** tool (the Sheets-era
  freshness doctor) and does **not** mean Grafana tooling is blocked. Every
  script in this directory works from a bare worktree with nothing but a
  Grafana token.

Quick sanity check that the token is available:

```bash
python3 -c "
from skills.grafana_cloud_provisioning.provision import get_api_token
print('token found' if get_api_token('steadyangelfish2985') else 'MISSING — see RUNBOOK.md §14')
"
```

## Tool catalog

| Script | What it does | When to use |
|---|---|---|
| `deploy.py` | Pushes `dashboard.json` to Grafana Cloud; binds the BigQuery datasource UID. Runs automatically in CI on merge to `main`. | Manual re-push / first-time datasource setup (`--datasource-only --create-sa`). |
| `verify_panels.py` | Runs every panel's `rawSql` through `/api/ds/query` and reports OK/EMPTY/ERROR + row counts. | After any `dashboard.json` edit — "does every panel still return data?" |
| `compare_panels.py` | Prod (`origin/main`) vs. branch: full row-for-row diff of every panel's query result. `--mode inline` (default, pre-merge safe) inlines new BQ objects from their migration SQL text instead of requiring them to be deployed; `--mode live` runs branch SQL as-is (post-merge). | Any PR that touches `dashboard.json` and needs to prove "this didn't change the data" (Issue #126 bar). |
| `capture_screenshot.py` | Renders a panel to PNG via Grafana's render API and uploads it to a GitHub release, returning a stable `https://` URL. | PR §4 evidence — visual proof of a panel change. |
| `evidence.py` | Runs `verify_panels.py` + `compare_panels.py` + `capture_screenshot.py` in one command and prints a paste-ready PR §4 markdown block. | The one command to run before opening/updating a PR that touches Grafana. |

```bash
# One-shot evidence for a PR that changed panels 79 and 81
python3 agents/bhaga/grafana/evidence.py --changed-panels 79 81

# Just check nothing broke
python3 agents/bhaga/grafana/verify_panels.py

# Prove a refactor didn't change any panel's data (pre-merge, new BQ objects not deployed yet)
python3 agents/bhaga/grafana/compare_panels.py --base origin/main --mode inline

# Same, post-merge once migrations have applied
python3 agents/bhaga/grafana/compare_panels.py --base origin/main --mode live
```

## Grafana = visualization only (Issue #126)

Every data panel's `rawSql` must be a thin `SELECT ... FROM vw_*|tvf_*(...)`
pass-through — no `WITH`, `UNION`, `JOIN`, window functions, or correlated
subqueries. Business/analytical logic belongs in a `core/migrations/*.sql`
view or table function, not in Grafana. This is enforced by
`scripts/check_grafana_no_logic.py` (wired into `verify.py` and
`grafana-dashboard-sync.yml`); panels not yet converted are tracked in that
script's `WAIVED_PANELS` against a follow-up issue. Rationale: fast panel
load (Grafana should be a thin read, not a compute engine) and portability
(swap Grafana for another tool without re-implementing logic that only ever
lived in a `rawSql` string).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `config.yaml not found` from `status.py` | Different tool (Sheets-era) — not related to Grafana tooling | Ignore for Grafana work; see Auth model above |
| `verify_panels.py` shows `Table-valued function not found` / `Table ... was not found` for `tvf_order_reco` / `vw_order_assistant_table` | Migration `029_order_assistant_functions.sql` hasn't been applied yet (pre-merge) | Expected pre-merge — use `compare_panels.py --mode inline` for pre-merge parity evidence instead |
| `capture_screenshot.py` / `verify_panels.py` fail with 401/403 | Grafana token missing/expired | `python3 -m skills.grafana_cloud_provisioning.provision` to re-check Keychain; regenerate token in Grafana Cloud org settings if revoked |
| Panel shows data in browser but `verify_panels.py` errors | Template variable default drifted from what the browser session has selected | Pass `--var name=value` to override, or check `dashboard.json` → `templating.list` defaults |
