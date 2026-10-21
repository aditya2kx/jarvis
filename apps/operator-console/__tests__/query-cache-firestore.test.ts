import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("google-auth-library", () => ({
  GoogleAuth: class {
    async getClient() {
      return { getAccessToken: async () => ({ token: "t" }) };
    }
  },
}));

const { getEntry, storeAvailable } = await import("@/lib/bq/cache/firestore");

const timeout = () => Promise.reject(new DOMException("The operation was aborted due to timeout", "TimeoutError"));
const ok = () => Promise.resolve(new Response(JSON.stringify({ name: "x", fields: { payload: { stringValue: "[1]" } } })));

afterEach(() => vi.unstubAllGlobals());

describe("Firestore store health", () => {
  it("a single slow read is a miss, not a store-wide backoff; a run of them backs off", async () => {
    const fetch = vi.fn<() => Promise<Response>>(timeout);
    vi.stubGlobal("fetch", fetch);
    for (let i = 0; i < 4; i++) expect(await getEntry(`k${i}`)).toBeNull();
    expect(storeAvailable()).toBe(true);

    fetch.mockImplementationOnce(ok);
    expect(await getEntry("k-ok")).toBe("[1]");
    for (let i = 0; i < 4; i++) await getEntry(`again${i}`);
    expect(storeAvailable()).toBe(true);

    await getEntry("fifth-in-a-row");
    expect(storeAvailable()).toBe(false);
    expect(await getEntry("during-backoff")).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(10);
  });
});
