import { describe, expect, it } from "vitest";
import { resolveStoreHours, squareWeekHours } from "@/lib/labor/open-hours";
import {
  DEFAULT_STAFFING,
  parseScheduleRules,
  staffedWindow,
  uncoveredWindows,
  type OpenHours,
  type StoreHours,
} from "@/lib/labor/schedule-inputs";

const DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
const period = (day: string, start: string, end: string) => ({
  day_of_week: day,
  start_local_time: start,
  end_local_time: end,
});
const nineToSix: OpenHours[] = Array(7).fill({ openMin: 540, closeMin: 1080 });
const eightToSix: OpenHours[] = Array(7).fill({ openMin: 480, closeMin: 1080 });
const hours = (days: OpenHours[]): StoreHours => ({ days, source: "square", asOf: "2026-11-23T15:00:00Z", changed: null });

describe("squareWeekHours", () => {
  it("reads Houston's 9–6 every day", () => {
    const loc = { business_hours: { periods: DAYS.map((d) => period(d, "09:00:00", "18:00:00")) } };
    expect(squareWeekHours(loc)).toEqual(nineToSix);
  });
  it("closes days Square lists no hours for, spans split hours, reads a midnight close as 24:00", () => {
    const loc = {
      business_hours: {
        periods: [
          period("SAT", "12:00:00", "20:00:00"),
          period("SAT", "07:00:00", "11:00:00"),
          period("FRI", "10:00", "00:00"),
        ],
      },
    };
    const w = squareWeekHours(loc);
    expect(w[5]).toEqual({ openMin: 420, closeMin: 1200 });
    expect(w[4]).toEqual({ openMin: 600, closeMin: 1440 });
    expect(w[0]).toBeNull();
    expect(squareWeekHours({})).toEqual(Array(7).fill(null));
  });
});

describe("staffedWindow", () => {
  it("is Square hours with the time before open and after close", () => {
    expect(staffedWindow(DEFAULT_STAFFING, hours(nineToSix), 0)).toEqual([510, 1110]);
    expect(staffedWindow({ ...DEFAULT_STAFFING, prepMin: 60, closeoutMin: 0 }, hours(nineToSix), 3)).toEqual([480, 1080]);
  });
  it("is null on a closed day and the fixed window without Square hours", () => {
    const days = [...nineToSix];
    days[6] = null;
    expect(staffedWindow(DEFAULT_STAFFING, hours(days), 6)).toBeNull();
    expect(staffedWindow(DEFAULT_STAFFING, null, 6)).toEqual([DEFAULT_STAFFING.openMin, DEFAULT_STAFFING.closeMin]);
  });
  it("keeps Austin's staffed time: Square 7 AM–8 PM ± 30 min is the old fixed 6:30 AM–8:30 PM", () => {
    const austin = hours(Array(7).fill({ openMin: 420, closeMin: 1200 }));
    expect(staffedWindow(DEFAULT_STAFFING, austin, 2)).toEqual([DEFAULT_STAFFING.openMin, DEFAULT_STAFFING.closeMin]);
  });
});

describe("uncoveredWindows with store hours", () => {
  it("uses each weekday's window and leaves closed days without a gap", () => {
    const days = [...nineToSix];
    days[6] = null;
    const [only] = uncoveredWindows([], DEFAULT_STAFFING, hours(days));
    expect(only!.groups).toEqual([
      { days: [0, 1, 2, 3, 4, 5], windows: [[510, 1110]] },
      { days: [6], windows: [] },
    ]);
  });
});

describe("parse of the before-open / after-close minutes", () => {
  it("defaults older versions to 30 / 30 and keeps saved values", () => {
    const old = parseScheduleRules({ dayRules: [], staffRules: [], staffing: { ...DEFAULT_STAFFING, prepMin: undefined, closeoutMin: undefined } });
    expect([old.staffing.prepMin, old.staffing.closeoutMin]).toEqual([30, 30]);
    const saved = parseScheduleRules({ dayRules: [], staffRules: [], staffing: { ...DEFAULT_STAFFING, prepMin: 45, closeoutMin: 15 } });
    expect([saved.staffing.prepMin, saved.staffing.closeoutMin]).toEqual([45, 15]);
  });
  it("rejects values off the 15-minute grid or over 3 hours", () => {
    expect(() => parseScheduleRules({ dayRules: [], staffRules: [], staffing: { ...DEFAULT_STAFFING, prepMin: 20 } })).toThrow();
    expect(() => parseScheduleRules({ dayRules: [], staffRules: [], staffing: { ...DEFAULT_STAFFING, closeoutMin: 195 } })).toThrow();
  });
});

describe("resolveStoreHours", () => {
  const today = "2026-11-25";
  it("prefers the live read and falls back to the newest nightly copy", () => {
    const copies = [{ asOf: "2026-11-24", days: nineToSix }];
    expect(resolveStoreHours({ days: nineToSix, readAt: "2026-11-25T15:00:00Z" }, copies, today)).toMatchObject({
      source: "square",
      changed: null,
    });
    expect(resolveStoreHours(null, copies, today)).toMatchObject({ source: "snapshot", asOf: "2026-11-24", days: nineToSix });
    expect(resolveStoreHours(null, [], today)).toBeNull();
  });
  it("calls out a live read that differs from last night", () => {
    const r = resolveStoreHours({ days: eightToSix, readAt: "2026-11-25T15:00:00Z" }, [{ asOf: "2026-11-24", days: nineToSix }], today);
    expect(r!.changed).toEqual({ on: "2026-11-25", before: nineToSix });
  });
  it("finds the latest change in the copies and drops it after 14 days", () => {
    const copies = [
      { asOf: "2026-11-24", days: eightToSix },
      { asOf: "2026-11-23", days: eightToSix },
      { asOf: "2026-11-22", days: nineToSix },
    ];
    expect(resolveStoreHours(null, copies, today)!.changed).toEqual({ on: "2026-11-23", before: nineToSix });
    expect(resolveStoreHours(null, copies, "2026-12-15")!.changed).toBeNull();
  });
});
