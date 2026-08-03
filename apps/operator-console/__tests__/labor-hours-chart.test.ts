import { describe, expect, it } from "vitest";
import {
  formatHoursWithPct,
  goalHoursAsSalesPct,
  laborTooltipContent,
  pctOfHoursGoal,
  scopedLaborMetrics,
  weeklyHoursGoalApplicable,
} from "@/components/labor/LaborHoursChart";
import { GOAL_FIELDS } from "@/lib/kpi/goal-fields";
import {
  LABOR_TYPE_OPTIONS,
  parseLaborTypes,
  serializeLaborTypes,
} from "@/lib/filters/labor-type";

describe("weeklyHoursGoalApplicable", () => {
  it("only applies at week Aggregation", () => {
    expect(weeklyHoursGoalApplicable("week")).toBe(true);
    expect(weeklyHoursGoalApplicable("day")).toBe(false);
    expect(weeklyHoursGoalApplicable("weekday")).toBe(false);
    expect(weeklyHoursGoalApplicable("month")).toBe(false);
  });
});

describe("pctOfHoursGoal", () => {
  it("is combined hours ÷ goal × 100", () => {
    expect(pctOfHoursGoal(195.7, 230)).toBe(85.1);
  });
});

describe("goalHoursAsSalesPct", () => {
  it("converts absolute hours goal into implied % of net sales", () => {
    // 230 / 265.2 * 0.399 * 100 ≈ 34.6
    expect(goalHoursAsSalesPct(230, 265.2, 0.399)).toBe(34.6);
  });
});

describe("formatHoursWithPct", () => {
  it("puts labor % in brackets beside hours", () => {
    expect(formatHoursWithPct(226.9, 0.314)).toBe("226.9 (31.4%)");
  });
});

describe("labor type filter", () => {
  it("collapses both selected to All", () => {
    expect(parseLaborTypes("Full-time,Part-time")).toBeNull();
    expect(serializeLaborTypes(null)).toBe("");
    expect([...LABOR_TYPE_OPTIONS]).toEqual(["Part-time", "Full-time"]);
  });
});

describe("scopedLaborMetrics", () => {
  it("uses totals when All", () => {
    expect(
      scopedLaborMetrics(
        {
          total_hours: 40,
          parttime_hours: 30,
          fulltime_hours: 10,
          labor_pct: 0.4,
          hourly_pct: 0.3,
          fulltime_pct: 0.1,
        },
        null,
      ),
    ).toEqual({ hours: 40, laborPct: 0.4 });
  });
});

describe("laborTooltipContent", () => {
  const row = {
    date: "Wk of Jul 6",
    bucket_iso: "2026-07-06",
    total_hours: 265.2,
    parttime_hours: 226.9,
    fulltime_hours: 38.3,
    labor_pct: 0.399,
    hourly_pct: 0.314,
    fulltime_pct: 0.085,
    net_sales: 11161,
  };

  it("lists PT/FT/Total and Goal with % of goal + % of sales on completed weeks", () => {
    const tip = laborTooltipContent(row, 230, "week", null);
    expect(tip.entries.map((e) => e.label)).toEqual([
      "Part-time (actual)",
      "Full-time (actual)",
      "Total (actual)",
    ]);
    // 265.2/230 ≈ 115.3% of goal; (230/265.2)*39.9% ≈ 34.6% of sales
    expect(tip.lines).toEqual(["Goal 230 hrs (115.3% of goal · 34.6% of sales)"]);
  });

  it("omits Goal when Aggregation is not week", () => {
    expect(laborTooltipContent(row, 230, "day", null).lines).toEqual([]);
    expect(laborTooltipContent(row, 230, "weekday", null).lines).toEqual([]);
    expect(laborTooltipContent(row, 230, "month", null).lines).toEqual([]);
  });

  it("adds Total (combined); Goal shows % of goal only while week still has schedule", () => {
    const tip = laborTooltipContent(
      {
        ...row,
        parttime_scheduled_hours: 35,
        fulltime_scheduled_hours: 3.5,
      },
      230,
      "week",
      null,
    );
    expect(tip.entries.map((e) => e.label)).toContain("Total (combined)");
    expect(tip.entries.find((e) => e.label === "Total (combined)")?.value).toBe("303.7");
    expect(tip.lines[0]).toBe("Goal 230 hrs (132.0% of goal)");
    expect(tip.lines[0]).not.toMatch(/of sales/);
  });

  it("shows scheduled hours without labor %; Goal uses scheduled when no actual", () => {
    const tip = laborTooltipContent(
      {
        ...row,
        total_hours: null,
        parttime_hours: null,
        fulltime_hours: null,
        labor_pct: null,
        hourly_pct: null,
        fulltime_pct: null,
        parttime_scheduled_hours: 40,
        fulltime_scheduled_hours: 16,
      },
      230,
      "week",
      null,
    );
    expect(tip.entries.map((e) => e.label)).toEqual([
      "Part-time (scheduled)",
      "Full-time (scheduled)",
      "Total (scheduled)",
    ]);
    expect(tip.entries[0]?.value).toBe("40");
    expect(tip.lines).toContain("Scheduled — no labor % (no Square sales yet)");
    expect(tip.lines.some((l) => l.includes("% of goal"))).toBe(true);
  });
});

describe("GOAL_FIELDS labor hours", () => {
  it("includes weekly labor hours goal", () => {
    expect(GOAL_FIELDS.find((f) => f.key === "goal_labor_hours_week")?.kind).toBe("hours");
  });
});
