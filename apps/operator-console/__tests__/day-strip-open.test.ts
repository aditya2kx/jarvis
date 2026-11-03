import { describe, expect, it } from "vitest";
import { dayStripOpenCount } from "@/lib/labor/day-strip";
import type { DraftShift } from "@/lib/labor/shift-draft";

const draft = (partial: Partial<DraftShift> & Pick<DraftShift, "employee">): DraftShift => ({
  date: "2026-11-09",
  startMin: 390,
  endMin: 840,
  hours: 7,
  kind: "open",
  trimmed: false,
  slot: "t",
  ...partial,
});

describe("dayStripOpenCount", () => {
  it("counts ADP open lanes alone", () => {
    expect(dayStripOpenCount({ open: 2 })).toBe(2);
    expect(dayStripOpenCount({ open: 0 })).toBe(0);
  });

  it("adds unassigned draft shifts so cleared weeks still show open tags", () => {
    expect(
      dayStripOpenCount({ open: 0 }, [
        draft({ employee: null }),
        draft({ employee: null, startMin: 840, endMin: 1230, hours: 6.5, kind: "close", slot: "c" }),
        draft({ employee: "Ada", slot: "a" }),
      ]),
    ).toBe(2);
  });

  it("does not count fills of ADP opens or shifts already in ADP", () => {
    expect(
      dayStripOpenCount({ open: 1 }, [
        draft({ employee: null, fillsOpen: true }),
        draft({ employee: null, inAdp: true }),
      ]),
    ).toBe(1);
  });
});
