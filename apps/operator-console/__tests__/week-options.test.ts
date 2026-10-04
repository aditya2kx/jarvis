import { describe, expect, it } from "vitest";
import { inWeeks, weekOptions, weekRange, weekStartOf } from "@/lib/labor/week-options";

describe("week options", () => {
  it("starts weeks on Monday", () => {
    expect(weekStartOf("2026-10-04")).toBe("2026-09-28");
    expect(weekStartOf("2026-10-05")).toBe("2026-10-05");
  });

  it("labels ranges within and across months", () => {
    expect(weekRange("2026-10-05")).toBe("Oct 5–11");
    expect(weekRange("2026-09-28")).toBe("Sep 28–Oct 4");
  });

  it("lists touched weeks newest first and tags this and next week", () => {
    const w = weekOptions(["2026-09-22", "2026-10-07", "2026-10-01"], "2026-10-04");
    expect(w.map((x) => x.label)).toEqual(["Next week · Oct 5–11", "This week · Sep 28–Oct 4", "Sep 21–27"]);
    expect(inWeeks("2026-10-11", new Set(["2026-10-05"]))).toBe(true);
  });
});
