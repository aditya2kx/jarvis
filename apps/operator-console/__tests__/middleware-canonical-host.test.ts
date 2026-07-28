import { describe, expect, it, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { CANONICAL_CONSOLE_HOST, HASH_CONSOLE_HOST } from "@/lib/iap/hosts";

vi.mock("@/auth", () => {
  // auth() as middleware wrapper: (handler) => handler with req.auth attached
  return {
    auth: (handler: (req: NextRequest & { auth: unknown }) => unknown) => {
      return (req: NextRequest) => handler(Object.assign(req, { auth: { user: { email: "adi@mypalmetto.co" } } }));
    },
  };
});

describe("middleware host policy (Issue #208 — no cross-host bounce)", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("does not redirect hash host", async () => {
    const { default: middleware } = await import("@/middleware");
    const req = new NextRequest(`https://${HASH_CONSOLE_HOST}/inventory?x=1`, {
      headers: { host: HASH_CONSOLE_HOST },
    });
    const res = await middleware(req as never, {} as never);
    expect(res?.headers.get("location")).toBeNull();
  });

  it("leaves the canonical host alone", async () => {
    const { default: middleware } = await import("@/middleware");
    const req = new NextRequest(`https://${CANONICAL_CONSOLE_HOST}/inventory`, {
      headers: { host: CANONICAL_CONSOLE_HOST },
    });
    const res = await middleware(req as never, {} as never);
    expect(res?.headers.get("location")).toBeNull();
  });

  it("logs iap_hash_host_hit when request host is the hash form", async () => {
    const spy = vi.spyOn(console, "info").mockImplementation(() => {});
    const { default: middleware } = await import("@/middleware");
    const req = new NextRequest(`https://${HASH_CONSOLE_HOST}/`, {
      headers: { host: HASH_CONSOLE_HOST },
    });
    await middleware(req as never, {} as never);
    expect(spy).toHaveBeenCalled();
    const payload = JSON.parse(String(spy.mock.calls[0]![0]));
    expect(payload.event).toBe("iap_hash_host_hit");
    expect(payload.path).toBe("/");
    spy.mockRestore();
  });
});
