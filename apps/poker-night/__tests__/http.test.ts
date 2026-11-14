import { describe, expect, it } from "vitest";
import { pollSince } from "@/lib/http";

describe("pollSince", () => {
  it("treats a missing or blank cursor as a full fetch (not version 0)", () => {
    expect(Number.isNaN(pollSince(null))).toBe(true);
    expect(Number.isNaN(pollSince(""))).toBe(true);
    expect(Number.isFinite(pollSince(null)) && pollSince(null) === 0).toBe(false);
  });

  it("parses an explicit cursor, including 0", () => {
    expect(pollSince("0")).toBe(0);
    expect(pollSince("-1")).toBe(-1);
    expect(pollSince("12")).toBe(12);
  });
});
