import "server-only";
import { GoogleAuth } from "google-auth-library";
import { normalizeEmail } from "./policy";

// Google sign-in to the console is Cloud Run direct IAP: only accounts holding
// roles/iap.httpsResourceAccessor on this service get past Google's login page. The
// Access page keeps that list in step with the console's own grants so adding or
// removing a person is one step. The runtime service account needs roles/iap.admin on
// this one IAP resource (granted by the deploy and preview workflows).
const ACCESSOR = "roles/iap.httpsResourceAccessor";

type Binding = { role: string; members?: string[]; condition?: unknown };
type Policy = { bindings?: Binding[]; etag?: string; version?: number };

export type IapStatus = { ok: true; members: string[] } | { ok: false; error: string };

function resource(): string {
  const project = process.env.IAP_PROJECT_NUMBER ?? "887772634501";
  const region = process.env.IAP_REGION ?? "us-central1";
  const service = process.env.IAP_SERVICE_NAME ?? "operator-console";
  return `https://iap.googleapis.com/v1/projects/${project}/iap_web/cloud_run-${region}/services/${service}`;
}

let auth: GoogleAuth | null = null;

async function call(method: "getIamPolicy" | "setIamPolicy", data: object): Promise<Policy> {
  auth ??= new GoogleAuth({ scopes: ["https://www.googleapis.com/auth/cloud-platform"] });
  const client = await auth.getClient();
  const res = await client.request<Policy>({ url: `${resource()}:${method}`, method: "POST", data });
  return res.data;
}

function localDev(): boolean {
  return Boolean(process.env.BYPASS_IAP_EMAIL);
}

function accessorUsers(policy: Policy): string[] {
  return (policy.bindings ?? [])
    .filter((b) => b.role === ACCESSOR && !b.condition)
    .flatMap((b) => b.members ?? [])
    .filter((m) => m.startsWith("user:"))
    .map((m) => normalizeEmail(m.slice("user:".length)));
}

function errorText(e: unknown): string {
  const status = (e as { response?: { status?: number } })?.response?.status;
  if (status === 403) return "the console's service account can't read or change Google sign-in (needs roles/iap.admin on this service)";
  return e instanceof Error ? e.message : String(e);
}

/** Google accounts that can get through sign-in today (user members only). */
export async function iapStatus(): Promise<IapStatus> {
  if (localDev()) return { ok: false, error: "local dev — no IAP in front of this server" };
  try {
    return { ok: true, members: accessorUsers(await call("getIamPolicy", {})) };
  } catch (e) {
    console.error(`console_access_iap_read_failed: ${errorText(e)}`);
    return { ok: false, error: errorText(e) };
  }
}

/** Adds or removes `user:<email>` on the accessor role. Read-modify-write on the etag, one retry. */
export async function setIapAccess(email: string, allowed: boolean): Promise<"granted" | "removed" | "unchanged"> {
  if (localDev()) return "unchanged";
  const member = `user:${normalizeEmail(email)}`;
  for (let attempt = 1; ; attempt++) {
    const policy = await call("getIamPolicy", {});
    const bindings = policy.bindings ?? [];
    let binding = bindings.find((b) => b.role === ACCESSOR && !b.condition);
    const has = (binding?.members ?? []).some((m) => m.toLowerCase() === member);
    if (has === allowed) return "unchanged";
    if (allowed) {
      if (!binding) {
        binding = { role: ACCESSOR, members: [] };
        bindings.push(binding);
      }
      binding.members = [...(binding.members ?? []), member];
    } else {
      binding!.members = (binding!.members ?? []).filter((m) => m.toLowerCase() !== member);
    }
    try {
      await call("setIamPolicy", { policy: { ...policy, bindings: bindings.filter((b) => b.members?.length) } });
      console.info(JSON.stringify({ event: "console_access_iap", email: normalizeEmail(email), allowed }));
      return allowed ? "granted" : "removed";
    } catch (e) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      if (status === 409 && attempt < 2) continue;
      throw new Error(`Google sign-in update failed: ${errorText(e)}`);
    }
  }
}
