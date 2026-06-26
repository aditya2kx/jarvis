# Contributing

## Ownership model
Every change lands via PR — no direct pushes to `main`.  The **operator** owns
ideation, requirement jamming, evidence definition, and final merge.  The
**agent** owns everything else: worktree setup, research, planning, build,
verify, evidence capture, PR, and babysit.

## The development loop — as success criteria
A change is **done** when these are all true (not when the agent says so):

1. `python3 scripts/verify.py --full` exits 0 (secret-scan, tests, doc-freshness, PR-desc, review-replies).
2. CI is green on the PR.
3. Every review comment has an inline reply (`check_pr_review_replies.py`).
4. PR §4 contains the agreed acceptance evidence (operator-approved before build started).
5. Operator squash-merges and confirms the post-merge state matches expected.

If verify fails locally, fix it before pushing — never rely on CI as your first feedback loop.

## The 10-phase lifecycle (5 tracking stages)
See **[docs/WORKFLOW.md](docs/WORKFLOW.md)** for the canonical map.
Work is tracked as a GitHub Issue with `stage:*` labels + a progress status block.

| Phase | Driver | Exit gate |
|---|---|---|
| 1 specify | operator | requirement stated |
| 2 setup | agent | worktree + brief + issue exist |
| 3 jam | **operator** | requirements agreed |
| 4 define-evidence | **operator** | PR §4 contract approved |
| 5 plan | agent | `check_plan_readiness.py` passes |
| 6 implement | agent | code + tests written |
| 7 verify | agent | `verify.py --full` green |
| 8 pr-evidence + babysit | agent | CI green + replies done |
| 9 merge | **operator** | operator squash-merges |
| 10 post-merge + retro | agent | prod verified + PROGRESS.md entry |

The agent **self-advances** through agent phases and **pauses** at the 3 operator-reserved gates above.

### Merge paths
- **Normal:** operator reviews and squash-merges via GitHub UI; `auto-merge-on-approval.yml` arms `--auto` on APPROVED review.
- **Ship-emoji override:** `aditya2kx` posts a standalone 🚀 or 🚢 PR comment to bypass the Claude evidence-confidence soft gate (< 95%) when all hard checks pass. See `docs/contributing/enforcement.md`.

### Post-merge lifecycle
After every merge, `pr-merged-lifecycle.yml` advances the phase tracker, runs read-only §4 post-merge verification commands, and posts a retrospective prompt on the tracking issue. The agent completes the retrospective (speed / cost / accuracy grade + preference harvest) in a follow-up chat and closes the issue. See `docs/WORKFLOW.md` § Post-merge lifecycle.

## Define acceptance evidence (operator-reserved step)
Before the agent starts building, the operator and agent must agree on *what
evidence the PR must show*.  The agent drafts a proposal; the operator approves it; it becomes PR §4.  This stops evidence being invented after the fact.

## Local fast loop
```bash
python3 scripts/verify.py --fast     # secret scan + doc-freshness + changed tests
python3 scripts/verify.py --full     # everything: full pytest + PR gates (if PR exists)
```
The pre-push git hook runs `--full` automatically.  See
[docs/contributing/local-loop.md](docs/contributing/local-loop.md).

## Deep references
| Topic | Doc |
|---|---|
| Local verify harness + sub-agent policy | [docs/contributing/local-loop.md](docs/contributing/local-loop.md) |
| Sandbox tiers + per-scenario evidence | [docs/contributing/sandbox-evidence.md](docs/contributing/sandbox-evidence.md) |
| Additive prod changes + feature flags | [docs/contributing/prod-changes.md](docs/contributing/prod-changes.md) |
| Claude Opus review bot | [docs/contributing/review-bot.md](docs/contributing/review-bot.md) |
| Pushing + bot account gotchas | [docs/contributing/push-gotchas.md](docs/contributing/push-gotchas.md) |
| Per-PR cost ledger + model routing | [docs/contributing/cost.md](docs/contributing/cost.md) |
| One-time GitHub enforcement settings | [docs/contributing/enforcement.md](docs/contributing/enforcement.md) |
| End-to-end lifecycle + agent hierarchy | [docs/WORKFLOW.md](docs/WORKFLOW.md) |
