import { describe, expect, it } from "vitest";
import {
  adpChangeKey,
  changePeople,
  personShifts,
  pruneQueue,
  readStoredQueue,
  shiftError,
  shiftText,
  stageAdp,
  stagedShift,
  summarizeApply,
  trayLines,
  unstageAdp,
  validateAdpChanges,
  type AdpChange,
  type ShiftChange,
} from "@/lib/labor/adp-changes";

const TODAY = "2026-10-28";
const T = { startMin: 420, endMin: 780 };
const shift = (c: ShiftChange): AdpChange => ({ area: "shift", c });
const del: ShiftChange = { kind: "shift-delete", date: "2026-10-31", employee: "Lee, Ana", ...T };
const edit: ShiftChange = { kind: "shift-edit", date: "2026-10-31", employee: "Lee, Ana", ...T, to: { startMin: 480, endMin: 780 } };

describe("keys and staging", () => {
  it("keeps one change per ADP shift: edit then delete leaves only the delete", () => {
    const q = stageAdp(stageAdp([], shift(edit)), shift(del));
    expect(q).toEqual([shift(del)]);
    expect(stagedShift(q, "shift|Lee, Ana|2026-10-31|420-780")).toEqual(del);
  });

  it("separates shift and unavailability keys, and unstages by key", () => {
    const u: AdpChange = { area: "unavailability", c: { kind: "approve", rowKey: "r1", employee: "Lee, Ana", summary: "x" } };
    const q = stageAdp(stageAdp([], shift(del)), u);
    expect(q.map(adpChangeKey)).toEqual(["s:shift|Lee, Ana|2026-10-31|420-780", "u:req|r1"]);
    expect(unstageAdp(q, ["u:req|r1"])).toEqual([shift(del)]);
    expect(changePeople(q)).toBe(1);
    const re = shift({ kind: "shift-reassign", date: "2026-10-31", employee: "Lee, Ana", toEmployee: "Roe, Sam", ...T });
    expect(changePeople([re])).toBe(2);
  });

  it("open shifts are keyed by slot, so two identical ones stay two", () => {
    const a = shift({ kind: "open-delete", slot: "d|adp|420|780", date: "2026-10-31", ...T });
    const b = shift({ kind: "open-delete", slot: "d|adp|420|780|1", date: "2026-10-31", ...T });
    expect(stageAdp([a], b)).toHaveLength(2);
  });
});

describe("shiftError", () => {
  it("only from tomorrow on", () => {
    expect(shiftError({ ...del, date: TODAY }, TODAY)).toMatch(/tomorrow/);
    expect(shiftError(del, TODAY)).toBeNull();
  });
  it("times, length, count, reassign target", () => {
    expect(shiftError({ ...del, endMin: 400 }, TODAY)).toMatch(/after the start/);
    expect(shiftError({ ...del, startMin: 0, endMin: 17 * 60 }, TODAY)).toMatch(/16 hours/);
    expect(shiftError({ ...edit, to: T }, TODAY)).toMatch(/Change the start or end/);
    expect(shiftError({ kind: "open-add", date: "2026-10-31", count: 0, ...T }, TODAY)).toMatch(/1 to 10/);
    expect(shiftError({ kind: "shift-reassign", date: "2026-10-31", employee: "A", toEmployee: "A", ...T }, TODAY)).toMatch(/someone else/);
    expect(shiftError({ kind: "shift-add", date: "2026-10-31", employee: " ", ...T }, TODAY)).toMatch(/Pick a person/);
  });
});

describe("validateAdpChanges", () => {
  it("rebuilds known fields only and drops the client slot", () => {
    const out = validateAdpChanges(
      [
        { area: "shift", c: { kind: "open-assign", slot: "x", date: "2026-10-31", employee: "Lee, Ana", ...T, extra: 1 } },
        { area: "unavailability", c: { kind: "add", employee: "Lee, Ana", summary: "s", date: "2026-10-30", allDay: true, from: null, to: null, weekly: false, until: null } },
      ],
      TODAY,
    );
    expect(out[0]).toEqual({ area: "shift", c: { kind: "open-assign", slot: "", date: "2026-10-31", employee: "Lee, Ana", ...T } });
    expect(out[1]!.area).toBe("unavailability");
  });

  it("names the bad change", () => {
    expect(() => validateAdpChanges([shift(del), shift({ ...del, date: TODAY })], TODAY)).toThrow(/Change 2: .*tomorrow/);
    expect(() => validateAdpChanges([shift(del), shift(del)], TODAY)).toThrow(/listed twice/);
    expect(() => validateAdpChanges([{ area: "nope", c: {} }], TODAY)).toThrow(/Unknown change/);
    expect(() => validateAdpChanges([{ area: "unavailability", c: { kind: "edit", employee: "A", summary: "" } }], TODAY)).toThrow(/ADP entry/);
    expect(() => validateAdpChanges([], TODAY)).toThrow(/Nothing to apply/);
  });

  it("two identical open-shift changes on one day stay two rows", () => {
    const a = { kind: "open-delete", slot: "s0", date: "2026-10-31", ...T };
    expect(validateAdpChanges([shift(a as ShiftChange), shift({ ...a, slot: "s1" } as ShiftChange)], TODAY)).toHaveLength(2);
  });
});

