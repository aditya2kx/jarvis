import { describe, expect, it } from "vitest";
import { GOAL_FIELDS, fractionToPercentInput, percentInputToFraction, sanitizeDollarInput } from "@/lib/kpi/goal-fields";

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
  it("tags every percent-kind goal that health.ts multiplies by 100 for display", () => {
    const percentKeys = GOAL_FIELDS.filter((f) => f.kind === "percent").map((f) => f.key);
    expect(percentKeys).toEqual([
      "goal_labor_pct_max",
      "goal_food_cost_pct_max",
      "goal_speed_on_time_pct_min",
    ]);
  });
});
