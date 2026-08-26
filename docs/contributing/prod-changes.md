# Production changes — additive-first, feature-flagged when risky

## Additive prod data-source exception
Changes that **only add** a new data source or a new column/metric (and cannot
silently produce wrong numbers on existing rows) may be tested against the live
Palmetto store data during development — before the PR is merged.  This is the
exception; the default is the isolated sandbox.

## Feature-flag decision rule
Ask: *"Could this change silently produce wrong numbers on existing data?"*
- YES → flag it.  Gated behind `FEATURE_FLAGS.md` entry + flag in `config.yaml`.
- NO  → no flag needed (additive: new column, new metric, new endpoint).

When in doubt, flag it.

## Grafana dashboard changes

**BHAGA Analytics Grafana is retired (Issue #276).** Store UI is Operator Console.
Do not push `agents/bhaga/grafana/dashboard.json` (file removed).

Jarvis Development (PR cost) is still Grafana Cloud:

```bash
python3 grafana/jarvis_dev/deploy.py
```

CI: `.github/workflows/grafana-jarvis-dev-sync.yml` on merge when `grafana/jarvis_dev/**` changes.
Console PR screenshots: `apps/operator-console/scripts/capture_evidence.py`.

## Post-merge verification
After the operator merges:
1. Wait for `deploy.yml` to finish (watch GitHub Actions).
2. Run `python3 -m agents.bhaga.scripts.status --store palmetto` to confirm all layers are fresh.
3. Re-read the affected sheet(s) and Firestore markers; diff expected vs actual.
4. Log the result in `PROGRESS.md`.
