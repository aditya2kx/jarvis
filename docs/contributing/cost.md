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

### How build cost is attributed to a PR

The Cursor usage API is account-global and carries no conversation id, so
`capture-build` has to decide which events belong to this PR. Two signals are
combined, and it helps to know what each one is for:

- **The caller's window** — `[session_started_at, now]`, seeded by
  `start_pr_session.py`. This bounds attribution; widening never escapes it.
- **The conversation's edit window** — from `ai_code_hashes`. This is only a
  *disambiguator* between chat spaces running in parallel.

The two are skewed by construction: usage events are cumulative per-model rows
stamped near the session's first request, while edits land much later (56 min on
PR #298, against a 5 min pad). Intersecting them therefore returned nothing and
the gate saw `$0` — with `validate` simultaneously demanding a `conversation`
attribution mode that `capture-build` could no longer reach.

So `filter_events_for_conversations` widens to the caller's window **when every
edit-active conversation in that window is bound** — there is nothing left to
disambiguate. If an unbound conversation was also editing, the strict edit window
still applies, because billing another chat space's cost to this PR is the
failure mode worth protecting against. The model-tier check applies either way,
which is what keeps a stray `composer-2.5` background request out of an
Opus-only conversation's total.

If the gate reports `$0`, check whether a second chat space was editing in the
same window before reaching for `record-build`.

## Model routing
Use the cheapest model that does the job well.  Escalate only when stuck.

The table below is generated from `scripts/dev_models.py` — the single source of
truth for dev-flow model slugs. To change the default model repo-wide, edit the
constants there and regenerate:
```bash
python3 scripts/dev_models.py emit-routing-md
```
Do not hand-edit the block between the markers; `test_dev_models.py` asserts it
stays in sync.

<!-- dev-models:begin -->
| Task | Model |
|---|---|
| Feature work, refactors, doc edits | Sonnet 5 medium thinking |
| Complex logic, architecture decisions | Sonnet 5 medium thinking |
| Hard bugs, plan reviews, code review | Opus 4.8 thinking medium |
| Doc-only changes, table of contents | Composer 2.5 |
<!-- dev-models:end -->

One chat per PR — each new PR gets a fresh Cursor chat.  Reusing a merged PR's
thread drags its full history into every turn at cache-read cost ($0.50/M on Opus).

## Session start
```bash
python3 scripts/new_requirement.py --requirement "Description" [--dry-run]
```
This creates a worktree, starts a cost-tracked session, seeds the phase ladder
brief, and opens Cursor in a new window.
