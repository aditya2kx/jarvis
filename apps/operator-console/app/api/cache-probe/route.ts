import { NextResponse } from "next/server";
import { operatorEmail } from "@/lib/auth/identity";
import { fq, q } from "@/lib/bq/client";
import { cacheStats } from "@/lib/bq/cache";

export const dynamic = "force-dynamic";

/**
 * End-to-end check of cache invalidation from an outside writer: reads the probe table
 * (migration 091) through the cache and reports whether the read was served from it.
 * Write a row from anywhere but the console, then poll this — RUNBOOK.md §17.
 */
export async function GET() {
  try {
    await operatorEmail();
  } catch {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }
  const rows = await q<{ row_count: number; max_n: number | null; last_written_at: string | null }>(
    `SELECT COUNT(*) AS row_count, MAX(n) AS max_n, CAST(MAX(written_at) AS STRING) AS last_written_at
     FROM ${fq("console_cache_probe")}`,
  );
  const read = cacheStats().recent[0];
  return NextResponse.json(
    { ok: true, probe: rows[0] ?? null, served: read?.outcome ?? null, ms: read?.ms ?? null },
    { headers: { "cache-control": "no-store" } },
  );
}
