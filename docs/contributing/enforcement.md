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

## Ship-emoji force-merge override
`aditya2kx` (repo OWNER) may post a standalone 🚀 or 🚢 comment on any PR to
bypass **only** the Claude evidence-confidence soft gate (< 95%).

**What it does NOT bypass:**
- Any hard CI check (`pr-description`, `doc-freshness`, `pr-cost-gate`, `secret-scan-staged`, `pytest-changed`)
- A Claude review verdict of `REQUEST CHANGES`
- Unreplied inline review threads (caught by the Claude-review CI step)

**Implementation:** `.github/workflows/ship-emoji-force-merge.yml` (active only
after the PR introducing it is merged to `main`; issue_comment workflows require
the workflow to be on the default branch).

**Authorization:** `SHIP_MERGE_AUTHORIZED_LOGINS` env var (default `aditya2kx`) +
`author_association == OWNER`. Both must match; the bot account is always excluded.

## Post-merge lifecycle
`.github/workflows/pr-merged-lifecycle.yml` runs on every merge:
1. Resolves the tracking issue.
2. Advances `merge` and `post-merge-verify` in `phase_state.py`.
3. Runs read-only §4 post-merge verification commands; posts results as issue comment.
4. Posts a retrospective prompt (speed / cost / accuracy + preference harvest).

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
