import { describe, expect, it } from "vitest";
import { adpRoster, draftDay, fillOpenShift, type Availability } from "@/lib/labor/shift-draft";

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

  it("won't trim a shift below the shortest-shift setting", () => {
    // Free 6:30–10:30 = 4 h of the opener: fine at a 4 h minimum, too short at 4.5 h.
    const roster: Availability[] = [{ employee: "Early", maxWeekHours: 40, windows: all([390, 630]) }];
    const run = (minShiftMin: number) =>
      draftDay({
        iso: "2026-09-28", mins, onFloor: zeros, need: floorNeed,
        roster, weekHours: new Map(), busy: new Set(), minShiftMin,
      });
    expect(run(240).some((s) => s.employee === "Early")).toBe(true);
    const strict = run(270);
    expect(strict.some((s) => s.employee === "Early")).toBe(false);
    expect(strict.every((s) => s.endMin - s.startMin >= 270)).toBe(true);
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

  it("starts a shift when someone is first short, not at the template's earlier start", () => {
    // Day rules: 1 person 6:30–8:00, 2 from 8:00 — the second opener starts at 8:00, not 7:30.
    const need = mins.map((t) => (t < 390 ? 0 : t < 480 ? 1 : t < 1230 ? 2 : 0));
    const out = draftDay({
      iso: "2026-10-12", mins, onFloor: zeros, need, roster: [], weekHours: new Map(), busy: new Set(),
    });
    expect(out.filter((s) => s.kind === "open").map((s) => s.startMin)).toEqual([390, 480]);
  });

  it("keeps a late-starting shift at least the shortest-shift length", () => {
    // Short only 13:00–14:00: the Monday mid (10:00–17:00) from 13:00 is 4 h, so it starts at 12:30 (4.5 h).
    const need = mins.map((t) => (t >= 780 && t < 840 ? 1 : 0));
    const [s] = draftDay({
      iso: "2026-10-12", mins, onFloor: zeros, need, roster: [], weekHours: new Map(), busy: new Set(),
      minShiftMin: 270,
    });
    expect([s!.startMin, s!.endMin]).toEqual([750, 1020]);
  });

  it("ends a shift after the last short step, not at the template's later end", () => {
    // Tuesday's close template ends 20:45; need stops at 20:30.
    const need = mins.map((t) => (t >= 840 && t < 1230 ? 1 : 0));
    const [s] = draftDay({
      iso: "2026-10-13", mins, onFloor: zeros, need, roster: [], weekHours: new Map(), busy: new Set(),
    });
    expect([s!.startMin, s!.endMin]).toEqual([840, 1230]);
  });

  it("follow the need: one shift per short run, split evenly past the longest shift", () => {
    // 1 person 6:30–20:30 (14 h) → two 7 h shifts; a 2nd person 8:00–13:00 → one 5 h shift.
    const need = mins.map((t) => (t < 390 || t >= 1230 ? 0 : t >= 480 && t < 780 ? 2 : 1));
    const out = draftDay({
      iso: "2026-10-12", mins, onFloor: zeros, need, roster: [], weekHours: new Map(), busy: new Set(),
      minShiftMin: 270, maxShiftMin: 480, shiftTimes: "need",
    });
    expect(out.map((s) => [s.kind, s.startMin, s.endMin])).toEqual([
      ["open", 390, 810],
      ["mid", 480, 780],
      ["close", 810, 1230],
    ]);
  });

  it("follow the need: pads a short run to the shortest shift inside staffed hours", () => {
    // Short 19:30–20:30 only, staffed until 20:30 → 16:00–20:30 (4.5 h).
    const need = mins.map((t) => (t < 390 || t >= 1230 ? 0 : 1));
    const onFloor = mins.map((t) => (t < 1170 ? 1 : 0));
    const [s] = draftDay({
      iso: "2026-10-12", mins, onFloor, need, roster: [], weekHours: new Map(), busy: new Set(),
      minShiftMin: 270, shiftTimes: "need",
    });
    expect([s!.startMin, s!.endMin]).toEqual([960, 1230]);
  });

  it("adds nothing when the day already meets need", () => {
    const out = draftDay({
      iso: "2026-09-28", mins, onFloor: floorNeed, need: floorNeed,
      roster: [], weekHours: new Map(), busy: new Set(),
    });
    expect(out).toEqual([]);
  });
});

