// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const poll = vi.fn();
const sync = vi.fn();
const running = vi.fn();
const push = vi.fn();
const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

vi.mock("@/app/labor/actions", () => ({
  pollAdpSyncAction: (...args: unknown[]) => poll(...args),
  syncAdpAction: (...args: unknown[]) => sync(...args),
  runningAdpSyncAction: (...args: unknown[]) => running(...args),
}));

vi.mock("@/lib/actions/ActionToast", () => ({
  useActionToast: () => ({ push }),
}));

import { SyncAdpButton } from "@/components/labor/SyncAdpButton";

const EXEC =
  "projects/jarvis-bhaga-prod/locations/us-central1/jobs/bhaga-daily-refresh/executions/mwcpl";

const stillRunning = {
  ok: true as const,
  data: {
    scrapedAt: "2026-10-16 16:00:00",
    advanced: false,
    execution: { done: false, succeeded: null, failed: false, message: null },
  },
};

describe("SyncAdpButton", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    poll.mockReset();
    sync.mockReset();
    running.mockReset();
    push.mockReset();
    refresh.mockReset();
    running.mockResolvedValue({ ok: true, data: null });
    sync.mockResolvedValue({
      ok: true,
      data: {
        executionName: EXEC,
        baselineScrapedAt: "2026-10-16 16:00:00",
        message: "ADP sync queued in the background — usually about 20 minutes. You can keep using the page.",
      },
      queued: ["adp-sync"],
    });
    poll.mockResolvedValue(stillRunning);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  async function clickSync() {
    render(<SyncAdpButton lastScrapedAt={null} targetDate="2026-10-16" />);
    await act(async () => {
      screen.getByRole("button", { name: "Sync ADP" }).click();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it("keeps showing still-syncing past 20 minutes, then ADP synced", async () => {
    await clickSync();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20 * 60 * 1000 + 4_000);
    });

    expect(screen.getByRole("status").textContent).toMatch(/past 20 minutes/);
    expect(screen.queryByText(/timed out/i)).toBeNull();

    poll.mockResolvedValue({
      ok: true,
      data: {
        scrapedAt: "2026-10-16 16:32:22",
        advanced: true,
        execution: { done: true, succeeded: true, failed: false, message: null },
      },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });

    expect(push).toHaveBeenCalledWith("ADP synced", "info");
    expect(screen.getByRole("status").textContent).toMatch(/Synced/);
    expect(refresh).toHaveBeenCalled();
  });

  it("stops polling when the execution fails", async () => {
    await clickSync();
    const pollsBeforeFailure = poll.mock.calls.length;

    poll.mockResolvedValue({
      ok: true,
      data: {
        scrapedAt: "2026-10-16 16:00:00",
        advanced: false,
        execution: {
          done: true,
          succeeded: false,
          failed: true,
          message: "container exited 1",
        },
      },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });

    expect(screen.getByRole("status").textContent).toMatch(/Sync failed: container exited 1/);
    const pollsAtFailure = poll.mock.calls.length;
    expect(pollsAtFailure).toBeGreaterThan(pollsBeforeFailure);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(poll.mock.calls.length).toBe(pollsAtFailure);
  });
});
