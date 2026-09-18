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

When any file under `grafana/` (Jarvis Development dashboard) changes,
`check_evidence_readiness.py` requires `§4` screenshot URL + `grafana/jarvis_dev/verify_panels.py`.
BHAGA Analytics Grafana is retired (Issue #276); `agents/bhaga/grafana/` no longer triggers G3.

```bash
python3 grafana/jarvis_dev/verify_panels.py
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

### Interpolating the PR number into the gate's `gh api` path

The verdict gate's body is a **quoted** heredoc (`python3 - <<'PYEOF'`), so the
shell performs no expansion inside it. `$PR_NUMBER` therefore reached `gh` as a
literal four-character string and every request 404'd:

```
##[error]Could not read PR comments, so the Claude verdict is unknown.
gh: Not Found (HTTP 404)
```

Read the value from `os.environ` instead. `${{ github.repository }}` in the same
path was fine — Actions substitutes its expressions before the shell ever runs —
which is what made the bug look selective. Because the gate fails closed, no
unreviewed PR was waved through; the cost was that a genuine `REQUEST CHANGES`
was indistinguishable from an API outage.

Anything the gate needs inside that heredoc must come from `env:` and be read
with `os.environ`, or the heredoc must be unquoted (which then requires escaping
every `$` in the Python body — don't).

## Bootstrap mode: a PR that edits this workflow is never reviewed

The "Detect workflow bootstrap" step sets `is_bootstrap=true` when the PR's diff
touches `.github/workflows/claude-review.yml`, and that flag gates **every**
review step *and* both the verdict and evidence-confidence gates. The
`Claude review` check then goes **green in ~10 seconds having reviewed nothing**.

This exists because `anthropics/claude-code-action` only runs when the workflow
is byte-identical to `main`, so it is not a bug — but it has a sharp consequence:

> **Never bundle a `claude-review.yml` change with substantive code.** The whole
> PR ships unreviewed, and the green check makes that invisible.

Land the workflow change in its own PR (it will be bootstrap-green, which is
correct for a workflow-only diff), merge it, then rebase the code PR onto the new
`main` so it gets a working gate and a real review. Issue #306 hit exactly this:
a one-line gate fix was initially bundled with the ADP receipts work, which would
have silently skipped review of that entire change set.

## Responding to comments
The agent **must reply to every inline comment** — either "fixed in <sha>" or
"won't fix because <reason>".  `check_pr_review_replies.py --pr N` is the gate;
`verify.py --full` runs it when a PR exists.

Batching multiple comments into one summary reply is **not acceptable** — reply
on each inline thread separately.

## Convergence loop (batch, not serial)

Every completed push triggers a paid Claude Opus review (~$2–4).  Serial fix-one-push
cycles mean N pushes = N paid reviews.  Batch all fixes into one push = 1 paid review.

### The turn budget, and why a green check can mean "unreviewed"

The review runs with `--max-turns 30` (raised from 14 after PR #298).  Reviewing A–F
over a large diff costs turns; when the budget runs out the run ends
`error_max_turns`, posts its cost comment, and posts **no verdict**.

Running out used to pass every gate: `Verify Claude review ran` only checked that an
execution file existed (one exists even for an aborted run), and both the verdict and
evidence-confidence gates exited 0 when they could not find a verdict.  A green
`Claude review` therefore meant *either* "no issues" *or* "no review".  On PR #298 it
meant the latter, and `pr_triage.py` compounded it by quoting the **previous** push's
verdict, which predated every file in the commit.

Both paths now fail closed: an aborted run fails `Verify Claude review ran`, and a
missing or unreadable verdict fails the verdict gate.  If `error_max_turns` recurs even
at 30 turns, the diff is too large to review in one pass — **split the PR** rather than
raising the cap again.

Note `pr_triage.py` reports the latest claude[bot] verdict on the PR, not one scoped to
the head SHA.  After a push, confirm the verdict comment is newer than the commit before
treating it as a review of that commit.

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

### The waiver reaches the reviewer, not just the gate

An active waiver (`evidence-waiver` label, or `Evidence tier: unit-only (waiver: <reason>)` in
the body) is resolved **before** the review by the `Resolve evidence-waiver state` step, which
calls `check_evidence_confidence.py --print-waiver` so detection lives in exactly one place, and
passes the result into the prompt as `EVIDENCE WAIVER ACTIVE FOR THIS PR: true|false`.

With the waiver active the reviewer must not return REQUEST CHANGES when a score ≥ 80% is its
only blocking finding; it scores honestly, still lists what the evidence does not prove, and
approves. Without it, < 95% blocks as before.

This exists because the waiver used to be half-wired: `check_evidence_confidence.py` accepted
82% under a waiver while the prompt still said "< 95% is BLOCKING" and the verdict gate grepped
`REQUEST CHANGES`, so a waived PR could not merge at all — two gates reading one number and
disagreeing (#316 / PR #317, where the reviewer reported no defect of any kind). A waiver covers
**evidence depth only**. Correctness bugs, security/PII leaks, data loss, missing tests for new
behavior, invariant violations and unproven backward-incompatible changes stay blocking, and the
verdict gate itself is unchanged.

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
