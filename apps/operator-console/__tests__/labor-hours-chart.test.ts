import { describe, expect, it } from "vitest";

/** Pure remap used by LaborHoursChart — keep in sync with component. */
function toPctPoints(fraction: number | null): number | null {
  if (fraction == null || Number.isNaN(Number(fraction))) return null;
  return Number((Number(fraction) * 100).toFixed(1));
}

function remapLaborChart(
  rows: {
    date: string;
    total_hours: number | null;
    parttime_hours: number | null;
    fulltime_hours: number | null;
    labor_pct: number | null;
    hourly_pct: number | null;
    fulltime_pct: number | null;
  }[],
  unit: "hours" | "pct_net_sales",
  breakdown: boolean,
) {
  if (unit === "pct_net_sales") {
    return rows.map((r) =>
      breakdown
        ? {
            date: r.date,
            parttime: toPctPoints(r.hourly_pct),
            fulltime: toPctPoints(r.fulltime_pct),
          }
        : { date: r.date, total: toPctPoints(r.labor_pct) },
    );
  }
  return rows.map((r) =>
    breakdown
      ? { date: r.date, parttime: r.parttime_hours, fulltime: r.fulltime_hours }
      : { date: r.date, total: r.total_hours },
  );
}

describe("labor L1 chart remap", () => {
  const rows = [
    {
      date: "Jul 1",
      total_hours: 40,
      parttime_hours: 30,
      fulltime_hours: 10,
      labor_pct: 0.25,
      hourly_pct: 0.18,
      fulltime_pct: 0.07,
    },
  ];

  it("aggregate hours", () => {
    expect(remapLaborChart(rows, "hours", false)).toEqual([{ date: "Jul 1", total: 40 }]);
  });

  it("breakdown hours", () => {
    expect(remapLaborChart(rows, "hours", true)).toEqual([
      { date: "Jul 1", parttime: 30, fulltime: 10 },
    ]);
  });

  it("aggregate % of net sales (labor $ / sales)", () => {
    expect(remapLaborChart(rows, "pct_net_sales", false)).toEqual([
      { date: "Jul 1", total: 25 },
    ]);
  });

  it("breakdown %", () => {
    expect(remapLaborChart(rows, "pct_net_sales", true)).toEqual([
      { date: "Jul 1", parttime: 18, fulltime: 7 },
    ]);
  });
});
