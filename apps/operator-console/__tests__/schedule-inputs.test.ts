import { describe, expect, it } from "vitest";
import {
  applyDayRules,
  daysLabel,
  DEFAULT_RULES,
  DEFAULT_STAFFING,
  parseScheduleRules,
  minToTime,
  staffLimits,
  timeToMin,
  type DayRule,
} from "@/lib/labor/schedule-inputs";
import { payPeriodStartFor } from "@/lib/payroll/openPeriod";

const mins = [390, 420, 450, 480, 510];
const need = [1, 1, 2, 2, 2];
const deliveries = new Set(["2026-10-02"]);
const weekdayOpen: DayRule = {
  id: "a",
  days: [0, 1, 2, 3, 4],
  delivery: "skip",
  fromMin: 390,
  toMin: 510,
  people: 1,
};

describe("applyDayRules", () => {
  it("replaces need on weekdays but skips delivery days and weekends", () => {
    expect(applyDayRules("2026-10-01", mins, need, [weekdayOpen], deliveries)).toEqual([1, 1, 1, 1, 2]);
    expect(applyDayRules("2026-10-02", mins, need, [weekdayOpen], deliveries)).toEqual(need);
    expect(applyDayRules("2026-10-03", mins, need, [weekdayOpen], deliveries)).toEqual(need);
  });

  it("targets delivery days and lets later rules win", () => {
    const delivery: DayRule = { ...weekdayOpen, id: "b", days: [0, 1, 2, 3, 4, 5, 6], delivery: "only", people: 3 };
    const fri: DayRule = { ...weekdayOpen, id: "c", days: [4], delivery: "any", fromMin: 450, people: 4 };
    expect(applyDayRules("2026-10-02", mins, need, [delivery, fri], deliveries)).toEqual([3, 3, 4, 4, 2]);
  });

  it("covers any set of days with one rule", () => {
    const tueThuSat: DayRule = { ...weekdayOpen, days: [1, 3, 5], delivery: "any", people: 5 };
    expect(applyDayRules("2026-09-29", mins, need, [tueThuSat], deliveries)).toEqual([5, 5, 5, 5, 2]);
    expect(applyDayRules("2026-09-30", mins, need, [tueThuSat], deliveries)).toEqual(need);
    expect(applyDayRules("2026-10-03", mins, need, [tueThuSat], deliveries)).toEqual([5, 5, 5, 5, 2]);
  });
});

describe("daysLabel", () => {
  it("names common sets and lists the rest", () => {
    expect(daysLabel([0, 1, 2, 3, 4, 5, 6])).toBe("Every day");
    expect(daysLabel([4, 0, 1, 2, 3])).toBe("Weekdays");
    expect(daysLabel([5, 6])).toBe("Weekends");
    expect(daysLabel([3, 1])).toBe("Tue, Thu");
  });
});

describe("parseScheduleRules day rules", () => {
  const base = { fromMin: 1170, toMin: 1230, people: 2 };
  const parse = (r: object) =>
    parseScheduleRules({ dayRules: [{ id: "x", ...base, ...r }], staffRules: [] }).dayRules[0];

  it("loads versions saved with a single scope", () => {
    expect(parse({ scope: "all", exceptDelivery: false })).toMatchObject({ days: [0, 1, 2, 3, 4, 5, 6], delivery: "any" });
    expect(parse({ scope: "weekdays", exceptDelivery: true })).toMatchObject({ days: [0, 1, 2, 3, 4], delivery: "skip" });
    expect(parse({ scope: "delivery", exceptDelivery: false })).toMatchObject({ delivery: "only" });
    expect(parse({ scope: "0", exceptDelivery: false })).toMatchObject({ days: [0] });
  });

  it("sorts days and rejects an empty or invalid set", () => {
    expect(parse({ days: [5, 1], delivery: "any" })).toMatchObject({ days: [1, 5] });
    expect(() => parse({ days: [], delivery: "any" })).toThrow(/at least one day/);
    expect(() => parse({ days: [7], delivery: "any" })).toThrow(/at least one day/);
    expect(() => parse({ days: [1], delivery: "sometimes" })).toThrow(/at least one day/);
  });
});

