import { describe, expect, it } from "vitest";
import { computeLaborForwardSummary } from "@/lib/kpi/labor-forward";
import {
  laborCoverageLabel,
  parseLaborLens,
  periodDayCount,
  viewForLaborLens,
} from "@/lib/kpi/labor-lens";

const summary = computeLaborForwardSummary({
  completedPtCost: 1000,
  completedFtCost: 200,
  completedNetSales: 4000,
  completedDayCount: 3,
  fwdScheduledHours: 50,
  fwdForecastOrders: 200,
  fwdDays: 4,
  avgPtWage: 15,
  aov: 16,
  avgFtCostPerOpenDay: 150,
  laborBurdenPct: 0.1,
});

describe("parseLaborLens", () => {
  it("defaults to wage", () => {
    expect(parseLaborLens(undefined)).toBe("wage");
    expect(parseLaborLens("nope")).toBe("wage");
  });
  it("accepts paid and blended", () => {
    expect(parseLaborLens("paid")).toBe("paid");
    expect(parseLaborLens("blended")).toBe("blended");
  });
});

describe("viewForLaborLens", () => {
  it("wage uses completed wage only", () => {
    const v = viewForLaborLens(summary, "wage", 31);
    expect(v.ptPct).toBeCloseTo(1000 / 4000);
    expect(v.totalPct).toBeCloseTo(1200 / 4000);
    expect(v.ptDollars).toBe(1000);
    expect(v.coverage).toMatch(/3 completed/);
    expect(v.coverage).toMatch(/31-day Period/);
  });

  it("paid uses completed all-in only", () => {
    const v = viewForLaborLens(summary, "paid");
    expect(v.paidUnavailable).toBe(false);
    expect(v.ptDollars).toBeCloseTo(1000 * 1.1);
    expect(v.totalPct).toBeCloseTo((1200 * 1.1) / 4000);
  });

  it("blended uses projected wage blend (not all-in)", () => {
    const v = viewForLaborLens(summary, "blended", 31);
    expect(v.ptDollars).toBeCloseTo(1000 + 50 * 15);
    expect(v.totalDollars).toBeCloseTo(1200 + 750 + 600);
    expect(v.title).toMatch(/Blended/i);
    expect(v.coverage).toMatch(/3 completed \+ 4 scheduled = 7 days/);
    expect(v.coverage).toMatch(/31-day Period/);
  });

  it("paid unavailable when burden is 0", () => {
    const s = computeLaborForwardSummary({
      completedPtCost: 1000,
      completedFtCost: 200,
      completedNetSales: 4000,
      completedDayCount: 3,
      fwdScheduledHours: 0,
      fwdForecastOrders: 0,
      fwdDays: 0,
      avgPtWage: 15,
      aov: 16,
      avgFtCostPerOpenDay: 150,
      laborBurdenPct: 0,
    });
    const v = viewForLaborLens(s, "paid");
    expect(v.paidUnavailable).toBe(true);
    expect(v.ptPct).toBeNull();
  });
});

describe("periodDayCount / laborCoverageLabel", () => {
  it("counts inclusive period days", () => {
    expect(periodDayCount("2026-07-01", "2026-07-31")).toBe(31);
  });
  it("formats blended coverage with period share", () => {
    expect(laborCoverageLabel(summary, "blended", 31)).toMatch(
      /3 completed \+ 4 scheduled = 7 days \(23% of 31-day Period/,
    );
  });
});
