import { describe, expect, it } from "vitest";
import {
  GOAL_FIELDS,
  fractionToPercentInput,
  percentInputToFraction,
  sanitizeDollarInput,
} from "@/lib/kpi/goal-fields";
import {
  avgPrepP95Min,
  countRiskyBases,
  elapsedDaysInWindow,
  paceFor,
  rollupStatus,
  statusFor,
} from "@/lib/kpi/scorecard-math";
import type { BaseRunwayRow, OrderQualityDailyRow } from "@/lib/bq/queries";

describe("fractionToPercentInput", () => {
  it("converts a store_config fraction to a whole-percent display value", () => {
    expect(fractionToPercentInput("0.15")).toBe("15");
  });

  it("keeps a decimal percent (not just whole numbers)", () => {
    expect(fractionToPercentInput("0.284")).toBe("28.4");
  });

  it("passes through non-numeric input unchanged", () => {
    expect(fractionToPercentInput("")).toBe("");
  });
});

describe("percentInputToFraction", () => {
  it("converts a whole-percent operator input to the stored fraction (fixes the 15 -> 1500% bug)", () => {
    expect(percentInputToFraction("15")).toBe("0.15");
  });

  it("round-trips a decimal percent", () => {
    expect(percentInputToFraction("28.4")).toBe("0.284");
  });

  it("round-trips through both conversions", () => {
    expect(percentInputToFraction(fractionToPercentInput("0.9"))).toBe("0.9");
    expect(fractionToPercentInput(percentInputToFraction("7.5"))).toBe("7.5");
  });
});

describe("sanitizeDollarInput", () => {
  it("caps a typed dollar amount to 2 decimal places (fixes the 50.999 bug)", () => {
    expect(sanitizeDollarInput("50.999")).toBe("50.99");
  });

  it("leaves a whole-number amount unchanged", () => {
    expect(sanitizeDollarInput("18000")).toBe("18000");
  });

  it("leaves an amount already at 2 decimals unchanged", () => {
    expect(sanitizeDollarInput("75000.50")).toBe("75000.50");
  });

  it("strips non-numeric characters (e.g. a pasted '$')", () => {
    expect(sanitizeDollarInput("$1,234.567")).toBe("1234.56");
  });

  it("collapses multiple decimal points to the first one", () => {
    expect(sanitizeDollarInput("12.34.56")).toBe("12.34");
  });

  it("passes through an empty string", () => {
    expect(sanitizeDollarInput("")).toBe("");
  });
});

describe("GOAL_FIELDS", () => {
  it("includes part-time and total labor % goals (Issue #166)", () => {
    const percentKeys = GOAL_FIELDS.filter((f) => f.kind === "percent").map((f) => f.key);
    expect(percentKeys).toEqual(["goal_hourly_labor_pct_max", "goal_labor_pct_max"]);
  });

  it("includes hierarchy goals: cash flow, orders, cost dollars, labor %, p95, bases at risk", () => {
    expect(GOAL_FIELDS.find((f) => f.key === "goal_cash_flow_weekly")?.kind).toBe("dollars");
    expect(GOAL_FIELDS.find((f) => f.key === "goal_orders_per_day")?.kind).toBe("count");
    expect(GOAL_FIELDS.find((f) => f.key === "goal_labor_cost_weekly")?.kind).toBe("dollars");
    expect(GOAL_FIELDS.find((f) => f.key === "goal_hourly_labor_pct_max")?.kind).toBe("percent");
    expect(GOAL_FIELDS.find((f) => f.key === "goal_labor_pct_max")?.kind).toBe("percent");
    expect(GOAL_FIELDS.find((f) => f.key === "goal_kds_p95_min")?.kind).toBe("minutes");
    expect(GOAL_FIELDS.find((f) => f.key === "goal_bases_at_risk_max")?.kind).toBe("count");
  });
});

describe("rollupStatus", () => {
  it("worst-wins: mixed on-track + at-risk → at-risk", () => {
    expect(rollupStatus(["on-track", "at-risk"])).toBe("at-risk");
  });

  it("all no-goal → no-goal (not on-track)", () => {
    expect(rollupStatus(["no-goal", "no-goal"])).toBe("no-goal");
  });

  it("on-track + no-goal → no-goal (missing goal is worse than on-track)", () => {
    expect(rollupStatus(["on-track", "no-goal"])).toBe("no-goal");
  });

  it("off-track beats at-risk", () => {
    expect(rollupStatus(["at-risk", "off-track", "on-track"])).toBe("off-track");
  });
});

describe("paceFor / statusFor (Issue #158)", () => {
  it("treats goal=0 lower-is-better with actual=0 as on-track", () => {
    expect(paceFor(0, 0, true)).toBe(1);
    expect(statusFor(paceFor(0, 0, true))).toBe("on-track");
  });

  it("treats goal=0 lower-is-better with actual>0 as off-track", () => {
    expect(paceFor(2, 0, true)).toBe(0);
    expect(statusFor(paceFor(2, 0, true))).toBe("off-track");
  });

  it("computes lower-is-better pace for prep p95", () => {
    expect(paceFor(8, 8, true)).toBe(1);
    expect(paceFor(10, 8, true)).toBeCloseTo(0.8);
    expect(statusFor(paceFor(10, 8, true))).toBe("off-track");
  });
});

describe("countRiskyBases", () => {
  it("counts Status=Risky rows", () => {
    const rows = [
      { Status: "Risky" },
      { Status: "Fine" },
      { Status: "Risky" },
    ] as unknown as BaseRunwayRow[];
    expect(countRiskyBases(rows)).toBe(2);
  });
});

describe("avgPrepP95Min", () => {
  it("averages kds_p95_min over the window", () => {
    const rows = [{ kds_p95_min: 6 }, { kds_p95_min: 10 }] as OrderQualityDailyRow[];
    expect(avgPrepP95Min(rows)).toBe(8);
  });

  it("returns null when no values", () => {
    expect(avgPrepP95Min([])).toBeNull();
  });
});

describe("elapsedDaysInWindow", () => {
  it("caps this_month end at today so future days do not dilute averages", () => {
    // July 1–31 window, "today" = July 12 → 12 days (not 31).
    expect(elapsedDaysInWindow("2026-07-01", "2026-07-31", "2026-07-12")).toBe(12);
  });

  it("uses full window when end is already in the past", () => {
    expect(elapsedDaysInWindow("2026-06-01", "2026-06-30", "2026-07-12")).toBe(30);
  });
});
