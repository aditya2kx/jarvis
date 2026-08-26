# BHAGA Grafana tooling (retired as operator UI)

The **BHAGA Analytics** Grafana dashboard (`bhaga-analytics-v1`) is **down**
(Issue #276). Palmetto Operator Console is the store UI. Do not push a BHAGA
dashboard JSON.

**Still live:** Jarvis Development (PR cost) at
https://steadyangelfish2985.grafana.net/d/jarvis-dev-cost-v1/jarvis-development
— source `grafana/jarvis_dev/`, CI `.github/workflows/grafana-jarvis-dev-sync.yml`.
The `grafana-bq-reader` SA and Grafana Cloud org stay for that dashboard.

## What remains in this directory

| Script | Why it is still here |
|---|---|
| `capture_screenshot.py` | GitHub release PNG upload (`upload_screenshot`, `_get_github_token`) used by `apps/operator-console/scripts/capture_evidence.py` |
| `deploy.py --delete-bhaga-analytics` | Idempotent DELETE of uid `bhaga-analytics-v1` |

BHAGA `dashboard.json`, `verify_panels.py`, `compare_panels.py`, and
`grafana-dashboard-sync.yml` are gone. New numbers = BQ view + Operator Console,
never Grafana `rawSql`.

Unpublish (if the live dashboard is still present):

```bash
python3 agents/bhaga/grafana/deploy.py --delete-bhaga-analytics
```
