import "server-only";
import { consoleOwners } from "./server";
import { grantHistory, latestGrants } from "./store";
import { iapStatus } from "./iap";
import {
  FULL_ACCESS,
  NO_ACCESS,
  grantSummary,
  parseDefaultPolicy,
  type DefaultPolicy,
  type Grant,
  type GrantVersion,
} from "./policy";

export type PersonRole = "owner" | "full" | "custom" | "removed" | "default";

export interface AccessPerson {
  email: string;
  role: PersonRole;
  grant: Grant;
  summary: string;
  /** Holds Google sign-in (IAP accessor) today; null when that couldn't be read. */
  signIn: boolean | null;
  changedBy: string | null;
  changedAt: string | null;
  note: string | null;
}

export interface AccessDirectory {
  people: AccessPerson[];
  history: GrantVersion[];
  defaultPolicy: DefaultPolicy;
  /** Why Google sign-in status couldn't be read (shown as a notice, never blocks editing). */
  signInError: string | null;
}

const ROLE_ORDER: Record<PersonRole, number> = { owner: 0, full: 1, custom: 2, default: 3, removed: 4 };

/**
 * Everyone the owner might want to manage: owners, anyone with a saved grant, and every
 * Google account that can sign in today (so existing users show up before their first edit).
 * Throws when the grants store can't be read.
 */
export async function accessDirectory(): Promise<AccessDirectory> {
  const owners = consoleOwners();
  const defaultPolicy = parseDefaultPolicy(process.env.CONSOLE_ACCESS_DEFAULT);
  const [latest, history, iap] = await Promise.all([latestGrants(), grantHistory(undefined, 200), iapStatus()]);
  const members = iap.ok ? new Set(iap.members) : null;
  const emails = new Set([...owners, ...latest.keys(), ...(members ?? [])]);

  const people = [...emails].map((email): AccessPerson => {
    const v = latest.get(email);
    const signIn = members ? members.has(email) : null;
    const base = { email, signIn, changedBy: v?.createdBy ?? null, changedAt: v?.createdAt ?? null, note: v?.note ?? null };
    if (owners.includes(email)) {
      return { ...base, role: "owner", grant: FULL_ACCESS, summary: "Owner — full access, manages access" };
    }
    if (v?.status === "revoked") return { ...base, role: "removed", grant: NO_ACCESS, summary: "No access" };
    if (v) return { ...base, role: v.grant.full ? "full" : "custom", grant: v.grant, summary: grantSummary(v.grant) };
    const grant = defaultPolicy === "full" ? FULL_ACCESS : NO_ACCESS;
    return { ...base, role: "default", grant, summary: `${grantSummary(grant)} (not yet set)` };
  });
  people.sort((a, b) => ROLE_ORDER[a.role] - ROLE_ORDER[b.role] || a.email.localeCompare(b.email));
  return { people, history, defaultPolicy, signInError: iap.ok ? null : iap.error };
}
