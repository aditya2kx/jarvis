import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { CANONICAL_CONSOLE_HOST, HASH_CONSOLE_HOST } from "@/lib/iap/hosts";

/**
 * After IAP auth, bounce hash-host bookmarks onto the canonical hostname so
 * GCP_IAP_* cookies and OAuth state stay on one host (Issue #194).
 */
export function middleware(request: NextRequest) {
  const host = request.headers.get("host")?.split(":")[0] ?? "";
  if (host !== HASH_CONSOLE_HOST) {
    return NextResponse.next();
  }
  const url = request.nextUrl.clone();
  url.host = CANONICAL_CONSOLE_HOST;
  url.protocol = "https:";
  url.port = "";
  console.info(
    JSON.stringify({
      event: "iap_canonical_redirect",
      from_host: host,
      to_host: CANONICAL_CONSOLE_HOST,
      path: request.nextUrl.pathname,
    }),
  );
  return NextResponse.redirect(url, 302);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
