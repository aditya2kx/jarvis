import { describe, expect, it } from "vitest";
import {
  mergeHoursPerPerson,
  personChartSupportsGrain,
  personHoursByGrain,
  type PersonDayHours,
} from "@/lib/labor/hours-per-person";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import type { DateWindow } from "@/lib/filters/range";

const pt = (date: string, employee: string, hours: number): PersonDayHours => ({
  date,
  employee,
  labor_bucket: "parttime",
  hours,
});
const ft = (date: string, employee: string, hours: number): PersonDayHours => ({
  date,
  employee,
  labor_bucket: "fulltime",
  hours,
});

const PERIOD_END = "2026-09-30";

// Clocked through 09-21, schedule from 09-22 (the handoff) through the ADP
// horizon on 10-04 — the page fetches past Period end for the dated charts.
const actual: PersonDayHours[] = [
  pt("2026-09-02", "Garcia, Jacob", 40),
  pt("2026-09-15", "Garcia, Jacob", 45.4),
  ft("2026-09-10", "Krause, Lindsay", 111.6),
];
const scheduled: PersonDayHours[] = [
  pt("2026-09-24", "Garcia, Jacob", 6.5),
  ft("2026-09-23", "Krause, Lindsay", 50.2),
  ft("2026-10-02", "Krause, Lindsay", 8),
  pt("2026-09-25", "Browning, Skyler", 7),
];

describe("mergeHoursPerPerson", () => {
  it("stacks clocked and scheduled hours per person, sorted by combined", () => {
    const { rows } = mergeHoursPerPerson(actual, scheduled, null, PERIOD_END);
    expect(rows.map((r) => [r.employee, r.combined])).toEqual([
      ["Krause, Lindsay", 161.8],
      ["Garcia, Jacob", 91.9],
      ["Browning, Skyler", 7],
    ]);
    const krause = rows[0];
    expect(krause.fulltime).toBe(111.6);
    expect(krause.fulltime_sched).toBe(50.2);
    expect(krause.parttime).toBeNull();
    expect(krause.parttime_sched).toBeNull();
  });

  it("drops scheduled days after Period end (no date axis to show them on)", () => {
    const krause = mergeHoursPerPerson(actual, scheduled, null, PERIOD_END).rows[0];
    // The 8h on 10-02 is outside the Period.
    expect(krause.fulltime_sched).toBe(50.2);
    const withOct = mergeHoursPerPerson(actual, scheduled, null, "2026-10-04").rows[0];
    expect(withOct.fulltime_sched).toBe(58.2);
  });

  it("uses the month chart's series labels and palette, in legend order", () => {
    const { series } = mergeHoursPerPerson(actual, scheduled, null, PERIOD_END);
    expect(series).toEqual([
      { key: "parttime", label: "Part-time", color: LABOR_CHART_COLORS.parttimeActual },
      { key: "fulltime", label: "Full-time", color: LABOR_CHART_COLORS.fulltimeActual },
      {
        key: "parttime_sched",
        label: "Part-time (scheduled)",
        color: LABOR_CHART_COLORS.parttimeScheduled,
      },
      {
        key: "fulltime_sched",
        label: "Full-time (scheduled)",
        color: LABOR_CHART_COLORS.fulltimeScheduled,
      },
    ]);
  });

  it("shows actual, scheduled and combined in the tooltip when both exist", () => {
    const garcia = mergeHoursPerPerson(actual, scheduled, null, PERIOD_END).rows[1];
    expect(garcia.tooltipEntries).toEqual([
      { label: "Actual", value: "85.4", color: LABOR_CHART_COLORS.parttimeActual },
      { label: "Scheduled", value: "6.5", color: LABOR_CHART_COLORS.parttimeScheduled },
      { label: "Total (combined)", value: "91.9" },
    ]);
  });

  it("clocked-only Period: no scheduled series and no combined line", () => {
    const { rows, series } = mergeHoursPerPerson(actual, [], null, PERIOD_END);
    expect(series.map((s) => s.key)).toEqual(["parttime", "fulltime"]);
    expect(rows.every((r) => r.parttime_sched == null && r.fulltime_sched == null)).toBe(true);
    expect(rows[0].tooltipEntries.map((e) => e.label)).toEqual(["Actual"]);
  });

  it("schedule-only Period (all future): bars are schedule only", () => {
    const { rows, series } = mergeHoursPerPerson([], scheduled, null, PERIOD_END);
    expect(series.map((s) => s.key)).toEqual(["parttime_sched", "fulltime_sched"]);
    expect(rows[0].tooltipEntries.map((e) => e.label)).toEqual(["Scheduled"]);
  });

  it("Part-time filter hides full-time people entirely", () => {
    const { rows, series } = mergeHoursPerPerson(actual, scheduled, ["Part-time"], PERIOD_END);
    expect(rows.map((r) => r.employee)).toEqual(["Garcia, Jacob", "Browning, Skyler"]);
    expect(series.map((s) => s.key)).toEqual(["parttime", "parttime_sched"]);
  });

  it("no labor type selected: nothing to draw", () => {
    expect(mergeHoursPerPerson(actual, scheduled, [], PERIOD_END)).toEqual({
      rows: [],
      series: [],
    });
  });

  it("ignores zero / non-numeric hours", () => {
    const { rows } = mergeHoursPerPerson(
      [pt("2026-09-01", "Padron, Lisette", 31.7), pt("2026-09-01", "Ghost", 0)],
      [pt("2026-09-25", "Padron, Lisette", Number.NaN)],
      null,
      PERIOD_END,
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].parttime_sched).toBeNull();
  });

  it("rounds .x5 totals half-up regardless of how the days were summed", () => {
    // Real Huynh days (minutes / 60) summing to exactly 62.15; float addition
    // lands at 62.1499…, which plain toFixed(1) shows as 62.1.
    const days = [5.75, 6.116666666666667, 6.85, 6.333333333333333, 5.966666666666667,
      4.416666666666667, 7.166666666666667, 6.75, 6.5, 6.3];
    const { rows } = mergeHoursPerPerson(
      days.map((h, i) => pt(`2026-09-${String(i + 1).padStart(2, "0")}`, "H", h)),
      [pt("2026-09-25", "H", 34.6)],
      null,
      PERIOD_END,
    );
    expect(rows[0].parttime).toBe(62.2);
    expect(rows[0].combined).toBe(96.8);
  });

  it("breaks ties on combined hours by name", () => {
    const { rows } = mergeHoursPerPerson(
      [pt("2026-09-01", "B", 5), pt("2026-09-01", "A", 5)],
      [],
      null,
      PERIOD_END,
    );
    expect(rows.map((r) => r.employee)).toEqual(["A", "B"]);
  });
});