describe("adpRoster", () => {
  it("makes everyone available store hours, most hours first", () => {
    const r = adpRoster(new Map([["A", 5], ["B", 30]]));
    expect(r.map((a) => a.employee)).toEqual(["B", "A"]);
    expect(r[0]!.windows.every((w) => w?.[0] === 360 && w?.[1] === 1260)).toBe(true);
  });
});

describe("draftDay with ADP unavailability", () => {
  it("trims a shift around a blocked window and skips an all-day block", () => {
    const roster: Availability[] = [
      { employee: "Morning off", maxWeekHours: 40, windows: all([360, 1260]) },
      { employee: "Away", maxWeekHours: 40, windows: all([360, 1260]) },
    ];
    const out = draftDay({
      iso: "2026-10-03", mins, onFloor: zeros, need: floorNeed,
      roster, weekHours: new Map(), busy: new Set(),
      unavailable: new Map([
        ["Morning off", [{ fromMin: 360, toMin: 600, status: "pending" as const }]],
        ["Away", [{ fromMin: 0, toMin: 1440, status: "approved" as const }]],
      ]),
    });
    expect(out.some((s) => s.employee === "Away")).toBe(false);
    const m = out.filter((s) => s.employee === "Morning off");
    expect(m.length).toBeGreaterThan(0);
    expect(m.every((s) => s.startMin >= 600)).toBe(true);
  });
});

describe("fillOpenShift", () => {
  const slot = { iso: "2026-09-29", startMin: 540, endMin: 960 };

  it("suggests someone free for the whole slot, below-target first, and books their hours", () => {
    const roster: Availability[] = [
      { employee: "Busy", maxWeekHours: 40, windows: all([360, 1260]) },
      { employee: "Partial", maxWeekHours: 40, targetWeekHours: 30, windows: all([540, 720]) },
      { employee: "Blocked", maxWeekHours: 40, targetWeekHours: 30, windows: all([360, 1260]) },
      { employee: "Casual", maxWeekHours: 40, windows: all([360, 1260]) },
      { employee: "Target", maxWeekHours: 40, targetWeekHours: 30, windows: all([360, 1260]) },
    ];
    const weekHours = new Map([["Casual", 20]]);
    const busy = new Set(["Busy"]);
    const out = fillOpenShift({
      ...slot, roster, weekHours, busy,
      unavailable: new Map([["Blocked", [{ fromMin: 780, toMin: 840, status: "pending" as const }]]]),
    });
    expect(out).toMatchObject({ employee: "Target", kind: "mid", hours: 7, fillsOpen: true, trimmed: false });
    expect(weekHours.get("Target")).toBe(7);
    expect(busy.has("Target")).toBe(true);
  });

  it("returns null rather than trimming or breaking an hours cap", () => {
    const roster: Availability[] = [
      { employee: "Capped", maxWeekHours: 40, targetWeekHours: 20, windows: all([360, 1260]) },
    ];
    expect(
      fillOpenShift({ ...slot, roster, weekHours: new Map([["Capped", 15]]), busy: new Set() }),
    ).toBeNull();
  });
});

describe("last working day", () => {
  const leaving: Availability = { employee: "Leaving", maxWeekHours: 40, lastDay: "2026-09-30", windows: all([360, 1260]) };
  const fill = (iso: string) =>
    fillOpenShift({ iso, startMin: 540, endMin: 960, roster: [leaving], weekHours: new Map(), busy: new Set() });
  const draft = (iso: string) =>
    draftDay({ iso, mins, onFloor: zeros, need: floorNeed, roster: [leaving], weekHours: new Map(), busy: new Set() });

  it("is suggested through the last day and never after", () => {
    expect(fill("2026-09-30")?.employee).toBe("Leaving");
    expect(fill("2026-10-01")).toBeNull();
    expect(draft("2026-09-30").some((s) => s.employee === "Leaving")).toBe(true);
    expect(draft("2026-10-01").some((s) => s.employee === "Leaving")).toBe(false);
  });
});
