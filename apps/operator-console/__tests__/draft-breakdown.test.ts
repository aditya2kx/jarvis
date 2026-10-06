import { describe, expect, it } from "vitest";
import { addDay, emptyBreakdown, requiredByRule, shortHours } from "@/lib/labor/draft-breakdown";
import type { DayRule } from "@/lib/labor/schedule-inputs";

const mins = Array.from({ length: 4 * 24 }, (_, i) => i * 15);
const staffed = (t: number) => t >= 390 && t < 1230;
const rule = (id: string, fromMin: number, toMin: number, people: number): DayRule => ({
  id, days: [0, 1, 2, 3, 4, 5, 6], delivery: "any", fromMin, toMin, people,
});

describe("draft hours breakdown", () => {
  const floorOnly = mins.map((t) => (staffed(t) ? 1 : 0));
  const zeros = mins.map(() => 0);

  it("counts person-hours not already on the floor", () => {
    expect(shortHours(floorOnly, zeros)).toBe(14);
    expect(shortHours(floorOnly, mins.map((t) => (t >= 840 ? 1 : 0)))).toBe(7.5);
  });

  it("splits floor and each rule in order, later rules win", () => {
    const rules = [rule("a", 690, 840, 3), rule("b", 780, 840, 2)];
    const split = requiredByRule({ iso: "2026-10-12", mins, floorOnly, cover: zeros, dayRules: rules, deliveries: new Set() });
    expect(split.floor).toBe(14);
    // a: 11:30–2 at 3 people = +5h; b: lowers 1–2 PM from 3 to 2 = −1h.
    expect(split.rules).toEqual([{ id: "a", hours: 5 }, { id: "b", hours: -1 }]);
  });

  it("puts drafted hours beyond required person-hours into shift shape", () => {
    const b = emptyBreakdown(10, ["a"]);
    addDay(b, { floor: 14, rules: [{ id: "a", hours: 5 }] }, 22);
    expect(b).toEqual({ existing: 10, floor: 14, rules: [{ id: "a", hours: 5 }], shape: 3, peak: 0 });
  });
});
