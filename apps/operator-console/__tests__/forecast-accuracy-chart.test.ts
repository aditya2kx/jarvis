import { describe, expect, it } from "vitest";
import { mergeForecastAccuracyChart } from "@/lib/kpi/forecast-accuracy-chart";

describe("mergeForecastAccuracyChart", () => {
  it("keeps Period actuals and extends forecast into the forward horizon", () => {
    const chart = mergeForecastAccuracyChart(
      [
        {
          date: "2026-07-25",
          forecast_orders: 100,
          actual_orders: 110,
          forecast_items: 200,
          actual_items: 220,
        },
        {
          date: "2026-07-26",
          forecast_orders: 105,
          actual_orders: 100,
          forecast_items: 210,
          actual_items: 200,
        },
      ],
      [
        {
          date: "2026-07-27",
          forecast_orders: 120,
          forecast_items: 240,
        },
        {
          date: "2026-08-05",
          forecast_orders: 130,
          forecast_items: 260,
        },
      ],
      { forecastKey: "forecast_orders", actualKey: "actual_orders", grain: "day" },
    );

    expect(chart).toEqual([
      { date: "Jul 25", forecast: 100, actual: 110 },
      { date: "Jul 26", forecast: 105, actual: 100 },
      { date: "Jul 27", forecast: 120, actual: null },
      { date: "Aug 5", forecast: 130, actual: null },
    ]);
  });

  it("prefers forward forecast on overlapping today while preserving actual", () => {
    const chart = mergeForecastAccuracyChart(
      [
        {
          date: "2026-07-27",
          forecast_orders: 90,
          actual_orders: 95,
          forecast_items: 1,
          actual_items: 1,
        },
      ],
      [{ date: "2026-07-27", forecast_orders: 120, forecast_items: 2 }],
      { forecastKey: "forecast_orders", actualKey: "actual_orders", grain: "day" },
    );

    expect(chart).toEqual([{ date: "Jul 27", forecast: 120, actual: 95 }]);
  });

  it("formats week grain buckets for both series", () => {
    const chart = mergeForecastAccuracyChart(
      [
        {
          date: "2026-07-20",
          forecast_orders: 700,
          actual_orders: 710,
          forecast_items: 1,
          actual_items: 1,
        },
      ],
      [{ date: "2026-07-27", forecast_orders: 800, forecast_items: 2 }],
      { forecastKey: "forecast_orders", actualKey: "actual_orders", grain: "week" },
    );

    expect(chart).toHaveLength(2);
    expect(chart[0]?.actual).toBe(710);
    expect(chart[0]?.date).toBe("Wk of Jul 20");
    expect(chart[1]?.actual).toBeNull();
    expect(chart[1]?.forecast).toBe(800);
    expect(chart[1]?.date).toBe("Wk of Jul 27");
  });
});
