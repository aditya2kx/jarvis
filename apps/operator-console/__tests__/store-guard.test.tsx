// @vitest-environment jsdom
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

const push = vi.fn();
vi.mock("@/lib/actions/ActionToast", () => ({
  useActionToast: () => ({ push }),
}));

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

import { StoreGuard, staleStoreMessage } from "@/components/shell/StoreGuard";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { STORE_COOKIE } from "@/lib/config/stores";

function selectStore(store: string) {
  document.cookie = `${STORE_COOKIE}=${store}; path=/`;
}

const austinTab = ({ children }: { children: ReactNode }) => <StoreGuard store="palmetto">{children}</StoreGuard>;

describe("staleStoreMessage", () => {
  it("is null while the tab shows the selected store", () => {
    selectStore("palmetto");
    expect(staleStoreMessage("palmetto")).toBeNull();
  });

  it("names both stores when another tab switched", () => {
    selectStore("houston");
    expect(staleStoreMessage("palmetto")).toMatch(/showing Austin.*now on Houston.*nothing was saved/i);
  });
});

describe("useConsoleAction wrong-store guard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  // Unmount between tests so an earlier tab's focus listener is gone.
  afterEach(cleanup);

  it("runs the action when the tab matches the selected store", async () => {
    selectStore("palmetto");
    const action = vi.fn(async () => ({ ok: true as const, message: "Saved." }));
    const { result } = renderHook(() => useConsoleAction(), { wrapper: austinTab });
    await act(async () => {
      await result.current.run(action);
    });
    expect(action).toHaveBeenCalledTimes(1);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("refuses to save and reloads when the console moved to another store", async () => {
    selectStore("houston");
    const action = vi.fn(async () => ({ ok: true as const, message: "Saved." }));
    const { result } = renderHook(() => useConsoleAction(), { wrapper: austinTab });
    let ack!: Awaited<ReturnType<typeof result.current.run>>;
    await act(async () => {
      ack = await result.current.run(action);
    });
    expect(action).not.toHaveBeenCalled();
    expect(ack.ok).toBe(false);
    if (!ack.ok) expect(ack.error).toMatch(/nothing was saved/i);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("re-renders a stale tab as soon as it gets focus", () => {
    selectStore("houston");
    renderHook(() => null, { wrapper: austinTab });
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
