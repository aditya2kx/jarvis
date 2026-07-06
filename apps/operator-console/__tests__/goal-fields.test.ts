import { describe, expect, it } from "vitest";
import { GOAL_FIELDS, fractionToPercentInput, percentInputToFraction } from "@/lib/kpi/goal-fields";

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