describe("staffLimits", () => {
  it("merges rule kinds per person", () => {
    const m = staffLimits([
      { id: "1", employee: "D", kind: "target_week_hours", value: 40 },
      { id: "2", employee: "J", kind: "max_shifts_per_period", value: 1 },
      { id: "3", employee: "", kind: "target_week_hours", value: 25 },
    ]);
    expect(m.get("D")).toEqual({ targetWeekHours: 40 });
    expect(m.get("J")).toEqual({ maxShiftsPerPeriod: 1 });
    expect(m.size).toBe(2);
  });
});

describe("payPeriodStartFor", () => {
  it("maps dates onto the biweekly Palmetto calendar", () => {
    expect(payPeriodStartFor("2026-09-21")).toBe("2026-09-21");
    expect(payPeriodStartFor("2026-10-04")).toBe("2026-09-21");
    expect(payPeriodStartFor("2026-10-05")).toBe("2026-10-05");
  });
});

describe("time helpers", () => {
  it("round-trips HH:MM", () => {
    expect(timeToMin("06:30")).toBe(390);
    expect(minToTime(390)).toBe("06:30");
    expect(timeToMin("")).toBeNull();
  });
});

describe("parseScheduleRules", () => {
  it("round-trips the defaults from JSON", () => {
    expect(parseScheduleRules(JSON.stringify(DEFAULT_RULES))).toEqual(DEFAULT_RULES);
  });

  it("rejects a window that ends before it starts", () => {
    const bad = { dayRules: [{ ...DEFAULT_RULES.dayRules[0], fromMin: 600, toMin: 500 }], staffRules: [] };
    expect(() => parseScheduleRules(bad)).toThrow(/Day rule 1/);
  });

  it("rejects a staff rule without an employee", () => {
    const bad = { dayRules: [], staffRules: [{ id: "x", employee: "", kind: "target_week_hours", value: 40 }] };
    expect(() => parseScheduleRules(bad)).toThrow(/Staff rule 1/);
  });

  it("rejects a missing list", () => {
    expect(() => parseScheduleRules({ dayRules: [] })).toThrow();
  });

  it("loads versions saved before staffing basics with the defaults", () => {
    expect(parseScheduleRules({ dayRules: [], staffRules: [] }).staffing).toEqual(DEFAULT_STAFFING);
  });

  it("validates staffing basics", () => {
    const withStaffing = (patch: object) => ({
      dayRules: [],
      staffRules: [],
      staffing: { ...DEFAULT_STAFFING, ...patch },
    });
    expect(parseScheduleRules(withStaffing({ ordersPerPerson: 4.5 })).staffing.ordersPerPerson).toBe(4.5);
    expect(() => parseScheduleRules(withStaffing({ ordersPerPerson: 0 }))).toThrow(/Orders per person/);
    expect(() => parseScheduleRules(withStaffing({ minPeople: 1.5 }))).toThrow(/Minimum people/);
    expect(() => parseScheduleRules(withStaffing({ closeMin: 300 }))).toThrow(/Staffed hours/);
    expect(() => parseScheduleRules(withStaffing({ minShiftMin: 30 }))).toThrow(/Shortest shift/);
  });
});

describe("last working day staff rule", () => {
  it("parses a dated rule, rejects a missing date, and surfaces lastDay in limits", () => {
    const rule = { id: "l", employee: "Krause, Lindsay", kind: "last_day", value: 0, date: "2026-09-30" };
    const parsed = parseScheduleRules({ dayRules: [], staffRules: [rule] });
    expect(parsed.staffRules[0]).toMatchObject({ kind: "last_day", date: "2026-09-30" });
    expect(staffLimits(parsed.staffRules).get("Krause, Lindsay")).toEqual({ lastDay: "2026-09-30" });
    expect(() => parseScheduleRules({ dayRules: [], staffRules: [{ ...rule, date: "" }] })).toThrow(/last day needs a date/);
  });
});
