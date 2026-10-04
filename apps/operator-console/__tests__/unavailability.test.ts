import { describe, expect, it } from "vitest";
import {
  blocksOn,
  collapseWeekly,
  coversDate,
  freeSegment,
  overlapMinutes,
  scheduleConflicts,
  type UnavailabilityInput,
} from "@/lib/labor/unavailability";

const weeklySat: UnavailabilityInput = {
  employee: "A", status: "pending", first_date: "2026-10-03", from_time: "06:00", to_time: "10:00",
  all_day: false, repeat_weekday: 5, repeat_until: "2026-11-01",
};
const oneOff: UnavailabilityInput = {
  employee: "B", status: "approved", first_date: "2026-10-04", from_time: null, to_time: null,
  all_day: true, repeat_weekday: null, repeat_until: null,
};

describe("coversDate", () => {
  it("expands weekly repeats through repeat_until only", () => {
    expect(coversDate(weeklySat, "2026-10-03")).toBe(true);
    expect(coversDate(weeklySat, "2026-10-10")).toBe(true);
    expect(coversDate(weeklySat, "2026-10-11")).toBe(false);
    expect(coversDate(weeklySat, "2026-11-07")).toBe(false);
    expect(coversDate(weeklySat, "2026-09-26")).toBe(false);
  });
  it("one-off rows cover their date only", () => {
    expect(coversDate(oneOff, "2026-10-04")).toBe(true);
    expect(coversDate(oneOff, "2026-10-11")).toBe(false);
  });
});

describe("blocksOn", () => {
  it("maps windows and all-day blocks per employee", () => {
    expect(blocksOn([weeklySat, oneOff], "2026-10-10").get("A")).toEqual([
      { fromMin: 360, toMin: 600, status: "pending" },
    ]);
    expect(blocksOn([weeklySat, oneOff], "2026-10-04").get("B")).toEqual([
      { fromMin: 0, toMin: 1440, status: "approved" },
    ]);
  });
});

describe("freeSegment / overlapMinutes", () => {
  const b = [{ fromMin: 600, toMin: 720, status: "pending" as const }];
  it("keeps the longest unblocked part", () => {
    expect(freeSegment(390, 840, b)).toEqual([390, 600]);
    expect(freeSegment(700, 1200, b)).toEqual([720, 1200]);
    expect(freeSegment(600, 720, b)).toEqual([600, 600]);
  });
  it("measures overlap", () => {
    expect(overlapMinutes(540, 660, b)).toBe(60);
    expect(overlapMinutes(800, 900, b)).toBe(0);
  });
});

describe("scheduleConflicts", () => {
  const shifts = [
    { date: "2026-10-10", employee: "A", shift_ranges_json: JSON.stringify(["8:00 AM - 2:00 PM"]) },
    { date: "2026-10-10", employee: "B", shift_ranges_json: JSON.stringify(["8:00 AM - 2:00 PM"]) },
    { date: "2026-10-17", employee: "A", shift_ranges_json: JSON.stringify(["1:30 PM - 8:30 PM"]) },
  ];
  it("flags only shifts that overlap the person's own block", () => {
    const c = scheduleConflicts([weeklySat, oneOff], shifts);
    expect(c).toHaveLength(1);
    expect(c[0]).toMatchObject({ date: "2026-10-10", employee: "A", shift: { startMin: 480, endMin: 840 } });
  });
});

describe("collapseWeekly", () => {
  const block = (first_date: string, from_time = "06:00"): UnavailabilityInput => ({
    employee: "A", status: "approved", first_date, from_time, to_time: "10:00",
    all_day: false, repeat_weekday: null, repeat_until: null,
  });
  it("folds weekly approved blocks into one repeating row", () => {
    const out = collapseWeekly([block("2026-10-03"), block("2026-10-17"), block("2026-10-10")]);
    expect(out).toEqual([{ ...block("2026-10-03"), repeat_weekday: 5, repeat_until: "2026-10-17" }]);
  });
  it("keeps gaps, other windows and pending rows separate", () => {
    const out = collapseWeekly([block("2026-10-03"), block("2026-10-17"), block("2026-10-10", "07:00"), weeklySat]);
    expect(out).toHaveLength(4);
  });
});
