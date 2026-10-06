import { describe, expect, it } from "vitest";
import { suggestedCostByDay } from "@/components/labor/LaborWagesChart";

describe("suggestedCostByDay", () => {
  const rates = { byName: { "Ortiz, Ximena": 16.25, "Berding, Kenya": 15 }, avgPartTime: 15.5 };

  it("prices each draft at the suggested person's rate", () => {
    const out = suggestedCostByDay(
      new Map([["2026-10-13", new Map([["Ortiz, Ximena", 6], ["Berding, Kenya", 4]])]]),
      rates,
    );
    expect(out.get("2026-10-13")).toBe(6 * 16.25 + 4 * 15);
  });

  it("uses the average part-time rate for unassigned or unpriced drafts", () => {
    const out = suggestedCostByDay(
      new Map([["2026-10-14", new Map([["", 4], ["New Hire", 2]])]]),
      rates,
    );
    expect(out.get("2026-10-14")).toBe(6 * 15.5);
  });

  it("drops days with no cost", () => {
    const out = suggestedCostByDay(new Map([["2026-10-15", new Map([["", 4]])]]), {
      byName: {},
      avgPartTime: null,
    });
    expect(out.size).toBe(0);
  });
});
