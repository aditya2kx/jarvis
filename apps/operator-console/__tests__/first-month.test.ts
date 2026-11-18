import { describe, expect, it } from "vitest";
import type { CoveragePersonDay } from "@/lib/labor/coverage-model";
import { capFirstMonth, firstMonths, FIRST_MONTH_FROM } from "@/lib/labor/first-month";
import { parseScheduleRules, DEFAULT_STAFFING } from "@/lib/labor/schedule-inputs";
import { draftDay, weekCap, type Availability } from "@/lib/labor/shift-draft";

const lane = (employee: string, startMin = 390, endMin = 840): CoveragePersonDay => ({
  employee,
  labor_bucket: "parttime",
  segments: [{ kind: "actual", startMin, endMin, hours: (endMin - startMin) / 60 }],
});
const all = (w: [number, number] | null): ([number, number] | null)[] => Array(7).fill(w);

describe("firstMonths", () => {
  const days = new Map<string, CoveragePersonDay[]>([
    ["2026-09-10", [lane("Old, Timer")]],
    ["2026-10-30", [lane("Late, Oct")]],
    ["2026-11-03", [lane("New, Nov"), lane("Old, Timer")]],
    ["2026-11-25", [lane("Next, Week")]], // scheduled, upcoming
  ]);
  const employees = ["Old, Timer", "Late, Oct", "New, Nov", "Next, Week", "No, Shift"];

  it("counts from the first ADP shift on or after Nov 1, one calendar month long", () => {
    expect(FIRST_MONTH_FROM).toBe("2026-11-01");
    const out = firstMonths({ todayIso: "2026-11-18", employees, days });
    expect([...out.keys()].sort()).toEqual(["New, Nov", "Next, Week", "No, Shift"]);
    expect(out.get("New, Nov")).toEqual({ employee: "New, Nov", firstShift: "2026-11-03", until: "2026-12-03" });
    expect(out.get("Next, Week")!.until).toBe("2026-12-25");
    expect(out.get("No, Shift")).toEqual({ employee: "No, Shift", firstShift: null, until: null });
  });

  it("drops people once their month is over", () => {
    const out = firstMonths({ todayIso: "2026-12-03", employees, days });
    expect(out.has("New, Nov")).toBe(false);
    expect(out.has("Next, Week")).toBe(true);
  });

  it("caps nobody when the shift history did not load", () => {
    expect(firstMonths({ todayIso: "2026-11-18", employees, days: new Map() }).size).toBe(0);
  });
});

describe("capFirstMonth", () => {
  const months = firstMonths({
    todayIso: "2026-11-18",
    employees: ["New, Nov", "Vet, Eran", "Goal, Getter"],
    days: new Map([["2026-11-03", [lane("New, Nov"), lane("Goal, Getter")]], ["2026-10-01", [lane("Vet, Eran")]]]),
  });
  const roster: Availability[] = [
    { employee: "New, Nov", maxWeekHours: 40, windows: all([360, 1260]) },
    { employee: "Vet, Eran", maxWeekHours: 40, windows: all([360, 1260]) },
    { employee: "Goal, Getter", maxWeekHours: 40, targetWeekHours: 30, windows: all([360, 1260]) },
  ];

  it("lowers the weekly cap (and an hour target above it) for weeks starting inside the month", () => {
    const week = capFirstMonth(roster, "2026-11-30", months, 20);
    expect(week.map(weekCap)).toEqual([20, 40, 20]);
    expect(week[0]!.firstMonthUntil).toBe("2026-12-03");
    expect(week[1]).toBe(roster[1]);
  });

  it("leaves weeks after the month, and the rule switched off, alone", () => {
    expect(capFirstMonth(roster, "2026-12-07", months, 20)).toEqual(roster);
    expect(capFirstMonth(roster, "2026-11-23", months, null)).toBe(roster);
  });

  it("stops the draft giving a first-month person a shift past 20 h that week", () => {
    const mins: number[] = [];
    for (let t = 6 * 60; t < 21 * 60; t += 15) mins.push(t);
    const need = mins.map((t) => (t >= 390 && t < 840 ? 1 : 0)); // one 7.5 h opener
    const run = (r: Availability[]) =>
      draftDay({
        iso: "2026-11-24", mins, onFloor: mins.map(() => 0), need,
        roster: r.slice(0, 1), weekHours: new Map([["New, Nov", 15]]), busy: new Set(),
      });
    expect(run(roster)[0]!.employee).toBe("New, Nov");
    const capped = run(capFirstMonth(roster, "2026-11-23", months, 20));
    expect(capped.every((s) => s.employee !== "New, Nov" || s.hours <= 5)).toBe(true);
  });
});

describe("firstMonthWeekHours rule", () => {
  const base = { dayRules: [], staffRules: [] };
  it("defaults to 20 for older versions, keeps off, and validates the range", () => {
    const { firstMonthWeekHours: _drop, ...older } = DEFAULT_STAFFING;
    expect(parseScheduleRules({ ...base, staffing: older }).staffing.firstMonthWeekHours).toBe(20);
    expect(parseScheduleRules({ ...base, staffing: { ...DEFAULT_STAFFING, firstMonthWeekHours: null } }).staffing.firstMonthWeekHours).toBeNull();
    expect(() => parseScheduleRules({ ...base, staffing: { ...DEFAULT_STAFFING, firstMonthWeekHours: 0 } })).toThrow();
  });
});
