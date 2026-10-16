# Issue #399 — Sync ADP poll waits for the Cloud Run execution

Jam + §4 approved in chat 2026-10-16 and recorded via `phase_state.py advance --operator-approved`.

Evidence tier: unit-only
waiver: The bug is the Labor/Payroll poll clock. A live Sync ADP re-click logs into ADP and can rewrite prod wage rates (the 2026-10-16 run already did).

## Root cause

`bhaga-daily-refresh-mwcpl` (created 2026-10-16T16:11:40Z, `BHAGA_ADP_SYNC_ALL=1`, target 2026-10-15) exited 0 in 20m39s. `adp_shifts.scraped_at_utc` stamped at 16:32:22Z. The button gives up at a fixed 20 minutes in `apps/operator-console/components/labor/SyncAdpButton.tsx` line 31 (`TIMEOUT_MS`) and line 96, before it reads execution status. Prod job task timeout is 3600s (`gcloud run jobs describe bhaga-daily-refresh` → `timeoutSeconds: 3600`).

## Invariant

No change to what Sync ADP scrapes or to wage-rate merge. Read-only toward ADP payroll Submit (bhaga.mdc invariant 6). America/Chicago display of last-synced stays. The page must not report a timeout while the watched execution is still running. A failed execution still stops polling. Idempotent BQ upserts are untouched.

Feature flag: no new flag. This cannot silently produce wrong numbers — it only changes when the page stops watching (`docs/FEATURE_FLAGS.md` unchanged). Out of scope: pay-info blank retries, the Pascone $16.25 → $15.25 write, liability parse skip.

## UX

Reuse the existing outline `Button` and the running status line (`text-amber-700`, `role="status"`) in `SyncAdpButton.tsx` lines 198–226. No new component. Past 20 minutes the same line says the sync is still in progress. Hover/disabled stay as they are (`disabled={busy}`).

## Milestone 1 — pure poll decision (Composer)

New `apps/operator-console/lib/labor/sync-adp-poll.ts`:

```ts
export const SYNC_SOFT_MS = 20 * 60 * 1000;
export const SYNC_HARD_MS = 60 * 60 * 1000; // bhaga-daily-refresh task timeout

export type SyncPollDecision =
  | { kind: "ok" }
  | { kind: "error"; message: string }
  | { kind: "wait"; status: string };

export function decideSyncPoll(input: {
  elapsedMs: number;
  hasExecution: boolean;
  advanced: boolean;
  execution?: {
    done: boolean;
    succeeded: boolean | null;
    failed: boolean;
    message: string | null;
  };
}): SyncPollDecision
```

Order: failed → error (keep the "not yet complete" wording from `SyncAdpButton.tsx` lines 121–127); done+succeeded+advanced → ok; done+succeeded+!advanced → "Job finished but ADP data did not update — check the ADP scrape logs."; advanced && !hasExecution → ok; elapsed > 60 min → 60-minute job-limit error; elapsed > 20 min && !hasExecution → the existing 20-minute error; else wait. Past 20 minutes with a live execution, status contains "past 20 minutes" and does not say "timed out".

**Verify:** `cd apps/operator-console && npx vitest run __tests__/sync-adp-poll.test.ts`

Happy path: minute 21, execution still running → wait. Minute 21, execution succeeded and advanced → ok.
Failure: execution.failed at any time → error. Minute 21 with no execution name and !advanced → 20-minute timeout. Minute 60+ with execution still running → 60-minute timeout. done+succeeded+!advanced → data-did-not-update error.

## Milestone 2 — wire the button and the queue copy (Composer)

`SyncAdpButton.tsx` `pollOnce` (line 95): call `decideSyncPoll` on the poll result. Do not return the 20-minute error before reading execution status. `kind: "ok"` → existing `finishOk` (toast "ADP synced", stops the interval). `kind: "error"` → existing `finishErr` (stops the interval). `kind: "wait"` → `setStatusText`.

`apps/operator-console/lib/bhaga/adp-sync.ts` lines 41–43: replace "usually 5–12 min" with "usually about 20 minutes".

**Verify:** `cd apps/operator-console && npx vitest run __tests__/sync-adp-poll.test.ts`

## Milestone 3 — doc lock-step (Composer)

`docs/operator-console/ARCHITECTURE.md` line 524: the button polls until that Cloud Run execution finishes, up to the job's 60-minute task timeout, and a full sync is usually about 20 minutes.

**Verify:** `python3 scripts/check_doc_freshness.py`

## PR mechanics

One branch `fix/clicked-on-sync-adp-on-https`, one PR against `main`, `Closes #399`. Bot account `jarvis-agent-bot328`. Never self-merge; babysit via `pr-workflow.mdc`. Operator squash-merges.

Model routing: Composer for all three milestones (UI poll clock + one doc sentence). No Opus review of the plan.
