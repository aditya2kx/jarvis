import { describe, expect, it } from "vitest";
import {
  applyDayRules,
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
  scope: "weekdays",
  exceptDelivery: true,
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
    const delivery: DayRule = { ...weekdayOpen, id: "b", scope: "delivery", people: 3 };
    const fri: DayRule = { ...weekdayOpen, id: "c", scope: "4", exceptDelivery: false, fromMin: 450, people: 4 };
    expect(applyDayRules("2026-10-02", mins, need, [delivery, fri], deliveries)).toEqual([3, 3, 4, 4, 2]);
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
