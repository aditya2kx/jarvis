// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

vi.mock("@/lib/actions/ActionToast", () => ({
  useActionToast: () => ({ push: vi.fn() }),
}));

import { useConsoleAction } from "@/lib/actions/useConsoleAction";

describe("useConsoleAction busy lock (Issue #233)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("rejects a second run while the first is in flight", async () => {
    const { result } = renderHook(() => useConsoleAction());

    let resolveFirst!: (ack: { ok: true; message: string }) => void;
    const firstFn = () =>
      new Promise<{ ok: true; message: string }>((resolve) => {
        resolveFirst = resolve;
      });

    let firstPromise!: Promise<unknown>;
    await act(async () => {
      firstPromise = result.current.run(firstFn, { saving: "Posting…" });
    });

    await waitFor(() => expect(result.current.isPending).toBe(true));

    let second!: Awaited<ReturnType<typeof result.current.run>>;
    await act(async () => {
      second = await result.current.run(async () => ({
        ok: true,
        message: "should not run",
      }));
    });

    expect(second.ok).toBe(false);
    if (!second.ok) {
      expect(second.error).toMatch(/still in progress/i);
    }

    await act(async () => {
      resolveFirst({ ok: true, message: "Posted." });
      await firstPromise;
    });

    await waitFor(() => expect(result.current.isPending).toBe(false));
  });
});
