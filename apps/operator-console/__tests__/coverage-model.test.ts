import { describe, expect, it } from "vitest";
import {
  axisBounds,
  buildPersonDaysForDate,
  coverageNarrative,
  coverageStripDates,
  defaultCoverageDay,
  filterScheduledForCoverage,
  occupancySeries,
  parseHhMm,
  peopleActiveAt,
  resolveCoverageDay,
} from "@/lib/labor/coverage-model";

describe("parseHhMm", () => {
  it("parses 24h punch times", () => {
    expect(parseHhMm("08:00")).toBe(8 * 60);
    expect(parseHhMm("13:30")).toBe(13 * 60 + 30);
    expect(parseHhMm("bad")).toBeNull();
  });
});

describe("coverageStripDates", () => {
  it("returns every calendar day in the Period (scrollable in UI)", () => {
    const strip = coverageStripDates(
      { start: "2026-08-01", end: "2026-08-07" },
    );
    expect(strip).toEqual([
      "2026-08-01",
      "2026-08-02",
      "2026-08-03",
      "2026-08-04",
      "2026-08-05",
      "2026-08-06",
      "2026-08-07",
    ]);
  });

  it("includes schedule-horizon days when the page passes an extended window", () => {
    // laborChartWindow already extended Period end → Aug 16
    const strip = coverageStripDates({
      start: "2026-07-05",
      end: "2026-08-16",
    });
    expect(strip[0]).toBe("2026-07-05");
    expect(strip[strip.length - 1]).toBe("2026-08-16");
    expect(strip.length).toBe(43);
  });

  it("past-only Period stays within Period (no horizon)", () => {
    const strip = coverageStripDates({
      start: "2026-07-01",
      end: "2026-07-20",
    });
    expect(strip[0]).toBe("2026-07-01");
    expect(strip[strip.length - 1]).toBe("2026-07-20");
    expect(strip).toHaveLength(20);
  });
});

describe("defaultCoverageDay", () => {
  it("prefers today when in the strip, else last chip", () => {
    expect(
      defaultCoverageDay(["2026-07-30", "2026-07-31", "2026-08-01"], "2026-08-01"),
    ).toBe("2026-08-01");
    expect(defaultCoverageDay(["2026-08-01", "2026-08-02"], "2026-08-01")).toBe(
      "2026-08-01",
    );
    expect(defaultCoverageDay(["2026-07-20", "2026-07-21"], "2026-08-01")).toBe(
      "2026-07-21",
    );
  });

  it("honors a valid ?day= request", () => {
    expect(
      resolveCoverageDay("2026-07-30", ["2026-07-30", "2026-07-31"], "2026-08-01"),
    ).toBe("2026-07-30");
  });
});

describe("buildPersonDaysForDate + occupancy", () => {
  it("shows 1 until late morning then 2 (actuals)", () => {
    const people = buildPersonDaysForDate(
      "2026-07-31",
      [
        {
          date: "2026-07-31",
          employee: "Brooke",
          labor_bucket: "parttime",
          in_time: "09:00",
          out_time: "17:00",
          total_hours: 8,
        },
        {
          date: "2026-07-31",
          employee: "Luis",
          labor_bucket: "parttime",
          in_time: "11:00",
          out_time: "19:00",
          total_hours: 8,
        },
      ],
      [],
      null,
    );
    expect(people).toHaveLength(2);
    const { startMin, endMin } = axisBounds(people);
    const series = occupancySeries(people, startMin, endMin, 60);
    const at9 = series.find((p) => p.min === 9 * 60);
    const at11 = series.find((p) => p.min === 11 * 60);
    expect(at9?.actual).toBe(1);
    expect(at11?.actual).toBe(2);
    const narrative = coverageNarrative(
      occupancySeries(people, startMin, endMin, 15),
    );
    expect(narrative).toMatch(/1 until/i);
    expect(narrative).toMatch(/then 2/i);
  });

  it("parses scheduled ranges into swimlanes", () => {
    const people = buildPersonDaysForDate(
      "2026-08-01",
      [],
      [
        {
          date: "2026-08-01",
          employee: "Brooke",
          labor_bucket: "parttime",
          scheduled_hours: 7,
          shift_ranges_json: '["1:30 PM - 8:30 PM"]',
        },
      ],
      null,
    );
    expect(people[0]!.segments[0]).toMatchObject({
      kind: "scheduled",
      startMin: 13 * 60 + 30,
      endMin: 20 * 60 + 30,
      hours: 7,
    });
  });

  it("respects labor type filter", () => {
    const people = buildPersonDaysForDate(
      "2026-07-31",
      [
        {
          date: "2026-07-31",
          employee: "Brooke",
          labor_bucket: "parttime",
          in_time: "09:00",
          out_time: "17:00",
          total_hours: 8,
        },
        {
          date: "2026-07-31",
          employee: "Manager",
          labor_bucket: "fulltime",
          in_time: "08:00",
          out_time: "16:00",
          total_hours: 8,
        },
      ],
      [],
      ["Part-time"],
    );
    expect(people.map((p) => p.employee)).toEqual(["Brooke"]);
  });
});

describe("peopleActiveAt", () => {
  it("lists who covers a minute with start–end", () => {
    const people = buildPersonDaysForDate(
      "2026-07-31",
      [
        {
          date: "2026-07-31",
          employee: "Dolce",
          labor_bucket: "parttime",
          in_time: "06:30",
          out_time: "13:00",
          total_hours: 6.5,
        },
        {
          date: "2026-07-31",
          employee: "Ximena",
          labor_bucket: "parttime",
          in_time: "07:30",
          out_time: "14:30",
          total_hours: 7,
        },
      ],
      [],
      null,
    );
    const at730 = peopleActiveAt(people, 7 * 60 + 30);
    expect(at730.map((p) => p.employee)).toEqual(["Dolce", "Ximena"]);
    expect(at730[0]).toMatchObject({
      startMin: 6 * 60 + 30,
      endMin: 13 * 60,
    });
    expect(peopleActiveAt(people, 6 * 60).map((p) => p.employee)).toEqual([]);
  });
});

describe("filterScheduledForCoverage", () => {
  const sched = (date: string) => ({
    date,
    employee: "A",
    labor_bucket: "parttime",
    scheduled_hours: 8,
    shift_ranges_json: '[{"startMin":540,"endMin":1020,"hours":8}]',
  });
  const punch = (date: string) => ({
    date,
    employee: "A",
    labor_bucket: "parttime",
    in_time: "09:00",
    out_time: "17:00",
    total_hours: 8,
  });

  it("keeps past schedule only when that day has no punches", () => {
    const out = filterScheduledForCoverage(
      [punch("2026-08-23")],
      [sched("2026-08-23"), sched("2026-08-24"), sched("2026-08-25")],
      "2026-08-25",
    );
    expect(out.map((s) => s.date)).toEqual(["2026-08-24", "2026-08-25"]);
  });
});
