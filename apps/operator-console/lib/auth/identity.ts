import "server-only";
import { headers } from "next/headers";
import { OAuth2Client } from "google-auth-library";

// Cloud Run direct IAP (GA, no load balancer needed) fronts this service —
// see docs/operator-console/PLAN.md decision log 2026-07-05, which reverses
// the earlier "no IAP" pivot after confirming a custom "External" OAuth
// client works without a Google Workspace organization. Only the IAP service
// agent holds `run.invoker` on the Cloud Run service (end users hold
// `roles/iap.httpsResourceAccessor` instead), so IAP's identity headers
// cannot reach this app unless IAP itself set them. We additionally verify
// the signed `x-goog-iap-jwt-assertion` so a header-forwarding misconfig
// can't silently downgrade this to trusting an unverified header.
const IAP_EMAIL_HEADER = "x-goog-authenticated-user-email";
const IAP_JWT_HEADER = "x-goog-iap-jwt-assertion";
const IAP_ISSUER = "https://cloud.google.com/iap";

// Direct Cloud Run IAP's signed-header JWT audience format is
// `/projects/{PROJECT_NUMBER}/locations/{REGION}/services/{SERVICE_NAME}` —
// distinct from the load-balancer-backed `/global/backendServices/...`
// format used by Compute Engine/GKE IAP. Confirmed 2026-07-05 against
// https://cloud.google.com/iap/docs/signed-headers-howto. Overridable via env
// so a redeploy under a different project/region/service name doesn't need a
// code change.
const IAP_AUDIENCE =
  process.env.IAP_AUDIENCE ??
  `/projects/${process.env.IAP_PROJECT_NUMBER ?? "887772634501"}/locations/${
    process.env.IAP_REGION ?? "us-central1"
  }/services/${process.env.IAP_SERVICE_NAME ?? "operator-console"}`;

export const DEFAULT_STORE = "palmetto";

const oAuth2Client = new OAuth2Client();

async function verifyIapJwt(jwt: string): Promise<string | null> {
  try {
    const { pubkeys } = await oAuth2Client.getIapPublicKeys();
    const ticket = await oAuth2Client.verifySignedJwtWithCertsAsync(jwt, pubkeys, IAP_AUDIENCE, [
      IAP_ISSUER,
    ]);
    const email = ticket.getPayload()?.email;
    return typeof email === "string" ? email : null;
  } catch {
    return null; // never fabricate an identity on a verification failure
  }
}

/**
 * The signed-in operator's email, for `updated_by` on every write and for
 * store-scoping. Next.js 16 requires `headers()` to be awaited (no sync
 * access) — see the Next 16 upgrade notes in node_modules/next/dist/docs.
 * Access itself is gated by IAP's IAM (`roles/iap.httpsResourceAccessor`)
 * before the request ever reaches this app; this function only resolves
 * *who*, and requires the signed JWT to verify and agree with the plain
 * header before trusting either. Throws if IAP's headers are absent
 * (misconfiguration, or IAP disabled) and `BYPASS_IAP_EMAIL` is unset for
 * local dev — never fabricate an identity.
 */
export async function operatorEmail(): Promise<string> {
  const h = await headers();
  const headerEmail = h.get(IAP_EMAIL_HEADER)?.replace(/^accounts\.google\.com:/, "");
  const jwt = h.get(IAP_JWT_HEADER);

  if (headerEmail && jwt) {
    const verifiedEmail = await verifyIapJwt(jwt);
    if (!verifiedEmail || verifiedEmail.toLowerCase() !== headerEmail.toLowerCase()) {
      throw new Error("operatorEmail: IAP JWT assertion failed verification or email mismatch");
    }
    return headerEmail;
  }

  if (process.env.BYPASS_IAP_EMAIL) {
    return process.env.BYPASS_IAP_EMAIL;
  }

  if (process.env.DEBUG_AUTH_HEADERS) {
    console.error("DEBUG_AUTH_HEADERS:", JSON.stringify([...h.entries()]));
  }
  throw new Error("operatorEmail: no IAP header/JWT present and BYPASS_IAP_EMAIL unset");
}
