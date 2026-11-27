import { describe, expect, it } from "vitest";
import { dayStripOpenCount } from "@/lib/labor/day-strip";

describe("dayStripOpenCount", () => {
  it("counts ADP open lanes alone", () => {
    expect(dayStripOpenCount({ open: 2 })).toBe(2);
    expect(dayStripOpenCount({ open: 0 })).toBe(0);
  });
});
