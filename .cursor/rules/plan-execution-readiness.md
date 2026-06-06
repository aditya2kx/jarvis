---
description: Review/refine a plan so a lower-tier LLM (Sonnet/Composer) can execute it with zero additional research. Invoke when the operator says "make this plan execution-ready" or "review this plan for a lower-tier/dumb LLM".
---
# Plan execution-readiness review

Apply this checklist to the current plan. Edit the plan in place until every box is satisfiable. Derive everything from `CONTRIBUTING.md` + `.cursor/rules/bhaga-principles.md` and cite what you used.

A plan is execution-ready when a Sonnet/Composer model could implement it without opening any file you did not cite:

1. Every change cites the exact file path, line number, function/symbol, and (for new code) the full signature or DDL.
2. Concrete artifacts are inline: SQL DDL, function stubs, exact CLI commands, env vars, migration numbers.
3. Milestones are 3-4 max, each independently verifiable, each with its own test command and pass criterion (CONTRIBUTING dev-loop).
4. Per-scenario evidence is enumerated (happy path + each failure/recovery + legacy), not "all green".
5. Sandbox tier is stated: Tier-1 e2e always; Tier-2 live run iff a live-only path changes.
6. Invariants are explicitly preserved (idempotent upserts, integer cents, America/Chicago, read-only ADP, sandbox isolation).
7. Feature-flag decision is made via the "can it silently produce wrong numbers?" test, with FEATURE_FLAGS.md entry if flagged.
8. Docs lock-step targets are listed (RUNBOOK/README/DOMAIN/bhaga.md/PROGRESS) + `check_doc_freshness.py`.
9. Branch/PR mechanics noted (one branch = one coherent change; `--no-verify` push; bot account; never self-merge; reply-to-every-comment gate).
10. Model routing per milestone (Composer/Sonnet/Opus) per the cost playbook; one chat per PR.

Output: the edited plan. If a requirement cannot be made concrete without a decision, ask the operator ONE focused question rather than guessing.
