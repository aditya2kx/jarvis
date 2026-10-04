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

describe("hours per person stacking", () => {
  it("adds open shifts as their own row and drafts on the suggested person", async () => {
    const { mergeHoursPerPerson, withOpenAndDraft, OPEN_ROW, UNASSIGNED_ROW } = await import("@/lib/labor/hours-per-person");
    const base = mergeHoursPerPerson(
      [{ date: "2026-10-05", employee: "A", labor_bucket: "parttime", hours: 6 }],
      [{ date: "2026-10-06", employee: "B", labor_bucket: "parttime", hours: 5 }],
      null,
      "9999-12-31",
    );
    const out = withOpenAndDraft(base, new Map([["A", 7], ["", 4.5]]), 8);
    expect(out.rows.map((r) => [r.employee, r.combined, r.open ?? null, r.suggested ?? null])).toEqual([
      ["A", 6, null, 7],
      [OPEN_ROW, 0, 8, null],
      ["B", 5, null, null],
      [UNASSIGNED_ROW, 0, null, 4.5],
    ]);
    expect(out.series.map((s) => s.key)).toEqual(["parttime", "parttime_sched", "open", "suggested"]);
  });

  it("lists every week through the forward horizon", () => {
    const w = weekOptions([], "2026-10-04", { start: "2026-09-21", end: "2026-10-31" });
    expect(w.map((x) => x.start)).toEqual(["2026-10-26", "2026-10-19", "2026-10-12", "2026-10-05", "2026-09-28", "2026-09-21"]);
  });
});
