import { describe, expect, it } from "vitest";
import {
  isoWeekdayMon0,
  needSeries,
  shortNarrative,
  shortPersonHours,
  shortWindows,
  type DemandCell,
} from "@/lib/labor/staffing-need";

const pts = (from: number, to: number, counts: number[] = []) => {
  const out = [];
  for (let t = from, i = 0; t < to; t += 15, i++) out.push({ min: t, actual: 0, scheduled: counts[i] ?? 0, open: 0 });
  return out;
};

describe("isoWeekdayMon0", () => {
  it("maps Mon→0 and Sun→6", () => {
    expect(isoWeekdayMon0("2026-09-21")).toBe(0);
    expect(isoWeekdayMon0("2026-09-26")).toBe(5);
    expect(isoWeekdayMon0("2026-09-27")).toBe(6);
  });
});

describe("needSeries", () => {
  const sat: DemandCell[] = [
    { dow: 5, hour: 6, orders: 0 },
    { dow: 5, hour: 13, orders: 23.25 },
    { dow: 0, hour: 13, orders: 40 },
    { dow: 5, hour: 20, orders: 4.5 },
  ];

  it("is zero outside the labor floor and at least the floor inside it", () => {
    const need = needSeries("2026-09-26", pts(6 * 60, 8 * 60), sat, 4);
    // 6:00, 6:15 closed; 6:30+ → the one-person floor
    expect(need).toEqual([0, 0, 1, 1, 1, 1, 1, 1]);
    // closes at 8:30 PM
    expect(needSeries("2026-09-26", pts(20 * 60, 21 * 60), sat, 4)).toEqual([2, 2, 0, 0]);
  });

  it("uses a custom floor", () => {
    const floor = [{ fromMin: 7 * 60, toMin: 8 * 60, min: 2 }];
    expect(needSeries("2026-09-26", pts(6 * 60 + 45, 7 * 60 + 15), sat, 4, floor)).toEqual([0, 2]);
  });

  it("raises need from demand for the same weekday only", () => {
    const need = needSeries("2026-09-26", pts(13 * 60, 13 * 60 + 15), sat, 4);
    expect(need).toEqual([6]); // ceil(23.25 / 4)
  });

  it("falls back to 4 orders/person for a non-positive setting", () => {
    expect(needSeries("2026-09-26", pts(13 * 60, 13 * 60 + 15), sat, 0)).toEqual([6]);
  });
});

describe("shortfall", () => {
  it("merges equal-shortfall steps and totals person-hours", () => {
    const p = pts(16 * 60, 17 * 60, [1, 1, 1, 2]);
    const need = [2, 2, 2, 2];
    const w = shortWindows(p, need);
    expect(w).toEqual([{ fromMin: 960, toMin: 1005, short: 1 }]);
    expect(shortPersonHours(p, need)).toBe(0.75);
    expect(shortNarrative(w)).toBe("Short: 4 PM–4:45 PM (−1)");
  });

  it("prefers clocked headcount over scheduled", () => {
    const p = [{ min: 600, actual: 3, scheduled: 1, open: 0 }];
    expect(shortWindows(p, [2])).toEqual([]);
  });

  it("counts ADP open slots as coverage", () => {
    expect(shortWindows([{ min: 600, actual: 0, scheduled: 1, open: 1 }], [2])).toEqual([]);
  });
});
