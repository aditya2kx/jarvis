# Claude Opus review bot

## What it does
`.github/workflows/claude-review.yml` runs on every PR push.  It reads the diff +
PR description + `CONTRIBUTING.md` + `.github/claude-review-guidelines.md` and posts
inline comments.  It checks:
- PR description completeness (§1–§6)
- Evidence confidence (§4 claims match the diff)
- BHAGA correctness invariants
- Code quality (no hardcoding, idempotency, etc.)

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
