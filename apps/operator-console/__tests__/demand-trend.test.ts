import { describe, expect, it } from "vitest";
import { shiftCalendarDate } from "@/lib/filters/range";
import { capLabel, forecastWeek, scaleDemand, trendFactor, weekCap, type TrendDay } from "@/lib/labor/demand-trend";

const AS_OF = "2026-11-16"; // a Monday; the 8 weeks before it are Sep 21 – Nov 15
const W = [0.4, 0.3, 0.2, 0.1];

/** 56 days before AS_OF; `orders(i)` with i = 0 the oldest day. */
const days = (orders: (i: number) => number, extra: Partial<TrendDay> = {}): TrendDay[] =>
  Array.from({ length: 56 }, (_, i) => ({
    date: shiftCalendarDate(AS_OF, "day", -56 + i),
    orders: orders(i),
    net_sales: orders(i) * 17.5,
    labor_cost: orders(i) ? 400 : 0,
    hours: orders(i) ? 25 : 0,
    ...extra,
  }));

describe("trendFactor", () => {
  it("is 1 for steady weeks, no weights or no history", () => {
    expect(trendFactor(days(() => 80), AS_OF, W)).toBeCloseTo(1);
    expect(trendFactor(days((i) => (i >= 49 ? 200 : 80)), AS_OF, null)).toBe(1);
    expect(trendFactor([], AS_OF, W)).toBe(1);
  });

  it("weights the latest week most: a +30% week lifts the next forecast, then fades as it ages", () => {
    const spikeAt = (week: number) => days((i) => (Math.floor(i / 7) === 7 - week ? 104 : 80));
    const lift = [0, 1, 2, 3, 4].map((w) => trendFactor(spikeAt(w), AS_OF, W) * (80 * 8 + 24) / (80 * 8) - 1);
    // vs a flat 80/day baseline: +12%, +9%, +6%, +3%, then gone (outside the 4 weeks).
    expect(lift.map((x) => Math.round(x * 100))).toEqual([12, 9, 6, 3, 0]);
  });

  it("follows a slow month down", () => {
    // Weeks 5–8 back at 100/day, the last 4 at 70/day: 70 ÷ 85 ≈ 0.82.
    expect(trendFactor(days((i) => (i < 28 ? 100 : 70)), AS_OF, W)).toBeCloseTo(70 / 85, 3);
  });

  it("ignores closed days and weeks with no open days", () => {
    const sundaysClosed = days((i) => (i % 7 === 6 ? 0 : 80));
    expect(trendFactor(sundaysClosed, AS_OF, W)).toBeCloseTo(1);
    const lastWeekClosed = days((i) => (i >= 49 ? 0 : 80));
    expect(trendFactor(lastWeekClosed, AS_OF, W)).toBeCloseTo(1);
  });
});

describe("scaleDemand", () => {
  it("scales every hour by the factor", () => {
    expect(scaleDemand([{ dow: 0, hour: 9, orders: 10 }], 0.85)).toEqual([{ dow: 0, hour: 9, orders: 8.5 }]);
  });
});

describe("forecastWeek + weekCap", () => {
  it("forecasts the week's sales by weekday × trend and labor $ per paid hour", () => {
    const f = forecastWeek(days(() => 80), AS_OF, 0.85);
    expect(f.sales).toBeCloseTo(80 * 17.5 * 7 * 0.85);
    expect(f.costPerHour).toBeCloseTo(16);
  });

  it("caps hours at the labor share of forecast sales", () => {
    const c = weekCap(0.4, { sales: 8000, costPerHour: 16 }, 210);
    expect(c).toEqual({ kind: "labor", hours: 200, pct: 0.4, sales: 8000 });
    expect(capLabel(c!)).toBe("200h cap · 40% of $8.0k forecast sales");
  });

  it("falls back to the weekly hours goal without a cap or without history", () => {
    expect(weekCap(null, { sales: 8000, costPerHour: 16 }, 210)).toEqual({ kind: "goal", hours: 210 });
    expect(weekCap(0.4, { sales: null, costPerHour: null }, 210)).toEqual({ kind: "goal", hours: 210 });
    expect(capLabel({ kind: "goal", hours: 210 })).toBe("210h goal");
    expect(weekCap(0.4, { sales: null, costPerHour: null })).toBeNull();
  });
});
