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

## Grafana evidence gate (G3)

When any file under `agents/bhaga/grafana/` or `grafana/` changes, `check_evidence_readiness.py`
requires `§4` to contain **all three**:

1. A viewable `https://` screenshot URL (e.g. GitHub releases PNG via `capture_screenshot.py`).
2. `verify_panels.py` output (`OK=N`).
3. An explicit OK mention for **each changed panel id** — e.g. `panel 76 executed OK` or `76 ... OK`.

The changed panel ids are extracted from the diff of `dashboard.json` files in both grafana
directories. A PR that changes panel 76 but only writes `OK=19` without mentioning panel 76
specifically will fail the gate.

```bash
# Capture + verify for changed panel(s):
python3 agents/bhaga/grafana/capture_screenshot.py --panel 76 --label my-change
python3 agents/bhaga/grafana/verify_panels.py
```

## Operator Console evidence gate (G5)

When application code under `apps/operator-console/{app,components,lib}/` changes (excluding
`*.test.*`), `check_evidence_readiness.py` requires `§4` to contain a viewable `https://`
screenshot URL of a **working scenario**. `Evidence tier: unit-only` **cannot** waive this.

```bash
# Local console (BYPASS_IAP_EMAIL set) then:
python3 apps/operator-console/scripts/capture_evidence.py \
  --path /payroll --label payroll-unpaid-default \
  --path '/payroll?period=2026-06-15' --label payroll-paid-viewonly
```

## Fetching the latest bot comment (pagination)

All three gates that read `claude[bot]` comments — bootstrap `has_prior`, the
verdict gate, and the evidence-confidence gate — must fetch **every** page of
`gh api repos/.../issues/$PR_NUMBER/comments`, not just the first 30. A bare
`gh api ... --jq '... | last'` silently truncates on PRs with 30+ comments and
can gate on a stale round's verdict/score instead of the latest one. The fix:
`gh api --paginate ... --jq '.[]'` (unrolls each page to one object per line,
so pages concatenate into flat NDJSON) piped into `jq -s '...'` for the actual
`select`/`last` logic (`--slurp` is not combinable with `--jq` in `gh api`).

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
This enumerates every blocking signal:
- **unresolved_threads** — inline review-comment roots with no reply, classified as `claude-bot` / `bugbot` / `human`.
- **failing_checks** — CI checks in FAILURE / ERROR / CANCELLED state, each with an inline `log_tail` (last 50 lines of `gh run view --log-failed`) so the agent can diagnose without leaving the terminal.
- **pending_checks** — CI checks still in PENDING / IN_PROGRESS / QUEUED / WAITING state. These are blocking ("wait, don't push yet"). Race-safety guarantee: if a pending check later fails, the merge-protection gate blocks the merge and the next `pr_triage` round surfaces it in `failing_checks`.
- **merge_status** — BEHIND base or DIRTY (merge conflict) flags.
- **claude_verdict** — latest Claude bot verdict + evidence-confidence score. The blocking floor is 95% by default, lowered to 80% when the PR carries `Evidence tier: unit-only (waiver: ...)` in its body or an `evidence-waiver` label (mirrors `check_evidence_confidence.py`).

Add `--json` to get machine-readable output including all sections.

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
