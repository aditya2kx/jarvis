import { describe, expect, it, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { CANONICAL_CONSOLE_HOST, HASH_CONSOLE_HOST } from "@/lib/iap/hosts";

vi.mock("@/auth", () => {
  return {
    auth: (handler: (req: NextRequest & { auth: unknown }) => unknown) => {
      return (req: NextRequest) =>
        handler(Object.assign(req, { auth: null }));
    },
  };
});

describe("proxy/middleware host + auth gate (Issue #210)", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("redirects unauthenticated requests to /login", async () => {
    const { default: middleware } = await import("@/proxy");
    const req = new NextRequest(`https://${CANONICAL_CONSOLE_HOST}/home`, {
      headers: { host: CANONICAL_CONSOLE_HOST },
    });
    const res = await middleware(req as never, {} as never);
    expect(res?.headers.get("location")).toContain("/login");
  });

  it("does not redirect /login when unauthenticated", async () => {
    const { default: middleware } = await import("@/proxy");
    const req = new NextRequest(`https://${CANONICAL_CONSOLE_HOST}/login`, {
      headers: { host: CANONICAL_CONSOLE_HOST },
    });
    const res = await middleware(req as never, {} as never);
    expect(res?.headers.get("location")).toBeNull();
  });

  it("logs iap_hash_host_hit on hash host (still redirects to login)", async () => {
    const spy = vi.spyOn(console, "info").mockImplementation(() => {});
    const { default: middleware } = await import("@/proxy");
    const req = new NextRequest(`https://${HASH_CONSOLE_HOST}/`, {
      headers: { host: HASH_CONSOLE_HOST },
    });
    await middleware(req as never, {} as never);
    expect(spy).toHaveBeenCalled();
    const payload = JSON.parse(String(spy.mock.calls[0]![0]));
    expect(payload.event).toBe("iap_hash_host_hit");
    spy.mockRestore();
  });
});
