// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

const push = vi.fn();
vi.mock("@/lib/actions/ActionToast", () => ({
  useActionToast: () => ({ push }),
}));

import { UnrecognizedActionError } from "next/dist/client/components/unrecognized-action-error";
import { STALE_ACTION_MESSAGE, useConsoleAction } from "@/lib/actions/useConsoleAction";

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

describe("useConsoleAction stale Server Action (Issue #401)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("replaces Next's raw not-found error with reload copy", async () => {
    const { result } = renderHook(() => useConsoleAction());
    let ack!: Awaited<ReturnType<typeof result.current.run>>;
    await act(async () => {
      ack = await result.current.run(() =>
        Promise.reject(
          new UnrecognizedActionError(
            'Server Action "60337d2b" was not found on the server.',
          ),
        ),
      );
    });
    expect(ack).toEqual({ ok: false, error: STALE_ACTION_MESSAGE });
    expect(result.current.error).toBe(STALE_ACTION_MESSAGE);
    expect(push).toHaveBeenCalledWith(STALE_ACTION_MESSAGE, "error");
  });

  it("keeps other thrown errors as-is", async () => {
    const { result } = renderHook(() => useConsoleAction());
    let ack!: Awaited<ReturnType<typeof result.current.run>>;
    await act(async () => {
      ack = await result.current.run(() => Promise.reject(new Error("boom")));
    });
    expect(ack).toEqual({ ok: false, error: "boom" });
    expect(push).toHaveBeenCalledWith("boom", "error");
  });
});
