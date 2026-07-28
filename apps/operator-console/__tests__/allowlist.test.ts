import { describe, expect, it } from "vitest";
import { isAllowlisted } from "@/lib/auth/allowlist";

describe("isAllowlisted", () => {
  const raw = "adi@mypalmetto.co, aditya.2ky@gmail.com, lindsay@mypalmetto.co";

  it("accepts allowlisted emails case-insensitively", () => {
    expect(isAllowlisted("Adi@MyPalmetto.co", raw)).toBe(true);
    expect(isAllowlisted("aditya.2ky@gmail.com", raw)).toBe(true);
  });

  it("rejects unknown emails", () => {
    expect(isAllowlisted("stranger@example.com", raw)).toBe(false);
  });

  it("rejects empty when allowlist empty", () => {
    expect(isAllowlisted("adi@mypalmetto.co", "")).toBe(false);
  });
});
