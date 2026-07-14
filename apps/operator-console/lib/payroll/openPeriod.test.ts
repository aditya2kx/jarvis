import { describe, expect, it } from "vitest";
import {
  calendarOpenPayPeriod,
  mostRecentClosedPeriod,
  unpaidCurrentPayPeriod,
  isPeriodUnpaid,
} from "@/lib/payroll/openPeriod";

describe("openPeriod calendar (parity with update_model_sheet)", () => {
  it("mostRecentClosedPeriod matches Palmetto example 2026-06-02 → May 18–31", () => {
    expect(mostRecentClosedPeriod("2026-06-02")).toEqual({
      start: "2026-05-18",
      end: "2026-05-31",
    });
  });

  it("on 2026-07-13 closed is Jun 29–Jul 12 and calendar open is Jul 13–26", () => {
    expect(mostRecentClosedPeriod("2026-07-13")).toEqual({
      start: "2026-06-29",
      end: "2026-07-12",
    });
    expect(calendarOpenPayPeriod("2026-07-13")).toEqual({
      start: "2026-07-13",
      end: "2026-07-26",
    });
  });
});

describe("unpaidCurrentPayPeriod (Issue #170)", () => {
  it("day after unpaid period_end keeps closed biweek (not next calendar)", () => {
    expect(unpaidCurrentPayPeriod("2026-07-13", false)).toEqual({
      start: "2026-06-29",
      end: "2026-07-12",
    });
  });

  it("day after paid period_end advances to next calendar biweek", () => {
    expect(unpaidCurrentPayPeriod("2026-07-13", true)).toEqual({
      start: "2026-07-13",
      end: "2026-07-26",
    });
  });

  it("mid-period with prior closed paid uses calendar open biweek", () => {
    // 2026-07-05 is inside Jun 29–Jul 12; closed as of that day is Jun 15–28
    expect(mostRecentClosedPeriod("2026-07-05")).toEqual({
      start: "2026-06-15",
      end: "2026-06-28",
    });
    expect(unpaidCurrentPayPeriod("2026-07-05", true)).toEqual({
      start: "2026-06-29",
      end: "2026-07-12",
    });
  });

  it("isPeriodUnpaid treats null/undefined as unpaid", () => {
    expect(isPeriodUnpaid(null)).toBe(true);
    expect(isPeriodUnpaid(undefined)).toBe(true);
    expect(isPeriodUnpaid(0)).toBe(false);
    expect(isPeriodUnpaid(1527.1)).toBe(false);
  });
});
