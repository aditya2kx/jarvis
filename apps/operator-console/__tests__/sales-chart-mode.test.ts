import { describe, expect, it } from "vitest";
import {
  assertModeFilterCoherence,
  parseChartMode,
  parseCompare,
} from "@/lib/filters/chart-mode";
import { priorWindow, type DateWindow } from "@/lib/filters/range";
import { mergePriorSeries, pivotSalesChart } from "@/lib/charts/sales-pivot";

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
  it("is true only for 1/true", () => {
    expect(parseCompare("1")).toBe(true);
    expect(parseCompare("true")).toBe(true);
    expect(parseCompare("0")).toBe(false);
    expect(parseCompare(undefined)).toBe(false);
  });
});

describe("assertModeFilterCoherence", () => {
  it("clears compare in composition", () => {
    expect(assertModeFilterCoherence("composition", true, true)).toEqual({
      mode: "composition",
      breakdown: true,
      compare: false,
    });
  });

  it("clears breakdown in trend", () => {
    expect(assertModeFilterCoherence("trend", true, true)).toEqual({
      mode: "trend",
      breakdown: false,
      compare: true,
    });
  });

  it("allows compare off in trend", () => {
    expect(assertModeFilterCoherence("trend", false, false)).toEqual({
      mode: "trend",
      breakdown: false,
      compare: false,
    });
  });
});

describe("priorWindow", () => {
  it("shifts an equal-length window ending the day before start", () => {
    const win: DateWindow = {
      start: "2026-07-10",
      end: "2026-07-16",
      label: "Last 7 days",
      preset: "7d",
    };
    expect(priorWindow(win)).toEqual({
      start: "2026-07-03",
      end: "2026-07-09",
      label: "Prior period",
      preset: "custom",
    });
  });

  it("handles a single-day window", () => {
    const win: DateWindow = {
      start: "2026-07-15",
      end: "2026-07-15",
      label: "Custom",
      preset: "custom",
    };
    expect(priorWindow(win)).toEqual({
      start: "2026-07-14",
      end: "2026-07-14",
      label: "Prior period",
      preset: "custom",
    });
  });
});

describe("mergePriorSeries", () => {
  it("aligns prior values by index onto current labels", () => {
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
    const merged = mergePriorSeries(current, prior, "net_sales");
    expect(merged.series.map((s) => s.key)).toEqual(["net_sales", "prior_net_sales"]);
    expect(merged.series[1]?.dashed).toBe(true);
    expect(merged.data).toEqual([
      { date: "Jul 10", net_sales: 100, prior_net_sales: 80 },
      { date: "Jul 11", net_sales: 200, prior_net_sales: 90 },
    ]);
  });
});
