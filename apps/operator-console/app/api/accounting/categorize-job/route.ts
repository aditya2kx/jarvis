import { NextResponse } from "next/server";
import { holdsAccess } from "@/lib/access/server";
import { latestCategorizeJob } from "@/lib/plaid/categorize-jobs";

export const dynamic = "force-dynamic";

/**
 * Latest Accounting categorize job (Reapply / Revert) for the ledger's progress line.
 * A GET route, not a server action: Next runs a page's server actions one at a time,
 * so a status poll would queue behind the Reapply it is reporting on.
 */
export async function GET() {
  if (!(await holdsAccess("accounting:ledger"))) {
    return NextResponse.json({ ok: false, error: "forbidden" }, { status: 403 });
  }
  try {
    const job = await latestCategorizeJob();
    return NextResponse.json({ ok: true, job }, { headers: { "cache-control": "no-store" } });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error(`plaid_categorize_job_status_failed: ${msg}`);
    return NextResponse.json({ ok: false, error: "Couldn't read job status." }, { status: 500 });
  }
}
