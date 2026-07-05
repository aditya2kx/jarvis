import "server-only";
import { headers } from "next/headers";

// Cloud Run direct IAP is not viable here — creating the IAP OAuth brand
// requires a Google Workspace organization, which jarvis-bhaga-prod does not
// have (confirmed 2026-07, see docs/operator-console/EXECUTION.md §5.4). The
// service instead deploys `--no-allow-unauthenticated` with native Cloud Run
// IAM (roles/run.invoker): GFE rejects any request without a valid Google-
// signed ID token from an authorized principal *before* it reaches this app,
// so by the time this code runs the caller is already verified — decoding
// (not re-verifying) the same token's `email` claim is safe in that trust
// boundary. IAP kept the header path as a fallback should the brand
// limitation ever lift (e.g. a custom "External" OAuth client).
const IAP_EMAIL_HEADER = "x-goog-authenticated-user-email";
const ALLOWED_DOMAIN = "@mypalmetto.co";

export const DEFAULT_STORE = "palmetto";

// Cloud Run's own IAM check puts its verified ID token in a dedicated
// `X-Serverless-Authorization` header — NOT `Authorization`, which Cloud Run
// leaves untouched for the app's own use (confirmed live 2026-07-05 via a
// header dump: `gcloud run services proxy` sends no `Authorization` header
// at all, only `x-serverless-authorization: bearer <JWT>`). The JWT's
// `email` claim is present directly, no extra network round-trip needed.
async function emailFromBearerToken(h: Headers): Promise<string | null> {
  const raw = h.get("x-serverless-authorization") ?? h.get("authorization") ?? "";
  const token = raw.match(/^Bearer\s+(\S+)$/i)?.[1];
  if (!token) return null;

  const segments = token.split(".");
  if (segments.length === 3) {
    try {
      const email = JSON.parse(Buffer.from(segments[1], "base64url").toString("utf8"))?.email;
      if (typeof email === "string") return email;
    } catch {
      // not a valid JWT payload — fall through to tokeninfo below
    }
  }

  // Opaque OAuth2 access token (no decodable payload): resolve identity via
  // Google's tokeninfo endpoint. Cloud Run's IAM already authorized this
  // exact token before the request reached us; this call only resolves
  // *who*, never *whether*.
  try {
    const res = await fetch(
      `https://oauth2.googleapis.com/tokeninfo?access_token=${encodeURIComponent(token)}`,
    );
    if (!res.ok) return null;
    const email = (await res.json())?.email;
    return typeof email === "string" ? email : null;
  } catch {
    return null; // never fabricate an identity
  }
}

/**
 * The signed-in operator's email, for `updated_by` on every write and for
 * store-scoping. Next.js 16 requires `headers()` to be awaited (no sync
 * access) — see the Next 16 upgrade notes in node_modules/next/dist/docs.
 * Throws if neither an IAP header nor a Bearer identity token is present, or
 * if running locally without either where BYPASS_IAP_EMAIL is unset — never
 * fabricate an identity.
 */
export async function operatorEmail(): Promise<string> {
  const h = await headers();
  const iapEmail = h.get(IAP_EMAIL_HEADER)?.replace(/^accounts\.google\.com:/, "");
  const email = iapEmail || (await emailFromBearerToken(h)) || process.env.BYPASS_IAP_EMAIL || "";
  if (!email) {
    if (process.env.DEBUG_AUTH_HEADERS) {
      console.error("DEBUG_AUTH_HEADERS:", JSON.stringify([...h.entries()]));
    }
    throw new Error("operatorEmail: no IAP header or Bearer identity token present");
  }
  if (!email.endsWith(ALLOWED_DOMAIN)) {
    throw new Error(`operatorEmail: ${email} is outside ${ALLOWED_DOMAIN}`);
  }
  return email;
}
