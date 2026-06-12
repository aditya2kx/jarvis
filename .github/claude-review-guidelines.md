# Claude PR review guidelines (Jarvis / BHAGA)

This is the rubric the automated **Claude Opus (medium thinking)** reviewer follows on every PR
(`.github/workflows/claude-review.yml`). It is also the human-readable contract for
what a good PR here looks like. Keep it current as invariants evolve.

The reviewer's job is to protect **correctness, backward compatibility, and the
self-documenting nature of this repo** — not to nitpick style. Flag real issues only.

---

## A. PR description completeness (gate)
The PR description MUST have all five sections from `.github/pull_request_template.md`,
**filled in, not placeholder text**:
1. **What** the change is.
2. **Motivation.**
3. **End-to-end test with evidence** — actual command + output / sheet diff / log, not "should work".
4. **Backward compatibility + proof** — an explicit claim AND evidence (flag default, additive header
   diff, or legacy suite green).
5. The checklist, with the boxes honestly reflecting reality.

If any section is missing, empty, or still contains the template comments → **REQUEST CHANGES** and
say which section.

## B. Backward compatibility (the headline ask)
- New behavior that changes an existing flow should be **behind a feature flag** (pattern:
  `JARVIS_THREAD_CONVERSATION_ENABLED`), defaulting off, so the legacy path is unchanged until cutover.
- **Schema changes must be additive.** Adding a column is fine (`_reconcile_header` auto-migrates).
  Reordering or removing a column breaks existing sheets — flag it as breaking.
- The nightly `daily_refresh` and every existing Model/raw tab must still work unchanged. If the PR
  claims backward compatibility, confirm the proof actually demonstrates the legacy path (existing
  tests green, or a legacy run) — don't accept an unsubstantiated claim.

## C. BHAGA correctness invariants (from `.cursor/rules/bhaga.md`)
- **Money:** `Decimal`, cents precision, round-half-up. No float arithmetic on money. Allocated totals
  must reconcile to the pool (no cents lost/created).
- **Idempotent writes:** upsert by natural key. A re-run must overwrite, never duplicate. Flag any new
  write path that appends without a key or could double-count.
- **Pool-by-day fairness:** tip allocation is computed per day the team earned, weighted by hours.
- **Timezone = `America/Chicago`** for ALL date/time selection (Square, ADP, reviews). Flag any naive
  `datetime.now()`, UTC, or local-tz date math that drives a date window or a sheet date.
- **Cloud = GCS, never laptop files.** Prod/cloud data comes from GCS `bhaga-scrape-cache`; secrets from
  Secret Manager. Flag any prod path reading `extracted/downloads/` or macOS Keychain, or a backfill
  reading laptop `extracted/downloads/` for prod (backfill defaults to GCS-only).
- **Config-driven:** sheet IDs / store specifics come from `store-profiles/<store>.json` — flag hardcoding.

## D. Testing & verification
- Tests added/updated; new code should be ~**100% covered**.
- The "end-to-end test" evidence is a **prod-like e2e run against isolated sandbox sheets** (for BHAGA,
  the per-PR `Sandbox e2e` workflow / `agents/bhaga/scripts/sandbox_e2e.py`) with recorded output — not
  just unit tests. A direct-against-prod run is acceptable only when sandbox isolation can't exercise
  the path. Unit tests are necessary but are not the proof of doneness. Flag PRs whose only evidence is
  "unit tests pass".

## D2a. Evidence confidence rating (required in every review)

For every PR, after reading §4 End-to-end test, the reviewer MUST produce a confidence rating and
include it in the summary comment. This is the primary mechanism for ensuring evidence quality is
actually evaluated, not just checked for existence.

**Rating scale:**
| Score | Meaning |
|---|---|
| 100% | Real prod/sandbox execution output covering **all** changed code paths with actual values shown |
| 95–99% | Real execution covering the main paths; only trivially unreachable or purely defensive branches uncovered |
| 80–94% | Unit tests + strong structural argument; only minor uncovered paths (e.g. error branches with no side effects) |
| 50–79% | Unit tests only; OR evidence covers only the happy path; OR evidence is for adjacent code not the exact changed path |
| < 50% | No meaningful evidence, evidence describes expected behavior without showing actual output, or evidence contradicts other findings |

**What the reviewer must output in the summary:**
```
### Evidence confidence: XX%
**Proves:** [list what real output / real execution demonstrates]
**Does NOT prove:** [list changed code paths with no real execution evidence]
**Evidence gaps (run these to close the gap):**
- `<specific command with expected output shape>`
- `<specific command with expected output shape>`
```

**Blocking threshold:** confidence < 95% → BLOCKING → REQUEST CHANGES. The evidence gaps section
becomes the exact requirement the author must satisfy before re-review.

A separate CI step ("Evidence confidence gate") also parses this score from your summary comment
and fails the check if the extracted percentage is below 95. This means the numeric score you
write is machine-read — write it accurately.

**Why unit tests alone are < 100%:** unit tests mock the environment; they prove the logic compiles and
the mocked paths return expected values. They do NOT prove: (a) the real BQ/Sheets/Cloud Run/gcloud
auth chain works, (b) the change integrates correctly with upstream data, (c) the feature is actually
observable by the end user (Grafana panel, Slack message, sheet update). For infrastructure changes
where real execution is cheap (a BQ query, a Cloud Run job, a sheet read), always require it.

## D2b. Cost & cleanup discipline (from CONTRIBUTING.md § Design & execution principles)
- **Token / cost:** flag obvious cost regressions — per-row network calls, unbounded LLM turns,
  full-tab rewrites where an incremental upsert fits, missing batching/caching.
- **Cleanup:** if this PR adds a feature flag or a parallel path, there should be a plan to remove the
  old path (an explicit cleanup milestone). Flag dead flags / abandoned forks left behind.

## E. Security
- **No secrets / PII committed:** tokens, passwords, API keys. (Sheet IDs and the operator's own email
  are config and acceptable — but flag any *new* credential-looking value.)
- **Don't flag `git push --no-verify` as a process violation.** On this personal repo a clean
  `--no-verify` push past the enterprise pre-push hook is the operator-sanctioned procedure (see
  CONTRIBUTING.md "Pushing & opening PRs… gotchas"). Only raise it if the diff actually contains a
  secret.

## F. Docs lock-step
- Coupled docs must move with code. The `doc-freshness` CI check (`scripts/check_doc_freshness.py`)
  reports this; if it flagged a missing doc and the PR didn't address it, call that out.

---

## Output format
- Post **inline comments** on the exact diff lines where issues occur (use
  `mcp__github_inline_comment__create_inline_comment` with `confirmed: true`).
- Post **one top-level summary** via `gh pr comment` with:
  - A one-line **verdict**: `APPROVE`, `COMMENT`, or `REQUEST CHANGES`.
  - A short checklist of sections A–F with ✅ / ⚠️ / ❌.
  - **Evidence confidence rating** (always required — see D2a): score, what it proves, what it doesn't, suggested additional commands if < 100%.
  - Critical issues first; suggestions last.
  - "Optional (non-blocking)" section if any optional findings.
- Be specific and actionable. Do **not** comment on pure style/formatting/naming unless it causes a
  genuine readability or correctness problem. Skip issues already raised in existing PR comments.
