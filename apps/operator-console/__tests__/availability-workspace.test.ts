import { describe, expect, it } from "vitest";
import { dateSpans, mondayOf, weekDates, weekGrid } from "@/lib/labor/availability-week";
import { changeKey, entryError, stage, stagedFor, unstage, type StagedChange } from "@/lib/labor/availability-changes";
import type { UnavailabilityInput } from "@/lib/labor/unavailability";

const weeklySat: UnavailabilityInput = {
  employee: "A", status: "pending", first_date: "2026-10-03", from_time: "06:00", to_time: "10:00",
  all_day: false, repeat_weekday: 5, repeat_until: "2026-11-01",
};
const allDayMon: UnavailabilityInput = {
  employee: "B", status: "approved", first_date: "2026-10-26", from_time: null, to_time: null,
  all_day: true, repeat_weekday: null, repeat_until: null,
};

describe("week helpers", () => {
  it("anchors weeks on Monday", () => {
    expect(mondayOf("2026-10-31")).toBe("2026-10-26");
    expect(mondayOf("2026-10-26")).toBe("2026-10-26");
    expect(mondayOf("2026-11-01")).toBe("2026-10-26");
    expect(weekDates("2026-10-26")).toEqual([
      "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01",
    ]);
  });
});

describe("dateSpans", () => {
  it("folds consecutive days, across a month end, into runs", () => {
    expect(dateSpans(["2026-11-02", "2026-10-31", "2026-11-01", "2026-11-05", "2026-11-01"])).toEqual([
      { from: "2026-10-31", to: "2026-11-02" },
      { from: "2026-11-05", to: "2026-11-05" },
    ]);
  });
});

describe("weekGrid", () => {
  const shifts = [
    { date: "2026-10-31", employee: "A", shift_ranges_json: JSON.stringify(["7:00 AM - 1:00 PM"]) },
    { date: "2026-10-27", employee: "B", shift_ranges_json: JSON.stringify(["7:00 AM - 1:00 PM"]) },
  ];
  const grid = weekGrid([weeklySat, allDayMon], shifts, ["A", "B", "C"], "2026-10-26");

  it("keeps every person in the order given, with seven days each", () => {
    expect(grid.map((r) => r.employee)).toEqual(["A", "B", "C"]);
    expect(grid.every((r) => r.days.length === 7)).toBe(true);
    expect(grid[2]!.days.every((d) => !d.entries.length && !d.shifts.length)).toBe(true);
  });

  it("expands a weekly request onto its weekday and flags the overlapping shift", () => {
    const sat = grid[0]!.days[5]!;
    expect(sat.entries).toHaveLength(1);
    expect(sat.entries[0]).toMatchObject({ fromMin: 360, toMin: 600, clash: true });
    expect(sat.shifts).toEqual([{ startMin: 420, endMin: 780, hours: 6, clash: true }]);
    expect(grid[0]!.days[6]!.entries).toHaveLength(0);
  });

  it("hides a secondary entry an ADP entry already covers, keeps one it does not", () => {
    const rule = { ...allDayMon, status: "approved" as const, first_date: "2026-10-26", tag: "rule" };
    const partial = { ...rule, all_day: false, from_time: "06:00", to_time: "10:00", employee: "A" };
    const g = weekGrid<UnavailabilityInput & { tag?: string }>(
      [{ ...allDayMon, tag: "adp" }, rule, partial],
      [],
      ["A", "B"],
      "2026-10-26",
      (r) => r.tag === "rule",
    );
    expect(g[1]!.days[0]!.entries.map((e) => e.row.tag)).toEqual(["adp"]);
    expect(g[0]!.days[0]!.entries.map((e) => e.row.tag)).toEqual(["rule"]);
  });

  it("does not flag a shift on a different day than the entry", () => {
    const b = grid[1]!;
    expect(b.days[0]!.entries[0]).toMatchObject({ fromMin: 0, toMin: 1440, clash: false });
    expect(b.days[1]!.shifts[0]!.clash).toBe(false);
  });
});

describe("staged changes", () => {
  const approve: StagedChange = { kind: "approve", rowKey: "pending|A|2026-10-03", employee: "A", summary: "x" };
  const deny: StagedChange = { ...approve, kind: "deny" };
  const delDate: StagedChange = { kind: "delete", rowKey: "approved|B", employee: "B", scope: "date", date: "2026-10-26", summary: "x" };
  const delSeries: StagedChange = { ...delDate, scope: "series" };

  it("keeps one decision per request: the latest wins", () => {
    const q = stage(stage([], approve), deny);
    expect(q).toEqual([deny]);
    expect(stagedFor(q, approve.rowKey)?.kind).toBe("deny");
  });

  it("treats one date and the whole series as different changes", () => {
    expect(changeKey(delDate)).not.toBe(changeKey(delSeries));
    expect(stage(stage([], delDate), delSeries)).toHaveLength(2);
  });

  it("undoes by key", () => {
    const q = stage(stage([], approve), delDate);
    expect(unstage(q, changeKey(approve))).toEqual([delDate]);
  });
});

describe("entryError", () => {
  const base = { date: "2026-10-31", allDay: false, from: "06:00", to: "10:00", weekly: false, until: null };
  it("accepts a valid window and an all-day entry", () => {
    expect(entryError(base)).toBeNull();
    expect(entryError({ ...base, allDay: true, from: null, to: null })).toBeNull();
  });
  it("rejects an end time not after the start", () => {
    expect(entryError({ ...base, to: "06:00" })).toMatch(/after/);
  });
  it("rejects a repeat that ends before it starts", () => {
    expect(entryError({ ...base, weekly: true, until: "2026-10-01" })).toMatch(/before/);
  });
});
