import "server-only";
import { auth } from "@/auth";

/**
 * The signed-in operator's email, for `updated_by` on every write and for
 * store-scoping. Access is gated by Auth.js Google OAuth + ALLOWED_EMAILS
 * (Issue #210 — replaces Cloud Run browser IAP). Throws if no session and
 * `BYPASS_IAP_EMAIL` is unset for local dev — never fabricate an identity.
 *
 * `BYPASS_IAP_EMAIL` keeps the historical env name used by local README /
 * vitest; it bypasses Auth.js when unset in prod (never set on Cloud Run).
 */
export const DEFAULT_STORE = "palmetto";

export async function operatorEmail(): Promise<string> {
  const session = await auth();
  const email = session?.user?.email;
  if (email) {
    console.info(
      JSON.stringify({
        event: "authjs_identity",
        outcome: "ok",
        email_domain: email.includes("@") ? email.split("@")[1] : null,
      }),
    );
    return email;
  }

  if (process.env.BYPASS_IAP_EMAIL) {
    return process.env.BYPASS_IAP_EMAIL;
  }

  console.info(
    JSON.stringify({
      event: "authjs_identity",
      outcome: "missing",
    }),
  );
  throw new Error("operatorEmail: no Auth.js session and BYPASS_IAP_EMAIL unset");
}
