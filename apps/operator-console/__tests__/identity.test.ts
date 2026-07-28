import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mockAuth = vi.fn();
vi.mock("@/auth", () => ({
  auth: () => mockAuth(),
}));

describe("operatorEmail (Auth.js)", () => {
  const ORIGINAL_ENV = { ...process.env };

  beforeEach(() => {
    vi.resetModules();
    mockAuth.mockReset();
    process.env = { ...ORIGINAL_ENV };
    delete process.env.BYPASS_IAP_EMAIL;
  });

  afterEach(() => {
    process.env = ORIGINAL_ENV;
  });

  it("returns the session email when Auth.js has a user", async () => {
    mockAuth.mockResolvedValue({ user: { email: "adi@mypalmetto.co" } });
    const { operatorEmail } = await import("@/lib/auth/identity");
    await expect(operatorEmail()).resolves.toBe("adi@mypalmetto.co");
  });

  it("throws when no session and BYPASS_IAP_EMAIL is unset", async () => {
    mockAuth.mockResolvedValue(null);
    const { operatorEmail } = await import("@/lib/auth/identity");
    await expect(operatorEmail()).rejects.toThrow(/no Auth\.js session/);
  });

  it("falls back to BYPASS_IAP_EMAIL for local dev when no session", async () => {
    mockAuth.mockResolvedValue(null);
    process.env.BYPASS_IAP_EMAIL = "dev@mypalmetto.co";
    const { operatorEmail } = await import("@/lib/auth/identity");
    await expect(operatorEmail()).resolves.toBe("dev@mypalmetto.co");
  });
});
