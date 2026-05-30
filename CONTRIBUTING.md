# Contributing — the PR process (all agents, all chat spaces)

This applies to every change, regardless of which IDE / model / chat space you're in
(Opus, Sonnet, cheaper models, cloud agents — all the same). It exists so work from many
sessions stays safe and reviewable.

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
   (see below). Address its findings (fix, or reply why not) before merge.
5. **Merge only when:** required CI is green (`doc-freshness`, tests), the Claude review has no
   unresolved REQUEST CHANGES, and the PR description is complete. Then squash-merge to `main`; the
   deploy runs from `main`.

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
   - Require status checks to pass: `doc-freshness`, the test job, and (optionally) the Claude review.
   - Block direct pushes (don't allow bypass, or restrict who can).

Once both are set, the process is enforced server-side, not just by convention.
