import { describe, expect, it } from "vitest";
import {
  calendarOpenPayPeriod,
  mostRecentClosedPeriod,
} from "@/lib/payroll/openPeriod";

describe("openPeriod calendar (parity with update_model_sheet)", () => {
  it("mostRecentClosedPeriod matches Palmetto example 2026-06-02 → May 18–31", () => {
    expect(mostRecentClosedPeriod("2026-06-02")).toEqual({
      start: "2026-05-18",
      end: "2026-05-31",
    });
  });

  it("on 2026-07-13 closed is Jun 29–Jul 12 and open is Jul 13–26", () => {
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
