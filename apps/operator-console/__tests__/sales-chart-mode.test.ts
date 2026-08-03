import { describe, expect, it } from "vitest";
import {
  assertModeFilterCoherence,
  parseChartMode,
  parseCompare,
} from "@/lib/filters/chart-mode";
import {
  enumerateBucketStarts,
  priorWindow,
  type DateWindow,
} from "@/lib/filters/range";
import { compareGrainLabel, mergePriorSeries, pctChange } from "@/lib/charts/compare-series";
import { pivotSalesChart } from "@/lib/charts/sales-pivot";

describe("parseChartMode", () => {
  it("defaults to composition", () => {
    expect(parseChartMode(undefined)).toBe("composition");
    expect(parseChartMode("")).toBe("composition");
    expect(parseChartMode("bogus")).toBe("composition");
  });

  it("accepts trend", () => {
    expect(parseChartMode("trend")).toBe("trend");
    expect(parseChartMode("TREND")).toBe("trend");
  });
});

describe("parseCompare", () => {
  it("parses off / day / week / month", () => {
    expect(parseCompare(undefined)).toBe("off");
    expect(parseCompare("0")).toBe("off");
    expect(parseCompare("off")).toBe("off");
    expect(parseCompare("day")).toBe("day");
    expect(parseCompare("week")).toBe("week");
    expect(parseCompare("month")).toBe("month");
  });

  it("maps legacy 1/true to Aggregation grain", () => {
    expect(parseCompare("1", "day")).toBe("day");
    expect(parseCompare("true", "week")).toBe("week");
    expect(parseCompare("1", "month")).toBe("month");
    expect(parseCompare("1", "weekday")).toBe("week");
  });
});

describe("assertModeFilterCoherence", () => {
  it("clears compare in composition", () => {
    expect(assertModeFilterCoherence("composition", true, "week")).toEqual({
      mode: "composition",
      breakdown: true,
      compare: "off",
    });
  });

  it("clears breakdown in trend", () => {
    expect(assertModeFilterCoherence("trend", true, "day")).toEqual({
      mode: "trend",
      breakdown: false,
      compare: "day",
    });
  });

  it("allows compare off in trend", () => {
    expect(assertModeFilterCoherence("trend", false, "off")).toEqual({
      mode: "trend",
      breakdown: false,
      compare: "off",
    });
  });
});

describe("priorWindow", () => {
  it("day grain: each point vs previous day (window shifted by 1 day of buckets)", () => {
    const win: DateWindow = {
      start: "2026-07-10",
      end: "2026-07-16",
      label: "Last 7 days",
      preset: "7d",
    };
    expect(priorWindow(win, "day")).toEqual({
      start: "2026-07-09",
      end: "2026-07-15",
      label: "Prior period",
      preset: "custom",
    });
  });

  it("handles a single-day window → previous day", () => {
    const win: DateWindow = {
      start: "2026-07-15",
      end: "2026-07-15",
      label: "Custom",
      preset: "custom",
    };
    expect(priorWindow(win, "day")).toEqual({
      start: "2026-07-14",
      end: "2026-07-14",
      label: "Prior period",
      preset: "custom",
    });
  });

  it("week grain: prior buckets are exactly current buckets − 1 week", () => {
    // Last-30d style window through Jul 26
    const win: DateWindow = {
      start: "2026-06-27",
      end: "2026-07-26",
      label: "Last 30 days",
      preset: "30d",
    };
    const prior = priorWindow(win, "week");
    const curBuckets = enumerateBucketStarts(win, "week");
    const priorBuckets = enumerateBucketStarts(prior, "week");
    expect(curBuckets).toEqual([
      "2026-06-22",
      "2026-06-29",
      "2026-07-06",
      "2026-07-13",
      "2026-07-20",
    ]);
    expect(priorBuckets).toEqual([
      "2026-06-15",
      "2026-06-22",
      "2026-06-29",
      "2026-07-06",
      "2026-07-13",
    ]);
    expect(prior).toEqual({
      start: "2026-06-15",
      end: "2026-07-19", // end of Wk of Jul 13
      label: "Prior period",
      preset: "custom",
    });
  });

  it("day display + week compare: each day vs same weekday previous week", () => {
    const win: DateWindow = {
      start: "2026-07-13", // Mon
      end: "2026-07-19", // Sun
      label: "This week",
      preset: "this_week",
    };
    expect(priorWindow(win, "day", "week")).toEqual({
      start: "2026-07-06",
      end: "2026-07-12",
      label: "Prior period",
      preset: "custom",
    });
  });

  it("day display + month compare: each day vs ~same calendar day previous month", () => {
    const win: DateWindow = {
      start: "2026-07-10",
      end: "2026-07-12",
      label: "Custom",
      preset: "custom",
    };
    expect(priorWindow(win, "day", "month")).toEqual({
      start: "2026-06-10",
      end: "2026-06-12",
      label: "Prior period",
      preset: "custom",
    });
  });
});

