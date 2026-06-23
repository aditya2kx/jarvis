# Per-PR cost ledger and model routing

## Cost ledger
Every PR has a cost ledger in BigQuery (`jarvis_dev`).  The `pre-commit` hook
captures review cost automatically.  You must record build cost manually:

```bash
python3 scripts/pr_cost_ledger.py record-build --pr N --cost-usd X.XX --model sonnet
```

Before your final push, bind the branch to the PR number and sync:
```bash
python3 scripts/pr_cost_ledger.py bind-pr --branch <branch>
python3 scripts/pr_cost_ledger.py sync --pr N
git add metrics/pr_cost/ && git commit -m "chore(cost): sync PR #N ledger"
```

The `pr-cost-gate.yml` CI check reads BQ — zero build cost is a hard failure.

View cost data: https://steadyangelfish2985.grafana.net/d/jarvis-dev-cost-v1/jarvis-development

## Model routing
Use the cheapest model that does the job well.  Escalate only when stuck.

| Task | Model |
|---|---|
| Feature work, refactors, doc edits | **Sonnet 4.6 medium** (default) |
| Complex logic, architecture decisions | Sonnet 4.6 medium thinking |
| Hard bugs, plan reviews, code review | **Opus 4.8** (escalate explicitly) |
| Doc-only changes, table of contents | Composer 2.5 |

One chat per PR — each new PR gets a fresh Cursor chat.  Reusing a merged PR's
thread drags its full history into every turn at cache-read cost ($0.50/M on Opus).

## Session start
```bash
python3 scripts/new_requirement.py --requirement "Description" [--dry-run]
```
This creates a worktree, starts a cost-tracked session, seeds the phase ladder
brief, and opens Cursor in a new window.
