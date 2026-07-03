import { describe, expect, it } from "vitest";
import { formatCents, formatPct, formatNumber, formatDate } from "@/lib/format";

describe("formatCents", () => {
  it("formats integer cents as USD", () => {
    expect(formatCents(123456)).toBe("$1,234.56");
  });
  it("handles null/undefined as em dash", () => {
    expect(formatCents(null)).toBe("—");
    expect(formatCents(undefined)).toBe("—");
  });
});

describe("formatPct", () => {
  it("formats a 0-1 fraction as a percentage", () => {
    expect(formatPct(0.284)).toBe("28.4%");
  });
});

describe("formatNumber", () => {
  it("groups thousands", () => {
    expect(formatNumber(12345)).toBe("12,345");
  });
});

describe("formatDate", () => {
  it("renders in America/Chicago regardless of host tz", () => {
    expect(formatDate("2026-07-04T00:00:00Z")).toBe("Jul 3");
  });
});
