import { describe, expect, it } from "vitest";
import {
  actualPunchWindow,
  clockedHoursTargetDate,
  extendEndForScheduleHorizon,
  laborChartWindow,
  periodIncludesToday,
  scheduledShiftWindow,
} from "@/lib/labor/actual-schedule-windows";
import type { DateWindow } from "@/lib/filters/range";

const win = (start: string, end: string): DateWindow => ({
  start,
  end,
  label: "t",
  preset: "custom",
});

describe("actual / schedule windows", () => {
  const today = "2026-08-01";

  it("periodIncludesToday", () => {
    expect(periodIncludesToday(win("2026-07-01", "2026-07-31"), today)).toBe(false);
    expect(periodIncludesToday(win("2026-07-20", "2026-08-05"), today)).toBe(true);
    expect(periodIncludesToday(win("2026-08-01", "2026-08-07"), today)).toBe(true);
  });

  it("actualPunchWindow ends yesterday when Period reaches today+", () => {
    expect(actualPunchWindow(win("2026-07-20", "2026-08-05"), today)).toEqual({
      ...win("2026-07-20", "2026-07-31"),
      preset: "custom",
    });
    expect(actualPunchWindow(win("2026-07-01", "2026-07-15"), today)).toEqual(
      win("2026-07-01", "2026-07-15"),
    );
    expect(actualPunchWindow(win("2026-08-01", "2026-08-07"), today)).toBeNull();
  });

  it("clockedHoursTargetDate uses past chip / closed period end else yesterday", () => {
    expect(
      clockedHoursTargetDate({ todayIso: today, coverageDay: "2026-07-30" }),
    ).toBe("2026-07-30");
    expect(
      clockedHoursTargetDate({ todayIso: today, coverageDay: "2026-08-01" }),
    ).toBe("2026-07-31");
    expect(
      clockedHoursTargetDate({ todayIso: today, periodEnd: "2026-07-26" }),
    ).toBe("2026-07-26");
    expect(
      clockedHoursTargetDate({ todayIso: today, periodEnd: "2026-08-09" }),
    ).toBe("2026-07-31");
  });

  it("scheduledShiftWindow starts today when Period reaches today+", () => {
    expect(scheduledShiftWindow(win("2026-07-20", "2026-08-05"), today)).toEqual({
      ...win("2026-08-01", "2026-08-05"),
      preset: "custom",
    });
    expect(scheduledShiftWindow(win("2026-07-01", "2026-07-15"), today)).toBeNull();
  });

  it("future-only Period keeps schedule window without horizon (scope B / #243)", () => {
    // includesToday=false; scheduleHorizonEnd=null — still non-null for coverage.
    const future = scheduledShiftWindow(
      win("2026-08-12", "2026-08-18"),
      today,
      null,
    );
    expect(future).toEqual({
      ...win("2026-08-12", "2026-08-18"),
      preset: "custom",
    });
    expect(periodIncludesToday(win("2026-08-12", "2026-08-18"), today)).toBe(false);
  });

  it("extends schedule + chart spine to ADP horizon when Period includes today", () => {
    expect(extendEndForScheduleHorizon("2026-08-01", "2026-08-16")).toBe("2026-08-16");
    expect(extendEndForScheduleHorizon("2026-08-20", "2026-08-16")).toBe("2026-08-20");
    expect(
      scheduledShiftWindow(win("2026-07-05", "2026-08-01"), today, "2026-08-16"),
    ).toEqual({
      ...win("2026-08-01", "2026-08-16"),
      preset: "custom",
    });
    expect(
      laborChartWindow(win("2026-07-05", "2026-08-01"), today, "2026-08-16").end,
    ).toBe("2026-08-16");
    expect(
      laborChartWindow(win("2026-07-01", "2026-07-15"), today, "2026-08-16").end,
    ).toBe("2026-07-15");
  });
});
