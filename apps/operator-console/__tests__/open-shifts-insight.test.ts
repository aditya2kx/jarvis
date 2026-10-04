import { describe, expect, it } from "vitest";
import { peopleToHire, shiftKind, summarizeOpenShifts, type OpenShift } from "@/lib/labor/open-shifts-insight";

const shift = (date: string, startMin: number, endMin: number, extra: Partial<OpenShift> = {}): OpenShift => ({
  date, startMin, endMin, hours: (endMin - startMin) / 60, source: "draft", ...extra,
});

describe("open shifts insight", () => {
  it("labels shifts like the draft", () => {
    expect(shiftKind(390, 810)).toBe("open");
    expect(shiftKind(600, 960)).toBe("mid");
    expect(shiftKind(780, 1230)).toBe("close");
  });

  it("rolls up by weekday and type, separates suggested ADP shifts, finds the busiest date", () => {
    const s = summarizeOpenShifts([
      shift("2026-10-13", 390, 810),
      shift("2026-10-13", 780, 1230),
      shift("2026-10-13", 840, 1230),
      shift("2026-10-14", 390, 810, { source: "adp", suggested: "Huang, Wing" }),
    ]);
    expect(s.grid[1]!.open).toEqual({ shifts: 1, hours: 7 });
    expect(s.grid[1]!.close).toEqual({ shifts: 2, hours: 14 });
    expect(s.total).toEqual({ shifts: 4, hours: 28 });
    expect(s.unfilled).toEqual({ shifts: 3, hours: 21 });
    expect(s.peak).toEqual({ date: "2026-10-13", shifts: 3 });
    // 21h/week at 30h each = 1 person, but Tuesday needs 3 at once.
    expect(peopleToHire(s, 1, 30)).toBe(3);
  });
});
