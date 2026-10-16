/**
 * When the Labor/Payroll "Sync ADP" poll should stop.
 *
 * The 2026-10-16 click (`bhaga-daily-refresh-mwcpl`) exited 0 in 20m39s, after
 * the page had already given up at a fixed 20 minutes. A live execution keeps
 * being watched until it finishes. The hard cap matches the prod job's
 * task timeout (3600s on `bhaga-daily-refresh`).
 */

export const SYNC_SOFT_MS = 20 * 60 * 1000;
export const SYNC_HARD_MS = 60 * 60 * 1000;

export const SYNC_TIMEOUT_20 =
  "Sync timed out after 20 minutes — check ADP login / Cloud Run logs, then try again.";
export const SYNC_TIMEOUT_60 =
  "Sync still running after 60 minutes — the Cloud Run job limit. Check ADP login / Cloud Run logs, then try again.";
export const SYNC_DATA_STALE =
  "Job finished but ADP data did not update — check the ADP scrape logs.";
export const SYNC_NOT_DEPLOYED =
  "Cloud job failed: Sync ADP path not deployed yet. Use local console with BYPASS_IAP, or merge/deploy this branch first.";

export type SyncExecution = {
  done: boolean;
  succeeded: boolean | null;
  failed: boolean;
  message: string | null;
};

export type SyncPollInput = {
  elapsedMs: number;
  hasExecution: boolean;
  advanced: boolean;
  execution?: SyncExecution;
};

export type SyncPollDecision =
  | { kind: "ok" }
  | { kind: "error"; message: string }
  | { kind: "wait"; status: string };

function waitStatus(input: SyncPollInput): string {
  const past = input.elapsedMs > SYNC_SOFT_MS;
  if (input.execution?.done) {
    return past ? "Finishing… (past 20 minutes)." : "Finishing…";
  }
  if (input.advanced) {
    return past
      ? "Clocked hours synced — still reading schedule and pay rates (past 20 minutes)."
      : "Clocked hours synced — still reading schedule and pay rates…";
  }
  return past
    ? "Still syncing ADP — past 20 minutes and still going. You can keep using the page."
    : "Syncing ADP… (you can keep using the page)";
}

export function decideSyncPoll(input: SyncPollInput): SyncPollDecision {
  const execution = input.execution;
  if (execution?.failed) {
    return {
      kind: "error",
      message: execution.message?.includes("not yet complete")
        ? SYNC_NOT_DEPLOYED
        : `Sync failed: ${execution.message ?? "Cloud Run execution failed"}`,
    };
  }
  if (execution?.done && execution.succeeded && input.advanced) {
    return { kind: "ok" };
  }
  if (input.advanced && !input.hasExecution) {
    return { kind: "ok" };
  }
  if (execution?.done && execution.succeeded && !input.advanced) {
    return { kind: "error", message: SYNC_DATA_STALE };
  }
  if (input.elapsedMs > SYNC_HARD_MS) {
    return { kind: "error", message: SYNC_TIMEOUT_60 };
  }
  if (input.elapsedMs > SYNC_SOFT_MS && !input.hasExecution) {
    return { kind: "error", message: SYNC_TIMEOUT_20 };
  }
  return { kind: "wait", status: waitStatus(input) };
}
