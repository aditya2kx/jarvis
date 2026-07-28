import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { HASH_CONSOLE_HOST } from "@/lib/iap/hosts";

/**
 * Auth.js session gate (Issue #210) + hash-host breadcrumb (Issue #208).
 * Unauthenticated callers are redirected to /login (via authorized callback).
 * Do NOT cross-redirect hash ↔ canonical — leftover IAP dual-host footgun.
 */
export default auth((request) => {
  const host = request.headers.get("host")?.split(":")[0] ?? "";
  if (host === HASH_CONSOLE_HOST) {
    console.info(
      JSON.stringify({
        event: "iap_hash_host_hit",
        path: request.nextUrl.pathname,
      }),
    );
  }
  return NextResponse.next();
});

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
