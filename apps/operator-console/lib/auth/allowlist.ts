/**
 * Email allowlist for Operator Console Auth.js Google sign-in (Issue #210).
 * Source: ALLOWED_EMAILS env (comma-separated), provisioned via Secret Manager.
 */
export function isAllowlisted(
  email: string,
  raw: string = process.env.ALLOWED_EMAILS ?? "",
): boolean {
  const set = new Set(
    raw
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean),
  );
  return set.has(email.trim().toLowerCase());
}
