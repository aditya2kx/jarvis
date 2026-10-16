import { describe, expect, it } from "vitest";
import {
  SYNC_DATA_STALE,
  SYNC_HARD_MS,
  SYNC_NOT_DEPLOYED,
  SYNC_SOFT_MS,
  SYNC_TIMEOUT_20,
  SYNC_TIMEOUT_60,
  decideSyncPoll,
  type SyncExecution,
} from "@/lib/labor/sync-adp-poll";

const running: SyncExecution = {
  done: false,
  succeeded: null,
  failed: false,
  message: null,
};

const succeeded: SyncExecution = {
  done: true,
  succeeded: true,
  failed: false,
  message: null,
};

describe("decideSyncPoll", () => {
  it("keeps waiting past 20 minutes while the execution is still running", () => {
    const decision = decideSyncPoll({
      elapsedMs: SYNC_SOFT_MS + 1,
      hasExecution: true,
      advanced: false,
      execution: running,
    });
    expect(decision.kind).toBe("wait");
    if (decision.kind === "wait") {
      expect(decision.status).toMatch(/past 20 minutes/);
      expect(decision.status).not.toMatch(/timed out/i);
    }
  });

  it("succeeds after 20 minutes once the execution finishes and hours advanced", () => {
    const decision = decideSyncPoll({
      elapsedMs: 21 * 60 * 1000,
      hasExecution: true,
      advanced: true,
      execution: succeeded,
    });
    expect(decision).toEqual({ kind: "ok" });
  });

  it("surfaces a failed execution and does not wait out the clock", () => {
    const decision = decideSyncPoll({
      elapsedMs: 5 * 60 * 1000,
      hasExecution: true,
      advanced: false,
      execution: {
        done: true,
        succeeded: false,
        failed: true,
        message: "container exited 1",
      },
    });
    expect(decision).toEqual({
      kind: "error",
      message: "Sync failed: container exited 1",
    });
  });

  it("keeps the not-deployed failure wording", () => {
    const decision = decideSyncPoll({
      elapsedMs: 60_000,
      hasExecution: true,
      advanced: false,
      execution: {
        done: true,
        succeeded: false,
        failed: true,
        message: "image not yet complete for this path",
      },
    });
    expect(decision).toEqual({ kind: "error", message: SYNC_NOT_DEPLOYED });
  });

  it("times out at 20 minutes only when there is no execution to watch", () => {
    const decision = decideSyncPoll({
      elapsedMs: SYNC_SOFT_MS + 1,
      hasExecution: false,
      advanced: false,
    });
    expect(decision).toEqual({ kind: "error", message: SYNC_TIMEOUT_20 });
  });

  it("stops at the Cloud Run job limit when the execution never finishes", () => {
    const decision = decideSyncPoll({
      elapsedMs: SYNC_HARD_MS + 1,
      hasExecution: true,
      advanced: true,
      execution: running,
    });
    expect(decision).toEqual({ kind: "error", message: SYNC_TIMEOUT_60 });
  });

  it("reports a finished job whose hours did not move", () => {
    const decision = decideSyncPoll({
      elapsedMs: 10 * 60 * 1000,
      hasExecution: true,
      advanced: false,
      execution: succeeded,
    });
    expect(decision).toEqual({ kind: "error", message: SYNC_DATA_STALE });
  });

  it("treats hours moving with no execution name as success", () => {
    const decision = decideSyncPoll({
      elapsedMs: 8 * 60 * 1000,
      hasExecution: false,
      advanced: true,
    });
    expect(decision).toEqual({ kind: "ok" });
  });
});
