# PR #23 session brief

## Requirement
Requirements HTML report: render Playground/REQUIREMENTS.md as a standalone HTML page (like report.html for costs) — status colour-coded rows, filterable by status, auto-generated and committed on every change to the tracker.

## Branch
`(unknown branch)`

## Session started (cost attribution anchor)
`2026-06-04T03:16:44+00:00`

Open a **new** Cursor chat for this PR, then implement. Build cost is attributed to
chat space(s) with AI edits after this timestamp (see `pr_cost_ledger.py sync`).

## Prior PR cost reference
PR #22 'chore: requirements tracker + jarvis.md HL#22 + PR-21 cost l': $0.49 total (build $0.49 / review $0.00, 0 review runs)

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
Before your final push: `python3 scripts/pr_cost_ledger.py sync --pr 23`
Then: `git add metrics/pr_cost/ && git commit -m "chore(cost): sync PR #23 ledger"`
