import { describe, expect, it } from "vitest";
import type { CoveragePersonDay } from "@/lib/labor/coverage-model";
import {
  overlapMinutes,
  progressLabel,
  shiftKindOf,
  trainingLeftOf,
  trainingProgress,
} from "@/lib/labor/new-hire-training";
import { DEFAULT_TRAINING, type TrainingRules } from "@/lib/labor/schedule-inputs";

const rules: TrainingRules = { ...DEFAULT_TRAINING, experienced: ["Pro, Dolce"] };
const lane = (employee: string, startMin: number, endMin: number): CoveragePersonDay => ({
  employee,
  labor_bucket: "parttime",
  segments: [{ kind: "actual", startMin, endMin, hours: (endMin - startMin) / 60 }],
});
const opener = (who: string) => lane(who, 390, 840); // 6:30 AM – 2 PM
const closer = (who: string) => lane(who, 810, 1230); // 1:30 – 8:30 PM

describe("shiftKindOf / overlapMinutes", () => {
  it("classifies by ADP clock times", () => {
    expect(shiftKindOf(390, 840)).toBe("open");
    expect(shiftKindOf(450, 840)).toBe("open");
    expect(shiftKindOf(600, 1020)).toBe("mid");
    expect(shiftKindOf(810, 1230)).toBe("close");
  });

  it("counts covered minutes once across overlapping spans", () => {
    expect(overlapMinutes(600, 900, [[500, 700], [650, 800], [850, 1000]])).toBe(250);
    expect(overlapMinutes(600, 900, [])).toBe(0);
  });
});

describe("trainingProgress", () => {
  const today = "2026-10-30";

  it("counts supervised openers and closers since the ADP hire date and flags unsupervised ADP shifts", () => {
    const days = new Map<string, CoveragePersonDay[]>([
      ["2026-10-20", [opener("Pro, Dolce"), lane("New, Kai", 450, 840)]], // supervised opener
      ["2026-10-21", [opener("Other, Sam"), lane("New, Kai", 450, 840)]], // not experienced → doesn't count
      ["2026-10-22", [closer("Pro, Dolce"), lane("New, Kai", 900, 1230)]], // 5.5 h together → closer
      ["2026-11-02", [opener("Other, Sam"), lane("New, Kai", 450, 840)]], // upcoming, unsupervised
    ]);
    const out = trainingProgress({
      todayIso: today,
      rules,
      employees: ["Pro, Dolce", "New, Kai", "Other, Sam"],
      hireDates: new Map([["New, Kai", "2026-10-15"], ["Other, Sam", "2025-01-10"]]),
      days,
    });
    expect([...out.keys()]).toEqual(["New, Kai"]);
    const kai = out.get("New, Kai")!;
    expect(kai).toMatchObject({ hireDate: "2026-10-15", open: 1, close: 1, cleared: false });
    expect(kai.unsupervised).toEqual(["2026-11-02"]);
    expect(trainingLeftOf(kai)).toEqual({ open: 1, close: 1 });
    expect(progressLabel(kai)).toBe("1/2 openers · 1/2 closers");
  });

  it("requires the overlap minimum, then clears once both counts are met", () => {
    const short = { ...rules, overlapMin: 240 };
    const days = new Map<string, CoveragePersonDay[]>([
      // Dolce leaves at 2 PM, Kai starts 11 AM: 3 h together — short of 4 h.
      ["2026-10-20", [opener("Pro, Dolce"), lane("New, Kai", 660, 1230)]],
    ]);
    const args = { todayIso: today, employees: ["New, Kai"], hireDates: new Map([["New, Kai", "2026-10-01"]]), days };
    expect(trainingProgress({ ...args, rules: short }).get("New, Kai")!.close).toBe(0);
    expect(trainingProgress({ ...args, rules }).get("New, Kai")!.close).toBe(1);

    const one = { ...rules, openShifts: 0, closeShifts: 1 };
    const cleared = trainingProgress({ ...args, rules: one }).get("New, Kai")!;
    expect(cleared.cleared).toBe(true);
  });

  it("falls back to the first ADP shift without a hire date, and skips long-timers and the switch-off", () => {
    const days = new Map<string, CoveragePersonDay[]>([
      ["2026-05-01", [opener("Old, Ana")]],
      ["2026-10-25", [opener("Fresh, Lee")]],
    ]);
    const base = { todayIso: today, employees: ["Old, Ana", "Fresh, Lee"], hireDates: new Map<string, string>(), days };
    const out = trainingProgress({ ...base, rules });
    expect([...out.keys()]).toEqual(["Fresh, Lee"]);
    expect(out.get("Fresh, Lee")).toMatchObject({ hireDate: null, since: "2026-10-25", open: 0 });
    expect(trainingProgress({ ...base, rules: { ...rules, newHireDays: 0 } }).size).toBe(0);
  });
});
