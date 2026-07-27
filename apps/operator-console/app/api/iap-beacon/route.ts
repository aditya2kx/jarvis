import { createHash } from "crypto";
import { NextResponse } from "next/server";
import { operatorEmail } from "@/lib/auth/identity";
import { insertIapEvent } from "@/lib/iap/events";

export const dynamic = "force-dynamic";

type Body = {
  host?: string;
  ua?: string;
  referrer?: string | null;
  path?: string;
  dual_host?: boolean;
};

/**
 * Authenticated success beacon — IAP already verified the caller.
 * Inserts jarvis_dev.console_iap_events (Issue #194).
 */
export async function POST(req: Request) {
  let email: string;
  try {
    email = await operatorEmail();
  } catch {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }

  let body: Body = {};
  try {
    body = (await req.json()) as Body;
  } catch {
    body = {};
  }

  const emailHash = createHash("sha256").update(email.toLowerCase()).digest("hex").slice(0, 16);

  try {
    await insertIapEvent({
      event: "login_success",
      host: typeof body.host === "string" ? body.host.slice(0, 200) : null,
      ua: typeof body.ua === "string" ? body.ua.slice(0, 500) : null,
      referrer: typeof body.referrer === "string" ? body.referrer.slice(0, 500) : null,
      path: typeof body.path === "string" ? body.path.slice(0, 200) : null,
      dual_host: Boolean(body.dual_host),
      email_hash: emailHash,
    });
  } catch (e) {
    console.error(
      "iap_beacon_insert_failed:",
      e instanceof Error ? e.message : String(e),
    );
    return NextResponse.json({ ok: false }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
