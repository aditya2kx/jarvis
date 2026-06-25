# Claude Opus review bot

## What it does
`.github/workflows/claude-review.yml` runs on every PR push.  It reads the diff +
PR description + `CONTRIBUTING.md` + `.github/claude-review-guidelines.md` and posts
inline comments.  It checks:
- PR description completeness (§1–§6)
- Evidence confidence (§4 claims match the diff)
- BHAGA correctness invariants
- Code quality (no hardcoding, idempotency, etc.)

## Full review vs. RE-REVIEW
On the first review of a PR the bot reads the **whole** diff.  On later pushes it
focuses on what changed since its last review (a "RE-REVIEW", driven by
`build_claude_review_context.py`'s delta bundle).  A re-review is only triggered when
**a prior `claude[bot]` review comment actually exists** on the PR — detected by the
"Detect prior Claude review" step in `claude-review.yml` (`has_prior`), which gates
`--prev-head`/`--has-prior-review`.  This avoids the misfire where a first *completed*
review that happened to run on a `synchronize` event (because the `opened` run was
cancelled by the concurrency rule) wrongly labelled itself a re-review.  Unit coverage:
`scripts/test_build_claude_review_context.py::TestEffectiveDelta` /
`TestManifestReReviewText`.

## Responding to comments
The agent **must reply to every inline comment** — either "fixed in <sha>" or
"won't fix because <reason>".  `check_pr_review_replies.py --pr N` is the gate;
`verify.py --full` runs it when a PR exists.

Batching multiple comments into one summary reply is **not acceptable** — reply
on each inline thread separately.

## Convergence loop
1. Push commits.
2. Wait for the bot review (typically 2–3 minutes).
3. Read every comment.
4. Fix or reply.
5. Re-push.
6. Repeat until no unresolved comments.

The `babysit` skill automates this loop.  Read `~/.cursor/skills-cursor/babysit/SKILL.md`
and follow it — do not hand back to the operator until CI is green and all comments are resolved.
