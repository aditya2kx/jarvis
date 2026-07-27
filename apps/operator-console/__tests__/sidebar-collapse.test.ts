import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import {
  SIDEBAR_STORAGE_KEY,
  LG_MIN_PX,
  readStoredCollapsed,
  writeStoredCollapsed,
  defaultCollapsedForWidth,
} from "@/components/shell/useSidebarCollapsed";

describe("defaultCollapsedForWidth", () => {
  it("collapses below the lg breakpoint", () => {
    expect(defaultCollapsedForWidth(LG_MIN_PX - 1)).toBe(true);
    expect(defaultCollapsedForWidth(900)).toBe(true);
  });

  it("expands at and above lg", () => {
    expect(defaultCollapsedForWidth(LG_MIN_PX)).toBe(false);
    expect(defaultCollapsedForWidth(1280)).toBe(false);
  });
});

describe("sidebar localStorage helpers", () => {
  const store = new Map<string, string>();

  beforeEach(() => {
    store.clear();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns null when unset", () => {
    expect(readStoredCollapsed()).toBeNull();
  });

  it("round-trips collapsed preference", () => {
    writeStoredCollapsed(true);
    expect(store.get(SIDEBAR_STORAGE_KEY)).toBe("1");
    expect(readStoredCollapsed()).toBe(true);

    writeStoredCollapsed(false);
    expect(store.get(SIDEBAR_STORAGE_KEY)).toBe("0");
    expect(readStoredCollapsed()).toBe(false);
  });
});
