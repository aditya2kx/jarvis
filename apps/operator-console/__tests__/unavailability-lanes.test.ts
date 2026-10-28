import { describe, expect, it } from "vitest";
import { changeKey, entryError, stage, stagedFor, unstage, type StagedChange } from "@/lib/labor/availability-changes";
import { dayUnavailabilityCounts, hitsBlock, laneBlocks, type UnavailabilityInput } from "@/lib/labor/unavailability";

const weeklySat: UnavailabilityInput = {
  employee: "A", status: "pending", first_date: "2026-10-03", from_time: "06:00", to_time: "10:00",
  all_day: false, repeat_weekday: 5, repeat_until: "2026-11-01",
};
const allDayMon: UnavailabilityInput = {
  employee: "B", status: "approved", first_date: "2026-10-26", from_time: null, to_time: null,
  all_day: true, repeat_weekday: null, repeat_until: null,
};

describe("laneBlocks", () => {
  type Row = UnavailabilityInput & { row_key: string };
  const row = (r: Partial<Row>): Row => ({ ...allDayMon, row_key: "approved|B|2026-10-26", ...r });

  it("expands a weekly request onto its weekday only", () => {
    const sat = laneBlocks([{ ...weeklySat, row_key: "pending|A" }], "2026-10-31");
    expect(sat.get("A")).toEqual([expect.objectContaining({ fromMin: 360, toMin: 600 })]);
    expect(laneBlocks([{ ...weeklySat, row_key: "pending|A" }], "2026-11-01").size).toBe(0);
  });

  it("drops a scheduling-rule day an ADP entry already covers, keeps one it does not", () => {
    const adp = row({});
    const rule = row({ row_key: "rule|r1|2026-10-26" });
    const partialAdp = row({ employee: "A", all_day: false, from_time: "06:00", to_time: "10:00", row_key: "approved|A" });
    const ruleA = row({ employee: "A", row_key: "rule|r2|2026-10-26" });
    const m = laneBlocks([adp, rule, partialAdp, ruleA], "2026-10-26");
    expect(m.get("B")!.map((b) => b.row.row_key)).toEqual(["approved|B|2026-10-26"]);
    expect(m.get("A")!.map((b) => b.row.row_key)).toEqual(["rule|r2|2026-10-26", "approved|A"]);
  });
});

describe("dayUnavailabilityCounts", () => {
  it("counts people off and people with a request waiting, once each", () => {
    const dup = { ...allDayMon, from_time: "06:00", to_time: "08:00", all_day: false };
    const req = { ...weeklySat, first_date: "2026-10-26", repeat_weekday: 0 };
    expect(dayUnavailabilityCounts([allDayMon, dup, req], "2026-10-26")).toEqual({ off: 1, requests: 1 });
    expect(dayUnavailabilityCounts([allDayMon, req], "2026-10-27")).toEqual({ off: 0, requests: 0 });
  });
});

describe("hitsBlock", () => {
  it("flags overlap only, not shifts that merely touch the block", () => {
    const blocks = [{ fromMin: 360, toMin: 600 }];
    expect(hitsBlock(420, 780, blocks)).toBe(true);
    expect(hitsBlock(600, 780, blocks)).toBe(false);
    expect(hitsBlock(300, 360, blocks)).toBe(false);
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
