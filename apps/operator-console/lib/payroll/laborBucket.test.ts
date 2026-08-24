import { describe, expect, it } from "vitest";
import { laborTypeForEmployee, rowMatchesLaborType } from "./laborBucket";

describe("laborTypeForEmployee", () => {
  it("marks tip-pool exclusion as Full-time (Lindsay)", () => {
    expect(
      laborTypeForEmployee({
        isSalaried: false,
        excludedFromLaborPct: true,
        excludedFromTipPool: true,
      }),
    ).toBe("Full-time");
  });

  it("marks hourly tipped staff as Part-time", () => {
    expect(
      laborTypeForEmployee({
        isSalaried: false,
        excludedFromLaborPct: false,
        excludedFromTipPool: false,
      }),
    ).toBe("Part-time");
  });
});

describe("rowMatchesLaborType", () => {
  it("shows both when selected is null (All)", () => {
    expect(rowMatchesLaborType("Full-time", null)).toBe(true);
    expect(rowMatchesLaborType("Part-time", null)).toBe(true);
  });

  it("hides Lindsay on Part-time only", () => {
    expect(rowMatchesLaborType("Full-time", ["Part-time"])).toBe(false);
    expect(rowMatchesLaborType("Part-time", ["Part-time"])).toBe(true);
  });

  it("shows none when selection is empty", () => {
    expect(rowMatchesLaborType("Part-time", [])).toBe(false);
  });
});
