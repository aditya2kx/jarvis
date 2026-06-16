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
- The "end-to-end test" evidence is a **prod-like e2e run against isolated sandbox** with recorded
  output — not just unit tests. Unit tests are necessary but are not the proof of doneness. Flag PRs
  whose only evidence is "unit tests pass".
- **Standard route to >=95% for infra changes: a targeted live-sandbox scenario.** When the changed
  code path touches BQ writes, Firestore state, Cloud Run env injection, the OTP gate, or any other
  infra layer that unit tests can only mock, the correct evidence is a **targeted `sandbox_scenarios`
  scenario** run on the real `bhaga-sandbox-refresh` Cloud Run job + real Firestore. This reads prod /
  writes only sandbox (`bhaga_sandbox` + `sandbox_runs`). The scenario is scoped to the changed path
  via `skip` (drop all steps beyond the gate), a precondition seed (e.g. `_seed_stale_pending_otp`),
  and a `verify` gate that reads Firestore/BQ state after the run. See `CONTRIBUTING.md` §4 Tier 2 and
  `RUNBOOK.md` §13 for the 3-step recipe.
- For a **gate-only infra change** (e.g. OTP checkpoint, force re-prompt, Cloud Run env injection):
  the gate fires before any browser launch, so the proof is cheap (no scrape, no operator OTP reply,
  the job exits `EXIT_PENDING`). The verify reads the Firestore checkpoint state and asserts the infra
  behavior fired. This counts as **real execution** against the live stack.
- A direct-against-prod run is acceptable only when sandbox isolation can't exercise the path.

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

**Evidence gaps must name a specific command, not a vague ask.** When evidence is insufficient, the
**Evidence gaps** section must name the concrete targeted action the author should take, not a generic
"run a Cloud Run job." Use one of these forms:
- "Add a `sandbox_scenarios` scenario scoped to `<changed path>` (with `skip=<steps>` and
  `verify=<gate>`), run it via `.github/sandbox-live.yml` + the `sandbox-live` label, and paste the
  auto-posted PR evidence comment into §4." — use this for OTP gate, Firestore state, Cloud Run env,
  or any infra path unit tests can only mock.
- "Add the `run-sandbox-e2e` label and confirm the `Sandbox e2e` CI check posts a green summary with
  tip-pool conservation passing." — use this for core model/allocation changes.
- "`bq query --project_id jarvis-bhaga-prod 'SELECT …'` + paste the output." — use this for BQ writes
  where a point-in-time query is sufficient.
A targeted sandbox run that exercises the **exact** changed path with a seeded precondition and a
post-run verify gate (Firestore/BQ state) counts as 95-100% real-execution evidence.

## D2b. Grafana dashboard changes — push to prod as part of the PR, post evidence

If this PR modifies `agents/bhaga/grafana/dashboard.json`:

1. **Push to prod Grafana before requesting review.** Run from repo root:
   ```
   python3 agents/bhaga/grafana/deploy.py --org-slug steadyangelfish2985
   ```
   This binds the real datasource UID and pushes the dashboard to the live Grafana Cloud org.
   Output should end with:
   ```
   [bhaga-grafana-deploy] Dashboard deployed: https://steadyangelfish2985.grafana.net/d/bhaga-analytics-v1/bhaga-analytics
   ```

2. **Required evidence in §4:** paste the deploy output (the `[bhaga-grafana-deploy] Dashboard deployed: <url>` line) and a direct link to the affected panel(s) in prod Grafana so the reviewer can open them. For visual changes (axis caps, colors, panel layout) also attach a screenshot — the reviewer cannot see Grafana directly.

3. **Flag as REQUEST CHANGES** if `dashboard.json` changed but §4 has no deploy output and no Grafana URL evidence. "Will sync on merge" is not acceptable — the push is cheap (one command, <5s) and must happen before review so the reviewer can verify prod reflects the change.

4. **`grafana-dashboard-sync.yml` on merge is a safety net**, not the primary deploy path for reviewed changes. If the PR was pushed before this workflow existed, or the workflow failed, re-run `deploy.py` manually.

## D2c. Cost & cleanup discipline (from CONTRIBUTING.md § Design & execution principles)
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
