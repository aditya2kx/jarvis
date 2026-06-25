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
Push the dashboard from your branch *before* opening the PR so the reviewer sees
the live panel:
```bash
python3 agents/bhaga/grafana/deploy.py --org-slug steadyangelfish2985
```
Capture a screenshot for PR §4.  The `grafana-dashboard-sync.yml` CI workflow
re-syncs from `main` on every merge (so the `dashboard.json` in the repo is always
the source of truth — do not use `deploy.py` for anything other than `dashboard.json`).

## Post-merge verification
After the operator merges:
1. Wait for `deploy.yml` to finish (watch GitHub Actions).
2. Run `python3 -m agents.bhaga.scripts.status --store palmetto` to confirm all layers are fresh.
3. Re-read the affected sheet(s) and Firestore markers; diff expected vs actual.
4. Log the result in `PROGRESS.md`.
