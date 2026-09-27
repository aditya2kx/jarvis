import { describe, expect, it } from "vitest";
import { draftDay, sampleRoster, type Availability } from "@/lib/labor/shift-draft";

const mins: number[] = [];
for (let t = 6 * 60; t < 21 * 60; t += 15) mins.push(t);
const floorNeed = mins.map((t) => (t < 390 ? 0 : t < 450 ? 1 : t < 1230 ? 2 : 0));
const zeros = mins.map(() => 0);
const all = (w: [number, number] | null): ([number, number] | null)[] => Array(7).fill(w);

describe("draftDay", () => {
  it("covers an empty day with open + close blocks, all unassigned when no roster", () => {
    const out = draftDay({
      iso: "2026-09-28", mins, onFloor: zeros, need: floorNeed,
      roster: [], weekHours: new Map(), busy: new Set(),
    });
    expect(out.every((s) => s.employee === null)).toBe(true);
    const cover = mins.map((t) => out.filter((s) => t >= s.startMin && t < s.endMin).length);
    expect(cover.every((c, i) => c >= floorNeed[i]!)).toBe(true);
    expect(out.map((s) => s.kind).sort()).toEqual(["close", "close", "open", "open"]);
  });

  it("gives first pick to people below their hour target and trims to availability", () => {
    const roster: Availability[] = [
      { employee: "Casual", maxWeekHours: 40, windows: all([360, 1260]) },
      { employee: "Target", maxWeekHours: 40, targetWeekHours: 25, windows: all([360, 720]) },
    ];
    const out = draftDay({
      iso: "2026-09-28", mins, onFloor: zeros, need: floorNeed,
      roster, weekHours: new Map([["Casual", 10]]), busy: new Set(),
    });
    const t = out.find((s) => s.employee === "Target")!;
    expect(t.kind).toBe("open");
    expect(t.endMin).toBe(720);
    expect(t.trimmed).toBe(true);
  });

  it("never schedules past the hour target", () => {
    const roster: Availability[] = [
      { employee: "Target", maxWeekHours: 40, targetWeekHours: 25, windows: all([360, 1260]) },
    ];
    const out = draftDay({
      iso: "2026-09-28", mins, onFloor: zeros, need: floorNeed,
      roster, weekHours: new Map([["Target", 22]]), busy: new Set(),
    });
    expect(out.some((s) => s.employee)).toBe(false);
  });

  it("caps shifts per pay period and counts new ones", () => {
    const roster: Availability[] = [
      { employee: "Once", maxWeekHours: 40, maxShiftsPerPeriod: 1, windows: all([360, 1260]) },
    ];
    const periodShifts = new Map<string, number>();
    const day1 = draftDay({
      iso: "2026-09-28", mins, onFloor: zeros, need: floorNeed,
      roster, weekHours: new Map(), busy: new Set(), periodShifts,
    });
    expect(day1.filter((s) => s.employee === "Once")).toHaveLength(1);
    expect(periodShifts.get("Once")).toBe(1);
    const day2 = draftDay({
      iso: "2026-09-29", mins, onFloor: zeros, need: floorNeed,
      roster, weekHours: new Map(), busy: new Set(), periodShifts,
    });
    expect(day2.some((s) => s.employee)).toBe(false);
  });

  it("respects weekly max hours and people already working that day", () => {
    const roster: Availability[] = [
      { employee: "Full", maxWeekHours: 30, windows: all([360, 1260]) },
      { employee: "Busy", maxWeekHours: 40, windows: all([360, 1260]) },
    ];
    const out = draftDay({
      iso: "2026-09-28", mins, onFloor: zeros, need: floorNeed,
      roster, weekHours: new Map([["Full", 28]]), busy: new Set(["Busy"]),
    });
    expect(out.some((s) => s.employee)).toBe(false);
  });

  it("adds nothing when the day already meets need", () => {
    const out = draftDay({
      iso: "2026-09-28", mins, onFloor: floorNeed, need: floorNeed,
      roster: [], weekHours: new Map(), busy: new Set(),
    });
    expect(out).toEqual([]);
  });
});

describe("sampleRoster", () => {
  it("gives the two highest-hour people the widest availability", () => {
    const r = sampleRoster(new Map([["A", 5], ["B", 30], ["C", 25]]));
    expect(r.slice(0, 2).map((a) => [a.employee, a.maxWeekHours])).toEqual([["B", 40], ["C", 36]]);
  });
});
