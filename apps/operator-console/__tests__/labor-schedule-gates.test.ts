import { describe, expect, it } from "vitest";
import {
  showChartSchedule,
  showCoverageSchedule,
} from "@/lib/labor/schedule-fetch-gates";

describe("showChartSchedule", () => {
  it("is true when today in Period, schedule window exists, grain not hour", () => {
    expect(
      showChartSchedule({
        includesToday: true,
        hasSchedWin: true,
        grain: "day",
      }),
    ).toBe(true);
  });

  it("is false on hour grain even when today + schedule window", () => {
    expect(
      showChartSchedule({
        includesToday: true,
        hasSchedWin: true,
        grain: "hour",
      }),
    ).toBe(false);
  });

  it("is false for future-only Period (includesToday false)", () => {
    expect(
      showChartSchedule({
        includesToday: false,
        hasSchedWin: true,
        grain: "day",
      }),
    ).toBe(false);
  });

  it("is false when schedule window missing", () => {
    expect(
      showChartSchedule({
        includesToday: true,
        hasSchedWin: false,
        grain: "week",
      }),
    ).toBe(false);
  });
});

describe("showCoverageSchedule", () => {
  it("is true whenever schedule window exists (any Aggregation)", () => {
    expect(showCoverageSchedule({ hasSchedWin: true })).toBe(true);
  });

  it("is false when schedule window missing (past-only Period)", () => {
    expect(showCoverageSchedule({ hasSchedWin: false })).toBe(false);
  });
});
