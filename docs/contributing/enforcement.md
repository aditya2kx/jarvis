# One-time GitHub enforcement settings

These settings are done once by a repo admin (the operator).  Agents do not need
to re-configure them; they are listed here so any machine can verify the state.

## Branch protection for `main`
`main` must have branch protection enabled:
- Require status checks to pass before merging
- Required checks: `pr-description`, `doc-freshness`, `pr-cost-gate`
- Dismiss stale reviews when new commits are pushed
- Restrict who can push to `main` (only `jarvis-agent-bot328` via PRs)

## Recommended required CI checks (minimal set)
- `pr-description` — PR template completeness
- `doc-freshness` — code↔doc couplings
- `pr-cost-gate` — nonzero build cost recorded in BQ

## GitHub Issue labels (for phase tracking)
Run once per repo to create the `jarvis-work` label set:
```bash
python3 scripts/phase_state.py ensure-labels
```
This is also called by `scripts/install-git-hooks.sh` on every fresh clone.

## Why the "Add checks" picker looked empty
GitHub only shows checks that have run at least once on the repo.  If you see
an empty list when adding required checks, trigger a CI run first (push to a
branch and open a draft PR), then add the checks.
