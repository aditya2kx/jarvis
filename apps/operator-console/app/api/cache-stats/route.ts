import { NextResponse } from "next/server";
import { operatorEmail } from "@/lib/auth/identity";
import { cacheStats } from "@/lib/bq/cache";

export const dynamic = "force-dynamic";

/**
 * Query-cache diagnostics for this instance: hit/miss counts, recent reads (query id,
 * timing, tables read — never row data) and parity results from `x-console-cache: verify`
 * renders. RUNBOOK.md §17.
 */
export async function GET() {
  try {
    await operatorEmail();
  } catch {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }
  return NextResponse.json({ ok: true, ...cacheStats() }, { headers: { "cache-control": "no-store" } });
}
