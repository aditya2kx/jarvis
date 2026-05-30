# Claude PR review guidelines (Jarvis / BHAGA)

This is the rubric the automated **Claude Sonnet** reviewer follows on every PR
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

## D2. Cost & cleanup discipline (from CONTRIBUTING.md § Design & execution principles)
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
  - Critical issues first; suggestions last.
- Be specific and actionable. Do **not** comment on pure style/formatting/naming unless it causes a
  genuine readability or correctness problem. Skip issues already raised in existing PR comments.
