import { describe, expect, it } from "vitest";
import {
  GOAL_FIELDS,
  fractionToPercentInput,
  percentInputToFraction,
  sanitizeDollarInput,
} from "@/lib/kpi/goal-fields";
import { avgPrepP95Min, countRiskyBases, paceFor, statusFor } from "@/lib/kpi/scorecard-math";
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
  it("tags Home percent-kind goals that convert via fraction helpers", () => {
    const percentKeys = GOAL_FIELDS.filter((f) => f.kind === "percent").map((f) => f.key);
    expect(percentKeys).toEqual(["goal_hourly_labor_pct_max", "goal_labor_pct_max"]);
  });

  it("includes p95 minutes and bases-at-risk count fields", () => {
    expect(GOAL_FIELDS.find((f) => f.key === "goal_kds_p95_min")?.kind).toBe("minutes");
    expect(GOAL_FIELDS.find((f) => f.key === "goal_bases_at_risk_max")?.kind).toBe("count");
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
    ] as BaseRunwayRow[];
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
