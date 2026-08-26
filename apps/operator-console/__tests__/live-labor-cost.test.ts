import { describe, expect, it } from "vitest";
import {
  isFullTimeLaborBucket,
  laborPctOfNetSales,
  shiftLaborDollars,
} from "@/lib/labor/live-labor-cost";

describe("isFullTimeLaborBucket", () => {
  it("treats salaried or excluded_from_labor_pct as FT", () => {
    expect(isFullTimeLaborBucket({ isSalaried: true })).toBe(true);
    expect(isFullTimeLaborBucket({ excludedFromLaborPct: true })).toBe(true);
    expect(isFullTimeLaborBucket({ isSalaried: false, excludedFromLaborPct: false })).toBe(false);
  });
});

describe("shiftLaborDollars", () => {
  it("puts hourly staff dollars in the PT bucket", () => {
    expect(shiftLaborDollars(10, 15.25, {})).toEqual({ hourlyCost: 152.5, fulltimeCost: 0 });
  });

  it("puts Lindsay-style excluded staff in the FT bucket", () => {
    expect(shiftLaborDollars(8, 25, { excludedFromLaborPct: true })).toEqual({
      hourlyCost: 0,
      fulltimeCost: 200,
    });
  });
});

describe("laborPctOfNetSales — Issue #267 frozen-rate regression", () => {
  // Weeks of 2026-08-10 / 2026-08-17: hours matched; model dollars used ~$1.25
  // token rates (~$9/hr) vs restored ~$15.50. Presentation must use live $.
  it("does not accept the frozen 12.8% / 17.8% PT figures once rates are restored", () => {
    expect(laborPctOfNetSales(1777.23, 10004.43)).toBeCloseTo(0.178, 3);
    expect(laborPctOfNetSales(1584.97, 12400.24)).toBeCloseTo(0.128, 3);
    expect(laborPctOfNetSales(3048.59, 10004.43)).toBeCloseTo(0.305, 3);
    expect(laborPctOfNetSales(2907.37, 12400.24)).toBeCloseTo(0.234, 3);
  });
});
