import { describe, expect, it } from "vitest";
import {
  actualPunchWindow,
  scheduleTakesOverFrom,
  scheduledShiftWindow,
} from "@/lib/labor/actual-schedule-windows";
import type { DateWindow } from "@/lib/filters/range";

/**
 * Regression for the week of 2026-09-07, reported the night it finished.
 *
 * The chart handed off from actual to scheduled at a fixed "yesterday", so on
 * Sunday 09-13 it drew that day's 31.4-hour schedule instead of the 28.3 hours
 * already clocked and ingested — a forecast painted over a fact, and a finished
 * week reported as 184.3 combined hours instead of its true 181.3.
 */

const win = (start: string, end: string): DateWindow => ({
  start,
  end,
  label: "t",
  preset: "custom",
});

const TODAY = "2026-09-13";

describe("scheduleTakesOverFrom", () => {
  it("moves to tomorrow once today's punches have landed", () => {
    expect(scheduleTakesOverFrom(TODAY, "2026-09-13")).toBe("2026-09-14");
  });

  it("stays on today while today's punches are still missing", () => {
    expect(scheduleTakesOverFrom(TODAY, "2026-09-12")).toBe(TODAY);
  });

  it("never moves earlier than today, however far the ingest has lagged", () => {
    // A stale ingest means those days are awaiting punches, not that they were
    // never worked. Painting schedule backwards over them would invent hours.
    expect(scheduleTakesOverFrom(TODAY, "2026-09-05")).toBe(TODAY);
  });

  it("falls back to today when actuals cannot be read at all", () => {
    expect(scheduleTakesOverFrom(TODAY, null)).toBe(TODAY);
    expect(scheduleTakesOverFrom(TODAY, undefined)).toBe(TODAY);
  });

  it("crosses a month end correctly", () => {
    expect(scheduleTakesOverFrom("2026-08-31", "2026-08-31")).toBe("2026-09-01");
  });
});

describe("the Sep 7 week, as the operator saw it", () => {
  const week = win("2026-09-07", "2026-09-13");

  it("counts Sunday as actual once its hours are in, and shows no schedule", () => {
    const boundary = scheduleTakesOverFrom(TODAY, "2026-09-13");
    expect(actualPunchWindow(week, boundary)?.end).toBe("2026-09-13");
    // Every day of the week is now a fact; nothing left to forecast.
    expect(scheduledShiftWindow(week, boundary)).toBeNull();
  });

  it("is what the old fixed-yesterday boundary got wrong", () => {
    // Same week, boundary pinned at today: Sunday drops out of actuals and
    // reappears as schedule — the double-count the operator reported.
    expect(actualPunchWindow(week, TODAY)?.end).toBe("2026-09-12");
    expect(scheduledShiftWindow(week, TODAY)?.start).toBe("2026-09-13");
  });

  it("still forecasts the week ahead, which is genuinely unworked", () => {
    const boundary = scheduleTakesOverFrom(TODAY, "2026-09-13");
    const next = win("2026-09-14", "2026-09-20");
    expect(scheduledShiftWindow(next, boundary)?.start).toBe("2026-09-14");
    expect(actualPunchWindow(next, boundary)).toBeNull();
  });

  it("keeps showing schedule for today when punches have not arrived yet", () => {
    const boundary = scheduleTakesOverFrom(TODAY, "2026-09-12");
    expect(actualPunchWindow(week, boundary)?.end).toBe("2026-09-12");
    expect(scheduledShiftWindow(week, boundary)?.start).toBe(TODAY);
  });
});
