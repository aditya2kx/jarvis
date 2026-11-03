import { describe, expect, it } from "vitest";
import type { CoveragePersonDay } from "@/lib/labor/coverage-model";
import {
  overlapMinutes,
  progressLabel,
  shiftKindOf,
  staffTags,
  isTrainingShift,
  trainingLeftOf,
  trainingProgress,
  trainsKind,
  TRAINING_FROM,
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

  it("treats a kind as training until its supervised shifts are done; mids until one is or openers and closers are", () => {
    expect(isTrainingShift({ open: 1, close: 0, mid: 1 }, "close")).toBe(false);
    expect(isTrainingShift({ open: 1, close: 0, mid: 1 }, "open")).toBe(true);
    expect(isTrainingShift({ open: 1, close: 0, mid: 1 }, "mid")).toBe(true);
    expect(isTrainingShift({ open: 1, close: 0, mid: 0 }, "mid")).toBe(false);
    expect(isTrainingShift({ open: 0, close: 2, mid: 1 }, "open")).toBe(false);
    expect(isTrainingShift({ open: 0, close: 0, mid: 1 }, "mid")).toBe(false);
    expect(isTrainingShift(undefined, "open")).toBe(false);
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
    expect(trainingLeftOf(kai)).toEqual({ open: 1, close: 1, mid: 1 });
    expect(progressLabel(kai)).toBe("1/2 openers · 1/2 closers · 0/1 mid");
  });

  it("counts a mid beside anyone experienced, then lets them work mids alone", () => {
    const pros: TrainingRules = { ...rules, experienced: ["Pro, Dolce", "Pro, Ola"], trainsOnly: { "Pro, Ola": "open" } };
    const days = new Map<string, CoveragePersonDay[]>([
      ["2026-10-20", [lane("Pro, Dolce", 390, 900), lane("New, Kai", 600, 1020)]], // 10 AM – 3 PM together
      ["2026-11-02", [lane("New, Kai", 600, 1020)]], // upcoming mid alone: fine now
    ]);
    const args = { todayIso: today, employees: ["New, Kai"], hireDates: new Map([["New, Kai", "2026-10-15"]]) };
    const kai = trainingProgress({ ...args, rules: pros, days }).get("New, Kai")!;
    expect(kai).toMatchObject({ mid: 1, needMid: 1, unsupervised: [], trainingDates: ["2026-10-20"] });
    expect(trainingLeftOf(kai)).toEqual({ open: 2, close: 2, mid: 0 });

    // A trainer who only trains openers still supervises a mid.
    const besideOla = new Map([["2026-10-20", [lane("Pro, Ola", 390, 900), lane("New, Kai", 600, 1020)]]]);
    expect(trainingProgress({ ...args, rules: pros, days: besideOla }).get("New, Kai")!.mid).toBe(1);

    // Alone before their first supervised mid: flagged.
    const alone = new Map([["2026-11-02", [lane("New, Kai", 600, 1020)]]]);
    expect(trainingProgress({ ...args, rules: pros, days: alone }).get("New, Kai")!.unsupervised).toEqual(["2026-11-02"]);
    expect(trainingProgress({ ...args, rules: { ...pros, midShifts: 0 }, days: alone }).get("New, Kai")!.unsupervised).toEqual([]);
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

  it("counts shifts worked, and only flags upcoming unsupervised shifts of a kind not yet cleared", () => {
    const days = new Map<string, CoveragePersonDay[]>([
      ["2026-10-20", [closer("Pro, Dolce"), closer("New, Lee")]],
      ["2026-10-22", [closer("Pro, Dolce"), closer("New, Lee")]],
      ["2026-10-24", [closer("New, Lee")]], // alone, but closers are already done
      ["2026-11-02", [closer("New, Lee")]], // upcoming close alone: fine
      ["2026-11-03", [opener("New, Lee")]], // upcoming opener alone: still needs supervision
    ]);
    const wing = trainingProgress({
      todayIso: today,
      rules,
      employees: ["New, Lee"],
      hireDates: new Map([["New, Lee", "2026-09-26"]]),
      days,
    }).get("New, Lee")!;
    expect(wing).toMatchObject({ worked: 3, open: 0, close: 2, cleared: false, marked: false });
    expect(wing.unsupervised).toEqual(["2026-11-03"]);
    // Closes after both supervised closers count toward need; the opener is still training.
    expect(wing.trainingDates).toEqual(["2026-10-20", "2026-10-22", "2026-11-03"]);
    expect(trainingLeftOf(wing)).toEqual({ open: 2, close: 0, mid: 1 });
  });

  it("treats someone the operator marked trained as cleared, still counting their shifts", () => {
    const days = new Map<string, CoveragePersonDay[]>([
      ["2026-10-20", [closer("New, Lee")]],
      ["2026-11-03", [opener("New, Lee")]],
    ]);
    const out = trainingProgress({
      todayIso: today,
      rules: { ...rules, trained: ["New, Lee"] },
      employees: ["New, Lee"],
      hireDates: new Map([["New, Lee", "2026-09-26"]]),
      days,
    });
    expect(out.get("New, Lee")).toMatchObject({ cleared: true, marked: true, worked: 1, unsupervised: [] });
    expect(staffTags(rules, out).has("New, Lee")).toBe(false);
  });

  it("lets a closers-only trainer supervise closes but not openers, and still trains them on openers", () => {
    const closesOnly: TrainingRules = { ...rules, experienced: ["Pro, Dolce", "New, Lee"], trainsOnly: { "New, Lee": "close" } };
    const days = new Map<string, CoveragePersonDay[]>([
      ["2026-10-20", [closer("New, Lee"), closer("New, Kai")]], // beside a closers trainer → counts
      ["2026-10-21", [opener("New, Lee"), opener("New, Kai")]], // openers: Lee can't supervise
      ["2026-10-22", [opener("Pro, Dolce"), opener("New, Lee")]], // Lee's own supervised opener
    ]);
    const out = trainingProgress({
      todayIso: today,
      rules: closesOnly,
      employees: ["Pro, Dolce", "New, Lee", "New, Kai"],
      hireDates: new Map([["New, Lee", "2026-09-26"], ["New, Kai", "2026-10-15"]]),
      days,
    });
    expect(out.get("New, Kai")).toMatchObject({ open: 0, close: 1 });
    expect(out.get("New, Lee")).toMatchObject({ needOpen: 2, needClose: 0, open: 1, cleared: false });
    expect(trainingLeftOf(out.get("New, Lee")!)).toEqual({ open: 1, close: 0, mid: 0 });
    expect(progressLabel(out.get("New, Lee")!)).toBe("1/2 openers");
    expect(trainsKind(closesOnly, "New, Lee", "close")).toBe(true);
    expect(trainsKind(closesOnly, "New, Lee", "open")).toBe(false);
    expect(trainsKind(closesOnly, "New, Lee", "mid")).toBe(true);
    expect(trainsKind(closesOnly, "Pro, Dolce", "mid")).toBe(true);
    expect(trainsKind(closesOnly, "New, Kai", "close")).toBe(false);
  });

  it("clears only the kind a new hire is marked trained on", () => {
    const out = trainingProgress({
      todayIso: today,
      rules: { ...rules, trainedOnly: { "New, Lee": "close" } },
      employees: ["New, Lee"],
      hireDates: new Map([["New, Lee", "2026-09-26"]]),
      days: new Map([["2026-11-03", [closer("New, Lee")]]]),
    });
    expect(out.get("New, Lee")).toMatchObject({ needOpen: 2, needClose: 0, cleared: false, marked: false, unsupervised: [] });
    expect(trainingLeftOf(out.get("New, Lee")!)).toEqual({ open: 2, close: 0, mid: 1 });
  });

  it("falls back to the first ADP shift without a hire date, and skips long-timers and an empty experienced list", () => {
    const days = new Map<string, CoveragePersonDay[]>([
      ["2026-05-01", [opener("Old, Ana")]],
      ["2026-10-25", [opener("Fresh, Lee")]],
    ]);
    const base = { todayIso: today, employees: ["Old, Ana", "Fresh, Lee"], hireDates: new Map<string, string>(), days };
    const out = trainingProgress({ ...base, rules });
    expect([...out.keys()]).toEqual(["Fresh, Lee"]);
    expect(out.get("Fresh, Lee")).toMatchObject({ hireDate: null, since: "2026-10-25", open: 0 });
    expect(trainingProgress({ ...base, rules: { ...rules, experienced: [] } }).size).toBe(0);
  });

  it("applies to everyone hired on or after TRAINING_FROM, however long ago", () => {
    const out = trainingProgress({
      todayIso: "2027-06-01",
      rules,
      employees: ["Aug, One", "Jul, Last"],
      hireDates: new Map([["Aug, One", TRAINING_FROM], ["Jul, Last", "2026-07-31"]]),
      days: new Map(),
    });
    expect([...out.keys()]).toEqual(["Aug, One"]);
  });
});

describe("staffTags", () => {
  it("tags experienced staff and new hires still training, with plain-language detail", () => {
    const days = new Map<string, CoveragePersonDay[]>([
      ["2026-11-02", [opener("Other, Sam"), lane("New, Kai", 450, 840)]],
    ]);
    const progress = trainingProgress({
      todayIso: "2026-10-30",
      rules,
      employees: ["New, Kai"],
      hireDates: new Map([["New, Kai", "2026-10-15"]]),
      days,
    });
    const tags = staffTags(rules, progress);
    expect(tags.get("Pro, Dolce")).toEqual({ kind: "experienced" });
    const kai = tags.get("New, Kai");
    expect(kai).toMatchObject({ kind: "training", label: "New hire · 0/2 openers · 0/2 closers · 0/1 mid" });
    expect(kai?.kind === "training" && kai.detail).toBe(
      "Hired Oct 15. Trains beside experienced staff (3h+ together) until they've done 2 openers, 2 closers and 1 mid" +
        " — training shifts count toward need.",
    );
    const onTop = staffTags({ ...rules, countTowardNeed: false }, progress).get("New, Kai");
    expect(onTop?.kind === "training" && onTop.detail).toBe(
      "Hired Oct 15. Trains on top of need beside experienced staff (3h+ together) until they've done 2 openers, 2 closers and 1 mid" +
        " — each kind counts toward need once done.",
    );
    expect(kai?.kind === "training" && [...kai.unsupervised]).toEqual(["2026-11-02"]);
    expect(staffTags({ ...rules, experienced: [] }, progress).size).toBe(0);
  });
});