describe("trayLines", () => {
  it("folds a person's same-hours adds across days, shifts by day then unavailability", () => {
    const add = (date: string): AdpChange => shift({ kind: "shift-add", date, employee: "Lee, Ana", ...T });
    const lines = trayLines([
      { area: "unavailability", c: { kind: "deny", rowKey: "r", employee: "Bo", summary: "Bo · Fri" } },
      add("2026-11-02"),
      add("2026-10-30"),
      shift(del),
    ]);
    expect(lines.map((l) => [l.area, l.label])).toEqual([
      ["shift", "Add"],
      ["shift", "Delete"],
      ["unavailability", "Deny"],
    ]);
    expect(lines[0]!.text).toMatch(/^Lee, Ana · 2 days · Oct 30, Nov 2 · 7 AM–1 PM$/);
    expect(lines[0]!.keys).toHaveLength(2);
    expect(lines[1]!.destructive).toBe(true);
  });

  it("describes each kind in plain words", () => {
    expect(shiftText({ kind: "shift-reassign", date: "2026-10-31", employee: "A", toEmployee: "B", ...T })).toMatch(/A → B$/);
    expect(shiftText({ kind: "open-add", date: "2026-10-31", count: 2, ...T })).toMatch(/^Open shift · .* · 2 of them$/);
    expect(shiftText(edit)).toMatch(/→ 8 AM–1 PM$/);
  });
});

describe("pruneQueue + stored queue", () => {
  const now = {
    todayIso: TODAY,
    unavailabilityKeys: new Set(["r1"]),
    openSlots: new Set(["s1"]),
    hasShift: (e: string, d: string) => e === "Lee, Ana" && d === "2026-10-31",
  };
  it("drops changes ADP has overtaken or days now past", () => {
    const keep = [
      shift(del),
      shift({ kind: "open-delete", slot: "s1", date: "2026-10-31", ...T }),
      { area: "unavailability", c: { kind: "approve", rowKey: "r1", employee: "A", summary: "" } } as AdpChange,
    ];
    const drop = [
      shift({ ...del, employee: "Gone" }),
      shift({ ...del, date: TODAY }),
      shift({ kind: "open-edit", slot: "s9", date: "2026-10-31", ...T, to: { startMin: 480, endMin: 780 } }),
      { area: "unavailability", c: { kind: "deny", rowKey: "r9", employee: "A", summary: "" } } as AdpChange,
    ];
    expect(pruneQueue([...keep, ...drop], now)).toEqual(keep);
  });
  it("reads back only well-formed changes", () => {
    expect(readStoredQueue(JSON.stringify([shift(del), { area: "x" }, null]))).toEqual([shift(del)]);
    expect(readStoredQueue("{bad")).toEqual([]);
  });
});

describe("personShifts + summarizeApply", () => {
  it("parses a person's shifts with the draft tag, soonest first", () => {
    const out = personShifts(
      [
        { date: "2026-11-01", employee: "A", shift_ranges_json: JSON.stringify(["7:00 AM - 1:00 PM DRAFT"]) },
        { date: "2026-10-30", employee: "A", shift_ranges_json: JSON.stringify(["2:00 PM - 8:00 PM"]) },
        { date: "2026-10-27", employee: "A", shift_ranges_json: JSON.stringify(["2:00 PM - 8:00 PM"]) },
        { date: "2026-10-30", employee: "B", shift_ranges_json: JSON.stringify(["2:00 PM - 8:00 PM"]) },
      ],
      "A",
      "2026-10-29",
    );
    expect(out).toEqual([
      { date: "2026-10-30", startMin: 840, endMin: 1200, draft: false },
      { date: "2026-11-01", startMin: 420, endMin: 780, draft: true },
    ]);
  });
  it("counts outcomes and lists failures", () => {
    const s = summarizeApply([
      { change_id: "1", area: "shift", summary: "A", status: "applied", error: null },
      { change_id: "2", area: "shift", summary: "B", status: "failed", error: "not found" },
    ]);
    expect([s.applied, s.failed, s.errors]).toEqual([1, 1, ["B: not found"]]);
  });
});
