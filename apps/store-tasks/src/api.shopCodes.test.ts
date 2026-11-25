import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("api.setShopCode / shopCodes paths", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const body = init?.body ? JSON.parse(String(init.body)) : null;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            store: String(url).includes("houston") ? "houston" : "palmetto",
            set: true,
            code: body?.code || "555666",
            needs_rotate: false,
            updated_at: "2026-11-25T00:00:00Z",
            url: "https://store-tasks.example/",
            device: null,
            path: url,
            method: init?.method,
            body,
          }),
        };
      }),
    );
    // jsdom-ish stubs for auth headers
    const store: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => {
        store[k] = v;
      },
      removeItem: (k: string) => {
        delete store[k];
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("PUT /v1/shop-codes/<store> when store is set; blank code issues", async () => {
    const { api } = await import("./api");
    const r = await api.setShopCode("sess", { store: "houston" });
    expect(r.code).toBe("555666");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/v1/shop-codes/houston");
    expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBe("PUT");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({ code: "" });
  });

  it("PUT /v1/shop-code with a custom code for the current store", async () => {
    const { api } = await import("./api");
    const r = await api.setShopCode("sess", { code: "123456" });
    expect(r.code).toBe("123456");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/v1/shop-code");
  });

  it("GET /v1/shop-codes lists stores", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          stores: [
            { store: "palmetto", name: "Austin", set: true, code: "167260", needs_rotate: false, updated_at: null },
            { store: "houston", name: "Houston", set: true, code: "493243", needs_rotate: false, updated_at: null },
          ],
          url: "https://store-tasks.example/",
        }),
      })),
    );
    const { api } = await import("./api");
    const listed = await api.shopCodes("sess");
    expect(listed.stores.map((s) => [s.store, s.code])).toEqual([
      ["palmetto", "167260"],
      ["houston", "493243"],
    ]);
  });
});
