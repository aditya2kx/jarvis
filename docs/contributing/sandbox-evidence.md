# Sandbox tiers and per-scenario evidence

## Evidence contract (PR §4)
Before the agent starts building, the operator approves a list of *acceptance
evidence*.  The agent then assembles that exact evidence and pastes it into PR §4.
`check_pr_description.py` enforces the section exists; the review bot checks that
the evidence is plausible (evidence-confidence gate).

**Never invent evidence after the fact.** Agree on it first.

## Evidence tiers (declare in plan)

Every plan **must** contain an explicit `Evidence tier:` declaration. `check_plan_readiness.py`
enforces this at plan-creation time, before any code is written.

| Tier | Declaration | Sub-field required | Claude floor |
|---|---|---|---|
| **sandbox-live** | `Evidence tier: sandbox-live` | `scenario: <name>` | 95% |
| **sandbox-e2e** | `Evidence tier: sandbox-e2e` | — | 95% |
| **unit-only** | `Evidence tier: unit-only` | `waiver: <reason>` | 80% (lowered by CI gate) |

- `sandbox-live` triggers `.github/workflows/sandbox-live-run.yml` (add `sandbox-live` label).
- `unit-only` is only appropriate for docs/scripts-only changes where no runtime or infra path is
  modified. Add `waiver: <one-sentence reason>` on the same or adjacent line.
- Choosing the wrong tier is caught at plan-creation time, **not** after a multi-commit CI cycle.

The local predictor `scripts/check_evidence_readiness.py --pr N` mirrors the Claude rubric D2a
and exits 1 when §4 appears to be pytest-only with no waiver. Run it before pushing.

## Sandbox tiers (deployment)

| Tier | When | How |
|---|---|---|
| **Tier 1 — e2e** | Always required | CI sandbox via `.github/workflows/sandbox-e2e.yml`; `sandbox-e2e` label on the PR. |
| **Tier 2 — live run** | Only when a live-only path changes | Manual trigger via `.github/workflows/sandbox-live-run.yml`; `sandbox-live` label. Teardown after evidence is captured. |

Never run a live scenario against prod data.  The sandbox uses isolated targets.

> **Remove the `sandbox-live` label the moment evidence is captured.** The label
> triggers `sandbox-live-run.yml` on every `synchronize` (i.e. every push), so each
> later commit re-runs a full live Square+ADP scrape (cost + possible `[SANDBOX]` OTP
> prompt). Capture the evidence line → delete the label → then reset
> `.github/sandbox-live.yml` to `scenarios: []`. Do not leave it armed across the
> babysit loop.

## Per-scenario evidence checklist
Each scenario should produce:
1. **Happy path** — the expected output exists (screenshot / log excerpt / diff of the sheet row / API response).
2. **Failure recovery** — trigger the failure condition, verify the system recovers to the expected state.
3. **Legacy / idempotency** — re-running on existing data produces the same output (no double-writes, no wrong numbers).

Evidence goes in PR §4 as quoted command output or a link to the auto-posted sandbox comment.
