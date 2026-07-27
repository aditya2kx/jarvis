import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "@/middleware";
import { CANONICAL_CONSOLE_HOST, HASH_CONSOLE_HOST } from "@/lib/iap/hosts";

describe("middleware canonical host redirect (Issue #194 A2)", () => {
  it("302-redirects hash host bookmarks to the canonical hostname", () => {
    const req = new NextRequest(`https://${HASH_CONSOLE_HOST}/inventory?x=1`, {
      headers: { host: HASH_CONSOLE_HOST },
    });
    const res = middleware(req);
    expect(res.status).toBe(302);
    const loc = res.headers.get("location");
    expect(loc).toBeTruthy();
    const url = new URL(loc!);
    expect(url.host).toBe(CANONICAL_CONSOLE_HOST);
    expect(url.pathname).toBe("/inventory");
    expect(url.search).toBe("?x=1");
  });

  it("leaves the canonical host alone", () => {
    const req = new NextRequest(`https://${CANONICAL_CONSOLE_HOST}/inventory`, {
      headers: { host: CANONICAL_CONSOLE_HOST },
    });
    const res = middleware(req);
    // NextResponse.next() → opaque continue (no Location bounce)
    expect(res.headers.get("location")).toBeNull();
  });
});
