import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { unstable_rethrow } from "next/navigation";
import { operatorEmail } from "@/lib/auth/identity";
import { MUTATING_ACTIONS, type MutatingActionName } from "@/lib/actions/registry";
import {
  NO_ACCESS,
  canKey,
  isValidEmail,
  normalizeEmail,
  parseDefaultPolicy,
  parseOwners,
  resolveAccess,
  type Access,
} from "./policy";
import { latestGrants } from "./store";

/** Owner-only "View as": set by POST /api/access, ignored for anyone who isn't an owner. */
export const VIEW_AS_COOKIE = "oc_view_as";

export interface ConsoleAccess extends Access {
  /** The signed-in Google account (differs from `email` while an owner uses View as). */
  realEmail: string;
  realIsOwner: boolean;
  viewingAs: string | null;
  /** Set when access couldn't be checked; the grant is then NO_ACCESS (fail closed). */
  error: string | null;
}

/** Local dev (`BYPASS_IAP_EMAIL`) signs in as an owner unless CONSOLE_OWNER_EMAILS is set explicitly. */
export function consoleOwners(): string[] {
  const raw = process.env.CONSOLE_OWNER_EMAILS;
  const bypass = process.env.BYPASS_IAP_EMAIL;
  const owners = parseOwners(raw);
  return !raw?.trim() && bypass ? [...owners, normalizeEmail(bypass)] : owners;
}

export const getAccess = cache(async (): Promise<ConsoleAccess> => {
  let realEmail: string;
  try {
    realEmail = normalizeEmail(await operatorEmail());
  } catch (e) {
    unstable_rethrow(e);
    console.error(`console_access_identity_failed: ${e instanceof Error ? e.message : String(e)}`);
    return {
      email: "",
      isOwner: false,
      source: "default",
      grant: NO_ACCESS,
      realEmail: "",
      realIsOwner: false,
      viewingAs: null,
      error: "Couldn't verify your Google sign-in.",
    };
  }
  const owners = consoleOwners();
  const realIsOwner = owners.includes(realEmail);
  const cookie = realIsOwner ? (await cookies()).get(VIEW_AS_COOKIE)?.value : undefined;
  const viewingAs =
    cookie && isValidEmail(cookie) && normalizeEmail(cookie) !== realEmail ? normalizeEmail(cookie) : null;
  const email = viewingAs ?? realEmail;
  const defaultPolicy = parseDefaultPolicy(process.env.CONSOLE_ACCESS_DEFAULT);
  const base = { realEmail, realIsOwner, viewingAs };

  if (owners.includes(email)) {
    return { ...resolveAccess({ email, owners, latest: undefined, defaultPolicy }), ...base, error: null };
  }
  try {
    const latest = (await latestGrants()).get(email);
    return { ...resolveAccess({ email, owners, latest, defaultPolicy }), ...base, error: null };
  } catch (e) {
    console.error(
      `console_access_store_failed email=${email}: ${e instanceof Error ? e.message : String(e)}`,
    );
    return {
      email,
      isOwner: false,
      source: "default",
      grant: NO_ACCESS,
      ...base,
      error: "Couldn't check your access right now.",
    };
  }
});

export const ACCESS_DENIED_MESSAGE =
  "You don't have access to this part of the console. Nothing was changed — ask the owner to update your access.";

/**
 * First line of every mutating server action: a refusal ack when the signed-in person
 * holds none of the action's `access` keys (lib/actions/registry.ts), else null.
 */
export async function actionDenied(name: MutatingActionName): Promise<{ ok: false; error: string } | null> {
  const entry = MUTATING_ACTIONS.find((a) => a.name === name);
  const a = await getAccess();
  if (a.error) return { ok: false, error: `${a.error} Nothing was changed.` };
  if (entry && entry.access.some((k) => canKey(a.grant, k))) return null;
  console.info(JSON.stringify({ event: "console_access_denied", action: name, email: a.email }));
  return { ok: false, error: ACCESS_DENIED_MESSAGE };
}
