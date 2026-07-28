import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { HASH_CONSOLE_HOST } from "@/lib/iap/hosts";

function isPublicPath(pathname: string): boolean {
  return (
    pathname.startsWith("/api/auth") ||
    pathname === "/login" ||
    pathname.startsWith("/login/")
  );
}

/**
 * Auth.js session gate (Issue #210) + hash-host breadcrumb (Issue #208).
 * Next.js 16: export as `proxy` (middleware.ts renamed). Explicit redirect —
 * do not rely on `authorized` alone (was observed not gating on Cloud Run).
 */
const gate = auth((request) => {
  const host = request.headers.get("host")?.split(":")[0] ?? "";
  const path = request.nextUrl.pathname;
  if (host === HASH_CONSOLE_HOST) {
    console.info(
      JSON.stringify({
        event: "iap_hash_host_hit",
        path,
      }),
    );
  }

  if (!request.auth && !isPublicPath(path)) {
    const url = new URL("/login", request.nextUrl.origin);
    if (path !== "/" && path !== "/login") {
      url.searchParams.set("callbackUrl", `${path}${request.nextUrl.search}`);
    }
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
});

export default gate;
export const proxy = gate;

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
