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

## Convergence loop (batch, not serial)

Every completed push triggers a paid Claude Opus review (~$2–4).  Serial fix-one-push
cycles mean N pushes = N paid reviews.  Batch all fixes into one push = 1 paid review.

**Step 1 — collect all signals in one pass:**
```bash
python3 scripts/pr_triage.py --pr N
```
This enumerates every blocking signal: unresolved inline threads (classified as
claude-bot / bugbot / human), failing CI checks with log links, behind-base / conflict
flags, and the latest Claude verdict + evidence-confidence score.

**Step 2 — fix everything before pushing:**
- Resolve merge conflicts.
- Address every unresolved thread (fix the code or write your rebuttal).
- Fix every failing CI check that is within this PR's scope.

**Step 3 — reply on every thread before pushing:**
```bash
gh api repos/{repo}/pulls/{pr}/comments/{id}/replies -f body='fixed in <sha> / won't fix: <reason>'
```
Each thread must have a reply. `check_pr_review_replies.py` gates on this.

**Step 4 — push once:**
One commit. One push. This triggers exactly one paid Opus re-review.

**Step 5 — re-collect once:**
After the re-review completes, run `pr_triage.py` again.  Only loop back to Step 2
if genuinely new blocking signals appear.  Do NOT re-raise issues that Claude already
approved in a prior round.

The `babysit` skill automates this loop.  Read `~/.cursor/skills-cursor/babysit/SKILL.md`
and follow it — do not hand back to the operator until CI is green and all comments are resolved.
