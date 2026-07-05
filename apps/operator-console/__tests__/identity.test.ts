import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Next's webpack build swaps this package's export for a no-op; vitest runs
// straight through node so it must be mocked the same way.
vi.mock("server-only", () => ({}));

const getIapPublicKeys = vi.fn();
const verifySignedJwtWithCertsAsync = vi.fn();

vi.mock("google-auth-library", () => ({
  OAuth2Client: class {
    getIapPublicKeys = getIapPublicKeys;
    verifySignedJwtWithCertsAsync = verifySignedJwtWithCertsAsync;
  },
}));

const mockHeaders = vi.fn();
vi.mock("next/headers", () => ({
  headers: () => mockHeaders(),
}));

function headersFrom(entries: Record<string, string>) {
  const map = new Map(Object.entries(entries));
  return { get: (key: string) => map.get(key.toLowerCase()) ?? null, entries: () => map.entries() };
}

// Mirrors the direct-Cloud-Run-IAP header contract documented in identity.ts:
// x-goog-authenticated-user-email (plain) + x-goog-iap-jwt-assertion (signed,
// re-verified here) — never a bare header, never a fabricated identity.
describe("operatorEmail", () => {
  const ORIGINAL_ENV = { ...process.env };

  beforeEach(() => {
    vi.resetModules();
    getIapPublicKeys.mockReset();
    verifySignedJwtWithCertsAsync.mockReset();
    mockHeaders.mockReset();
    process.env = { ...ORIGINAL_ENV };
    delete process.env.BYPASS_IAP_EMAIL;
  });

  afterEach(() => {
    process.env = ORIGINAL_ENV;
  });

  it("returns the email when the JWT verifies and matches the header", async () => {
    mockHeaders.mockReturnValue(
      headersFrom({
        "x-goog-authenticated-user-email": "accounts.google.com:adi@mypalmetto.co",
        "x-goog-iap-jwt-assertion": "signed.jwt.token",
      }),
    );
    getIapPublicKeys.mockResolvedValue({ pubkeys: {} });
    verifySignedJwtWithCertsAsync.mockResolvedValue({
      getPayload: () => ({ email: "adi@mypalmetto.co" }),
    });

    const { operatorEmail } = await import("@/lib/auth/identity");
    await expect(operatorEmail()).resolves.toBe("adi@mypalmetto.co");
  });

  it("throws when the JWT fails verification", async () => {
    mockHeaders.mockReturnValue(
      headersFrom({
        "x-goog-authenticated-user-email": "adi@mypalmetto.co",
        "x-goog-iap-jwt-assertion": "tampered.jwt.token",
      }),
    );
    getIapPublicKeys.mockResolvedValue({ pubkeys: {} });
    verifySignedJwtWithCertsAsync.mockRejectedValue(new Error("bad signature"));

    const { operatorEmail } = await import("@/lib/auth/identity");
    await expect(operatorEmail()).rejects.toThrow(/IAP JWT assertion failed/);
  });

  it("throws when the verified JWT email disagrees with the header email", async () => {
    mockHeaders.mockReturnValue(
      headersFrom({
        "x-goog-authenticated-user-email": "adi@mypalmetto.co",
        "x-goog-iap-jwt-assertion": "signed.jwt.token",
      }),
    );
    getIapPublicKeys.mockResolvedValue({ pubkeys: {} });
    verifySignedJwtWithCertsAsync.mockResolvedValue({
      getPayload: () => ({ email: "someone-else@mypalmetto.co" }),
    });

    const { operatorEmail } = await import("@/lib/auth/identity");
    await expect(operatorEmail()).rejects.toThrow(/IAP JWT assertion failed/);
  });

  it("throws when no IAP headers are present and BYPASS_IAP_EMAIL is unset", async () => {
    mockHeaders.mockReturnValue(headersFrom({}));

    const { operatorEmail } = await import("@/lib/auth/identity");
    await expect(operatorEmail()).rejects.toThrow(/no IAP header\/JWT present/);
  });

  it("falls back to BYPASS_IAP_EMAIL for local dev when no IAP headers are present", async () => {
    mockHeaders.mockReturnValue(headersFrom({}));
    process.env.BYPASS_IAP_EMAIL = "dev@mypalmetto.co";

    const { operatorEmail } = await import("@/lib/auth/identity");
    await expect(operatorEmail()).resolves.toBe("dev@mypalmetto.co");
  });
});
