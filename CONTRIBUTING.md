# Contributing — the PR process (all agents, all chat spaces)

This applies to every change, regardless of which IDE / model / chat space you're in
(Opus, Sonnet, cheaper models, cloud agents — all the same). It exists so work from many
sessions stays safe and reviewable.

## Ownership model (why this process exists)

The goal is a clean split of ownership that lets the operator step back:

- **The agent owns the entire development, end to end.** Once requirements are agreed, you own
  everything: building, tests, the prod e2e, recording evidence, opening the PR, getting **all CI
  green**, and **addressing every review comment — from humans and from the Claude bot — autonomously,
  iterating until the PR is merge-ready, with no operator intervention.** Don't hand back a half-done
  PR and wait. Drive it to green: read the comments/failing checks, fix, re-push, repeat. If a comment
  is wrong, reply with why; if it's right, fix it.
- **You owe the operator two kinds of evidence:**
  1. **Evidence of understanding** — *before* building, prove (via Ask + Plan) you understood every
     requirement. Restate it back, surface ambiguities, get agreement.
  2. **Evidence it works** — *during/after* building, present enough proof (prod e2e output, sheet
     diffs, logs) to **convince** the operator the requirements are actually met. The burden of proof
     is on you, not on the operator to go verify.
- **The operator owns only the final sign-off.** Their job shrinks to: give requirements incrementally,
  and own the final PR — reviewing and merging **once all CIs are green and every comment is
  addressed.** They are the final approver, not your debugger or your tester.
- **Full agency ≠ no guardrails.** You still pause for the must-ask categories — destructive /
  irreversible actions, scope changes, genuine architecture forks, external-service / secret config.
  Your agency is over the *how* and the *loop*, never over scope or risk.

## The development loop (how agents should work)

Follow this for any non-trivial feature. It's designed so the agent can self-correct in a
tight build → verify → fix loop without the operator babysitting every step.

1. **Take requirements incrementally — Ask mode first.** Don't jump to code. Stay in **Ask /
   read-only mode** until you *fully* understand the ask. Pull requirements from the operator in
   increments, ask clarifying questions, and restate your understanding before proposing anything.
2. **Plan mode before implementing.** Switch to **Plan mode** and present the *entire*
   implementation plan for approval. No code until the plan is agreed.
3. **Plan = 3–4 milestones, max — each independently verifiable.** Every milestone must end in a
   state you can **verify and fix on your own**, so you can run the build→verify→fix loop yourself
   (the operator isn't in the loop for routine correction). If a milestone can't be closed by your
   own verification, it's too big — split it. Include the per-milestone test plan (what you'll run
   to prove it) in the plan.
4. **Verify with a real end-to-end run against prod — not just unit tests.** The proof a milestone /
   PR works is a **prod (or prod-like) e2e** with recorded evidence. Unit tests are necessary but are
   *not* the evidence of doneness.
5. **100% code coverage.** New code is fully covered by tests; the e2e is on top of that, not instead.
6. **Record and present evidence in the PR.** Every claim ("it works", "it's backward compatible") is
   backed by commands + output / sheet diffs / logs in the PR description (template §3 and §4). If the
   reviewer or operator can't *see* what you did, it didn't happen.

## Design & execution principles

- **Make the system iteratively more stable and configurable.** Each change should leave things more
  robust and more config-driven than it found them — never add one-off hardcoding or a new fragile
  path. Prefer small, reversible steps that compound.
- **Be mindful of tokens and cost — build *and* prod.** During the build: don't thrash or burn tokens;
  plan before you act. In prod: batch Sheets/API calls, bound LLM turns, cache, avoid per-row network.
  Call out the cost implication of a design in the plan and PR.
- **Backward compatible by default; feature-flag; then clean up.** New behavior that changes an
  existing flow goes behind a feature flag (default off), schema changes are additive, and you *prove*
  the legacy path still works. Make the **cleanup** (remove the flag / retire the old path) an explicit
  final milestone — don't leave dead flags and forks lying around.

## The rules

1. **Never push to `main` directly.** `main` is the deployed branch (push to `main` → image
   rebuild → prod, see `RUNBOOK.md` §9). All change lands via PR.
2. **Work on a branch, open a PR.**
   ```bash
   git checkout -b <type>/<short-desc>      # feat/ fix/ docs/ chore/ refactor/
   # ...edit, then:
   python3 -m pytest agents/bhaga/scripts/ skills/ core/ cloud/   # build + verify (don't ask — just do it)
   python3 scripts/check_doc_freshness.py                          # docs in lock-step
   git push -u origin HEAD
   gh pr create --base main --fill   # then complete the template (see below)
   ```
3. **Fill in the PR template completely** (`.github/pull_request_template.md`): **what** the change is,
   **motivation**, **end-to-end test with evidence**, and **backward compatibility + proof**. Empty or
   placeholder sections get a REQUEST CHANGES.
4. **The Claude Opus reviewer bot runs automatically** on every PR and posts inline + summary comments
   (see below). The agent addresses every finding autonomously (fix, or reply why not) and re-pushes —
   looping until the PR is merge-ready.
5. **The agent NEVER merges. Only the operator merges.** The agent's job ends at *merge-ready*: all CI
   green (`doc-freshness`, tests, Claude review ran), no unresolved `REQUEST CHANGES`, and the PR
   description complete. The agent then stops and hands the PR to the operator. The **operator** does
   the final review and squash-merge to `main` (which triggers deploy). Merging is the human sign-off —
   never automate it, never ask the operator to delegate it to you.

> Bootstrapping note: branch protection (server-side enforcement of "no direct push to `main`") is a
> GitHub **settings** change — see "Enabling enforcement" below. Until it's enabled, rule 1 is a
> convention; please honor it.

## The review bot (Claude Opus)

- Workflow: `.github/workflows/claude-review.yml`. Triggers on PR `opened` / `synchronize` / `reopened`.
- Model: **Claude Opus** (`--model opus`), cost-bounded with `--max-turns` and per-PR `concurrency`
  cancellation. It reads the diff + PR description and posts inline comments + a summary verdict.
- **What it looks for** is the rubric in `.github/claude-review-guidelines.md` — PR-description
  completeness, backward compatibility (feature-flagged / additive schema / legacy path proven), BHAGA
  correctness invariants (Decimal money, idempotent upserts, `America/Chicago`, GCS-not-laptop,
  config-driven), testing, security (no secrets/PII), and docs lock-step. Edit that file to change what
  the bot enforces.
- **Dormant until activated:** the job no-ops unless the repo secret `ANTHROPIC_API_KEY` is set, so the
  workflow itself costs nothing to merge.

## Enabling enforcement (one-time, requires repo admin)

These are GitHub-side and can't be done from the repo alone:

1. **Add the API key:** repo → Settings → Secrets and variables → Actions → New secret
   `ANTHROPIC_API_KEY` (an Anthropic key with Opus access). Cost note: this spends Anthropic API credits
   per PR review.
2. **Protect `main`:** repo → Settings → Branches → add a rule for `main`:
   - Require a pull request before merging.
   - **Require approvals (1)** — so a PR can't be merged without the operator's explicit review approval.
     This is what makes "the agent never merges, only the operator merges" a hard, server-side gate
     rather than a convention. (Leave "allow specified actors to bypass" unset.)
   - Require status checks to pass: `Doc Freshness`, the test job, and (optionally) `Claude review`.
     These appear in the picker only after they've run once (open one PR first).
   - Block direct pushes (don't allow bypass, or restrict who can).

Once these are set, the process is enforced server-side, not just by convention: every change needs a
PR, green CI, and **your** approval before it can merge.
