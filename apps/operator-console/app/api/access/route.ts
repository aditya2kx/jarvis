import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { consoleOwners, getAccess, VIEW_AS_COOKIE } from "@/lib/access/server";
import { appendGrant } from "@/lib/access/store";
import { setIapAccess } from "@/lib/access/iap";
import { accessDirectory } from "@/lib/access/people";
import { NO_ACCESS, allowedHrefs, isValidEmail, normalizeEmail, normalizeGrant } from "@/lib/access/policy";
import type { ActionAck } from "@/lib/actions/types";

// Owner-only writes for the Access page and the per-page "Manage access" drawer. A route
// (not server actions) so middleware.ts can let exactly this path through on a preview
// whose grants live in a throwaway sandbox table.
function ack(body: ActionAck, status = 200) {
  return NextResponse.json(body, { status });
}

async function syncSignIn(email: string, allowed: boolean): Promise<string | null> {
  try {
    const r = await setIapAccess(email, allowed);
    if (r === "granted") return " They can now sign in with Google.";
    if (r === "removed") return " Google sign-in removed.";
    return "";
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error(`console_access_iap_sync_failed email=${email} allowed=${allowed}: ${msg}`);
    return null;
  }
}

/** People list for the per-page "Manage access" drawer (loaded when it opens). */
export async function GET() {
  const me = await getAccess();
  if (!me.realIsOwner) return NextResponse.json({ error: "Only the console owner can see access." }, { status: 403 });
  try {
    return NextResponse.json(await accessDirectory());
  } catch (e) {
    console.error(`console_access_directory_failed: ${e instanceof Error ? e.message : String(e)}`);
    return NextResponse.json({ error: "Couldn't load access right now." }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const me = await getAccess();
  if (!me.realIsOwner) return ack({ ok: false, error: "Only the console owner can change access." }, 403);

  let body: { op?: string; email?: unknown; grant?: unknown; note?: unknown };
  try {
    body = await req.json();
  } catch {
    return ack({ ok: false, error: "Bad request." }, 400);
  }

  if (body.op === "view-as") {
    const jar = await cookies();
    if (body.email == null) {
      jar.delete(VIEW_AS_COOKIE);
      return ack({ ok: true, message: "Back to your own view." });
    }
    if (typeof body.email !== "string" || !isValidEmail(body.email)) {
      return ack({ ok: false, error: "Enter a valid email." }, 400);
    }
    jar.set(VIEW_AS_COOKIE, normalizeEmail(body.email), {
      httpOnly: true,
      secure: true,
      sameSite: "lax",
      path: "/",
      maxAge: 8 * 60 * 60,
    });
    return ack({ ok: true, message: `Viewing as ${normalizeEmail(body.email)}.` });
  }

  if (body.op !== "save" && body.op !== "revoke") return ack({ ok: false, error: "Unknown request." }, 400);
  if (typeof body.email !== "string" || !isValidEmail(body.email)) {
    return ack({ ok: false, error: "Enter a valid email address." }, 400);
  }
  const email = normalizeEmail(body.email);
  if (consoleOwners().includes(email)) {
    return ack({ ok: false, error: "Owners always have full access — nothing to change." }, 400);
  }
  const note = typeof body.note === "string" && body.note.trim() ? body.note.trim().slice(0, 500) : null;
  const grant = body.op === "save" ? normalizeGrant(body.grant) : NO_ACCESS;
  const hasAny = grant.full || allowedHrefs(grant).length > 0;
  const status = hasAny ? "active" : "revoked";

  try {
    await appendGrant({ email, status, grant, note, by: me.realEmail });
  } catch (e) {
    console.error(`console_access_save_failed email=${email}: ${e instanceof Error ? e.message : String(e)}`);
    return ack({ ok: false, error: "Couldn't save access — nothing was changed. Try again in a moment." }, 500);
  }
  console.info(JSON.stringify({ event: "console_access_saved", email, status, by: me.realEmail }));

  const signIn = await syncSignIn(email, hasAny);
  const saved = hasAny ? `Saved access for ${email}.` : `Removed all access for ${email}.`;
  if (signIn === null) {
    return ack({
      ok: true,
      message: `${saved} Console access is updated, but Google sign-in couldn't be ${hasAny ? "granted" : "removed"} — see Pipeline logs (console_access_iap_sync_failed).`,
    });
  }
  return ack({ ok: true, message: saved + signIn });
}