describe("compareGrainLabel", () => {
  it("names the Compare dropdown option", () => {
    expect(compareGrainLabel("off")).toBe("Off");
    expect(compareGrainLabel("day")).toBe("Previous day");
    expect(compareGrainLabel("week")).toBe("Previous week");
    expect(compareGrainLabel("month")).toBe("Previous month");
  });
});

describe("pctChange", () => {
  it("computes signed percent change", () => {
    expect(pctChange(120, 100)).toBe(20);
    expect(pctChange(80, 100)).toBe(-20);
  });

  it("returns null when prior is missing or zero", () => {
    expect(pctChange(10, null)).toBeNull();
    expect(pctChange(null, 10)).toBeNull();
    expect(pctChange(10, 0)).toBeNull();
  });
});

describe("mergePriorSeries", () => {
  it("aligns prior values by index onto current labels; % change is tooltip-only", () => {
    const current = pivotSalesChart(
      [
        { date: "Jul 10", source: null, net_sales: 100, orders: 1, items_sold: 1 },
        { date: "Jul 11", source: null, net_sales: 200, orders: 2, items_sold: 2 },
      ],
      "net_sales",
      false,
      "Net sales",
    );
    const prior = pivotSalesChart(
      [
        { date: "Jul 3", source: null, net_sales: 80, orders: 1, items_sold: 1 },
        { date: "Jul 4", source: null, net_sales: 90, orders: 1, items_sold: 1 },
      ],
      "net_sales",
      false,
      "Net sales",
    );
    const merged = mergePriorSeries(current, prior, "net_sales", "Prior period", [
      "Wk of Jul 3",
      "Wk of Jul 4",
    ]);
    expect(merged.series.map((s) => s.key)).toEqual([
      "net_sales",
      "prior_net_sales",
    ]);
    expect(merged.series.some((s) => s.yAxisId === "right")).toBe(false);
    expect(merged.data).toEqual([
      {
        date: "Jul 10",
        net_sales: 100,
        prior_net_sales: 80,
        pct_net_sales: 25,
        prior_bucket: "Wk of Jul 3",
      },
      {
        date: "Jul 11",
        net_sales: 200,
        prior_net_sales: 90,
        pct_net_sales: expect.closeTo((200 - 90) / 90 * 100),
        prior_bucket: "Wk of Jul 4",
      },
    ]);
  });

  it("keeps null prior buckets as gaps (not fake zeros)", () => {
    const current = {
      data: [{ date: "A", net_sales: 10 }, { date: "B", net_sales: 20 }],
      series: [{ key: "net_sales", label: "Net sales" }],
    };
    const prior = {
      data: [{ date: "pA", net_sales: null }, { date: "pB", net_sales: 5 }],
      series: [{ key: "net_sales", label: "Net sales" }],
    };
    const merged = mergePriorSeries(current, prior, "net_sales");
    expect(merged.data[0]?.prior_net_sales).toBeNull();
    expect(merged.data[0]?.pct_net_sales).toBeNull();
    expect(merged.data[1]?.prior_net_sales).toBe(5);
    expect(merged.data[1]?.pct_net_sales).toBe(300);
  });
});
