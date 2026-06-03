# PR #16 session brief

## Requirement
Dashboard on a free visualization tool (Chartio / Looker Studio / alternative) — shareable with team, backed by BigQuery if data is already there. Explore what data is in BQ, pick the right tool, build the dashboard.

## Branch
`feat/dashboard`

## Prior PR cost reference
PR #15 'feat(cost): reco engine v2 + tactical playbook + new-chat-pe': $13.03 total (build $12.54 / review $0.49, 2 review runs)

## Model routing (CONTRIBUTING § Cost-efficiency playbook):
  • Sonnet 4.6     — DEFAULT for feature code, refactors, most edits
  • Opus 4.8 med   — Hard multi-file reasoning, subtle bugs, architecture decisions
  • Opus 4.8 high  — Only when genuinely stuck; adds ~30% output tokens vs medium
  • Composer 2.5   — Mechanical: renames, test scaffolding, doc edits, log reading
  Rates (verified 2026-06-03): Opus cache-read $0.50/M · Sonnet $0.30/M · Composer $0.20/M

Context discipline:
  • One chat per PR — do NOT continue the previous PR's thread (cache-read bloat)
  • /clear or new chat between unrelated sub-tasks within the same PR
  • Prefer Plan mode + targeted file reads over open-ended exploration
  • Run `pr_cost_ledger.py sync --pr <n>` before your final push to commit build+review cost

## Cost gate reminder
Before your final push: `python3 scripts/pr_cost_ledger.py sync --pr 16`
Then: `git add metrics/pr_cost/ && git commit -m "chore(cost): sync PR #16 ledger"`
