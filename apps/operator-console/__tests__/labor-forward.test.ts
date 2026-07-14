import { describe, expect, it } from "vitest";
import {
  computeLaborForwardSummary,
  type LaborForwardRaw,
} from "@/lib/kpi/labor-forward";

function base(over: Partial<LaborForwardRaw> = {}): LaborForwardRaw {
  return {
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
    laborBurdenPct: 0,
    ...over,
  };
}

describe("computeLaborForwardSummary", () => {
  it("computes completed PT/total % from wage costs and net sales", () => {
    const s = computeLaborForwardSummary(base());
    expect(s.hasCompleted).toBe(true);
    expect(s.completedPtCost).toBe(1000);
    expect(s.completedTotalCost).toBe(1200);
    expect(s.completedPtPct).toBeCloseTo(1000 / 4000);
    expect(s.completedTotalPct).toBeCloseTo(1200 / 4000);
    expect(s.laborBurdenPct).toBe(0);
    expect(s.completedPtCostAllIn).toBeNull();
  });

  it("blends completed + forward into projected % (PT and total)", () => {
    // fwd PT = 50 × 15 = 750; fwd FT = 150 × 4 = 600; fwd sales = 200 × 16 = 3200
    const s = computeLaborForwardSummary(base());
    expect(s.hasForward).toBe(true);
    expect(s.projectedPtCost).toBeCloseTo(1000 + 750);
    expect(s.projectedTotalCost).toBeCloseTo(1200 + 750 + 600);
    expect(s.projectedNetSales).toBeCloseTo(4000 + 3200);
    expect(s.projectedPtPct).toBeCloseTo(1750 / 7200);
    expect(s.projectedTotalPct).toBeCloseTo(2550 / 7200);
  });

  it("hides all-in when burden is 0 and applies multiplier when set", () => {
    const off = computeLaborForwardSummary(base({ laborBurdenPct: 0 }));
    expect(off.projectedTotalCostAllIn).toBeNull();
    expect(off.projectedTotalPctAllIn).toBeNull();

    const on = computeLaborForwardSummary(base({ laborBurdenPct: 0.13 }));
    expect(on.laborBurdenPct).toBe(0.13);
    expect(on.completedTotalCostAllIn).toBeCloseTo(1200 * 1.13);
    expect(on.projectedPtCostAllIn).toBeCloseTo(1750 * 1.13);
    expect(on.projectedTotalPctAllIn).toBeCloseTo((2550 * 1.13) / 7200);
  });

  it("treats empty-forward (past-only period) as no projection blend", () => {
    const s = computeLaborForwardSummary(
      base({ fwdDays: 0, fwdScheduledHours: 0, fwdForecastOrders: 0 }),
    );
    expect(s.hasForward).toBe(false);
    expect(s.projectedPtCost).toBe(1000);
    expect(s.projectedTotalCost).toBe(1200);
    expect(s.projectedPtPct).toBeCloseTo(0.25);
  });

  it("treats empty-completed (future-only period) as schedule+forecast only", () => {
    const s = computeLaborForwardSummary(
      base({
        completedDayCount: 0,
        completedPtCost: 0,
        completedFtCost: 0,
        completedNetSales: 0,
      }),
    );
    expect(s.hasCompleted).toBe(false);
    expect(s.completedPtCost).toBeNull();
    expect(s.completedPtPct).toBeNull();
    expect(s.projectedPtCost).toBeCloseTo(750);
    expect(s.projectedTotalCost).toBeCloseTo(750 + 600);
    expect(s.projectedNetSales).toBeCloseTo(3200);
    expect(s.projectedPtPct).toBeCloseTo(750 / 3200);
  });

  it("does not invent forward PT cost when avg wage is missing", () => {
    const s = computeLaborForwardSummary(base({ avgPtWage: null }));
    expect(s.projectedPtCost).toBe(1000); // completed only; fwd PT = 0
    expect(s.avgPtWage).toBeNull();
  });

  it("returns null ratios when sales denominator is zero", () => {
    const s = computeLaborForwardSummary(
      base({
        completedNetSales: 0,
        aov: null,
        fwdForecastOrders: 0,
      }),
    );
    expect(s.completedPtPct).toBeNull();
    expect(s.projectedPtPct).toBeNull();
  });
});
