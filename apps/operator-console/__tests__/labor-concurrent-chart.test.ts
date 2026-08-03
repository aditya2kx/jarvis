import { describe, expect, it } from "vitest";
import {
  concurrentTooltipEntries,
  stackOrScopedConcurrent,
} from "@/components/labor/LaborConcurrentChart";

describe("concurrentTooltipEntries", () => {
  const row = {
    date: "Wk of Jul 27",
    parttime_concurrent: 1.9,
    fulltime_concurrent: 0.3,
    total_concurrent: 2.2,
    parttime_scheduled_concurrent: 2.5,
    fulltime_scheduled_concurrent: 1.0,
    // Non-additive hours÷span total — must NOT be used as stacked bar Total.
    total_scheduled_concurrent: 2.8,
  };

  it("lists actual, scheduled, and combined totals", () => {
    const tip = concurrentTooltipEntries(row, null);
    expect(tip.map((e) => e.label)).toEqual([
      "Part-time (actual)",
      "Full-time (actual)",
      "Total (actual)",
      "Part-time (scheduled)",
      "Full-time (scheduled)",
      "Total (scheduled)",
      "Total (combined)",
    ]);
    expect(tip.find((e) => e.label === "Total (actual)")?.value).toBe("2.2");
    expect(tip.find((e) => e.label === "Total (scheduled)")?.value).toBe("3.5");
    // mean of stacked totals 2.2 and 3.5
    expect(tip.find((e) => e.label === "Total (combined)")?.value).toBe("2.9");
  });

  it("Total (scheduled) matches stacked bar (PT+FT), not hours÷span total", () => {
    const tip = concurrentTooltipEntries(
      {
        date: "Wk of Aug 10",
        parttime_concurrent: null,
        fulltime_concurrent: null,
        total_concurrent: null,
        parttime_scheduled_concurrent: 2.0,
        fulltime_scheduled_concurrent: 2.4,
        total_scheduled_concurrent: 2.4,
      },
      null,
    );
    expect(tip.find((e) => e.label === "Total (scheduled)")?.value).toBe("4.4");
  });
});

describe("stackOrScopedConcurrent", () => {
  it("sums PT+FT when both labor types are shown", () => {
    expect(stackOrScopedConcurrent(2.0, 2.4, 2.4, null)).toBe(4.4);
  });
});