const sept: DateWindow = { start: "2026-09-01", end: "2026-09-30", label: "t", preset: "custom" };

describe("personHoursByGrain", () => {
  it("buckets one person's clocked + scheduled hours by week on the chart spine", () => {
    const rows = personHoursByGrain({
      actual,
      scheduled,
      employee: "Garcia, Jacob",
      grain: "week",
      stat: "total",
      win: sept,
    });
    // Weeks starting Monday: Aug 31 (partial), Sep 7, 14, 21, 28.
    expect(rows.map((r) => r.bucket_iso)).toEqual([
      "2026-08-31",
      "2026-09-07",
      "2026-09-14",
      "2026-09-21",
      "2026-09-28",
    ]);
    expect(rows.map((r) => [r.parttime_hours, r.parttime_scheduled_hours])).toEqual([
      [40, null],
      [null, null],
      [45.4, null],
      [null, 6.5],
      [null, null],
    ]);
    // Labor % is store-level; never attributed to one person.
    expect(rows.every((r) => r.labor_pct == null && r.net_sales == null)).toBe(true);
  });

  it("only counts the selected person, and only inside the spine window", () => {
    const rows = personHoursByGrain({
      actual,
      scheduled,
      employee: "Krause, Lindsay",
      grain: "all",
      stat: "total",
      win: sept,
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].fulltime_hours).toBe(111.6);
    // 10-02 is past the spine end, so only the 09-23 schedule counts.
    expect(rows[0].fulltime_scheduled_hours).toBe(50.2);
  });

  it("follows the chart spine past Period end when the page extends it", () => {
    const rows = personHoursByGrain({
      actual,
      scheduled,
      employee: "Krause, Lindsay",
      grain: "month",
      stat: "total",
      win: { ...sept, end: "2026-10-04" },
    });
    expect(rows.map((r) => [r.bucket_iso, r.fulltime_scheduled_hours])).toEqual([
      ["2026-09-01", 50.2],
      ["2026-10-01", 8],
    ]);
  });

  it("Stat Average at Weekday divides by how many of that weekday the Period has", () => {
    // Sep 2026 has five Wednesdays (2, 9, 16, 23, 30).
    const rows = personHoursByGrain({
      actual: [pt("2026-09-02", "A", 5), pt("2026-09-09", "A", 5)],
      scheduled: [pt("2026-09-23", "A", 5)],
      employee: "A",
      grain: "weekday",
      stat: "avg",
      win: sept,
    });
    expect(rows).toHaveLength(7);
    const wed = rows[2];
    expect(wed.parttime_hours).toBe(2);
    expect(wed.parttime_scheduled_hours).toBe(1);
  });

  it("is not offered at Hour of day", () => {
    expect(personChartSupportsGrain("hour")).toBe(false);
    expect(personChartSupportsGrain("week")).toBe(true);
    expect(personChartSupportsGrain("all")).toBe(true);
  });
});
