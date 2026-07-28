import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "@/middleware";
import { CANONICAL_CONSOLE_HOST, HASH_CONSOLE_HOST } from "@/lib/iap/hosts";

describe("middleware host policy (Issue #208 — no cross-host bounce)", () => {
  it("does not redirect hash host (keeps IAP cookie jar on that host)", () => {
    const req = new NextRequest(`https://${HASH_CONSOLE_HOST}/inventory?x=1`, {
      headers: { host: HASH_CONSOLE_HOST },
    });
    const res = middleware(req);
    expect(res.headers.get("location")).toBeNull();
  });

  it("leaves the canonical host alone", () => {
    const req = new NextRequest(`https://${CANONICAL_CONSOLE_HOST}/inventory`, {
      headers: { host: CANONICAL_CONSOLE_HOST },
    });
    const res = middleware(req);
    expect(res.headers.get("location")).toBeNull();
  });

  it("logs iap_hash_host_hit when request host is the hash form", () => {
    const spy = vi.spyOn(console, "info").mockImplementation(() => {});
    const req = new NextRequest(`https://${HASH_CONSOLE_HOST}/`, {
      headers: { host: HASH_CONSOLE_HOST },
    });
    middleware(req);
    expect(spy).toHaveBeenCalled();
    const payload = JSON.parse(String(spy.mock.calls[0]![0]));
    expect(payload.event).toBe("iap_hash_host_hit");
    expect(payload.path).toBe("/");
    spy.mockRestore();
  });
});
