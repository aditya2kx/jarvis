import "server-only";
import { headers } from "next/headers";

// Google IAP (Cloud Run direct integration, see docs/operator-console/EXECUTION.md
// §5.4) verifies the caller and forwards their identity in this header. IAP
// itself enforces @mypalmetto.co membership at the proxy — the domain check
// below is a defense-in-depth backstop, not the primary gate.
const IAP_EMAIL_HEADER = "x-goog-authenticated-user-email";
const ALLOWED_DOMAIN = "@mypalmetto.co";

export const DEFAULT_STORE = "palmetto";

/**
 * The signed-in operator's email, for `updated_by` on every write and for
 * store-scoping. Next.js 16 requires `headers()` to be awaited (no sync
 * access) — see the Next 16 upgrade notes in node_modules/next/dist/docs.
 * Throws if IAP did not forward an identity, or if running locally without
 * IAP where BYPASS_IAP_EMAIL is unset — never fabricate an identity.
 */
export async function operatorEmail(): Promise<string> {
  const h = await headers();
  const raw = h.get(IAP_EMAIL_HEADER) ?? process.env.BYPASS_IAP_EMAIL ?? "";
  const email = raw.replace(/^accounts\.google\.com:/, "");
  if (!email) {
    throw new Error("operatorEmail: no IAP identity header present");
  }
  if (!email.endsWith(ALLOWED_DOMAIN)) {
    throw new Error(`operatorEmail: ${email} is outside ${ALLOWED_DOMAIN}`);
  }
  return email;
}
