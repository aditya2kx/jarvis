import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { HASH_CONSOLE_HOST } from "@/lib/iap/hosts";

/**
 * Issue #208 — do NOT redirect hash ↔ canonical after IAP.
 * Cross-host 302 forces a second OAuth onto a host with no __Host-GCP_IAP_*
 * cookies (Error code 9 / account-picker loops on mobile). Operators use the
 * canonical host only; hash hits are logged, not bounced.
 */
export function middleware(request: NextRequest) {
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
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
