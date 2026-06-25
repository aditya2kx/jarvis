# Sandbox tiers and per-scenario evidence

## Evidence contract (PR §4)
Before the agent starts building, the operator approves a list of *acceptance
evidence*.  The agent then assembles that exact evidence and pastes it into PR §4.
`check_pr_description.py` enforces the section exists; the review bot checks that
the evidence is plausible (evidence-confidence gate).

**Never invent evidence after the fact.** Agree on it first.

## Sandbox tiers

| Tier | When | How |
|---|---|---|
| **Tier 1 — e2e** | Always required | CI sandbox via `.github/workflows/sandbox-e2e.yml`; `sandbox-e2e` label on the PR. |
| **Tier 2 — live run** | Only when a live-only path changes | Manual trigger via `.github/workflows/sandbox-live-run.yml`; `sandbox-live` label. Teardown after evidence is captured. |

Never run a live scenario against prod data.  The sandbox uses isolated targets.

## Per-scenario evidence checklist
Each scenario should produce:
1. **Happy path** — the expected output exists (screenshot / log excerpt / diff of the sheet row / API response).
2. **Failure recovery** — trigger the failure condition, verify the system recovers to the expected state.
3. **Legacy / idempotency** — re-running on existing data produces the same output (no double-writes, no wrong numbers).

Evidence goes in PR §4 as quoted command output or a link to the auto-posted sandbox comment.
